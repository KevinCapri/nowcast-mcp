"""공연장 지역 연계(2026-11-28) 시험: 지역 칸이 개편 전 이름인 큰 공연장이 법정동 연계표로 지금 시군구에서도 나오는지 본다.
① 인천 중구(운서동) 인스파이어 공연이 '영종구'로 물어도 나옴 ② 옛 이름 '중구'로 물어도 그대로 나옴(더하기만)
③ 같은 옛 구에서 갈라진 '제물포구'로는 안 나옴 ④ 부천시(중동) → 원미구, 소사구로는 안 나옴 ⑤ 화성시 향남읍 → 만세구
"""
import datetime as dt
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import perf

con = sqlite3.connect(":memory:")
con.executescript("""
CREATE TABLE kopis_venue(mt10id TEXT PRIMARY KEY, name TEXT, sido TEXT, sigungu TEXT, adres TEXT, la REAL, lo REAL, seats INTEGER, opende TEXT, fetched TEXT);
CREATE TABLE kopis_perf(mt20id TEXT PRIMARY KEY, name TEXT, s TEXT, e TEXT, mt10id TEXT, venue TEXT, genre TEXT, fetched TEXT);
""")
V = [("FC003670", "인스파이어 엔터테인먼트 리조트", "인천광역시", "중구", "인천광역시 중구 공항문화로 127 (운서동)"),
     ("FC001911", "부천실내체육관", "경기도", "부천시", "경기도 부천시 석천로 293 (중동)"),
     ("FC000777", "화성종합경기타운", "경기도", "화성시", "경기도 화성시 향남읍 향남로 470")]
for i, (vid, nm, sd, sg, ad) in enumerate(V):
    con.execute("INSERT INTO kopis_venue VALUES(?,?,?,?,?,NULL,NULL,10000,'','2026-11-15')", (vid, nm, sd, sg, ad))
    con.execute("INSERT INTO kopis_perf VALUES(?,?,?,?,?,?,'대중음악','2026-11-15')",
                (f"PF{i}", f"공연{i} [시험]", "2026-07-11", "2026-07-12", vid, nm))
a, z = dt.date(2026, 7, 6), dt.date(2026, 7, 12)
names = lambda sd, sg: [p["공연장"] for p in perf.in_range(con, sd, sg, a, z)]
assert names("인천광역시", "영종구") == ["인스파이어 엔터테인먼트 리조트"], names("인천광역시", "영종구")   # ①
assert names("인천광역시", "중구") == ["인스파이어 엔터테인먼트 리조트"]                                 # ②
assert names("인천광역시", "제물포구") == []                                                             # ③
assert names("경기도", "부천시 원미구") == ["부천실내체육관"] and names("경기도", "부천시 소사구") == []     # ④
assert names("경기도", "화성시 만세구") == ["화성종합경기타운"] and names("경기도", "화성시 동탄구") == []   # ⑤
assert perf.now_region("인천광역시", "중구", "인천광역시 중구 제물량로218번길 3 - 0 인천아트플랫폼") is None
print("공연장 지역 연계 시험 통과: 영종구·원미구·만세구에서 나오고, 옛 이름으로도 그대로, 갈라진 다른 구로는 안 나옴")
