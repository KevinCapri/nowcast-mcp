"""KOPIS afterdate 받기(2026-11-25) 시험: 가짜 KOPIS로 며칠 밤을 돌려
① 하룻밤 MAX_CALLS번에서 멈추고 다음 날 그 자리부터 이어지는지 ② 7구간을 다 끝내야 afterdate가 회차 시작일로 옮겨지는지
③ 공연장 이름+시도로 찾고 큰 공연장 공연만 쌓는지, 못 찾은 이름은 따로 모이는지 ④ 새 큰 공연장의 공연을 한 번 따로 받는지 본다.
  python tests/test_kopis_afterdate.py"""
import datetime as dt
import sqlite3
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import load_kopis as K

db = Path(tempfile.mkdtemp()) / "k.db"
con = sqlite3.connect(db)
K.setup(con)
con.executemany("INSERT INTO kopis_venue VALUES(?,?,?,?,?,?,?,?,?,?)", [
    ("FC1", "큰홀", "서울특별시", "송파구", "", None, None, 15000, "", ""),
    ("FC2", "작은극장", "서울특별시", "종로구", "", None, None, 100, "", ""),
])
con.commit()
con.close()

calls = []


def fake(path, _tries=2, _wait=60, **p):
    if K._count[0] >= K.MAX_CALLS:
        raise K.Budget("다 씀")
    K._count[0] += 1
    calls.append((path, p.get("stdate"), p.get("cpage"), p.get("afterdate")))
    root = ET.Element("dbs")

    def row(**kv):
        d = ET.SubElement(root, "db")
        for k, v in kv.items():
            ET.SubElement(d, k).text = v
    if path == "pblprfr":                              # 구간마다 250건(100·100·50, 3쪽)
        for i in range({1: 100, 2: 100, 3: 50}[int(p["cpage"])]):
            venue = ("큰홀", "작은극장", "없는홀", "새큰홀")[i % 4]
            row(mt20id=f"P{p['stdate']}{p['cpage']}{i:03d}", prfnm="공연", prfpdfrom="2026.12.01", prfpdto="2026.12.02",
                fcltynm=venue, area="서울특별시" if venue != "새큰홀" else "경기도", genrenm="연극")
    elif path == "prfplc":
        if int(p["cpage"]) == 1:
            for vid in ("FC1", "FC2", "FC9"):
                row(mt10id=vid)
    elif path == "prfplc/FC9":
        row(fcltynm="새큰홀", adres="경기도 고양시 일산서구 킨텍스로 1", seatscale="5,000", la="37.6", lo="126.7", opende="2026")
    return root


K.xml = fake
K.key = lambda n: "KEY"
K.connect = lambda: sqlite3.connect(db)
K.MIN_GAP = 0
states = []
for night in range(5):
    day = dt.date(2026, 11, 25) + dt.timedelta(days=night)

    class D(dt.date):
        @classmethod
        def today(cls):
            return day
    K.dt.date = D
    K._count[0] = 0
    calls.clear()
    K.main()
    c = sqlite3.connect(db)
    states.append((dict(c.execute("SELECT k, v FROM kopis_state")), list(calls)))
    c.close()

s1, c1 = states[0]
assert len(c1) == K.MAX_CALLS and s1["win"] == "6" and s1["page"] == "3", s1          # ① 20번에서 멈춤, 7구간 3쪽이 남음
assert {c[3] for c in c1 if c[0] == "pblprfr"} == {"20261115"}                      # 첫 회차 afterdate
s2, c2 = states[1]
assert c2[0][1] == K._windows(dt.date(2026, 11, 25))[6][0].strftime("%Y%m%d") and c2[0][2] == 3   # 이어서 7구간 3쪽부터
assert s2["after"] == "2026-11-25" and "cycle" not in s2, s2                         # ② 회차 끝 → afterdate = 회차 시작일
s3, c3 = states[2]
assert {c[3] for c in c3 if c[0] == "pblprfr"} == {"20261125"}
c = sqlite3.connect(db)
kinds = dict(c.execute("SELECT mt10id, COUNT(*) FROM kopis_perf GROUP BY mt10id"))
assert "FC2" not in kinds and kinds.get("FC1", 0) > 0, kinds                          # ③ 큰 공연장만
assert c.execute("SELECT n FROM kopis_unmatched WHERE name='없는홀'").fetchone()[0] > 0
assert c.execute("SELECT seats FROM kopis_venue WHERE mt10id='FC9'").fetchone()[0] == 5000   # 새 공연장 상세
assert any(x[0] == "pblprfr" and x[3] is None for _, cs in states for x in cs), "새 큰 공연장 공연을 따로 받지 않음"   # ④
print("KOPIS afterdate 시험 통과: 하룻밤", K.MAX_CALLS, "번에서 멈추고 이어짐, 회차가 끝나야 afterdate 옮김, 큰 공연장만 쌓음, 새 큰 공연장 따로 받음")
