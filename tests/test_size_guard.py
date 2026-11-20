"""응답 길이 제한 장치 시험(자료 DB 없이 — PR마다 기본 검사에서 돎).

가짜로 아주 긴 결과를 돌려주는 함수에 size_guard를 씌워, ① 한도 안이면 결과가 그대로 나가는지
② 넘으면 안내로 바뀌는지 ③ 도구마다 줄일 것(기간·top·years_back)과 권장 값이 맞게 나오는지 본다.
실제 서버의 길이는 배포 뒤 실제 서비스 확인(tests/live_check.py)에서 본다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import size_guard as G  # noqa: E402

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)


def big(n):
    return {"표": [{"주차": f"{i}", "값": "가" * 40} for i in range(n)]}


logs = []
log = logs.append

# ① 짧으면 그대로(같은 객체)
small = {"a": 1, "한글": "값"}
f = G.guard("get_movement", lambda **k: small, log)
check(f(start_week="2026.01.1주차", end_week="2026.02.1주차") is small, "짧은 결과가 그대로 나가지 않음")

# ② 기간 도구(주): 넘으면 error + 줄일 것 기간 + 권장 N주
f = G.guard("get_movement", lambda **k: big(2000), log)
r = f(start_week="2019.01.1주차", end_week="2026.11.1주차", direction="유입")
check("error" in r and "기간" in r["error"], f"get_movement 안내 이상: {r}")
check(r.get("줄일 것", "").startswith("기간") and r.get("권장", "").endswith("주 이하"), f"get_movement 권장 이상: {r}")
check(G.wire_length(r) < G.LIMIT, "안내 자체가 한도를 넘음")

# ③ 기간 도구(날짜)
f = G.guard("get_datalab_daily", lambda **k: big(2000), log)
r = f(start_date="2025-01-01", end_date="2025-12-31")
check(r.get("권장", "").endswith("일 이하"), f"get_datalab_daily 권장 이상: {r}")

# ④ find_unusual_regions: top을 줄이라고, 기간은 그대로
f = G.guard("find_unusual_regions", lambda **k: big(2000), log)
r = f(top=20, start_week="2023.06.1주차", end_week="2026.11.1주차", week="", sido="", sigungu="")
check("top" in r.get("error", "") and "기간은 그대로" in r["error"] and r.get("줄일 것") == "top", f"find_unusual_regions 안내 이상: {r}")
n = int(r["권장"].split()[1])
check(1 <= n < 20, f"top 권장 값 이상: {n}")

# ⑤ compare_same_week: years_back
f = G.guard("compare_same_week", lambda **k: big(2000), log)
r = f(sido="x", sigungu="y", week="2026.01.3주차", years_back=3)
check(r.get("줄일 것") == "years_back", f"compare_same_week 안내 이상: {r}")

# ⑥ 줄일 인자 없는 도구: 안내만 + 서버 기록
logs.clear()
f = G.guard("get_metadata", lambda **k: big(2000), log)
r = f()
check("error" in r and "줄일 것" not in r, f"get_metadata 안내 이상: {r}")
check(any("get_metadata" in x for x in logs), "줄일 인자 없는 도구가 서버 기록에 안 남음")

# ⑦ 목록 결과(list_regions처럼)도 잼
f = G.guard("list_regions", lambda **k: [{"시도": "가" * 50}] * 600, log)
r = f(keyword="구")
check(isinstance(r, dict) and "error" in r, "목록 결과 길이를 못 잼")

# ⑧ 결과 칸을 건드리지 않음(한도 안 결과의 키 순서·값 그대로)
src = {"값의 성격": "x", "판정": "조금 많음", "배수(이용자가 물을 때만 말함)": 1.2}
f = G.guard("analyze_region_week", lambda **k: src, log)
check(list(f(sido="a", sigungu="b", week="c").keys()) == list(src.keys()), "결과 칸이 바뀜")

if fails:
    print("실패:")
    for m in fails:
        print(" -", m)
    sys.exit(1)
print(f"응답 길이 장치 시험 통과(한도 {G.LIMIT:,}자)")
