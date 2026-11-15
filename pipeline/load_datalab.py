"""한국관광 데이터랩 '지역별 방문자 수'(기초 시군구, 일별)를 받아 DB에 쌓는다.

- API: 한국관광공사 DataLabService/locgoRegnVisitrDDList (공공데이터포털 키, 환경변수 DATALAB_KEY)
- 지역 지정 변수가 없어 날짜만 넣으면 전국 시군구가 옴. 한 달 약 2.4만 행 → 1만 행씩 나눠 받음
- touDivCd: 1 현지인, 2 외지인, 3 외국인. 방문 지표로는 외지인(2)만 씀
- 자료는 약 한 달 늦게 나오고, 과거 값이 소급해서 바뀔 수 있어 최근 몇 달은 다시 받아 덮어씀
- 시와 일반구가 함께 들어 있음 → 표에는 그대로 두고, 쓸 때 일반구만 고름

쓰는 법:
  DATALAB_KEY=... python pipeline/load_datalab.py 2019-01 2026-10   # 기간(월) 지정
  DATALAB_KEY=... python pipeline/load_datalab.py                   # 최근 3개월 다시 받기
"""
import datetime as dt
import sys
import time

import pandas as pd
import requests
from urllib.parse import unquote

from common import connect, get, key

URL = "https://apis.data.go.kr/B551011/DataLabService/locgoRegnVisitrDDList"


def fetch_month(k, y, m):
    s = dt.date(y, m, 1)
    e = (s + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
    out, page = [], 1
    while True:
        r = get(URL, params={
            "serviceKey": k, "MobileOS": "ETC", "MobileApp": "nowcast-mcp", "_type": "json",
            "startYmd": s.strftime("%Y%m%d"), "endYmd": e.strftime("%Y%m%d"),
            "numOfRows": 10000, "pageNo": page})
        body = r.json()["response"]["body"]
        items = body.get("items") or {}
        items = items.get("item", []) if isinstance(items, dict) else []
        if isinstance(items, dict):
            items = [items]
        out += items
        if page * 10000 >= int(body.get("totalCount", 0)) or not items:
            break
        page += 1
        time.sleep(0.5)
    df = pd.DataFrame(out)
    if df.empty:
        return df
    df = df[["signguCode", "signguNm", "daywkDivCd", "touDivCd", "touNum", "baseYmd"]]
    df["touNum"] = pd.to_numeric(df["touNum"])
    df["touDivCd"] = df["touDivCd"].astype(int)
    df["day"] = pd.to_datetime(df["baseYmd"].astype(str), format="%Y%m%d").dt.strftime("%Y-%m-%d")
    return df.drop(columns="baseYmd")


def months(a, b):
    y, m = a
    while (y, m) <= b:
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def main():
    k = unquote(key("DATALAB_KEY"))  # 인코딩된 키를 넣어도 두 번 인코딩되지 않게
    if len(sys.argv) == 3:
        a = tuple(map(int, sys.argv[1].split("-")))
        b = tuple(map(int, sys.argv[2].split("-")))
    else:
        t = dt.date.today()
        b = (t.year, t.month)
        a = (t.year, t.month - 3) if t.month > 3 else (t.year - 1, t.month + 9)
    con = connect()
    con.execute("CREATE TABLE IF NOT EXISTS datalab_daily(signguCode TEXT, signguNm TEXT, daywkDivCd INT, "
                "touDivCd INT, touNum REAL, day TEXT)")
    for y, m in months(a, b):
        df = fetch_month(k, y, m)
        if df.empty:
            print(y, m, "자료 없음")
            continue
        con.execute("DELETE FROM datalab_daily WHERE substr(day,1,7)=?", (f"{y}-{m:02d}",))
        df.to_sql("datalab_daily", con, if_exists="append", index=False)
        con.commit()
        print(y, m, len(df), "행")
        time.sleep(1)
    con.execute("CREATE INDEX IF NOT EXISTS ix_dl ON datalab_daily(signguCode, touDivCd, day)")
    con.commit()


if __name__ == "__main__":
    main()
