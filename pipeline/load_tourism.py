"""주요 유료관광지 월별 방문객 수를 받아 쌓는다(한국문화관광연구원 관광자원통계서비스, 공공데이터포털).

- 주소: https://apis.data.go.kr/B551940/TourismResourceStatsService/getPchrgTrrsrtVisitorList
- 인증키: 공공데이터포털 키(환경변수 DATALAB_KEY, 데이터랩과 같은 키). 이용허락 제한 없음.
- 응답: 연월(ym), 시도, 시군구(시 단위, 예: '수원특례시'), 시군구 코드 4자리(addrCd), 관광지명, 방문객 수(내외국인 합계).
- 약 5개월 늦게 채워지고 늦게 들어오는 관광지가 많아, 매번 최근 8개월은 다시 받는다.
- 처음에는 2019-01부터 한 번에 최대 36개월씩 거꾸로 채운다. 500줄씩 나눠 받고, 90분이 넘거나 3달 연속 실패하면 멈춘다.
- 표: tourism_monthly(ym, sido, gungu, addr_cd, name, cnt) — 판정에는 쓰지 않고 지역 성격·일정 안내용.
"""
import datetime as dt
import sys
import time
import xml.etree.ElementTree as ET
from urllib.parse import unquote

from common import connect, get, key

URL = "https://apis.data.go.kr/B551940/TourismResourceStatsService/getPchrgTrrsrtVisitorList"
FIRST = "201901"
RECENT = 8          # 늦게 채워지므로 최근 몇 달은 다시 받음
REFRESH_DAYS = 27   # 최근 달을 다시 받는 간격
PER_RUN = 36        # 한 번에 채우는 옛 달 수
PAGE = 500          # 한 쪽 줄 수. 3000줄을 한 번에 달라면 공공데이터포털이 504(시간 초과)를 자주 냄(2026-11-16)
BUDGET = 90 * 60    # 이 단계가 쓸 최대 시간(초). 갱신 단계 한도(2시간)보다 일찍 스스로 멈추고, 못 받은 달은 다음 번에
STOP_AFTER = 3      # 몇 달 연속 실패하면 API가 멈춘 것으로 보고 그만


def months(a, b):
    y, m = int(a[:4]), int(a[4:])
    while f"{y}{m:02d}" <= b:
        yield f"{y}{m:02d}"
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _page(k, ym, no):
    r = get(URL, {"serviceKey": k, "pageNo": no, "numOfRows": PAGE, "YM": ym}, tries=3, timeout=90, wait=15)
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
    return rows, int(root.findtext(".//totalCount") or 0)


def fetch(k, ym):
    rows, total = _page(k, ym, 1)
    no = 1
    while len(rows) < total and no * PAGE < total:
        no += 1
        more, _ = _page(k, ym, no)
        if not more:
            break
        rows += more
    return rows


def main():
    k = unquote(key("DATALAB_KEY"))   # 인코딩된 키를 넣어도 두 번 인코딩되지 않게
    con = connect()
    con.execute("""CREATE TABLE IF NOT EXISTS tourism_monthly(
        ym TEXT, sido TEXT, gungu TEXT, addr_cd TEXT, name TEXT, cnt INTEGER, PRIMARY KEY(ym, addr_cd, name))""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_tourism_sido ON tourism_monthly(sido, gungu)")
    con.execute("CREATE TABLE IF NOT EXISTS tourism_meta(k TEXT PRIMARY KEY, v TEXT)")
    t = dt.date.today()
    last = f"{t.year}{t.month:02d}"
    have = {ym for (ym,) in con.execute("SELECT DISTINCT ym FROM tourism_monthly")}
    allm = list(months(FIRST, last))
    # 최근 달은 27일마다 다시 받고, 옛 달이 비어 있으면 매일 조금씩 채운다(update.py는 이 단계를 매일 부름)
    row = con.execute("SELECT v FROM tourism_meta WHERE k='recent'").fetchone()
    recent_due = not row or (t - dt.date.fromisoformat(row[0])).days >= REFRESH_DAYS
    recent = allm[-RECENT:] if recent_due else []
    old = [m for m in reversed(allm[:-RECENT]) if m not in have][:PER_RUN]
    if not recent and not old:
        print("할 일 없음(최근 달은 27일 안에 받았고 옛 달도 다 있음)", flush=True)
        con.close()
        return
    got, bad, streak, t0 = 0, 0, 0, time.time()
    for ym in recent + old:
        if time.time() - t0 > BUDGET:
            print(f"  {BUDGET // 60}분이 넘어 여기서 멈춤(남은 달은 다음 번에)", flush=True)
            break
        if streak >= STOP_AFTER:
            print(f"  {STOP_AFTER}달 연속 실패 — API가 멈춘 것으로 보고 그만(다음 번에 다시)", flush=True)
            break
        try:
            rows = fetch(k, ym)
            streak = 0
        except Exception as e:     # 한 달 실패는 건너뛰고 다음 실행 때 다시
            bad += 1
            streak += 1
            print(f"  {ym} 실패: {e}", flush=True)
            continue
        if not rows:
            continue
        con.execute("DELETE FROM tourism_monthly WHERE ym=?", (ym,))
        con.executemany("INSERT OR REPLACE INTO tourism_monthly VALUES(?,?,?,?,?,?)", rows)
        con.commit()
        got += 1
        print(f"  {ym}: {len(rows)}곳", flush=True)
    if recent and got:
        con.execute("INSERT OR REPLACE INTO tourism_meta VALUES('recent', ?)", (t.isoformat(),))
        con.commit()
    n = con.execute("SELECT COUNT(DISTINCT ym), MIN(ym), MAX(ym) FROM tourism_monthly").fetchone()
    print(f"받은 달 {got}, 실패 {bad} / 쌓인 달 {n[0]}({n[1]}~{n[2]})", flush=True)
    con.close()
    if recent and bad and not got:   # 최근 달까지 하나도 못 받으면 API 문제로 보고 알림(옛 달만 실패면 다음 날 다시)
        sys.exit(f"관광지 방문객을 한 달도 받지 못함(실패 {bad}달)")


if __name__ == "__main__":
    main()
