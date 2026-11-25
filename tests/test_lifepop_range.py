"""생활인구 여러 달 한 번에(0.6.6, 2026-11-25 Kevin 확정) 시험 — 자료 DB 없이(가짜 자료).

① 한 달만 물으면 결과가 예전과 같은지(새 함수를 안 거침)
② 여러 달: 공통 안내 한 번, '달마다' 줄은 공표 달 = 공표 값, 추정 달 = 추정 범위
③ with_estimate: 공표 달에도 '그 전 달까지만 알았다면' 추정값·차이(%)
④ 미공표 지역(등급 B): 맨 위 '맞혀 본 결과' 한 줄, 등급 C는 한 번만
⑤ 36개월이 응답 길이 한도(size_guard.LIMIT) 안인지, 37개월·거꾸로 된 기간은 안내
"""
import math
import os
import random
import sys
import tempfile
from pathlib import Path

os.environ["NOWCAST_DB"] = str(Path(tempfile.mkdtemp()) / "empty.db")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import lifepop as L  # noqa: E402
import server as S  # noqa: E402
import size_guard as G  # noqa: E402

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)


random.seed(5)
ALL = L.months_between("202201", "202610")
PUB_TO = "202606"                       # 공표는 2026-06까지
A_REG = ("강원특별자치도", "가군")        # 공표 지역
B_REG = ("경기도", "나시")                # 미공표, 통근 20%
C_REG = ("서울특별시", "다구")            # 미공표, 통근 40%
JF = 20000
NC = {m: (5000 * (1.3 if m[4:] in ("07", "08") else 1.0) * random.uniform(0.95, 1.05), 4) for m in ALL}
LIVE = {m: NC[m][0] * 4.0 * math.exp(random.gauss(0, 0.04)) + JF for m in ALL if m <= PUB_TO}


def by_period(db, table, codes, extra=""):
    if not codes:
        return {}
    if table == "kosis_life_pop":
        return dict(LIVE)
    if table == "kosis_reg_pop":
        return {"202001": JF - 500}
    if table == "kosis_foreigners":
        return {"2020": 500.0}
    return {}


L._codes = lambda db, sd, sg, col: ([(sd, sg)] if (sd, sg) == A_REG or col != "life_pop" else [])
L._by_period = by_period
L._nc_month = lambda db, sd, sg: dict(NC)
L._lunar_months = lambda db: set()
L.commute_share = lambda db, sd, sg: {B_REG: 20.0, C_REG: 40.0}.get((sd, sg))


def fake_unpub(db, sido, sigungu, ym):
    base = {"지역": f"{sido} {sigungu}", "달": ym, "공표": "이 지역은 생활인구 공표 대상(인구감소·관심지역)이 아니다."}
    if (sido, sigungu) == C_REG:
        return {**base, "추정": "하지 않음(등급 C)", "타지역 통근 비율(%)": 40.0, "이유": "통근이 많다."}
    if ym not in NC:
        return {"error": "그 달의 나우캐스트 주 자료가 3주 미만이라 추정할 수 없다(진행 중인 달은 추정하지 않음)."}
    stay = 30000.0
    e = 0.12
    return {**base, "등급": "B", "참고 추정 생활인구(명, 비공식)": round(stay + JF, -2),
            "참고 추정 체류인구(명, 비공식)": round(stay, -2),
            "과거 둘 중 한 번은 이 범위 안": [round(stay * math.exp(-e) + JF, -2), round(stay * math.exp(e) + JF, -2)],
            "과거 열 번 중 여덟 번은 이 범위 안": [round(stay * math.exp(-2 * e) + JF, -2), round(stay * math.exp(2 * e) + JF, -2)],
            "범위를 정한 근거": "공표 지역 94곳을 하나씩 빼고 맞혀 본 오차(같은 방법, 통근 비율 30% 미만 지역, 최근 12개월)",
            "추정 방법": "출근 몫 빼기 방법. 식", "쓴 방법": "출근 몫 빼기 방법",
            "주의": ["생활인구가 공표되지 않는 지역의 비공식 참고 추정치다."]}


L.estimate_unpublished = fake_unpub
S.analysis.series = lambda db, sd, sg: [1]
S._as_of = lambda: "시험"
TOOL = S.estimate_life_pop

