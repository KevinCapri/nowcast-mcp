"""KOSIS 표 메모리 시험(자료 DB 없이 — PR마다 기본 검사에서 돎, 0.6.4, 2026-11-23).

lifepop.load_memory로 KOSIS 표 5개·지역 연결표를 메모리에 올린 뒤 _by_period·_codes가
① DB에서 읽던 값과 똑같은지(빈 값·여러 항목·여러 분류 줄·여러 코드 합 포함)
② 메모리에 올린 뒤에는 KOSIS 표를 다시 읽지 않는지
③ 모르는 조건이 오면 전처럼 DB에서 읽는지 본다.
"""
import random
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import lifepop as L  # noqa: E402

random.seed(7)
TABLES = L.KOSIS_TABLES
ITEMS = {"kosis_life_pop": ["T001", "T002"], "kosis_reg_pop": ["T20"], "kosis_foreigners": ["13103870964T1"],
         "kosis_commute": ["T10", "T30"], "kosis_migration": ["T10", "T20"]}
CODES = [f"{41000 + i}" for i in range(40)]
PRDS = [f"{y}{m:02d}" for y in (2024, 2025, 2026) for m in range(1, 13)]

calls = {"kosis": 0}


class DB:
    def __init__(self):
        self.c = sqlite3.connect(":memory:")
        for t in TABLES:
            self.c.execute(f"CREATE TABLE {t}(PRD_DE TEXT, C1 TEXT, C2 TEXT, ITM_ID TEXT, DT REAL)")
            rows = []
            for code in CODES:
                for itm in ITEMS[t]:
                    for prd in random.sample(PRDS, 20):
                        for c2 in ("a", "b"):            # 분류 줄이 여러 개여도 합쳐야 함
                            dt = None if random.random() < 0.1 else float(random.randint(0, 500000))
                            rows.append((prd, code, c2, itm, dt))
            self.c.executemany(f"INSERT INTO {t} VALUES (?,?,?,?,?)", rows)
        self.c.execute("CREATE TABLE region_map(sido TEXT, sigungu TEXT, reg_pop TEXT, life_pop TEXT, commute TEXT, "
                       "foreigners TEXT)")
        self.c.executemany("INSERT INTO region_map VALUES (?,?,?,?,?,?)", [
            ("경기도", "가평군", "41000", "41001", "41002", None),
            ("경기도", "수원시", "41003,41004,41005", "", "41006", "41007"),
            ("경기도", "수원시", "41039", "", "", ""),     # 같은 이름 두 줄이면 처음 줄(DB fetchone과 같음)
        ])

    def execute(self, q, *a):
        if any(f"FROM {t} " in q for t in TABLES) and "GROUP BY PRD_DE" in q:
            calls["kosis"] += 1
        return self.c.execute(q, *a)


db = DB()
cases = []
for t in TABLES:
    for extra in ["", *(f"AND ITM_ID='{i}'" for i in ITEMS[t]), "AND ITM_ID='없음'"]:
        for _ in range(15):
            cases.append((t, random.sample(CODES, random.randint(1, 5)), extra))
cases.append(("kosis_reg_pop", ["없는코드"], ""))

L._MEM.clear()
before = [L._by_period(db, t, c, e) for t, c, e in cases]
codes_before = [L._codes(db, sd, sg, col) for sd, sg in (("경기도", "가평군"), ("경기도", "수원시"), ("서울특별시", "종로구"))
                for col in ("reg_pop", "life_pop", "commute", "foreigners")]

n = L.load_memory(db)
assert n == len(TABLES) + 1, n
calls["kosis"] = 0
after = [L._by_period(db, t, c, e) for t, c, e in cases]
codes_after = [L._codes(db, sd, sg, col) for sd, sg in (("경기도", "가평군"), ("경기도", "수원시"), ("서울특별시", "종로구"))
               for col in ("reg_pop", "life_pop", "commute", "foreigners")]

assert calls["kosis"] == 0, f"메모리에 올린 뒤에도 KOSIS 표를 {calls['kosis']}번 읽음"
for (t, c, e), a, b in zip(cases, before, after):
    assert a == b, (t, c, e, a, b)
assert codes_before == codes_after, (codes_before, codes_after)
assert sum(1 for x in before if x) > 100

# 모르는 조건은 DB에서 읽는다
r = L._by_period(db, "kosis_reg_pop", ["41000"], "AND C2='a'")
assert calls["kosis"] == 1
assert r == {str(p): v for p, v in db.c.execute(
    "SELECT PRD_DE, SUM(CAST(DT AS REAL)) FROM kosis_reg_pop WHERE C1='41000' AND C2='a' GROUP BY PRD_DE") if v is not None}

print(f"KOSIS 메모리 시험 통과: {len(cases)}건 값 같음, 올린 뒤 KOSIS 표 다시 읽기 0번")
