"""get_movement 0.6.0(2026-11-22): 주별 흐름 + 나눠 보기(옛 get_breakdown) + 시군구 '많이 오는 곳' 순위 + 지역 범위(인구감소지역 등).
결정 기록은 프로젝트 문서 Tool_0.6.0_설계.md 2-1·2-2(한 번에 주는 길이, 다 주기 어려운 결과는 답을 미리 계산).
기능 끄기: Azure 앱 설정 PAUSE_FEATURES에 movement_rank가 있으면 시군구 순위를 내지 않음."""
import csv
from pathlib import Path
from statistics import mean

import analysis

HERE = Path(__file__).resolve().parent
FLOW_WEEKS = 52          # 주 단위 흐름은 한 번에 1년
RANK_DEFAULT, RANK_MAX = 20, 100
AREAS = ("전체", "인구감소지역", "인구활력+지역")   # 인구감소지역은 관심지역 포함(2026-11-22 Kevin). 옛 값 '인구감소관심지역'도 같은 범위로 받음
AREA_NAME = {"인구감소지역": "인구감소지역(관심지역 포함 110곳)", "인구감소관심지역": "인구감소지역(관심지역 포함 110곳)",
             "인구활력+지역": "인구활력+지역(12곳)"}
MARK_NOTES = ["*는 인구감소관심지역(18곳), 표시 없는 곳은 인구감소지역(92곳)이에요.",
              "+는 인구활력+지역(12곳)이에요. 여건이 나아져 5년간 지원을 이어 받는 곳이에요."]
DEPOP_SOURCE = "행정안전부 2026-10-07 인구감소지역·인구감소관심지역 재지정·고시"
_DEPOP = None