# ① 한 달은 예전 모양 그대로
one = TOOL(*A_REG, "2026-09")
check("참고 추정 생활인구(명)" in one and "달마다" not in one, f"한 달 결과 모양이 바뀜: {list(one)}")
old = L.estimate(None, *A_REG, "202609")
check(one["참고 추정 생활인구(명)"] == old["참고 추정 생활인구(명)"], "한 달 추정값이 다름")
pub_one = TOOL(*A_REG, "2026-06")
check(pub_one.get("공표된 생활인구") == round(LIVE["202606"]), "공표 달 한 달 결과가 공표 값이 아님")

# ② 여러 달
r = TOOL(*A_REG, "2026-04", end_month="2026-09")
lines = r.get("달마다", [])
check(len(lines) == 6, f"달 수 {len(lines)}")
check(all("공표 값(명)" in x and "추정 범위(명, 과거 둘 중 한 번)" not in x for x in lines[:3]), "공표 달에 추정이 붙음")
check(all("추정 범위(명, 과거 둘 중 한 번)" in x and "공표 값(명)" not in x for x in lines[3:]), "추정 달 모양 이상")
check(lines[3]["추정 범위(명, 과거 둘 중 한 번)"] == old["과거 둘 중 한 번은 이 범위 안"] if lines[3]["달"] == "2026-09" else
      lines[5]["추정 범위(명, 과거 둘 중 한 번)"] == old["과거 둘 중 한 번은 이 범위 안"], "여러 달의 9월 범위가 한 달 결과와 다름")
check("견줄 때 지킬 것" in r and "단정" in r["견줄 때 지킬 것"], "비교 규칙 없음")
check(str(r).count("신뢰구간") <= 2, "공통 안내가 달마다 되풀이됨")

# ③ with_estimate
r = TOOL(*A_REG, "2026-04", end_month="2026-06", with_estimate=True)
x = r["달마다"][0]
check({"공표 값(명)", "추정값(명)", "추정과 공표 차이(%)"} <= set(x), f"with_estimate 줄 이상: {x}")
check(abs(x["추정과 공표 차이(%)"]) < 30, f"차이가 너무 큼: {x}")
check(list(x).index("공표 값(명)") < list(x).index("추정 범위(명, 과거 둘 중 한 번)"), "왼쪽 공표·오른쪽 추정 순서 아님")
r1 = TOOL(*A_REG, "2026-05", with_estimate=True)
check("달마다" in r1 and "추정값(명)" in r1["달마다"][0], "한 달 + with_estimate가 안 됨")

# ④ 미공표 지역
r = TOOL(*B_REG, "2026-01", end_month="2026-12")
check(any("94곳" in s and "±13%" in s for s in r.get("맞혀 본 결과", [])), f"맞혀 본 결과 줄 이상: {r.get('맞혀 본 결과')}")
check(sum(1 for x in r["달마다"] if "추정" in x and x["추정"].startswith("못 함")) == 2, "자료 없는 11·12월 표시 이상")
r = TOOL(*C_REG, "2026-01", end_month="2026-06")
check(r.get("추정", "").startswith("하지 않음") and "달마다" not in r, "등급 C가 한 번만 나오지 않음")

# ⑤ 길이·입력
r = TOOL(*A_REG, "2023-11", end_month="2026-10", with_estimate=True)
n = G.wire_length(r)
print(f"36개월(with_estimate) 길이: {n:,}자 / 한도 {G.LIMIT:,}자")
check(len(r["달마다"]) == 36 and n <= G.LIMIT, f"36개월이 한도를 넘음: {n}")
nb = G.wire_length(TOOL(*B_REG, "2023-11", end_month="2026-10"))
print(f"36개월(미공표 지역) 길이: {nb:,}자")
check(nb <= G.LIMIT, f"미공표 36개월이 한도를 넘음: {nb}")
check("error" in TOOL(*A_REG, "2023-10", end_month="2026-10"), "37개월이 막히지 않음")
check("error" in TOOL(*A_REG, "2026-09", end_month="2026-01"), "거꾸로 된 기간이 막히지 않음")
check("error" in TOOL(*A_REG, "2026-13"), "13월이 막히지 않음")
adv = G.advice("estimate_life_pop", {"year_month": "2023-11", "end_month": "2026-10"}, 40000)
check(adv.get("권장", "").endswith("개월 이하"), f"길이 안내 이상: {adv}")

if fails:
    print("실패:", *fails, sep="\n- ")
    sys.exit(1)
print("생활인구 여러 달 시험 통과")
