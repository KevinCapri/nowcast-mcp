"""회귀 시험(2026-11-15, claude/변경_관리_원칙.md 5번).

대표 질문 묶음의 핵심 값(판정·배수·평소 대비 등)을 기준 파일과 비교해, 고친 코드가 기존 결과를 바꿨는지 본다.
  NOWCAST_DB=/경로/nowcast.db python tests/regression.py          # 비교(바뀐 것과 걸린 시간 출력)
  NOWCAST_DB=/경로/nowcast.db python tests/regression.py --save   # 지금 결과를 기준으로 저장(바뀐 이유를 확인한 뒤에만)
- 자료가 새로 들어오면 최근 주 판정은 바뀔 수 있다(평소 값이 달라짐). 묶음은 자료가 더 붙어도 안 바뀌는 지난 주로 고른다.
- 결과 항목이 사라지거나 이름이 바뀌어도 잡는다(keys).
- 자료가 모두 든 DB로 돌려야 뜻이 있다(생활인구는 KOSIS 표가 있어야 추정이 나옴). 샌드박스 DB에 없는 자료는 '추정 안 함'으로 기준이 잡힌다.
"""
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "server"))
BASE = HERE / "baseline.json"

# (도구, 인자) — 축제·연휴·야구·벚꽃·단풍·대형 행사·공연·개편 지역·광주 이름 변경·자료 없음
CASES = [
    ("analyze_region_week", ("강원특별자치도", "화천군", "2026.01.2주차")),
    ("analyze_region_week", ("강원특별자치도", "화천군", "2025.10.1주차")),
    ("analyze_region_week", ("전북특별자치도", "정읍시", "2025.11.2주차")),
    ("analyze_region_week", ("경상남도", "창원시 진해구", "2025.04.1주차")),
    ("analyze_region_week", ("전라남도", "구례군", "2025.04.1주차")),
    ("analyze_region_week", ("경상남도", "창원시 마산회원구", "2025.08.3주차")),
    ("analyze_region_week", ("서울특별시", "송파구", "2025.04.1주차")),
    ("analyze_region_week", ("대구광역시", "수성구", "2025.10.3주차")),
    ("analyze_region_week", ("경상북도", "경주시", "2025.10.5주차")),
    ("analyze_region_week", ("전라남도", "목포시", "2023.10.3주차")),
    ("analyze_region_week", ("서울특별시", "구로구", "2025.12.2주차")),
    ("analyze_region_week", ("충청남도", "공주시", "2025.10.1주차")),
    ("analyze_region_week", ("경기도", "화성시", "2026.02.1주차")),
    ("analyze_region_week", ("광주광역시", "북구", "2025.09.3주차")),
    ("analyze_region_week", ("인천광역시", "중구", "2026.07.2주차")),
    ("analyze_region_week", ("서울특별시", "강남구", "2019.01.1주차")),
    ("analyze_region_week", ("없는도", "없는군", "2025.01.1주차")),
    ("find_unusual_regions", ("2025.10.1주차", 5)),
    ("estimate_life_pop", ("강원특별자치도", "화천군", "2026-10")),
    ("estimate_life_pop", ("경기도", "가평군", "2026-09")),
    ("forecast_festival", ("강원특별자치도", "화천군", "산천어")),
    ("get_region_calendar", ("경상북도", "경주시", "2025-10")),
    ("get_bloom_foliage", (2025, "단풍")),
    ("compare_same_week", ("강원특별자치도", "화천군", "2026.01.2주차")),
    ("compare_same_week", ("경상북도", "경주시", "2025.10.1주차")),
    ("compare_same_week", ("서울특별시", "강남구", "2019.03.1주차")),
]
KEYS = ["판정", "평소 출렁임의 몇 배", "평소 대비 변화(%)", "최종 변화(%)", "판정에 쓴 방식", "프로야구 홈경기 몫 추정(%)",
        "error"]

MULT_NEW = "배수(이용자가 물을 때만 말함)"
# 0.7.0(2026-11-28): 칸 이름·판정 방식 이름이 바뀜 → 옛 기준과 견주려고 옛 이름으로 되돌려 봄(바뀐 게 이름뿐인지 가리기, 설계서 E)
BACK = {"그 주 하루 평균 유입 건수": "관외 유입(일평균 건수)", "평소 하루 평균 유입 건수": "평소 값(직전 8주 중앙값, 연휴 주 제외)",
        "평소 유입 대비 증감률(%)": "평소 대비 변화(%)", "같은 주 비교 지역 증감률(%)": "비슷한 지역 10곳의 같은 주 변화(%)",
        "(비교 지역 몫 뺀) 보정한 증감률(%)": "비교군 대비 변화(%)", "이 지역 공휴일 반응(%)": "공휴일 보정(%)",
        "보정한 증감률(%)": "최종 변화(%)", "평소 등락(%)": "평소 출렁임(%)", "비교 지역(이용자가 물을 때만 말함)": "비교한 지역",
        "평소 등락 2배 넘게 달라진 지역 수": "평소 출렁임 2배 넘게 달라진 지역 수",
        "예측(100km 밖 비교 지역 몫 뺀 상승률, %)": "예측(비교군 대비 상승률, %)",
        "지난 회차 평균의 평소 등락 대비 몇 배": "지난 회차 평균의 평소 출렁임 대비 몇 배"}
