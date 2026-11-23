"""KOPIS(공연예술통합전산망) 공연장·공연 일정 받기.

- 공연장: 전체 목록을 받아 새로 생긴 곳만 상세(좌석 수·주소·좌표)를 받는다.
  주소에서 시도·시군구를 뽑아 둔다(주소·좌표는 나중에 읍면동 단위 분석에도 쓸 수 있게 그대로 보관).
- 공연: 큰 공연장(좌석 BIG_SEATS 이상)의 공연만 받는다. 작은 공연장은 시군구 유입을 움직이지 못한다.
  처음에는 START부터, 그 뒤에는 최근 90일 ~ 앞으로 180일을 다시 받는다(일정 변경 반영).
- 관객 수(예매 통계)는 쓰지 않는다. 공연 이름·기간·공연장만 '그 주에 있었던 일'의 판단 재료로 쓴다.
- 인증키는 환경변수 KOPIS_KEY. 3개월 넘게 안 쓰면 승인이 취소되므로 update.py가 6일마다 돌린다.
- 호출 한도(2026-11-23 KOPIS 담당자 답변): 같은 IP에서 1초에 10번을 넘으면 그 IP를 30~40분 막고 'Request Blocked'를
  돌려준다(홈페이지 접속도 함께 셈, 서비스키를 여러 개 써도 IP 기준으로 합침). 11-15 첫 받기 때 걸렸고, 막힌 동안에도
  400을 다시 시도하며 계속 불러 11-21~22 2시간씩 헛돎 → 모든 호출 사이를 MIN_GAP초 이상 띄우고(초당 4번 아래),
  막힘(400·'Request Blocked')이 오면 다시 시도하지 않고 그 자리에서 이번 받기를 멈춘다(실패 메일이 감).
- 공연 목록(pblprfr)은 개발가이드상 한 번에 31일까지 → 31일씩 요청

쓰는 법: python pipeline/load_kopis.py [--full]
"""
import datetime as dt
import sys
import time
import xml.etree.ElementTree as ET

from common import connect, get, key

BASE = "http://www.kopis.or.kr/openApi/restful"
BIG_SEATS = 3000
MIN_GAP = 0.25          # 호출 사이 최소 간격(초). KOPIS 한도는 IP당 초당 10번
SPAN = 31               # 공연 목록 한 번에 요청하는 날 수(개발가이드 최대 31일)
_last = [0.0]


class Blocked(RuntimeError):
    """KOPIS가 이 서버 IP를 막음(400·'Request Blocked'). 다시 시도하면 차단이 길어지므로 받기를 멈춘다."""
START = dt.date(2023, 1, 1)
SIDO_ALIAS = {"강원도": "강원특별자치도", "전라북도": "전북특별자치도"}


def xml(path, **params):
    gap = MIN_GAP - (time.monotonic() - _last[0])
    if gap > 0:
        time.sleep(gap)
    try:
        r = get(f"{BASE}/{path}", params=params, timeout=60, wait=10)   # 400은 다시 시도하지 않음(막힘)
    except RuntimeError as e:
        if "HTTP 400" in str(e) or "HTTP 429" in str(e):
            raise Blocked(f"KOPIS가 요청을 막음({path}): {e}") from None
        raise
    finally:
        _last[0] = time.monotonic()
    r.encoding = "utf-8"
    if "Request Blocked" in r.text[:500]:
        raise Blocked(f"KOPIS가 요청을 막음({path}): Request Blocked")
    return ET.fromstring(r.text)


def txt(e, tag):
    v = e.findtext(tag)
    return v.strip() if v else ""


def parse_addr(adres):
    """'서울특별시 구로구 경인로 430' → ('서울특별시', '구로구'), '경기도 고양시 일산서구 …' → ('경기도', '고양시 일산서구')."""
    t = adres.split()
    if len(t) < 2:
        return "", ""
    sido = SIDO_ALIAS.get(t[0], t[0])
    if sido.startswith("세종"):
        return sido, sido
    sg = t[1]
    if len(t) > 2 and sg.endswith("시") and t[2].endswith("구"):
        sg = f"{sg} {t[2]}"
    return sido, sg


