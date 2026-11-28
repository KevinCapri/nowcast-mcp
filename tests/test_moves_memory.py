"""이동 표 메모리 시험(자료 DB 없이 — PR마다 기본 검사에서 돎, 2026-11-23).

analysis.load_all로 시군구 유입·유출과 이름 목록을 메모리에 올린 뒤
① 유출 시군구 순위(movement.rank) ② list_regions(검색어 있음·없음) ③ 최신 유입 주차가
DB에서 읽던 것과 똑같은지 본다. LIKE와 뜻이 다를 수 있는 검색어(%, _, 영문)는 DB로 가는지도 본다.
"""
import random
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import analysis as A  # noqa: E402
import movement as M  # noqa: E402

random.seed(11)
NAMES = [("서울특별시", "중구"), ("서울특별시", "종로구"), ("부산광역시", "중구"), ("대구광역시", "동구"),
         ("강원특별자치도", "화천군"), ("강원도", "화천군"), ("전라남도", "함평군"), ("경기도", "수원시 장안구"),
         ("인천광역시", "중구"), ("인천광역시", "제물포구")]
WEEKS = [y * 1000 + m * 10 + w for y in (2024, 2025, 2026) for m in range(1, 13) for w in range(1, 5)]

con = sqlite3.connect(":memory:")
con.execute("CREATE TABLE moves(direction, category, item, sido, sigungu, week, within, outside, total, week_order)")
rows = []
for d in ("유입", "유출"):
    for sd, sg in NAMES:
        for wo in WEEKS:
            if d == "유출" and wo < 2025000:
                continue
            if sg == "제물포구" and wo < 2026070:
                continue
            if sg == "중구" and sd == "인천광역시" and wo >= 2026070:
                continue
            v = None if random.random() < 0.03 else float(random.randint(1000, 90000))
            rows.append((d, "시군구", sg, sd, sg, str(wo), 0, v, 0, wo))
    for sd in sorted({a for a, _ in NAMES}):
        rows.append((d, "시도", sd, sd, "", "x", 0, 1.0, 0, 2026011))
con.executemany("INSERT INTO moves VALUES (?,?,?,?,?,?,?,?,?,?)", rows)

calls = {"n": 0}


class DB:
    def execute(self, q, *a):
        calls["n"] += 1
        return con.execute(q, *a)


db = DB()


def list_regions_db(keyword):
    if not keyword:
        return [{"시도": r[0]} for r in con.execute("SELECT DISTINCT sido FROM moves WHERE category='시도' ORDER BY sido")]
    return [{"시도": a, "시군구": b} for a, b in con.execute(
        "SELECT DISTINCT sido, sigungu FROM moves WHERE category='시군구' AND (sigungu LIKE ? OR sido LIKE ?) "
        "ORDER BY sido, sigungu", (f"%{keyword}%", f"%{keyword}%"))]


def list_regions_mem(keyword):
    """server.list_regions와 같은 갈래(서버 전체를 띄우지 않으려고 같은 조건을 옮김)."""
    names = A._NAMES
    if not keyword:
        return [{"시도": s} for s in names["sido"]]
    if not any(ch in keyword for ch in "%_") and keyword == keyword.lower() == keyword.upper():
        return [{"시도": a, "시군구": b} for a, b in names["sgg"]
                if (b is not None and keyword in b) or (a is not None and keyword in a)]
    return list_regions_db(keyword)


pop = lambda sd, sg, a=None, b=None: 50000
M.depop = lambda: {}
M.in_area = lambda k, area: True
A._ALL = A._ALL_OUT = A._NAMES = None
before_rank = [M.rank(db, d, a, b, 100, 1, "전체", pop) for d in ("유입", "유출")
               for a, b in ((2025011, 2025124), (2026061, 2026124), (2019011, 2026124))]
before_latest = A.latest_inflow_week(db)
KW = ["", "중구", "화천", "강원", "구", "수원시 장안", "없음", "특별"]
before_list = [list_regions_db(k) for k in KW]

A.load_all(db)
calls["n"] = 0
after_rank = [M.rank(db, d, a, b, 100, 1, "전체", pop) for d in ("유입", "유출")
              for a, b in ((2025011, 2025124), (2026061, 2026124), (2019011, 2026124))]
after_latest = A.latest_inflow_week(db)
after_list = [list_regions_mem(k) for k in KW]
assert calls["n"] == 0, f"메모리에 올린 뒤에도 이동 표를 {calls['n']}번 읽음"

assert before_rank == after_rank
assert before_latest == after_latest == max(WEEKS)
assert before_list == after_list, [(k, a, b) for k, a, b in zip(KW, before_list, after_list) if a != b]
assert any(r and r["순위"] for r in after_rank)
print(f"이동 표 메모리 시험 통과: 순위 {len(after_rank)}건·이름 찾기 {len(KW)}건·최신 주차 같음, 올린 뒤 표 읽기 0번")
