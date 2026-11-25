"""KOPIS(공연예술통합전산망) 공연장·공연 일정 받기.

- 공연장: 전체 목록을 받아 새로 생긴 곳만 상세(좌석 수·주소·좌표)를 받는다.
  주소에서 시도·시군구를 뽑아 둔다(주소·좌표는 나중에 읍면동 단위 분석에도 쓸 수 있게 그대로 보관).
- 공연: 큰 공연장(좌석 BIG_SEATS 이상)의 공연만 쌓는다. 작은 공연장은 시군구 유입을 움직이지 못한다.
- 관객 수(예매 통계)는 쓰지 않는다. 공연 이름·기간·공연장만 '그 주에 있었던 일'의 판단 재료로 쓴다.
- 인증키는 환경변수 KOPIS_KEY. 3개월 넘게 안 쓰면 승인이 취소된다(매일 부르므로 걱정 없음).
- 호출 한도(2026-11-23 KOPIS 담당자 답변): 같은 IP에서 1초에 10번을 넘으면 그 IP를 30~40분 막고 'Request Blocked'를
  돌려준다(홈페이지 접속도 함께 셈, 서비스키를 여러 개 써도 IP 기준으로 합침). 11-15 첫 받기 때 걸렸고, 막힌 동안에도
  400을 다시 시도하며 계속 불러 11-21~22 2시간씩 헛돎 → 모든 호출 사이를 MIN_GAP초 이상 띄우고(11-25부터 1분),
  막힘(400·'Request Blocked')이 오면 다시 시도하지 않고 그 자리에서 이번 받기를 멈춘다(실패 메일이 감).
- 공연 목록(pblprfr)은 개발가이드상 한 번에 31일까지 → 31일씩 요청
- 연결이 안 될 때(2026-11-23 저녁, 응답 없이 ConnectTimeout만 나며 2시간을 헛돎): 한 호출은 한 번만 다시 시도하고,
  연결 실패가 FAIL_RUN번 이어지면 그 자리에서 멈춤(실패 메일). 11-23의 '처음 불러 보기·샘플 받기'는 afterdate 방식에서 뺌
  (하룻밤 호출이 MAX_CALLS번뿐이고 진행이 남아 다음 날 이어지므로)

- 2026-11-25 방식 바꿈(afterdate): 공연 목록은 정보를 그때그때 조회하라고 만든 '조회형' API인데, 큰 공연장마다 31일씩
  매일 수백 번 '무작정 다 받기'로 짜서 KOPIS 크롤러 차단에 걸렸다(짧은 시간에 8~10번 부르면 'Request Blocked').
  → 전국 공연 목록을 afterdate(그날 뒤로 등록·수정된 것만)로 받는다.
  ① 매일 새벽 정기 받기에서만(update.py NIGHT_ONLY — 배포로 다시 켜질 때 도는 받기에선 안 부름)
  ② 한 회차 = 회차 시작일 기준 지난 31일 + 앞으로 31일 × 6, 31일씩 7구간 × 100건씩 쪽 넘기기
  ③ 호출 간격 MIN_GAP(1분), 하룻밤 MAX_CALLS번까지. 못 끝내면 어디까지 했는지(kopis_state) 기억해 두고 다음 날 이어서.
     7구간을 다 끝내야 다음 afterdate를 그 회차 시작일로 옮긴다 → 빠지는 날이 없음. 첫 회차는 FIRST_AFTER(11-15)부터
  ④ 공연장 찾기: 목록의 공연장 이름 + 시도(area)로 kopis_venue에서 찾음(11-25 시험 100건 중 99건, 이름은 전국에 겹치지 않음).
     좌석 BIG_SEATS 이상 공연장 공연만 쌓음(지금처럼). 못 찾은 이름은 kopis_unmatched에 모아 둠
  ⑤ 공연장 목록은 한 달에 한 번, 남은 호출로 며칠에 나눠 받고 새 곳만 상세. 새 큰 공연장이 나오면 그곳 공연만 한 번 따로 받음
     (afterdate로는 그 공연장의 예전에 등록된 공연이 다시 안 오므로)
  ⑥ 막히면 지금처럼 그 자리에서 멈춤(몇 번째 호출인지 남김). 다음 날 이어서

쓰는 법: python pipeline/load_kopis.py
"""
import datetime as dt
import json
import sys
import time
import xml.etree.ElementTree as ET

from common import connect, get, key