def setup(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS kopis_venue(
      mt10id TEXT PRIMARY KEY, name TEXT, sido TEXT, sigungu TEXT, adres TEXT,
      la REAL, lo REAL, seats INTEGER, opende TEXT, fetched TEXT);
    CREATE TABLE IF NOT EXISTS kopis_perf(
      mt20id TEXT PRIMARY KEY, name TEXT, s TEXT, e TEXT, mt10id TEXT, venue TEXT, genre TEXT, fetched TEXT);
    CREATE INDEX IF NOT EXISTS kopis_perf_venue ON kopis_perf(mt10id, s, e);
    CREATE TABLE IF NOT EXISTS kopis_backfill(mt10id TEXT PRIMARY KEY, done TEXT);
    CREATE INDEX IF NOT EXISTS kopis_venue_region ON kopis_venue(sido, sigungu);
    """)


def load_venues(con, k):
    have = {r[0] for r in con.execute("SELECT mt10id FROM kopis_venue")}
    ids, page = [], 1
    while True:
        try:
            root = xml("prfplc", service=k, cpage=page, rows=100)
        except Blocked:
            raise
        except Exception as e:                         # 목록이 중간에 끊기면 이미 받은 공연장으로 계속
            print(f"  공연장 목록 {page}쪽 실패, 여기까지로 진행: {e}", flush=True)
            break
        rows = root.findall("db")
        if not rows:
            break
        ids += [txt(d, "mt10id") for d in rows]
        page += 1
    new = [i for i in ids if i and i not in have]
    print(f"공연장 {len(ids)}곳, 새로 받을 곳 {len(new)}곳", flush=True)
    today = dt.date.today().isoformat()
    for n, i in enumerate(new, 1):
        try:
            d = xml(f"prfplc/{i}", service=k).find("db")
        except Blocked:
            raise
        except Exception as e:
            print(f"  {i} 상세 실패: {e}", flush=True)
            continue
        if d is None:
            continue
        adres = txt(d, "adres")
        sido, sg = parse_addr(adres)
        try:
            seats = int(txt(d, "seatscale").replace(",", "") or 0)
        except ValueError:
            seats = 0
        def num(v):
            try:
                return float(v)
            except ValueError:
                return None
        con.execute("INSERT OR REPLACE INTO kopis_venue VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (i, txt(d, "fcltynm"), sido, sg, adres, num(txt(d, "la")), num(txt(d, "lo")),
                     seats, txt(d, "opende"), today))
        if n % 100 == 0:
            con.commit()
            print(f"  상세 {n}/{len(new)}", flush=True)
    con.commit()


def _venue_range(con, k, vid, vname, a, z, stamp):
    """한 공연장의 [a, z] 공연을 31일씩 받는다. (받은 건수, 실패한 구간 수). 막힘(Blocked)은 위로 올려 받기를 멈춤"""
    n = bad = 0
    s = a
    while s <= z:
        e = min(s + dt.timedelta(days=SPAN - 1), z)
        page = 1
        try:
            while True:
                root = xml("pblprfr", service=k, stdate=s.strftime("%Y%m%d"), eddate=e.strftime("%Y%m%d"),
                           cpage=page, rows=100, prfplccd=vid)
                rows = root.findall("db")
                for d in rows:
                    con.execute("INSERT OR REPLACE INTO kopis_perf VALUES(?,?,?,?,?,?,?,?)",
                                (txt(d, "mt20id"), txt(d, "prfnm"), txt(d, "prfpdfrom").replace(".", "-"),
                                 txt(d, "prfpdto").replace(".", "-"), vid, txt(d, "fcltynm") or vname,
                                 txt(d, "genrenm"), stamp))
                    n += 1
                if len(rows) < 100:
                    break
                page += 1
        except Blocked:
            con.commit()
            raise
        except Exception as ex:                        # 한 구간이 실패해도 나머지는 계속 받음
            bad += 1
            print(f"  건너뜀 {vname} {s}~{e}: {ex}", flush=True)
        s = e + dt.timedelta(days=1)
    con.commit()
    return n, bad


def load_perfs(con, k, full):
    """최근 석 달~앞으로 여섯 달은 매번 다시 받고, 2023년부터의 지난 자료는 공연장마다 한 번 다 받으면 끝(kopis_backfill).
    중간에 실패한 공연장은 다음 실행 때 지난 자료를 다시 받는다. 일부 구간만 실패하면 단계는 성공으로 끝내고 건수만 알린다."""
    big = con.execute("SELECT mt10id, name FROM kopis_venue WHERE seats>=?", (BIG_SEATS,)).fetchall()
    if full:
        con.execute("DELETE FROM kopis_backfill")
    done = {r[0] for r in con.execute("SELECT mt10id FROM kopis_backfill")}
    today = dt.date.today()
    recent = today - dt.timedelta(days=90)
    z = today + dt.timedelta(days=180)
    stamp = today.isoformat()
    todo = [v for v in big if v[0] not in done]
    print(f"큰 공연장 {len(big)}곳(지난 자료 받을 곳 {len(todo)}곳), 최근 기간 {recent} ~ {z}", flush=True)
    n = bad = 0
    for vid, vname in big:
        if vid in done:
            m, b = _venue_range(con, k, vid, vname, recent, z, stamp)
        else:
            m, b = _venue_range(con, k, vid, vname, START, z, stamp)
            if b == 0:
                con.execute("INSERT OR REPLACE INTO kopis_backfill VALUES(?,?)", (vid, stamp))
                con.commit()
        n += m
        bad += b
    print(f"공연 {n}건 받음, 실패해 건너뛴 구간 {bad}개(다음 실행 때 다시 받음)", flush=True)
    return n, bad


def main():
    k = key("KOPIS_KEY")
    full = "--full" in sys.argv
    con = connect()
    setup(con)
    try:
        load_venues(con, k)
        got, bad = load_perfs(con, k, full)
    except Blocked as e:                   # 막히면 더 부르지 않고 실패로 끝냄(메일). 30~40분 뒤 풀림, 계속되면 KOPIS에 회신
        con.commit()
        con.close()
        sys.exit(f"{e}\n→ 이번 받기를 멈춤. KOPIS 차단은 30~40분 뒤 저절로 풀림. 다음 실행에도 막히면 "
                 "kopis@gokams.or.kr에 회신(아직 차단 중이라고)")
    cnt = con.execute("SELECT COUNT(*), MIN(s), MAX(e) FROM kopis_perf").fetchone()
    print(f"쌓인 공연 {cnt[0]}건 ({cnt[1]} ~ {cnt[2]})", flush=True)
    con.close()
    if bad and not got:                    # 하나도 못 받고 전부 실패(인증키 만료 등)면 실패로 끝내 메일이 가게
        sys.exit(f"공연을 하나도 받지 못함(실패 구간 {bad}개)")


if __name__ == "__main__":
    main()