def depop():
    """canon → (종류 '감소'·'관심', 인구활력+지역 여부)"""
    global _DEPOP
    if _DEPOP is None:
        out = {}
        try:
            with open(HERE / "depop_regions.csv", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    out[analysis.canon(r["sido"], r["sigungu"])] = (r["kind"], r["vitality_plus"] == "1")
        except OSError:
            pass
        _DEPOP = out
    return _DEPOP


def in_area(key, area):
    if area == "전체":
        return True
    d = depop().get(key)
    if not d:
        return False
    if area in ("인구감소지역", "인구감소관심지역"):
        return True
    return d[1]


def month_of(wo):
    return wo // 10            # 주차 이름의 달(YYYYMM)


def month_ko(m):
    return f"{m // 100}년 {m % 100}월"


def split_month(rows_wo):
    """주차 번호 목록 → 달별로 묶음(최근 달이 마지막)."""
    out = {}
    for wo in rows_wo:
        out.setdefault(month_of(wo), []).append(wo)
    return out


def breakdown(db, direction, category, a, b):
    """항목을 비운 나눠 보기: 한 번에 한 달(주차 이름의 달). 더 긴 기간은 최근 달 + 기간 요약 + 다음 달 안내."""
    rows = db.execute("SELECT week, week_order, item, within, outside, total FROM moves WHERE direction=? AND category=? "
                      "AND week_order BETWEEN ? AND ? ORDER BY week_order, total DESC", (direction, category, a, b)).fetchall()
    if not rows:
        return None
    by_wo = {}
    for wk, wo, it, wi, ou, to in rows:
        by_wo.setdefault(wo, (wk, []))[1].append((it, wi, ou, to))
    months = split_month(sorted(by_wo))
    ms = sorted(months)
    show = ms[-1]
    out = {"단위": "일평균 이동 건수", "기준": f"{category}별", "보여 준 달": month_ko(show) + "(주차 이름의 달 기준)"}
    data = []
    for wo in months[show]:
        wk, its = by_wo[wo]
        s = sum(t for _, _, _, t in its) or 1
        data.append({"week": wk, "dates": analysis.week_dates(wo),
                     "항목": [{"item": it, "within": wi, "outside": ou, "total": to, "share_pct": round(to / s * 100, 1)}
                            for it, wi, ou, to in its]})
    out["data"] = data
    if len(ms) > 1:
        prev = ms[-2]
        out["한 번에 보여 주는 길이"] = (f"자료가 많아서 나눠 보기는 한 번에 한 달씩만 돼요. {month_ko(ms[0])}~{month_ko(show)} 중 "
                                f"{month_ko(show)}을 먼저 보여 드렸어요. 이어서 {month_ko(prev)}을 보여 드릴까요?(이용자에게 이렇게 물을 것)")
        share = {}
        for m in ms:
            acc = {}
            for wo in months[m]:
                its = by_wo[wo][1]
                s = sum(t for _, _, _, t in its) or 1
                for it, _, _, t in its:
                    acc.setdefault(it, []).append(t / s * 100)
            share[m] = {it: mean(v) for it, v in acc.items()}
        items = sorted({it for m in ms for it in share[m]})
        avg = {it: round(mean(share[m][it] for m in ms if it in share[m]), 1) for it in items}
        ch = sorted(((share[ms[-1]].get(it, 0) - share[ms[0]].get(it, 0), it) for it in items), key=lambda x: -abs(x[0]))
        out["기간 요약"] = {
            "기간 평균 비중(%)": dict(sorted(avg.items(), key=lambda x: -x[1])),
            "비중이 가장 크게 바뀐 항목": [f"{it}: {month_ko(ms[0])} → {month_ko(ms[-1])} {d:+.1f}%p" for d, it in ch[:3]],
        }
    return out


def rank(db, direction, a, b, top, rank_from, area, pop_of):
    """시군구 '많이 오는 곳': 그 기간 관외 이동 하루 평균 건수 순. pop_of(sido, sigungu) → 주민 수."""
    if direction == "유입" and analysis._ALL is not None:
        # 서버 저장소가 느려 긴 기간을 DB에서 읽으면 2분이 넘음 → 판정 쪽이 메모리에 올려 둔 관외 유입을 씀
        rows = [(sd, sg, wo, v) for (sd, sg), ser in analysis._ALL.items() for wo, _wk, v in ser if a <= wo <= b]
    else:
        rows = db.execute("SELECT sido, item, week_order, outside FROM moves WHERE direction=? AND category='시군구' "
                          "AND week_order BETWEEN ? AND ?", (direction, a, b)).fetchall()
    if not rows:
        return None
    acc, names, weeks = {}, {}, set()
    for sd, sg, wo, v in rows:
        if v is None:
            continue
        k = analysis.canon(sd, sg)
        acc.setdefault(k, {})[wo] = v
        names[k] = (sd, sg)
        weeks.add(wo)
    n_weeks = len(weeks)
    last = max(weeks)
    lst, gone = [], []
    for k, wv in acc.items():
        if last not in wv:          # 기간 끝에 자료가 없는 이름(개편으로 없어진 옛 이름 등)은 순위에서 뺌
            gone.append(k)
            continue
        if not in_area(k, area):
            continue
        lst.append((mean(wv.values()), k, len(wv)))
    lst.sort(reverse=True)
    top = max(1, min(int(top or RANK_DEFAULT), RANK_MAX))
    start = max(1, int(rank_from or 1))
    part = lst[start - 1:start - 1 + top]
    dp = depop()
    out_rows = []
    for i, (v, k, nw) in enumerate(part, start):
        sd, sg = names[k]
        mark = ""
        if area != "전체":
            kind, plus = dp.get(k, (None, False))
            mark = ("*" if kind == "관심" else "") + ("+" if plus else "")
        row = {"순위": i, "지역": f"{sd} {sg}{mark}", "하루 평균 관외 이동(건)": round(v)}
        p = pop_of(sd, sg)
        if p:
            row["주민 1명당(건)"] = round(v / p, 2)
        if nw < n_weeks:
            row["자료 있는 주"] = f"{nw}/{n_weeks}주(행정구역 개편 등으로 일부 기간만 있음)"
        out_rows.append(row)
    noun = "외부에서 들어오는 이동이 많은 곳" if direction == "유입" else "다른 지역으로 나가는 이동이 많은 곳"
    out = {
        "이름": noun,
        "지역 범위": AREA_NAME.get(area, area) + (f"({DEPOP_SOURCE})" if area != "전체" else ""),
        "읽는 법": ["많이 오는 곳은 지역 크기와 주변 인구에 따라 정해지는 면이 커요. 좋고 나쁨을 뜻하지 않아요.",
                 "지역이 작게 나뉜 곳(구)일수록 옆 지역에서 온 이동까지 들어가 크게 잡혀요. 넓은 시·군과 바로 견줄 때는 이 점을 함께 봐 주세요.",
                 "이 순위는 이동이 '많은' 곳이다. 평소보다 '늘어난' 곳은 find_unusual_regions로 따로 본다."],
        "지역 수": len(lst),
        "순위": out_rows,
    }
    if area != "전체":
        out["표시"] = MARK_NOTES
    if gone:
        olds = sorted({names[k][1] if names[k][1].endswith("시") else f"{names[k][0][:2]} {names[k][1]}" for k in gone})
        out["행정구역이 바뀐 곳"] = (f"기간 중 행정구역이 바뀐 곳은 기간 끝의 이름만 넣었어요(빠진 옛 이름: {', '.join(olds)}). "
                             "옛 이름은 그 이름으로 흐름을 따로 볼 수 있어요.")
    if start - 1 + top < len(lst):
        nxt = start + top
        out["이어서"] = (f"{len(lst)}곳 가운데 {start}~{start - 1 + len(part)}위를 보여 드렸어요. 이어서 {nxt}~{min(nxt + top - 1, len(lst))}위를 "
                       f"보여 드릴까요?(이용자가 원하면 rank_from={nxt}로 다시 부를 것)")
    return out


def strong_weeks(table, sido, sigungu, a, b, n=5):
    """판정 기간 안에서 평소보다 특히 많았던 주(연휴 주·원자료 확인 구간 뺌) 상위 n개."""
    r = table.get(analysis.canon(sido, sigungu))
    if not r:
        return []
    ws = [w for w in r["weeks"] if a <= w["wo"] <= b and not w.get("suspect") and not analysis.is_long_holiday(w["hol"])
          and w["ratio"] is not None and w["ratio"] > 0 and analysis.real_dir(w["ratio"], w["dev"])]
    return sorted(ws, key=lambda w: -w["ratio"])[:n]