BASE = "http://www.kopis.or.kr/openApi/restful"
# 2026-11-25: KOPIS 크롤러 차단이 User-Agent가 python-requests인 요청만 400 'Request Blocked'로 막음
# (GitHub Actions 새 IP에서 시험: python-requests 400, 우리 이름·curl·브라우저 200). IP 차단이 아니었음 → 우리 이름을 밝힘
HEADERS = {"User-Agent": "nowcast-mcp (+https://nowcast-mcp.azurewebsites.net)"}
BIG_SEATS = 3000
MIN_GAP = 60.0          # 호출 사이 최소 간격(초). 11-25: 0.25초·2초 간격 모두 8~10번째에 'Request Blocked'(짧은 시간 안 횟수로 막음) → 1분
MAX_CALLS = 20          # 하룻밤 호출 수 상한(처음엔 20번, 로그를 보며 늘림)
WINDOWS = 7             # 한 회차 구간 수: 지난 31일 1구간 + 앞으로 31일 6구간
FIRST_AFTER = dt.date(2026, 11, 15)   # 첫 회차 afterdate(마지막으로 다 받은 날)
SPAN = 31               # 공연 목록 한 번에 요청하는 날 수(개발가이드 최대 31일)
FAIL_RUN = 2            # 연결 실패가 이만큼 이어지면 받기를 멈춤
_last = [0.0]
_count = [0, None]       # 이번 받기의 호출 수, 첫 호출 시각(막힐 때 기록)
_fails = [0]            # 이어진 연결 실패 수


class Budget(RuntimeError):
    """이번 밤 호출 수(MAX_CALLS)를 다 씀. 어디까지 했는지는 kopis_state에 있고 다음 날 이어서 함."""


class Blocked(RuntimeError):
    """KOPIS가 이 서버 IP를 막음(400·'Request Blocked'). 다시 시도하면 차단이 길어지므로 받기를 멈춘다."""


class Unreachable(Blocked):
    """KOPIS에 연결이 안 됨(시간 초과 등, 응답이 없음). KOPIS 차단·KOPIS 점검·우리 서버 쪽 문제 중 무엇인지는 모름."""


def _conn_fail(e):
    return any(w in str(e) for w in ("Timeout", "ConnectionError"))

SIDO_ALIAS = {"강원도": "강원특별자치도", "전라북도": "전북특별자치도"}


def _where():
    """막힌 때: 이번 받기의 몇 번째 호출, 첫 호출부터 몇 초(11-25 — 차단 기준을 숫자로 찾으려고)."""
    t = time.monotonic() - _count[1] if _count[1] is not None else 0
    return f"{_count[0]}번째 호출, 첫 호출부터 {t:.0f}초, 간격 {MIN_GAP}초"


