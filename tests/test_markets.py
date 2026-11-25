"""전통시장 장날 자료(API) 시험 — 인터넷 없이(2026-11-25).

① 주소 → 시도·시군구(지번 주소 먼저, '○○시 ○○구'는 세 낱말)
② API 응답 여러 쪽을 다 모으고, 개수가 안 맞으면 실패
③ 지난번보다 1할 넘게 줄면 덮어쓰지 않음
④ 서버는 markets 표가 있으면 그것을, 없으면 markets.csv를 씀
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp()) / "t.db"
os.environ["NOWCAST_DB"] = str(tmp)
os.environ["DATALAB_KEY"] = "k"
sys.path.insert(0, str(ROOT / "pipeline"))
import load_markets as M  # noqa: E402

fails = []


def check(c, m):
    if not c:
        fails.append(m)


check(M.region("경상남도 창원시 마산합포구 오동동 1") == ("경상남도", "창원시 마산합포구"), "시+구")
check(M.region("전라남도 함평군 함평읍 1") == ("전라남도", "함평군"), "군")
check(M.region("") == (None, None), "빈 주소")


def item(i):
    return {"mrktNm": f"시장{i}", "mrktType": "5일장", "mrktEstblCycle": "2일+7일", "storNumber": "10" if i % 2 else "",
            "latitude": "35.1", "longitude": "126.5", "referenceDate": "2026-11-09",
            "lnmadr": "전라남도 함평군 함평읍 1", "rdnmadr": "x"}


class R:
    def __init__(self, j):
        self.j = j

    def json(self):
        return self.j


N = 1403
pages = {1: [item(i) for i in range(1000)], 2: [item(i) for i in range(1000, N)]}


def fake_get(url, params, **kw):
    p = params["pageNo"]
    if p in pages:
        return R({"header": {"resultCode": "00"}, "body": {"totalCount": N, "items": pages[p]}})
    return R({"header": {"resultCode": "03"}, "body": None})


M.get = fake_get
M.main()
con = sqlite3.connect(tmp)
check(con.execute("SELECT COUNT(*) FROM markets").fetchone()[0] == N, "1,403곳이 다 안 들어감")
check(con.execute("SELECT stores FROM markets WHERE name='시장0'").fetchone()[0] is None, "점포수 빈 값은 비워 둠")

pages[2] = pages[2][:10]
try:
    M.main()
    fails.append("개수가 안 맞는데 실패로 안 봄")
except RuntimeError:
    pass
check(con.execute("SELECT COUNT(*) FROM markets").fetchone()[0] == N, "실패했는데 표가 바뀜")

N2 = 1100
pages = {1: [item(i) for i in range(1000)], 2: [item(i) for i in range(1000, N2)]}
N = N2
try:
    M.main()
    fails.append("1할 넘게 줄었는데 덮어씀")
except SystemExit:
    pass
check(con.execute("SELECT COUNT(*) FROM markets").fetchone()[0] == 1403, "줄어든 자료로 표가 바뀜")

# ④ 서버 쪽 고르기
sys.path.insert(0, str(ROOT / "server"))
import server as S  # noqa: E402
rows = S._market_rows()
check(len(rows) == 1403 and rows[0]["name"].startswith("시장"), "markets 표를 먼저 쓰지 않음")
con.execute("DROP TABLE markets")
con.commit()
rows = S._market_rows()
check(len(rows) > 1000 and "base_date" in rows[0], "표가 없을 때 markets.csv로 돌아가지 않음")

if fails:
    print("실패:", *fails, sep="\n- ")
    sys.exit(1)
print("전통시장 장날 자료 시험 통과")
