"""get_datalab_daily: 옛 이름·새 이름이 같은 데이터랩 코드를 가져오는 지역(전남·광주·군위) 확인(2026-11-20).

예전에는 코드가 중복된 채로 물음표 수를 세서 sqlite 'Incorrect number of bindings' 오류가 났다.
실제 DB 없이 작은 임시 DB로 돌린다.
  python tests/test_datalab_codes.py   # 문제 있으면 끝 코드 1
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
tmp = Path(tempfile.mkdtemp()) / "t.db"
con = sqlite3.connect(tmp)
con.execute("CREATE TABLE region_map(sido, sigungu, datalab)")
con.executemany("INSERT INTO region_map VALUES (?,?,?)", [
    ("전라남도", "함평군", "46860,12860"),
    ("전남광주통합특별시", "함평군", "46860,12860"),
    ("강원특별자치도", "화천군", "42790,51790"),
])
con.execute("CREATE TABLE datalab_daily(signguCode, touDivCd, day, touNum)")
con.executemany("INSERT INTO datalab_daily VALUES (?,?,?,?)", [
    ("46860", 2, "2026-04-27", 1000), ("12860", 2, "2026-07-01", 500), ("42790", 2, "2026-01-05", 700),
])
con.execute("CREATE TABLE holidays(day, name)")
con.commit()
con.close()

os.environ["NOWCAST_DB"] = str(tmp)
sys.path.insert(0, str(HERE.parent / "server"))
import server as S  # noqa: E402

CHECKS = [
    (("전라남도", "함평군", "2026-04-27", "2026-07-01"), [1000, 500]),
    (("전남광주통합특별시", "함평군", "2026-04-27", "2026-04-27"), [1000]),
    (("강원특별자치도", "화천군", "2026-01-05", "2026-01-05"), [700]),
]
bad = 0
for args, want in CHECKS:
    try:
        r = S.get_datalab_daily(*args)
        got = [d["value"] for d in r.get("data", [])]
    except Exception as e:  # noqa: BLE001
        got = f"{type(e).__name__}: {e}"
    ok = got == want
    bad += not ok
    print("OK " if ok else "FAIL", args[0], args[1], got)
sys.exit(1 if bad else 0)
