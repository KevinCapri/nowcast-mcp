"""축제 참고 예측: "올해(다음) 축제 주에 관외 유입이 평소보다 얼마나 늘까"를 범위로 답한다.

방법:
- 축제 일정: 문체부 연도별 지역축제 개최 계획(2023~2026, 계획 일정)에서 같은 축제를 이어 붙인 표(festivals.csv)
- 회차마다 '주된 주'(행사 일수·주말이 가장 많이 든 주)의 상승률
  · 평소 대비 = ln(그 주 관외 유입 / 직전 8주 중앙값)
  · 비교군 대비 = 평소 대비 − 같은 주 '비슷하면서 100km 밖' 10곳의 평소 대비 중앙값
- 예측 조건: 지난 회차 1번 이상, 지난 회차 평소 대비 평균이 평소 출렁임의 2배 초과
- 예측 기준 = 지난 회차 비교군 대비 상승률의 평균(되돌림은 쓰지 않음)
- 범위(같은 묶음 축제들의 다음 회차 변화, 68건 채점):
  · 2~3.5배 묶음: 둘 중 한 번 −4~+5%, 열 중 여덟 −12~+11%
  · 3.5배 초과: 둘 중 한 번 −8~+2%, 열 중 여덟 −11~+5%
  · 지난 회차 3번 이상이면 '열 중 여덟'에 자기 최소~최대를 합침
"""
import csv
import datetime as dt
import math
import re
from pathlib import Path
from statistics import mean, median

import analysis as A
import safe

CSV = Path(__file__).with_name("festivals.csv")
BANDS = {"2~3.5배": ((-0.04, 0.05), (-0.12, 0.11)), "3.5배 초과": ((-0.08, 0.02), (-0.11, 0.05))}


