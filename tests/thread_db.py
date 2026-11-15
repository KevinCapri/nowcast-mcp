"""DB 창구가 동시 요청에도 실패하지 않는지 본다(자료 DB 없이 임시 DB로).

2026-11-16: 연결 하나를 여러 스레드가 같이 써서 동시 요청 일부가 'Error executing tool'로 실패했다.
server.py의 _ThreadDB(스레드마다 연결)를 그대로 가져와 8개 스레드로 시험한다."""
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path

src = (Path(__file__).resolve().parents[1] / "server" / "server.py").read_text(encoding="utf-8")
code = src[src.index("class _ThreadDB"):src.index("db = _ThreadDB(DB)")]
ns = {"sqlite3": sqlite3, "threading": threading}
exec(code, ns)

path = Path(tempfile.mkdtemp()) / "t.db"
con = sqlite3.connect(path)
con.execute("CREATE TABLE t(a, b)")
con.executemany("INSERT INTO t VALUES(?, ?)", [(i, i * 2) for i in range(20000)])
con.commit()
con.close()

db = ns["_ThreadDB"](path)
errors = []


def work():
    for _ in range(200):
        try:
            assert len(db.execute("SELECT a, b FROM t WHERE a BETWEEN ? AND ?", (10, 5000)).fetchall()) == 4991
            assert len(list(db.execute("SELECT a FROM t LIMIT 50"))) == 50
            assert db.execute("SELECT MAX(a) FROM t").fetchone()[0] == 19999
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))


threads = [threading.Thread(target=work) for _ in range(8)]
for t in threads:
    t.start()
for t in threads:
    t.join()
print(f"동시 요청 시험: 실패 {len(errors)}건")
sys.exit(1 if errors else 0)
