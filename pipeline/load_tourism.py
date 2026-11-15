"""주요 유료관광지 월별 방문객 수를 받아 쌓는다(한국문화관광연구원 관광자원통계서비스, 공공데이터포털).

- 주소: https://apis.data.go.kr/B551940/TourismResourceStatsService/getPchrgTrrsrtVisitorList
- 인증키: 공공데이터포털 키(환경변수 DATALAB_KEY, 데이터랩과 같은 키). 이용허락 제한 없음.
- 응답: 연월(ym), 시도, 시군구(시 단위, 예: '수원특례시'), 시군구 코드 4자리(addrCd), 관광지명, 방문객 수(내외국인 합계).
- 약 5개월 늦게 채워지고 늦게 들어오는 관광지가 많아, 매번 최근 8개월은 다시 받는다.
- 처음에는 2019-01부터 한 번에 최대 24개월씩 거꾸로 채운다(한 달 받는 데 1분 남짓 걸림).
- 표: tourism_monthly(ym, sido, gungu, addr_cd, name, cnt) — 판정에는 쓰지 않고 지역 성격·일정 안내용.
"""
import datetime as dt
import sys
import xml.etree.ElementTree as ET
from urllib.parse import unquote

from common import connect, get, key

URL = "https://apis.data.go.kr/B551940/TourismResourceStatsService/getPchrgTrrsrtVisitorList"
FIRST = "201901"
RECENT = 8          # 늦게 채워지므로 최근 몇 달은 매번 다시 받음
PER_RUN = 24        # 한 번에 채우는 옛 달 수


def months(a, b):
    y, m = int(a[:4]), int(a[4:])
    while f"{y}{m:02d}" <= b:
        yield f"{y}{m:02d}"
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def fetch(k, ym):
    r = get(URL, {"serviceKey": k, "pageNo": 1, "numOfRows": 3000, "YM": ym}, timeout=300)
    root = ET.fromstring(r.content)
    code = (root.findtext(".//resultCode") or "").strip()
    if code and code != "0000":
        raise RuntimeError(f"{ym} 결과 코드 {code} {root.findtext('.//resultMsg')}")
    rows = []
    for it in root.iter("item"):
        g = {c.tag: (c.text or "").strip() for c in it}
        try:
            cnt = int(float(g.get("csMvCnt") or 0))
        except ValueError:
            continue
        if g.get("resNm"):
            rows.append((ym, g.get("sido", ""), g.get("gungu", ""), g.get("addrCd", ""), g["resNm"], cnt))
    total = int(root.findtext(".//totalCount") or 0)
    if total > len(rows) and total > 3000:
        print(f"  {ym}: 전체 {total}건 중 {len(rows)}건만 받음(한 쪽 3000건 한도)", flush=True)
    return rows


def main():
    k = unquote(key("DATALAB_KEY"))   # 인코딩된 키를 넣어도 두 번 인코딩되지 않게
    con = connect()
    con.execute("""CREATE TABLE IF NOT EXISTS tourism_monthly(
        ym TEXT, sido TEXT, gungu TEXT, addr_cd TEXT, name TEXT, cnt INTEGER, PRIMARY KEY(ym, addr_cd, name))""")
    t = dt.date.today()
    last = f"{t.year}{t.month:02d}"
    have = {ym for (ym,) in con.execute("SELECT DISTINCT ym FROM tourism_monthly")}
    allm = list(months(FIRST, last))
    recent = allm[-RECENT:]
    old = [m for m in reversed(allm[:-RECENT]) if m not in have][:PER_RUN]
    got, bad = 0, 0
    for ym in recent + old:
        try:
            rows = fetch(k, ym)
        except Exception as e:     # 한 달 실패는 건너뛰고 다음 실행 때 다시
            bad += 1
            print(f"  {ym} 실패: {e}", flush=True)
            continue
        if not rows:
            continue
        con.execute("DELETE FROM tourism_monthly WHERE ym=?", (ym,))
        con.executemany("INSERT OR REPLACE INTO tourism_monthly VALUES(?,?,?,?,?,?)", rows)
        con.commit()
        got += 1
        print(f"  {ym}: {len(rows)}곳", flush=True)
    n = con.execute("SELECT COUNT(DISTINCT ym), MIN(ym), MAX(ym) FROM tourism_monthly").fetchone()
    print(f"받은 달 {got}, 실패 {bad} / 쌓인 달 {n[0]}({n[1]}~{n[2]})", flush=True)
    con.close()
    if bad and not got:
        sys.exit(f"관광지 방문객을 한 달도 받지 못함(실패 {bad}달)")


if __name__ == "__main__":
    main()
