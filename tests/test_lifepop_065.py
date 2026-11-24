"""0.6.5 미공표 지역 생활인구 공식 시험(자료 DB 없이 — 2026-11-24).

가짜 공표 지역 40곳(체류 = 외지인·주민으로 정한 값)을 만들어
① 'dl'·'sub' 공식이 그 관계를 되찾는지
② 미공표 지역에서 방법 고르기: 관광공사 값이 있는 달 → dl, 없는 달 → sub(+'다시 계산' 안내), 광역시 구 → sub
③ 표에 없는 세종·인천 새 구 → 통근 비율 채움, 등급 C '이용자에게 할 말'
④ '할 수 없음' 결과에 의견 보내기 줄
을 본다.
"""
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import lifepop as L  # noqa: E402

random.seed(3)
MONTHS = [f"2025{m:02d}" for m in range(7, 13)] + [f"2026{m:02d}" for m in range(1, 7)]
PUB = {}           # (sido, sigungu) → dict
for i in range(40):
    jf = random.uniform(2e4, 2e5)
    cm = jf * random.uniform(0.02, 0.1)
    rows = {}
    for m in MONTHS:
        dl = jf * random.uniform(0.05, 0.6)
        stay = math.exp(2.9) * dl ** 1.0 * jf ** -0.09 * math.exp(random.gauss(0, 0.05))
        nc = (stay - cm) / 5 + cm * L._wd_ratio(None, m)          # 관외 유입 = 방문 몫 + 출근 몫
        rows[m] = dict(dl=dl, stay=stay, nc=nc)
    PUB[("가도", f"군{i}")] = dict(jf=jf, cm=cm, rows=rows)

TARGET = ("나도", "시가")      # 미공표, 통근 20%
METRO = ("부산광역시", "가구")  # 미공표, 광역시 구
T = dict(jf=1e5, cm=6e3)
DL_T = {"202605": 3e4}          # 2026-05만 관광공사 값 있음


class DB:
    def execute(self, q, args=()):
        if "FROM region_map" in q:
            return Cur([(sd, sg) for sd, sg in PUB])
        raise RuntimeError("DB를 읽으면 안 됨: " + q[:40])


class Cur(list):
    def fetchall(self):
        return list(self)

    def fetchone(self):
        return None


def live(db, table, codes, extra=""):
    k = codes[0]
    return {m: r["stay"] + PUB[k]["jf"] for m, r in PUB[k]["rows"].items()} if k in PUB else {}


L._codes = lambda db, sd, sg, col: [(sd, sg)]
L._by_period = live
L.commute_share = lambda db, sd, sg: 20.0 if (sd, sg) in PUB or (sd, sg) in (TARGET, METRO) else None
L._reg = lambda db, sd, sg: ({"200001": (PUB.get((sd, sg)) or T)["jf"]}, {"2000": 0.0})
L._nc_month = lambda db, sd, sg: ({m: (r["nc"], 4) for m, r in PUB[(sd, sg)]["rows"].items()} if (sd, sg) in PUB
                                   else {"202605": (2e4, 4), "202609": (2e4, 4)})
L._dl_month = lambda db, sd, sg: ({m: r["dl"] for m, r in PUB[(sd, sg)]["rows"].items()} if (sd, sg) in PUB
                                  else dict(DL_T))
L._commute_in = lambda sd, sg: (PUB.get((sd, sg)) or T)["cm"]
L._wd_ratio = lambda db, ym: 21 / 30
L.A.canon = lambda sd, sg: (sd, sg)

db = DB()
F = L._fit(db, "dl")
assert F and abs(F["coef"][1] - 1.0) < 0.05 and abs(F["coef"][2] + 0.09) < 0.1, F["coef"]
G = L._fit(db, "sub")
assert G and G["n_regions"] == 40, G

r = L.estimate_unpublished(db, *TARGET, "202605")
assert r["쓴 방법"] == L.METHOD_NAME["dl"], r
assert "그 달 하루 평균 외지인(명, 한국관광공사)" in r
assert not any("다시 계산" in n for n in r["주의"])

r = L.estimate_unpublished(db, *TARGET, "202609")
assert r["쓴 방법"] == L.METHOD_NAME["sub"], r
assert r["주의"][0].startswith("관광공사 값이 나오면"), r["주의"]
assert "출근 인원(명, 인구총조사 2020 통근 유입)" in r

r = L.estimate_unpublished(db, *METRO, "202605")
assert r["쓴 방법"] == L.METHOD_NAME["sub"], r
assert not any("다시 계산" in n for n in r["주의"])

r = L.estimate_unpublished(db, "세종특별자치시", "세종시", "202605")
assert r["추정"].startswith("하지 않음(등급 C)") and r["통근 비율 출처"].startswith("인구총조사"), r
assert r["이용자에게 할 말"].startswith("세종시는"), r["이용자에게 할 말"]
assert "의견 보내기" in r

r = L.estimate_unpublished(db, "세종특별자치시", "세종특별자치시", "202605")
assert r["추정"].startswith("하지 않음(등급 C)") and r["이용자에게 할 말"].startswith("세종특별자치시는"), r

r = L.estimate_unpublished(db, "인천광역시", "제물포구", "202605")
assert r["타지역 통근 비율(%)"] == 60.0 and r["이용자에게 할 말"].startswith("제물포구는"), r

r = L.estimate_unpublished(db, "없는도", "없는군", "202605")
assert r["추정"] == "하지 않음" and "의견 보내기" in r, r
print("0.6.5 생활인구 공식 시험 통과: dl 계수", [round(c, 3) for c in F["coef"]], "· sub", [round(c, 3) for c in G["coef"]])
