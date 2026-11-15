"""지역의 요일·장날 리듬(region_rhythm)을 데이터랩 외지인 일별 방문자로 계산한다.

- 기간: 자료의 마지막 날부터 거꾸로 1년, 공휴일 제외
- 주 단위 수준을 뺀 뒤(같은 지역·같은 주 평균 대비) 요일별 중앙값 → 주말/평일 비율
  · 1.5 이상 주말형(관광지·쇼핑몰 등), 1.1 미만 평일형(외부 방문객이 평일에 오는 곳: 업무·병원·관공서 등)
  · 외지인 정의상 근무자(상주자)는 빠져 있어 출퇴근 지역은 이 값으로 가릴 수 없음
- 오일장: 평일만, 날짜 끝자리(5일 주기)별 중앙값의 최댓값 ÷ 중앙값. 1.05 이상이면 장날 리듬이 보임
  (2026-11-14 시험: 정선 2·7일, 구례 3·8일, 양양 4·9일, 성남 중원구(모란시장) 4·9일, 동해(북평장) 3·8일)
쓰는 법: python pipeline/build_rhythm.py   (데이터랩 적재·지역 연결표 뒤)
"""
import datetime as dt
import json
import os
import shutil
import sqlite3

import numpy as np
import pandas as pd

from common import DB, connect


def main():
    out_con = connect()
    if os.environ.get("WEBSITE_SITE_NAME"):          # /home은 네트워크 저장소라 임시 디스크에서 읽음
        tmp = "/tmp/rhythm_src.db"
        shutil.copy(DB, tmp)
        con = sqlite3.connect(tmp)
    else:
        con = out_con
    last = con.execute("SELECT MAX(day) FROM datalab_daily WHERE touDivCd=2").fetchone()[0]
    end = pd.Timestamp(last)
    start = end - pd.Timedelta(days=364)
    d = pd.read_sql("SELECT signguCode, day, touNum FROM datalab_daily WHERE touDivCd=2 AND day BETWEEN ? AND ?",
                    con, params=(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")))
    hol = {str(x) for (x,) in con.execute("SELECT day FROM holidays")}
    rm = pd.read_sql("SELECT sido, sigungu, datalab FROM region_map WHERE datalab IS NOT NULL", con)
    d["day"] = pd.to_datetime(d["day"])
    d = d[~d["day"].dt.strftime("%Y-%m-%d").isin(hol)]
    rows = []
    for sd, sg, codes in rm.itertuples(index=False):
        x = d[d.signguCode.isin(codes.split(","))].groupby("day").touNum.sum()
        if len(x) < 200:
            continue
        x = x.to_frame("v")
        x["lv"] = np.log(x.v.clip(lower=1))
        x["dow"] = x.index.dayofweek
        x["wk"] = x.index - pd.to_timedelta(x["dow"], "D")
        x["rel"] = x.lv - x.groupby("wk").lv.transform("mean")
        w = x.groupby("dow").rel.median()
        ratio = float(np.exp(w[[5, 6]].mean() - w[[0, 1, 2, 3, 4]].mean()))
        y = x[x.dow < 5].copy()
        y["c"] = y.index.day % 5
        c = y.groupby("c").rel.median()
        amp = float(np.exp(c.max() - c.median()))
        top = int(c.idxmax())
        days = f"{top if top else 5}·{top + 5 if top else 10}일"
        prof = {k: round(float(np.exp(v - w.mean())), 2) for k, v in zip("월화수목금토일", w.reindex(range(7)))}
        rows.append((sd, sg, round(ratio, 2), json.dumps(prof, ensure_ascii=False), round(amp, 3),
                     days if amp >= 1.05 else None, f"{start.date()}~{end.date()}"))
    out_con.execute("DROP TABLE IF EXISTS main.region_rhythm")
    out_con.execute("CREATE TABLE region_rhythm(sido TEXT, sigungu TEXT, weekend_ratio REAL, dow_profile TEXT, "
                    "market_amp REAL, market_days TEXT, period TEXT)")
    out_con.executemany("INSERT INTO region_rhythm VALUES (?,?,?,?,?,?,?)", rows)
    out_con.commit()
    print("region_rhythm", len(rows), "곳, 장날 리듬", sum(1 for r in rows if r[5]), "곳")


if __name__ == "__main__":
    main()
