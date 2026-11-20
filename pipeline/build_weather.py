"""기상청 API허브 ASOS 일자료를 받아 일별 표(weather_daily)에 쌓고, 나우캐스트 주차 단위(weather_week)로 묶는다.

- 처음(weather_daily가 비었을 때)만 2019-01-01부터 전부 받는다. 그다음부터는 최근 RECENT_DAYS일만 다시 받아 덮어쓴다
  (기상청이 최근 값을 고친 것도 반영). 요청마다 실패하면 30초 뒤 두 번까지 다시 시도한다.
- 일별 표로 바꾼 뒤 처음 만든 주 단위 표는 지금 weather_week와 견줘서 같을 때만 바꿔 끼운다(weather_meta.verified=1).
  다르면 지금 표를 그대로 두고 새 표는 weather_week_new에 남긴 뒤 실패로 끝낸다(운영 메일). 확인이 끝나기 전엔 매번 다시 견준다.
- 인증키는 코드에 쓰지 말고 환경변수 KMA_KEY로 넘긴다.
쓰는 법: KMA_KEY=... python pipeline/build_weather.py [--full]   (build_db.py를 먼저 돌려 moves 표가 있어야 함)
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
START = dt.date(2019, 1, 1)
RECENT_DAYS = 60
RECHECK_SKIP_WEEKS = 9      # 견줄 때 지금 표의 최근 9주는 뺌(기상청이 최근 값을 고쳤을 수 있음)
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


def get_year(key, s, e):
    """한 구간 요청. 실패(연결 오류·5xx·시간 초과)하면 30초 뒤 두 번까지 다시 시도."""
    for i in range(3):
        try:
            r = requests.get(URL, params={"tm1": s.strftime("%Y%m%d"), "tm2": e.strftime("%Y%m%d"),
                                          "stn": 0, "help": 0, "authKey": key}, timeout=120)
            r.raise_for_status()
            return r.text
        except requests.RequestException as ex:
            if i == 2:
                raise
            print(s, "~", e, "실패, 30초 뒤 다시:", ex, flush=True)
            time.sleep(30)


def fetch(key, start, end):
    """연 단위로 나눠 전국 관측소 일자료 받기"""
    out, s = [], start
    while s <= end:
        e = min(dt.date(s.year, 12, 31), end)
        text = get_year(key, s, e)
        if text.lstrip().startswith("{"):
            sys.exit(f"API 오류: {text[:200]}")
        out.append(parse(text))
        print(s, "~", e, len(out[-1]), "행", flush=True)
        s = e + dt.timedelta(days=1)
        time.sleep(1)
    return pd.concat(out)


def to_week(daily):
    daily = daily.copy()
    daily["date"] = pd.to_datetime(daily["date"])
    lab = daily["date"].dt.date.map(week_label)
    daily["week"], daily["week_order"] = lab.str[0], lab.str[1]
    wk = daily.groupby(["stn", "week", "week_order"]).agg(
        days=("date", "count"), ta_avg=("ta_avg", "mean"), ta_max=("ta_max", "max"), ta_min=("ta_min", "min"),
        rn_sum=("rn_day", "sum"), rain_days=("rn_day", lambda x: int((x >= 1.0).sum())),
        sd_max=("sd_max", "max")).reset_index()
    return wk.round({"ta_avg": 1, "rn_sum": 1})


def same_as_old(con, wk):
    """지금 weather_week와 새 주 단위 표 견주기(지금 표의 최근 RECHECK_SKIP_WEEKS주, 일수가 다른 주는 뺌)."""
    try:
        old = pd.read_sql("SELECT * FROM weather_week", con)
    except Exception:
        return True, "지금 weather_week 없음"
    keep = sorted(old["week_order"].unique())[:-RECHECK_SKIP_WEEKS]
    old = old[old["week_order"].isin(keep)]
    m = old.merge(wk, on=["stn", "week_order"], how="left", suffixes=("_old", "_new"))
    m = m[m["days_old"] == m["days_new"]]
    missing = len(old) - len(m)
    bad = []
    for c in ["ta_avg", "ta_max", "ta_min", "rn_sum", "rain_days", "sd_max"]:
        a, b = m[f"{c}_old"], m[f"{c}_new"]
        diff = ~(((a - b).abs() <= 0.051) | (a.isna() & b.isna()))
        if diff.any():
            bad.append(f"{c} {int(diff.sum())}줄")
    msg = f"견준 줄 {len(m)}, 새 표에 없거나 일수가 다른 줄 {missing}, 다른 값: {', '.join(bad) or '없음'}"
    return (not bad and missing <= len(old) * 0.001), msg


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

    # 2) 일자료 받기: 처음엔 2019년부터 전부, 그다음엔 최근 RECENT_DAYS일만
    con.execute("CREATE TABLE IF NOT EXISTS weather_daily(date TEXT, stn INT, ta_avg REAL, ta_max REAL, "
                "ta_min REAL, rn_day REAL, sd_max REAL, PRIMARY KEY(date, stn))")
    con.execute("CREATE TABLE IF NOT EXISTS weather_meta(key TEXT PRIMARY KEY, value TEXT)")
    first = con.execute("SELECT MIN(date) FROM weather_daily").fetchone()[0]
    full = "--full" in sys.argv or first is None or first > START.isoformat()
    end = dt.date.today() - dt.timedelta(days=1)
    start = START if full else end - dt.timedelta(days=RECENT_DAYS - 1)
    print("전부 받기" if full else "최근분만", start, "~", end, flush=True)
    new = fetch(key, start, end)
    new["date"] = new["date"].dt.strftime("%Y-%m-%d")
    with con:
        con.execute("DELETE FROM weather_daily WHERE date >= ?", (start.isoformat(),))
        new[["date", "stn", "ta_avg", "ta_max", "ta_min", "rn_day", "sd_max"]].to_sql(
            "weather_daily", con, if_exists="append", index=False)
    print("weather_daily", con.execute("SELECT COUNT(*) FROM weather_daily").fetchone()[0], "행", flush=True)

    # 3) 주차로 묶기. 처음 만든 표는 지금 표와 같을 때만 바꿔 끼움
    wk = to_week(pd.read_sql("SELECT * FROM weather_daily", con))
    verified = (con.execute("SELECT value FROM weather_meta WHERE key='verified'").fetchone() or ["0"])[0] == "1"
    if not verified:
        ok, msg = same_as_old(con, wk)
        print("지금 표와 견줌:", msg, flush=True)
        if not ok:
            wk.to_sql("weather_week_new", con, if_exists="replace", index=False)
            sys.exit("새로 만든 주 단위 날씨가 지금 값과 달라 바꿔 끼우지 않음(weather_week 그대로, 새 표는 "
                     f"weather_week_new). {msg}")
        with con:
            con.execute("INSERT OR REPLACE INTO weather_meta VALUES ('verified', '1')")
            con.execute("DROP TABLE IF EXISTS weather_week_new")
    wk.to_sql("weather_week", con, if_exists="replace", index=False)
    print("weather_week", wk.shape)


if __name__ == "__main__":
    main()
