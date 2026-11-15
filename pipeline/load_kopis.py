"""KOPIS(공연예술통합전산망) 공연장·공연 일정 받기.

- 공연장: 전체 목록을 받아 새로 생긴 곳만 상세(좌석 수·주소·좌표)를 받는다.
  주소에서 시도·시군구를 뽑아 둔다(주소·좌표는 나중에 읍면동 단위 분석에도 쓸 수 있게 그대로 보관).
- 공연: 큰 공연장(좌석 BIG_SEATS 이상)의 공연만 받는다. 작은 공연장은 시군구 유입을 움직이지 못한다.
  처음에는 START부터, 그 뒤에는 최근 90일 ~ 앞으로 180일을 다시 받는다(일정 변경 반영).
- 관객 수(예매 통계)는 쓰지 않는다. 공연 이름·기간·공연장만 '그 주에 있었던 일'의 판단 재료로 쓴다.
- 인증키는 환경변수 KOPIS_KEY. 3개월 넘게 안 쓰면 승인이 취소되므로 update.py가 6일마다 돌린다.

쓰는 법: python pipeline/load_kopis.py [--full]
"""
import datetime as dt
import sys
import time
import xml.etree.ElementTree as ET

from common import connect, get, key

BASE = "http://www.kopis.or.kr/openApi/restful"
BIG_SEATS = 3000
START = dt.date(2023, 1, 1)
SIDO_ALIAS = {"강원도": "강원특별자치도", "전라북도": "전북특별자치도"}


def xml(path, **params):
    r = get(f"{BASE}/{path}", params=params, timeout=60, wait=10)
    r.encoding = "utf-8"
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
    CREATE INDEX IF NOT EXISTS kopis_venue_region ON kopis_venue(sido, sigungu);
    """)


def load_venues(con, k):
    have = {r[0] for r in con.execute("SELECT mt10id FROM kopis_venue")}
    ids, page = [], 1
    while True:
        root = xml("prfplc", service=k, cpage=page, rows=100)
        rows = root.findall("db")
        if not rows:
            break
        ids += [txt(d, "mt10id") for d in rows]
        page += 1
        time.sleep(0.2)
    new = [i for i in ids if i and i not in have]
    print(f"공연장 {len(ids)}곳, 새로 받을 곳 {len(new)}곳", flush=True)
    today = dt.date.today().isoformat()
    for n, i in enumerate(new, 1):
        try:
            d = xml(f"prfplc/{i}", service=k).find("db")
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
        time.sleep(0.15)
    con.commit()


def load_perfs(con, k, full):
    big = con.execute("SELECT mt10id, name FROM kopis_venue WHERE seats>=?", (BIG_SEATS,)).fetchall()
    first = full or not con.execute("SELECT 1 FROM kopis_perf LIMIT 1").fetchone()
    today = dt.date.today()
    a = START if first else today - dt.timedelta(days=90)
    z = today + dt.timedelta(days=180)
    print(f"큰 공연장 {len(big)}곳, 기간 {a} ~ {z}", flush=True)
    stamp = today.isoformat()
    n = 0
    for vid, vname in big:
        s = a
        while s <= z:                                   # 석 달씩 끊어 받음
            e = min(s + dt.timedelta(days=91), z)
            page = 1
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
                time.sleep(0.15)
                if len(rows) < 100:
                    break
                page += 1
            s = e + dt.timedelta(days=1)
        con.commit()
    print(f"공연 {n}건 받음", flush=True)


def main():
    k = key("KOPIS_KEY")
    full = "--full" in sys.argv
    con = connect()
    setup(con)
    load_venues(con, k)
    load_perfs(con, k, full)
    cnt = con.execute("SELECT COUNT(*), MIN(s), MAX(e) FROM kopis_perf").fetchone()
    print(f"쌓인 공연 {cnt[0]}건 ({cnt[1]} ~ {cnt[2]})", flush=True)
    con.close()


if __name__ == "__main__":
    main()
