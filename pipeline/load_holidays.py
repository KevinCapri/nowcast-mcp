"""한국천문연구원 특일 정보에서 공휴일(getRestDeInfo)을 받아 DB에 넣고, 주차별 공휴일 수를 만든다.

- 공공데이터포털 키(환경변수 DATALAB_KEY), 응답 XML
- 선거일·임시공휴일 포함. 대체공휴일은 관보 공포 뒤에야 반영되므로 해마다 다시 받음
- 같은 날 공휴일이 두 개면 두 줄로 옴 → 주차별 일수는 날짜 기준으로 한 번만 셈
- 설·추석 연휴는 holiday_week.lunar(설·추석 날 수)로 따로 셈

쓰는 법: DATALAB_KEY=... python pipeline/load_holidays.py 2019 2027
"""
import datetime as dt
import sys
import time
import xml.etree.ElementTree as ET

import pandas as pd
import requests
from urllib.parse import unquote

from common import connect, get, key, week_label

URL = "https://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/getRestDeInfo"


def fetch_year(k, y):
    r = get(URL, {"ServiceKey": k, "solYear": y, "numOfRows": 100, "pageNo": 1}, timeout=60)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    rows = []
    for it in root.iter("item"):
        g = {c.tag: (c.text or "") for c in it}
        if g.get("isHoliday") != "Y":
            continue
        d = dt.datetime.strptime(g["locdate"], "%Y%m%d").date()
        rows.append(dict(day=d.isoformat(), name=g.get("dateName", ""), dow=d.weekday()))
    return rows


def main():
    k = unquote(key("DATALAB_KEY"))  # 인코딩된 키를 넣어도 두 번 인코딩되지 않게
    a, b = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) == 3 else (2019, dt.date.today().year + 1)
    rows = []
    for y in range(a, b + 1):
        rows += fetch_year(k, y)
        time.sleep(0.5)
    h = pd.DataFrame(rows)
    con = connect()
    h.to_sql("holidays", con, if_exists="replace", index=False)

    # 주차별: 평일 공휴일 날 수, 설·추석 날 수 (같은 날 두 줄은 한 번만)
    d = h.drop_duplicates("day").copy()
    d["date"] = pd.to_datetime(d["day"]).dt.date
    lab = d["date"].map(week_label)
    d["week"], d["week_order"] = lab.str[0], lab.str[1]
    d["weekday_holiday"] = (d["dow"] < 5).astype(int)
    lunar_days = set(h.loc[h["name"].str.contains("설날|추석"), "day"])
    d["lunar"] = d["day"].isin(lunar_days).astype(int)
    w = d.groupby(["week", "week_order"]).agg(weekday_holidays=("weekday_holiday", "sum"),
                                              lunar_days=("lunar", "sum"),
                                              names=("name", lambda x: ",".join(sorted(set(x))))).reset_index()
    w.to_sql("holiday_week", con, if_exists="replace", index=False)
    con.commit()
    print("holidays", len(h), "holiday_week", len(w))


if __name__ == "__main__":
    main()