NEW_ONLY = {"평소 등락 읽는 법", "비교 지역 고른 기준"}
VAL_BACK = [("평소 등락", "평소 출렁임"), ("비교 지역 몫 보정", "비교군 대비"), ("평소 유입 대비(비교 지역 없음)", "평소 대비(비교군 없음)"),
            ("평소 유입 대비 − 이 지역 공휴일 반응(평일 공휴일 주라 비교 지역 몫 보정은 참고만)",
             "평소 대비 − 이 지역 공휴일 반응(평일 공휴일 주라 비교군 대비는 참고만)")]


def back070(r):
    """0.7.0 새 이름 → 옛 이름(판정·방식 값 포함). 새로 생긴 칸은 그대로 둬 '새 항목'으로만 잡힘."""
    if not isinstance(r, dict):
        return r
    out = {}
    for k, v in r.items():
        k2 = BACK.get(k, k)
        if k in ("판정", "판정에 쓴 방식") and isinstance(v, str):
            for a, b in sorted(VAL_BACK, key=lambda x: -len(x[0])):
                v = v.replace(a, b)
        out[k2] = v
    return out


def core(tool, r):
    if not isinstance(r, dict):
        return {"type": str(type(r))}
    r = back070(r)
    if MULT_NEW in r and "평소 출렁임의 몇 배" not in r:   # 0.6.4 칸 이름 바뀜 → 옛 기준과 견주려고 옛 이름으로
        r = {("평소 출렁임의 몇 배" if k == MULT_NEW else k): v for k, v in r.items()}
    out = {"keys": sorted(r.keys())}
    for k in KEYS:
        if k in r:
            out[k] = r[k]
    if tool == "find_unusual_regions":
        for k in ("평소보다 늘어난 곳", "평소보다 줄어든 곳"):
            out[k] = [[x.get("지역"), x.get("배수(이용자가 물을 때만 말함)", x.get("배수"))] for x in (r.get(k) or [])][:5]
    if tool == "get_region_calendar":
        out["주별"] = [[w["주차"], len(w["일정"]), back070({"판정": (w["그 주 유입"] or {}).get("판정")})["판정"]
                       if isinstance(w["그 주 유입"], dict) else w["그 주 유입"]]
                     for w in r.get("주별", [])]
    if tool == "compare_same_week":
        out["요약"] = r.get("요약")
    if tool == "get_bloom_foliage":
        out["관측"] = [[g["구분"], len(g["지점"])] for g in r.get("관측", [])]
    if tool in ("estimate_life_pop", "forecast_festival"):
        out["values"] = {k: v for k, v in r.items()
                         if k in ("공표", "추정", "등급") or "범위 안" in k or (k.startswith("추정") and isinstance(v, (int, float)))}
    return out


def run():
    import server as S
    res, slow = {}, []
    for tool, args in CASES:
        t = time.time()
        try:
            r = getattr(S, tool)(*args)
        except Exception as e:
            r = {"예외": f"{type(e).__name__}: {e}"}
        sec = time.time() - t
        res[f"{tool}{args}"] = core(tool, r)
        if sec > 3:
            slow.append((tool, args, round(sec, 1)))
    return res, slow


def tools_match():
    """server.TOOLS(결과에 붙는 도구 목록)와 실제 등록된 도구가 같은지."""
    import asyncio
    import server as S
    real = sorted(t.name for t in asyncio.run(S.mcp.list_tools()))
    if real != sorted(S.TOOLS):
        print(f"[도구 목록 불일치] server.TOOLS를 고치고 VERSION·TOOLS_CHANGED도 올릴 것: 실제 {real} / TOOLS {sorted(S.TOOLS)}")
        return False
    return True


def main():
    if not os.environ.get("NOWCAST_DB"):
        print("NOWCAST_DB를 정해 줄 것")
        return 2
    ok = tools_match()
    res, slow = run()
    if "--save" in sys.argv:
        BASE.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"기준 저장: {len(res)}개")
        return 0
    base = json.loads(BASE.read_text(encoding="utf-8"))
    diff = 0
    for k, v in res.items():
        b = base.get(k)
        if b is None:
            print("새 시험:", k)
            continue
        for f in sorted(set(v) | set(b)):
            if f == "keys":
                gone = set(b["keys"]) - set(v.get("keys", []))
                if gone:
                    diff += 1
                    print(f"[사라진 항목] {k}: {sorted(gone)}")
                continue
            if v.get(f) != b.get(f):
                diff += 1
                print(f"[바뀜] {k} {f}: {b.get(f)} → {v.get(f)}")
    for x in slow:
        print("[느림 3초 넘음]", x)
    print(f"시험 {len(res)}개, 바뀐 곳 {diff}개" + ("" if diff else " → 기존 결과 그대로"))
    return 1 if (diff or not ok) else 0


if __name__ == "__main__":
    sys.exit(main())
