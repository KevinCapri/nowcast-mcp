"""생활인구 공표일(보도계획) 시험 — 인터넷 없이(2026-11-25).

① 보도계획 표 읽기: 생활인구 줄의 날짜·분기를 뽑고, 모양이 바뀌면 실패
② lifepop.release_info: 보도계획 날짜가 있으면 그것을, 없으면 적어 둔 날짜·어림
③ update.lifepop_waiting: 공표일이 지났는데 그 분기 마지막 달이 KOSIS에 없으면 True
"""
import datetime as dt
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp()) / "t.db"
os.environ["NOWCAST_DB"] = str(tmp)
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT / "server"))
import load_release_plan as P  # noqa: E402
import lifepop as L  # noqa: E402
import update as U  # noqa: E402

fails = []


def check(c, m):
    if not c:
        fails.append(m)


# 12월 표(11-25 실제 페이지에서 본 줄 모양)
PAGE = """<h3>2026년 12월 보도계획</h3><table><tr><th>보도일자</th><th>보도시간</th><th>보도자료명</th></tr>
<tr><td>12.02.( 수 )</td><td>08:00</td><td>2026년 11월 소비자물가동향</td><td>물가동향과</td></tr>
<tr><td>12.28.( 월 )</td><td>12:00</td><td>2026년 3/4분기 생활인구 산정 결과</td><td>빅데이터통계과</td><td></td></tr>
</table>"""
y, m, rows = P.parse(PAGE)
check((y, m) == (2026, 12), f"표 연·달 {y} {m}")
check([(k, d) for k, d, _ in rows] == [("2026Q3", "2026-12-28")], f"생활인구 줄 {rows}")
y, m, rows = P.parse(PAGE.replace("생활인구 산정 결과", "가계동향"))
check(rows == [], "생활인구 줄이 없으면 빈 목록이어야 함")
for bad in ("<html>점검 중</html>", "<h3>2026년 12월 보도계획</h3><p>표 없음</p>"):
    try:
        P.parse(bad)
        fails.append(f"모양이 바뀐 페이지를 실패로 안 봄: {bad[:20]}")
    except ValueError:
        pass

# ② release_info
con = sqlite3.connect(tmp)
con.execute("CREATE TABLE release_plan(key TEXT PRIMARY KEY, date TEXT, title TEXT, fetched TEXT)")
con.execute("INSERT INTO release_plan VALUES ('2027Q1', '2027-07-29', 't', 'f')")
con.commit()
r = L.release_info("202702", con)
check(r.get("공표일") == "2027-07-29" and "보도계획" in r["근거"], f"보도계획 날짜를 안 씀: {r}")
r = L.release_info("202609", con)
check(r.get("공표일") == "2026-12-28", f"적어 둔 날짜(12-28) 아님: {r}")
r = L.release_info("202611")
check("공표 예상" in r, f"보도계획·적어 둔 날짜가 없으면 어림이어야 함: {r}")

# ③ lifepop_waiting
con.execute("CREATE TABLE kosis_life_pop(C1 TEXT, ITM_ID TEXT, PRD_DE TEXT, DT TEXT)")
con.execute("INSERT INTO kosis_life_pop VALUES ('x', 'T001', '202606', '1')")
con.execute("DELETE FROM release_plan")
today = dt.date.today()
con.execute("INSERT INTO release_plan VALUES ('2026Q3', ?, 't', 'f')", ((today - dt.timedelta(days=1)).isoformat(),))
con.commit()
check(U.lifepop_waiting() is True, "공표일이 지났고 9월이 없으면 매일 받아야 함")
con.execute("INSERT INTO kosis_life_pop VALUES ('x', 'T001', '202609', '1')")
con.commit()
check(U.lifepop_waiting() is False, "9월이 들어왔으면 그만 받아야 함")
con.execute("UPDATE release_plan SET date=?", ((today + dt.timedelta(days=3)).isoformat(),))
con.execute("DELETE FROM kosis_life_pop WHERE PRD_DE='202609'")
con.commit()
check(U.lifepop_waiting() is False, "공표일 전에는 매일 받지 않아야 함")

if fails:
    print("실패:", *fails, sep="\n- ")
    sys.exit(1)
print("생활인구 공표일(보도계획) 시험 통과")
