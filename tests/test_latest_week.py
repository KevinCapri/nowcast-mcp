"""최신 주차 캐시 시험(자료 DB 없이 — PR마다 기본 검사에서 돎, 2026-11-21).

'SELECT MAX(week_order) FROM moves'는 표 전체를 훑어 느리다. analysis.latest_week가
① 10분 안에는 다시 훑지 않는지 ② 10분이 지나거나 비우면 다시 확인하는지
③ current_name을 비교 지역 10곳에 불러도 한 번만 훑는지 본다.
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import analysis as A  # noqa: E402

calls = {"n": 0}


class DB:
    def __init__(self):
        self.c = sqlite3.connect(":memory:")
        self.c.execute("CREATE TABLE moves(direction, category, item, sido, sigungu, week_order)")
        self.c.execute("INSERT INTO moves VALUES ('유입','시군구','가평군','경기도','가평군',2026111)")

    def execute(self, q, *a):
        if q.strip() == "SELECT MAX(week_order) FROM moves":
            calls["n"] += 1
        return self.c.execute(q, *a)


db = DB()
A.all_regions = lambda _db: [("경기도", "가평군")]
fails = []
for _ in range(10):
    r = A.current_name(db, "경기도", "가평군")
if calls["n"] != 1 or r != ("경기도", "가평군"):
    fails.append(f"10번 불러 {calls['n']}번 훑음(1번이어야 함), 결과 {r}")
A._latest["checked"] -= A.LATEST_TTL + 1
A.latest_week(db)
if calls["n"] != 2:
    fails.append("10분이 지나도 다시 확인하지 않음")
A._latest["wo"] = None
A.latest_week(db)
if calls["n"] != 3:
    fails.append("비운 뒤 다시 확인하지 않음")
if fails:
    print("실패:", *fails, sep="\n- ")
    sys.exit(1)
print("최신 주차 캐시 시험 통과")
