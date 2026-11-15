"""기상청 API허브 ASOS 일자료를 받아 나우캐스트 주차 단위로 묶어 DB에 넣는다.

인증키는 코드에 쓰지 말고 환경변수 KMA_KEY로 넘긴다.
쓰는 법: KMA_KEY=... python pipeline/build_weather.py   (build_db.py를 먼저 돌려 moves 표가 있어야 함)
"""
import datetime as dt
import os
import sqlite3
import sys
import time
from pathlib import Path

import pandas as pd
import requests

from station_map import station_for

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = Path("/home/data/nowcast.db") if os.environ.get("WEBSITE_SITE_NAME") else ROOT / "data" / "nowcast.db"
DB = Path(os.environ.get("NOWCAST_DB", DEFAULT_DB))
URL = "https://apihub.kma.go.kr/api/typ01/url/kma_sfcdd3.php"
STN_URL = "https://apihub.kma.go.kr/api/typ02/openApi/SfcMtlyInfoService/getSfcStnLstTbl"
COLS = {0: "date", 1: "stn", 10: "ta_avg", 11: "ta_max", 13: "ta_min", 38: "rn_day", 49: "sd_max"}


def parse(text):
    """칸 맞춤 텍스트 → 표. 기온 -50 이하는 값 없음, 강수·적설의 음수(-9.0)는 0."""
    rows = [l.split() for l in text.splitlines() if l and l[0].isdigit()]
    df = pd.DataFrame([[r[i] for i in COLS] for r in rows], columns=list(COLS.values()))
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    for c in ["stn", "ta_avg", "ta_max", "ta_min", "rn_day", "sd_max"]:
        df[c] = pd.to_numeric(df[c])
    df["stn"] = df["stn"].astype(int)
    for c in ["ta_avg", "ta_max", "ta_min"]:
        df.loc[df[c] <= -50, c] = float("nan")   # 영하 9도는 실제 기온일 수 있어 그대로 둠
    for c in ["rn_day", "sd_max"]:
        df.loc[df[c] < 0, c] = 0.0
    return df


def week_label(d):
    """나우캐스트 주차: 한 주는 월~일, 그 주의 목요일이 속한 달로 셈. 단 2022년은 수요일 기준."""
    mon = d - dt.timedelta(days=d.weekday())
    wed = mon + dt.timedelta(days=2)
    key = wed if wed.year == 2022 else mon + dt.timedelta(days=3)
    n = (key.day - 1) // 7 + 1
    return f"{key.year}.{key.month:02d}.{n}주차", key.year * 1000 + key.month * 10 + n


def fetch(key, start, end):
    """연 단위로 나눠 전국 관측소 일자료 받기"""
    out, s = [], start
    while s <= end:
        e = min(dt.date(s.year, 12, 31), end)
        r = requests.get(URL, params={"tm1": s.strftime("%Y%m%d"), "tm2": e.strftime("%Y%m%d"),
                                      "stn": 0, "help": 0, "authKey": key}, timeout=120)
        r.raise_for_status()
        if r.text.lstrip().startswith("{"):
            sys.exit(f"API 오류: {r.text[:200]}")
        out.append(parse(r.text))
        print(s, "~", e, len(out[-1]), "행")
        s = e + dt.timedelta(days=1)
        time.sleep(1)
    return pd.concat(out)


def main():
    key = os.environ.get("KMA_KEY")
    if not key:
        sys.exit("환경변수 KMA_KEY가 없음")
    con = sqlite3.connect(DB)
    st = requests.get(STN_URL, params={"pageNo": 1, "numOfRows": 200, "dataType": "JSON",
                                       "year": dt.date.today().year, "month": "09", "authKey": key},
                      timeout=60).json()
    st = st["response"]["body"]["items"]["item"][0]["stn_sfc"]["info"]
    names = {s["stn_id"]: s["stn_ko"] for s in st}

    # 1) 시군구 ↔ 관측소 짝 표
    sg = con.execute("SELECT DISTINCT sido, sigungu FROM moves WHERE category='시군구'").fetchall()
    m = [(a, b, *station_for(a, b, names)) for a, b in sg]
    pd.DataFrame([(a, b, n, names.get(n), how) for a, b, n, how in m],
                 columns=["sido", "sigungu", "stn", "stn_name", "how"]).to_sql(
        "station_map", con, if_exists="replace", index=False)

    # 2) 일자료 받기 → 주차로 묶기
    daily = fetch(key, dt.date(2019, 1, 1), dt.date.today() - dt.timedelta(days=1))
    lab = daily["date"].dt.date.map(week_label)
    daily["week"], daily["week_order"] = lab.str[0], lab.str[1]
    wk = daily.groupby(["stn", "week", "week_order"]).agg(
        days=("date", "count"), ta_avg=("ta_avg", "mean"), ta_max=("ta_max", "max"), ta_min=("ta_min", "min"),
        rn_sum=("rn_day", "sum"), rain_days=("rn_day", lambda x: int((x >= 1.0).sum())),
        sd_max=("sd_max", "max")).reset_index()
    wk = wk.round({"ta_avg": 1, "rn_sum": 1})
    wk.to_sql("weather_week", con, if_exists="replace", index=False)
    print("weather_week", wk.shape)


if __name__ == "__main__":
    main()
