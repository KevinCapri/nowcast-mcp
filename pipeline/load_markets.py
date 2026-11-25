"""전국전통시장표준데이터를 API로 받아 markets 표에 넣는다(2026-11-25 Kevin 확정).

- API: 공공데이터포털 15012894 '전국전통시장표준데이터' https://api.data.go.kr/openapi/tn_pubr_public_trdit_mrkt_api
  자동승인, 개발계정 하루 10,000건, 한 쪽 최대 1,000건(약 1,400곳 → 두 번). 키는 DATALAB_KEY(같은 공공데이터포털 키).
- 원자료: 개별 기관 자료를 매월 초 합쳐 올림, 갱신주기 연간 → update.py에서 27일마다.
- 이용 조건: 공공데이터포털 '이용허락범위 저작자표시'(자료_목록 D-0026). 서비스 출처 표시는 그대로.
- 지역: 지번 주소(없으면 도로명 주소)의 앞 두 낱말, '○○시 ○○구'면 세 낱말. 11-25 손으로 받은 server/markets.csv와
  1,403곳 모두 같게 나오는 것을 확인함.
- 받은 곳 수가 지난번보다 1할 넘게 줄었거나 오류면 덮어쓰지 않고 실패(update.py가 운영자 메일).
- 서버는 markets 표를 먼저 쓰고, 표가 없으면 server/markets.csv를 쓴다.
"""
import sys
from urllib.parse import unquote

import pandas as pd

from common import connect, get, key

URL = "https://api.data.go.kr/openapi/tn_pubr_public_trdit_mrkt_api"
COLS = ["sido", "sigungu", "name", "type", "cycle", "stores", "lat", "lon", "base_date"]


def region(addr):
    t = (addr or "").split()
    if len(t) < 2:
        return None, None
    sd, sg = t[0], t[1]
    if sg.endswith("시") and len(t) > 2 and t[2].endswith("구"):
        sg += " " + t[2]
    return sd, sg


def fetch(k):
    items, total = [], None
    for page in range(1, 6):
        j = get(URL, {"serviceKey": k, "pageNo": page, "numOfRows": 1000, "type": "json"}).json()
        j = j.get("response", j)
        code = (j.get("header") or {}).get("resultCode")
        if code == "03":                      # 데이터 없음 = 마지막 쪽 다음
            break
        if code != "00":
            raise RuntimeError(f"API 오류 {j.get('header')}")
        body = j.get("body") or {}
        total = int(body.get("totalCount") or 0)
        it = body.get("items") or []
        if isinstance(it, dict):
            it = it.get("item", [])
        items += it
        if len(items) >= total:
            break
    if total is None or len(items) != total:
        raise RuntimeError(f"받은 곳 {len(items)}곳이 전체 {total}곳과 다름")
    return items


def to_rows(items):
    rows = []
    for i in items:
        sd, sg = region(i.get("lnmadr") or i.get("rdnmadr"))
        if not sd:
            continue
        rows.append([sd, sg, i.get("mrktNm"), i.get("mrktType"), i.get("mrktEstblCycle"),
                     float(i["storNumber"]) if i.get("storNumber") else None,
                     i.get("latitude"), i.get("longitude"), i.get("referenceDate")])
    return pd.DataFrame(rows, columns=COLS)


def main():
    df = to_rows(fetch(unquote(key("DATALAB_KEY"))))
    con = connect()
    try:
        before = con.execute("SELECT COUNT(*) FROM markets").fetchone()[0]
    except Exception:
        before = 0
    if before and len(df) < before * 0.9:
        sys.exit(f"받은 곳이 {len(df)}곳으로 지난번 {before}곳보다 1할 넘게 줄어 바꾸지 않음(원자료 확인 필요)")
    df.to_sql("markets", con, if_exists="replace", index=False)
    print(f"전통시장 {len(df)}곳(지난번 {before}곳), 자료 기준일 {df['base_date'].max()}", flush=True)


if __name__ == "__main__":
    main()