def _load():
    rows = []
    with open(CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                r["y"] = int(r["y"])
                for k in ("s", "e"):
                    if r.get(k):
                        dt.date.fromisoformat(r[k])
            except Exception as e:
                safe.bad_row("festivals.csv", r, e)
                continue
            rows.append(r)
    return rows


FEST = _load()
FAR = {}
with open(A.PEERS_CSV, encoding="utf-8") as f:
    for r in csv.DictReader(f):
        if r.get("far"):
            FAR[A.canon(r["sido"], r["sigungu"])] = [tuple(p.split(" ", 1)) for p in r["far"].split(";")]


def norm(name):
    s = re.sub(r"제?\s*\d+\s*회|\d{4}\s*년?|\(.*?\)", "", name or "")
    return re.sub(r"[\s·\-_'\"]", "", s)


def week_order(d):
    """나우캐스트 주차 번호(목요일이 속한 달, 2022년만 수요일)."""
    mon = d - dt.timedelta(days=d.weekday())
    wed = mon + dt.timedelta(days=2)
    k = wed if wed.year == 2022 else mon + dt.timedelta(days=3)
    return k.year * 1000 + k.month * 10 + (k.day - 1) // 7 + 1, mon


def main_week(s, e):
    """행사 일수가 가장 많이 든 주(같으면 주말이 많이 든 주)의 월요일."""
    best, key = None, None
    d = s
    weeks = {}
    while d <= e:
        mon = d - dt.timedelta(days=d.weekday())
        a, b = weeks.get(mon, (0, 0))
        weeks[mon] = (a + 1, b + (d.weekday() >= 5))
        d += dt.timedelta(days=1)
    for mon, v in weeks.items():
        if key is None or v > key:
            best, key = mon, v
    return best


def find(sido, sigungu, name=""):
    c = A.canon(sido, sigungu)
    here = [r for r in FEST if A.canon(r["sido"], r["sigungu"]) == c]
    if not name:
        return here
    q = norm(name)
    return [r for r in here if q and (q in norm(r["name"]) or norm(r["name"]) in q)]


def _comp_series(db, sido, sigungu):
    hol = A.holidays(db)
    me = A.deviations(A.series(db, sido, sigungu), hol)
    far = [A.deviations(A.series(db, a, b), hol) for a, b in FAR.get(A.canon(sido, sigungu), [])]
    far = [p for p in far if p]

    def common(wo):
        xs = [p[wo][3] for p in far if wo in p and p[wo][3] is not None]
        return median(xs) if len(xs) >= 5 else None
    return hol, me, common, len(far)


def in_week(sido, sigungu, monday, holidays=()):
    """그 주(월~일)에 걸친 축제(문체부 계획): 이름, 계획 기간, 그 주에 든 행사 일수, 그중 주말·공휴일 일수."""
    c = A.canon(sido, sigungu)
    sun = monday + dt.timedelta(days=6)
    out, seen = [], set()
    for r in FEST:
        if A.canon(r["sido"], r["sigungu"]) != c or not r.get("s") or not r.get("e"):
            continue
        try:
            s, e = dt.date.fromisoformat(r["s"]), dt.date.fromisoformat(r["e"])
        except ValueError:
            continue
        if s > sun or e < monday or r["name"] in seen:
            continue
        seen.add(r["name"])
        days = [monday + dt.timedelta(days=i) for i in range(7) if s <= monday + dt.timedelta(days=i) <= e]
        off = sum(1 for d in days if d.weekday() >= 5 or d.isoformat() in holidays)
        out.append({"축제": r["name"], "계획 기간": f"{r['s']}~{r['e']}", "이 주에 든 행사 일수": len(days),
                    "그중 주말·공휴일": off})
    return out


def first_time(db, sido, sigungu):
    """지난 회차가 없는 축제: 비슷한 지역 10곳에서 열린 축제들의 '주된 주' 평소 대비 상승률 분포(낮은 신뢰도 참고 범위).
    모의시험(한 지역): 112건, −4~+30%, 중앙값 +7%, 평소 출렁임 2배 초과 14%. 개별 축제 오차 약 20%."""
    hol = A.holidays(db)
    vals, strong = [], 0
    for a, b in A.PEERS.get(A.canon(sido, sigungu), []):
        me = A.deviations(A.series(db, a, b), hol)
        if not me:
            continue
        s = A._mad([x[3] for wo, x in me.items() if wo >= A.START and x[3] is not None and not hol.get(wo)])
        for r in FEST:
            if A.canon(r["sido"], r["sigungu"]) != A.canon(a, b) or not r.get("main_mon"):
                continue
            wo, _ = week_order(dt.date.fromisoformat(r["main_mon"]))
            if wo in me and me[wo][3] is not None and not A.is_long_holiday(hol.get(wo)):
                vals.append(me[wo][3])
                strong += bool(s and me[wo][3] / s > 2)
    if len(vals) < 10:
        return None
    vals.sort()
    q = lambda p: vals[min(len(vals) - 1, int(p * len(vals)))]
    return {"근거 축제 수": len(vals), "근거": "인구·주변 인구가 비슷한 지역 10곳에서 열린 축제(문체부 계획 2023~2026)의 주된 주",
            "평소 대비 상승률 중앙값(%)": A._pct(median(vals)),
            "열 번 중 여덟 번은 이 안(%)": [A._pct(q(0.1)), A._pct(q(0.9))],
            "평소 출렁임 2배 넘게 크게 늘어난 비율(%)": round(strong / len(vals) * 100)}


def _count_hint(use, me):
    """건수로 묻는 이용자를 위한 참고 값(%만 주면 AI가 건수를 즉석 계산하는 일을 막음).
    기준 = 가장 최근 회차 주된 주의 평소 값(직전 8주 중앙값). 상승 = 지난 회차 평소 대비 평균.
    범위는 평소 대비 기준 채점 오차(중앙값 약 11%, 열 중 하나는 22% 넘게 틀림)."""
    last = max(use, key=lambda x: x["wo"])
    base = me[last["wo"]][2]
    if not base:
        return None
    mid = base * math.exp(mean(x["raw"] for x in use))
    f = lambda e: [round(mid * (1 - e), -2), round(mid * (1 + e), -2)]
    return {"기준(가장 최근 회차 주된 주의 평소 값, 일평균 건수)": round(base, -2), "기준 주차": last["주차"],
            "가운데 값": round(mid, -2),
            "과거 둘 중 한 번은 이 범위 안": f(0.11), "과거 열 번 중 아홉 번은 이 범위 안": f(0.22),
            "설명": "지난 회차 평소 대비 평균 상승을 가장 최근 회차의 평소 값에 곱한 참고 값이다. 단위는 하루 평균 이동 건수이며 "
                  "사람 수(명)가 아니다. 올해 평소 값이 그때와 다르면 그만큼 달라진다(통신사 가입자 변화 등). "
                  "평소 대비에는 그 주 전국 공통 흐름이 섞여 비교군 대비 예측보다 범위가 넓다."}


def forecast(db, sido, sigungu, name="", start_date="", end_date=""):
    found = find(sido, sigungu, name)
    if not found:
        listed = sorted({r["name"] for r in find(sido, sigungu)})
        ref = first_time(db, sido, sigungu)
        out = {"축제": name or None, "지역": f"{sido} {sigungu}", "예측": "하지 않음",
               "이유": "이 지역에서 그 축제의 지난 회차를 찾지 못했다(문체부 개최 계획 2023~2026 기준). "
                     "처음 여는 축제이거나 계획에 이름이 다르게 올랐을 수 있다.",
               "이 지역 축제(문체부 계획)": listed[:30]}
        if ref:
            out["낮은 신뢰도 참고 범위"] = ref
            out["주의"] = [
                "축제 하나를 예측한 값이 아니라 비슷한 지역 축제들의 분포다. 개별 축제에 대한 오차는 약 20%로 크다.",
                f"이 분포에서 평소보다 크게 늘어난 축제는 {ref['평소 출렁임 2배 넘게 크게 늘어난 비율(%)']}%뿐이다. "
                "이 자료(시군구·주 단위)에서는 아예 안 나타날 가능성이 더 크다고 말할 것.",
                "방문 인원(명)이 아니라 관외 유입 이동 건수의 상승률이다. 숫자를 '명'으로 바꾸지 말 것.",
                "이름이 계획에 다르게 올랐을 수 있으니 '이 지역 축제' 목록에 비슷한 이름이 있는지 이용자에게 확인할 것.",
            ]
        else:
            out["주의"] = ["비슷한 지역의 축제 기록도 모자라 참고 범위를 낼 수 없다. 숫자를 지어내지 말 것."]
        return out
    fids = sorted({r["fid"] for r in found})
    if len(fids) > 1 and name:
        names = sorted({r["name"] for r in found})
        return {"error": "같은 이름에 걸리는 축제가 여럿이다. 이름을 더 정확히 줄 것.", "후보": names[:20]}
    if not name:
        return {"안내": "축제 이름을 함께 넣을 것.", "이 지역 축제": sorted({r["name"] for r in found})[:30]}
    past = sorted([r for r in found if r["fid"] == fids[0]], key=lambda r: r["y"])

    hol, me, common, n_far = _comp_series(db, sido, sigungu)
    raw_plain = [x[3] for wo, x in me.items() if wo >= A.START and x[3] is not None and not hol.get(wo)]
    s = A._mad(raw_plain)
    rec = []
    for r in past:
        wo, mon = week_order(dt.date.fromisoformat(r["main_mon"]))
        if wo not in me or me[wo][3] is None:
            continue
        raw = me[wo][3]
        c = common(wo)
        rec.append({"y": r["y"], "name": r["name"], "기간": f"{r['s']}~{r['e']}", "주차": me[wo][0],
                    "wo": wo, "raw": raw, "comp": None if c is None else raw - c,
                    "long_hol": A.is_long_holiday(hol.get(wo)), "sus": A.is_suspect(sido, sigungu, wo)})
    use = [x for x in rec if not x["long_hol"] and x["comp"] is not None]
    shown = [{"연도": x["y"], "주된 주": x["주차"], "기간(계획)": x["기간"],
              "평소 대비(%)": A._pct(x["raw"]), "비교군 대비(%)": A._pct(x["comp"]),
              "연휴 주": x["long_hol"], **({"원자료 확인 필요 구간": A.SUSPECT_FLAG} if x["sus"] else {})} for x in rec]
    sus_notes = []
    if any(x["sus"] for x in rec):
        used = [str(x["y"]) for x in use if x["sus"]]
        unused = [str(x["y"]) for x in rec if x["sus"] and x not in use]
        sus_notes = A.suspect(sido, sigungu, min(x["wo"] for x in rec if x["sus"]), max(x["wo"] for x in rec if x["sus"]))
        if used:
            sus_notes.append(f"지난 회차 중 {'·'.join(used)}년 주된 주가 원자료 확인 필요 구간이다. 그 값도 예측 계산(또는 '예측 하지 않음' 판단)에 "
                             "들어갔으니, 결과를 말할 때 이 점을 먼저 밝히고 그해 값을 '작년엔 줄었다·늘었다'의 근거로 쓰지 말 것.")
        if unused:
            sus_notes.append(f"지난 회차 중 {'·'.join(unused)}년 주된 주{'도' if used else '가'} 원자료 확인 필요 구간이다(연휴 주 등이라 계산에는 들어가지 않았다). "
                             "그해 값을 '작년엔 줄었다·늘었다'의 근거로 쓰지 말 것.")
    base = {"축제": past[-1]["name"], "지역": f"{sido} {sigungu}", "지난 회차": shown,
            "평소 출렁임(%)": A._pct(s)}
    common_notes = [
        "이 서비스가 계산한 비공식 참고 예측이다. 방문 인원(명)이 아니라 관외 유입 이동 건수 기준이다.",
        "범위는 '과거 둘 중 한 번'·'열 번 중 여덟 번은 이 안'이라고 말할 것. '신뢰구간·오차범위'라고 부르지 말 것.",
        "축제 일정은 주로 문체부 연초 계획이라 실제와 다를 수 있다(일부는 언론 보도로 확인).",
        "주차·안전 인력 같은 운영 계획의 유일한 근거로 쓰지 말 것.",
    ]
    if not use or s is None:
        base["예측"] = "하지 않음"
        base["이유"] = "지난 회차 중 비교할 수 있는 주가 없다(자료 기간 밖이거나 연휴 주)."
        base["주의"] = sus_notes + common_notes
        return base
    ratio = mean(x["raw"] for x in use) / s
    base["지난 회차 평균의 평소 출렁임 대비 몇 배"] = round(ratio, 1)
    if ratio <= 2:
        base["예측"] = "하지 않음"
        base["이유"] = ("지난 회차에도 시군구·주 단위에서 평소 출렁임의 2배를 넘지 않았다. "
                      "이 자료로는 축제 효과가 잡히지 않아 예측할 것이 없다(전국 축제의 약 83%가 이렇다).")
        base["주의"] = sus_notes + common_notes
        return base
    group = "3.5배 초과" if ratio > 3.5 else "2~3.5배"
    (a1, b1), (a2, b2) = BANDS[group]
    lvl = math.exp(mean(x["comp"] for x in use))
    r1 = [round((lvl * (1 + a1) - 1) * 100), round((lvl * (1 + b1) - 1) * 100)]
    r2 = [round((lvl * (1 + a2) - 1) * 100), round((lvl * (1 + b2) - 1) * 100)]
    if len(use) >= 3:
        own = [(math.exp(x["comp"]) - 1) * 100 for x in use]
        r2 = [round(min(r2[0], min(own))), round(max(r2[1], max(own)))]
    base.update({
        "예측(비교군 대비 상승률, %)": round((lvl - 1) * 100),
        "과거 둘 중 한 번은 이 범위 안(%)": r1,
        "과거 열 번 중 여덟 번은 이 범위 안(%)": r2,
        "참고: 지난 회차 평소 대비 평균(%)": A._pct(mean(x["raw"] for x in use)),
        "참고: 예상 관외 유입(일평균 건수)": _count_hint(use, me),
        "묶음": f"지난 회차 효과 {group}",
        "범위 근거": "같은 묶음 축제들의 다음 회차 변화(68건 채점, 비교군 대비 기준 오차 중앙값 약 4%)"
                  + (" + 이 축제의 지난 회차 최소~최대" if len(use) >= 3 else ""),
    })
    notes = []
    if start_date and end_date:
        try:
            sd, ed = dt.date.fromisoformat(start_date), dt.date.fromisoformat(end_date)
            wo, mon = week_order(main_week(sd, ed))
            h = hol.get(wo)
            base["올해 주된 주"] = f"{mon} 주"
            if A.is_long_holiday(h):
                notes.append(f"올해 주된 주에 연휴({h[2]})가 겹친다. 연휴 효과가 더해지면 범위보다 높을 수 있다. "
                             "이용자가 원하면 연휴 효과를 더한 위쪽 경우를 따로 계산할 것.")
            elif h and h[0] > 0:
                notes.append(f"올해 주된 주에 평일 공휴일({h[2]})이 있다. 지난 회차와 공휴일 수가 다르면 치우칠 수 있다.")
        except Exception:
            notes.append("올해 날짜 형식은 YYYY-MM-DD.")
    gap = mean(x["raw"] for x in use) - mean(x["comp"] for x in use)
    if gap > 0.1:
        notes.append(f"축제 주에는 비슷한 지역들도 함께 늘었다(평소 대비 평균 {A._pct(mean(x['raw'] for x in use))}% 중 "
                     f"공통 흐름 몫이 큼, 휴가철·봄나들이·연휴 등). 축제만의 몫은 비교군 대비 값에 가깝다. "
                     "'평소보다 몇 % 늘까'를 물으면 평소 대비 값도 함께 알려 줄 것.")
    if any(x["long_hol"] for x in rec):
        notes.append("지난 회차 중 연휴와 겹친 해는 예측 기준에서 뺐다.")
    if n_far < 5:
        notes.append("비교군(100km 밖 비슷한 지역)이 모자라 비교군 대비 값이 불안정하다.")
    base["주의"] = sus_notes + notes + [
        "과거 채점에서 예측 대상 축제도 셋 중 하나는 그해 크게 늘지 않았다.",
        "날씨·개화·프로그램 변화처럼 과거에 없던 사정은 반영되지 않는다.",
    ] + common_notes
    return base