def xml(path, _tries=2, _wait=60, **params):
    if _count[0] >= MAX_CALLS:
        raise Budget(f"이번 밤 호출 {MAX_CALLS}번을 다 씀")
    gap = MIN_GAP - (time.monotonic() - _last[0])
    if gap > 0:
        time.sleep(gap)
    if _count[1] is None:
        _count[1] = time.monotonic()
    _count[0] += 1
    try:
        r = get(f"{BASE}/{path}", params=params, tries=_tries, timeout=60, wait=_wait, headers=HEADERS)   # 400은 다시 시도하지 않음(막힘)
    except RuntimeError as e:
        if "HTTP 400" in str(e) or "HTTP 429" in str(e):
            raise Blocked(f"KOPIS가 요청을 막음({path}, {_where()}): {e}") from None
        if _conn_fail(e):
            _fails[0] += 1
            if _fails[0] >= FAIL_RUN:
                raise Unreachable(f"KOPIS 연결 실패가 {_fails[0]}번 이어짐({path}): {e}") from None
        raise
    finally:
        _last[0] = time.monotonic()
    _fails[0] = 0
    r.encoding = "utf-8"
    if "Request Blocked" in r.text[:500]:
        raise Blocked(f"KOPIS가 요청을 막음({path}, {_where()}): Request Blocked")
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
    CREATE TABLE IF NOT EXISTS kopis_state(k TEXT PRIMARY KEY, v TEXT);
    CREATE TABLE IF NOT EXISTS kopis_unmatched(name TEXT, area TEXT, n INTEGER, last TEXT, PRIMARY KEY(name, area));
    """)


def get_state(con, k, default=None):
    row = con.execute("SELECT v FROM kopis_state WHERE k=?", (k,)).fetchone()
    return row[0] if row else default


def set_state(con, **kv):
    for k, v in kv.items():
        if v is None:
            con.execute("DELETE FROM kopis_state WHERE k=?", (k,))
        else:
            con.execute("INSERT OR REPLACE INTO kopis_state VALUES(?, ?)", (k, str(v)))
    con.commit()


def _venue_map(con):
    """(공연장 이름, 시도) → (mt10id, 좌석). 시도가 안 맞아도 이름이 전국에 하나면 그 이름만으로도 찾음."""
    by_both, by_name = {}, {}
    for vid, name, sido, seats in con.execute("SELECT mt10id, name, sido, seats FROM kopis_venue"):
        nm = (name or "").strip()
        by_both[(nm, sido)] = (vid, seats or 0)
        by_name.setdefault(nm, []).append((vid, seats or 0))
    return by_both, {n: v[0] for n, v in by_name.items() if len(v) == 1}


def _find(maps, name, area):
    by_both, by_name = maps
    nm = (name or "").strip()
    sd = SIDO_ALIAS.get(area, area)
    return by_both.get((nm, sd)) or by_name.get(nm)


def _windows(start):
    """회차 시작일 기준 지난 31일 + 앞으로 31일 × 6 (31일씩 WINDOWS구간)."""
    a = start - dt.timedelta(days=SPAN)
    return [(a + dt.timedelta(days=SPAN * i), a + dt.timedelta(days=SPAN * (i + 1) - 1)) for i in range(WINDOWS)]


def load_changes(con, k, today):
    """afterdate로 바뀐 공연만 받는다. 회차 진행(after·cycle·win·page)은 kopis_state에 있어 다음 날 이어서 한다."""
    after = dt.date.fromisoformat(get_state(con, "after", FIRST_AFTER.isoformat()))
    cycle = get_state(con, "cycle")
    if cycle is None:                       # 새 회차 시작
        cycle = today.isoformat()
        set_state(con, cycle=cycle, win=0, page=1)
    cycle = dt.date.fromisoformat(cycle)
    win, page = int(get_state(con, "win", 0)), int(get_state(con, "page", 1))
    wins = _windows(cycle)
    maps = _venue_map(con)
    stamp = today.isoformat()
    got = kept = miss = 0
    print(f"바뀐 공연 받기: afterdate {after}, 회차 시작 {cycle}, {win + 1}/{WINDOWS}구간 {page}쪽부터", flush=True)
    while win < WINDOWS:
        s, e = wins[win]
        root = xml("pblprfr", service=k, stdate=s.strftime("%Y%m%d"), eddate=e.strftime("%Y%m%d"),
                   cpage=page, rows=100, afterdate=after.strftime("%Y%m%d"))
        rows = root.findall("db")
        for d in rows:
            got += 1
            v = _find(maps, txt(d, "fcltynm"), txt(d, "area"))
            if v is None:
                miss += 1
                con.execute("INSERT INTO kopis_unmatched VALUES(?,?,1,?) ON CONFLICT(name, area) "
                            "DO UPDATE SET n=n+1, last=excluded.last", (txt(d, "fcltynm"), txt(d, "area"), stamp))
                continue
            if v[1] < BIG_SEATS:
                continue
            con.execute("INSERT OR REPLACE INTO kopis_perf VALUES(?,?,?,?,?,?,?,?)",
                        (txt(d, "mt20id"), txt(d, "prfnm"), txt(d, "prfpdfrom").replace(".", "-"),
                         txt(d, "prfpdto").replace(".", "-"), v[0], txt(d, "fcltynm"), txt(d, "genrenm"), stamp))
            kept += 1
        if len(rows) < 100:
            win, page = win + 1, 1
        else:
            page += 1
        set_state(con, win=win, page=page)
    # 7구간을 다 끝냄 → 다음 회차는 이 회차 시작일 뒤로 바뀐 것부터
    set_state(con, after=cycle.isoformat(), cycle=None, win=None, page=None)
    print(f"회차 끝: 받은 {got}건 중 큰 공연장 {kept}건 쌓음, 공연장 못 찾음 {miss}건 → 다음 afterdate {cycle}", flush=True)
    return got, kept, miss


def load_venues(con, k, today):
    """공연장 목록은 한 달에 한 번, 남은 호출로 며칠에 나눠 받는다. 새 곳만 상세를 받고, 새 큰 공연장은 공연을 한 번 따로 받음."""
    month = today.strftime("%Y-%m")
    if get_state(con, "venue_month") == month:
        return
    page = int(get_state(con, "venue_page", 1))
    queue = json.loads(get_state(con, "venue_new", "[]"))
    have = {r[0] for r in con.execute("SELECT mt10id FROM kopis_venue")}
    print(f"공연장 목록({month}): {page}쪽부터, 상세 기다리는 곳 {len(queue)}곳", flush=True)
    while page:
        rows = xml("prfplc", service=k, cpage=page, rows=100).findall("db")
        queue += [i for i in (txt(d, "mt10id") for d in rows) if i and i not in have and i not in queue]
        page = page + 1 if len(rows) == 100 else 0
        set_state(con, venue_page=page or None, venue_new=json.dumps(queue))
    stamp = today.isoformat()
    while queue:
        i = queue[0]
        d = xml(f"prfplc/{i}", service=k).find("db")
        if d is not None:
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
                         seats, txt(d, "opende"), stamp))
            if seats >= BIG_SEATS:
                big = json.loads(get_state(con, "venue_big", "[]"))
                set_state(con, venue_big=json.dumps(big + [i]))
            print(f"  새 공연장 {txt(d, 'fcltynm')} ({sido} {sg}, {seats}석)", flush=True)
        queue.pop(0)
        set_state(con, venue_new=json.dumps(queue))
    set_state(con, venue_month=month, venue_page=None, venue_new=None)
    print(f"공연장 목록({month}) 끝", flush=True)


def load_new_big(con, k, today):
    """새로 찾은 큰 공연장의 공연(지난 31일 ~ 앞으로 186일)을 한 번 받는다. 호출이 모자라면 다음 날 그 공연장부터 다시."""
    big = json.loads(get_state(con, "venue_big", "[]"))
    while big:
        vid = big[0]
        name = (con.execute("SELECT name FROM kopis_venue WHERE mt10id=?", (vid,)).fetchone() or [vid])[0]
        if MAX_CALLS - _count[0] < WINDOWS:          # 한 곳에 7번 넘게 드니 남은 호출이 모자라면 다음 날
            return
        n, bad = _venue_range(con, k, vid, name, today - dt.timedelta(days=SPAN), today + dt.timedelta(days=SPAN * 6),
                              today.isoformat())
        print(f"  새 큰 공연장 {name}: 공연 {n}건(실패 구간 {bad})", flush=True)
        big.pop(0)
        set_state(con, venue_big=json.dumps(big) if big else None)


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
        except (Blocked, Budget):                      # 막힘·오늘 몫 다 씀은 위로(새 큰 공연장은 다음 날 다시)
            con.commit()
            raise
        except Exception as ex:                        # 한 구간이 실패해도 나머지는 계속 받음
            bad += 1
            print(f"  건너뜀 {vname} {s}~{e}: {ex}", flush=True)
        s = e + dt.timedelta(days=1)
    con.commit()
    return n, bad


def main():
    k = key("KOPIS_KEY")
    con = connect()
    setup(con)
    today = dt.date.today()
    try:
        load_changes(con, k, today)        # 바뀐 공연이 먼저
        load_venues(con, k, today)         # 남은 호출로 공연장 목록(한 달에 한 번)
        load_new_big(con, k, today)
    except Budget as e:                    # 오늘 몫을 다 씀 → 다음 날 이어서(실패 아님)
        print(f"{e} → 다음 날 이어서", flush=True)
    except Unreachable as e:               # 연결이 안 되면 더 부르지 않고 실패로 끝냄(메일). 진행은 남아 다음 날 이어서
        con.close()
        sys.exit(f"{e}\n→ 이번 받기를 멈춤(연결 안 됨). 다음 날 이어서 함. KOPIS 차단·KOPIS 점검·우리 서버 쪽 문제 중 무엇인지 모름 → "
                 "update.log에서 이번 받기의 앞부분을 보고, Kudu에서 KOPIS와 다른 사이트에 한 번씩 접속해 볼 것")
    except Blocked as e:                   # 막히면 더 부르지 않고 실패로 끝냄(메일). 진행은 남아 다음 날 이어서
        con.close()
        sys.exit(f"{e}\n→ 이번 받기를 멈춤. 다음 날 이어서 함. 계속 막히면 '언제·어느 IP·어느 주소·어떤 응답'으로 KOPIS에 한 번만 문의")
    cnt = con.execute("SELECT COUNT(*), MIN(s), MAX(e) FROM kopis_perf").fetchone()
    print(f"이번 밤 호출 {_count[0]}번. 쌓인 공연 {cnt[0]}건 ({cnt[1]} ~ {cnt[2]})", flush=True)
    con.close()


if __name__ == "__main__":
    main()
