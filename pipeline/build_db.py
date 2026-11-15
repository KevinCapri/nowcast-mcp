"""나우캐스트 엑셀 원자료(유입·유출)를 SQLite 테이블 moves 하나로 합친다.

쓰는 법: python pipeline/build_db.py data/raw/inflow.xlsx data/raw/outflow.xlsx
DB 위치는 환경변수 NOWCAST_DB(기본: data/nowcast.db).
"""
import os
import re
import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = Path("/home/data/nowcast.db") if os.environ.get("WEBSITE_SITE_NAME") else ROOT / "data" / "nowcast.db"
DB = Path(os.environ.get("NOWCAST_DB", DEFAULT_DB))
RAW = ROOT / "data" / "raw"


def week_key(label):
    # '2026.10.5주차' -> (2026, 10, 5)
    y, m, w = re.match(r"(\d{4})\.(\d{2})\.(\d)주차", label).groups()
    return int(y), int(m), int(w)


def load(files):
    rows = []
    for direction, path in files.items():
        sheets = pd.read_excel(path, sheet_name=None)   # 시트 이름 → 표 (시트 순서 유지)
        s = list(sheets)
        a = sheets[s[0]]          # 전국/성별/연령별
        for _, r in a.iterrows():
            cat = {"전국": "전국", "성별": "성별", "연령별": "연령"}[r["구분1"]]
            rows.append((direction, cat, r["구분2"], "전국", "", r["주차구분"], r["관내"], r["관외"], r["합계"]))
        b = sheets[s[1]]          # 입지유형
        for _, r in b.iterrows():
            rows.append((direction, "입지유형", r["입지유형"], "전국", "", r["주차구분"], r["관내"], r["관외"], r["합계"]))
        c = sheets[s[2]]          # 시도
        for _, r in c.iterrows():
            rows.append((direction, "시도", r["시도"], r["시도"], "", r["주차구분"], r["관내"], r["관외"], r["합계"]))
        d = sheets[s[3]]          # 시군구
        for _, r in d.iterrows():
            rows.append((direction, "시군구", r["시군구"], r["시도"], r["시군구"], r["주차구분"], r["관내"], r["관외"], r["합계"]))
    df = pd.DataFrame(rows, columns=["direction", "category", "item", "sido", "sigungu", "week", "within", "outside", "total"])
    k = df["week"].map(week_key)
    df["year"] = k.str[0]
    df["month"] = k.str[1]
    df["wk"] = k.str[2]
    df["week_order"] = df["year"] * 1000 + df["month"] * 10 + df["wk"]
    return df


if __name__ == "__main__":
    inflow = sys.argv[1] if len(sys.argv) > 1 else RAW / "inflow.xlsx"
    outflow = sys.argv[2] if len(sys.argv) > 2 else RAW / "outflow.xlsx"
    df = load({"유입": inflow, "유출": outflow})
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    # 서버가 읽는 중에도 표가 비는 순간이 없게: 새 표에 다 넣은 뒤 한 번에 바꿔 끼움
    df.to_sql("moves_new", con, if_exists="replace", index=False)
    con.commit()
    con.isolation_level = None
    con.execute("BEGIN IMMEDIATE")
    con.execute("DROP TABLE IF EXISTS main.moves")
    con.execute("ALTER TABLE main.moves_new RENAME TO moves")
    con.execute("CREATE INDEX IF NOT EXISTS ix ON moves(direction, category, item, sigungu, week_order)")
    con.execute("COMMIT")
    print(df.shape, df.groupby("direction").week.nunique().to_dict())
