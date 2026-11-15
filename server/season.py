"""벚꽃 만발·단풍 절정(기상청 계절관측)과 관외 유입.

- 자료: kma_season.csv(기상청 기상자료개방포털 > 계절관측, 2019~). 봄·가을에 한 번씩 손으로 갱신.
  유명산 단풍은 자료에 관측 지점 이름만 있어 산 이름을 붙였다(2026년 첫 단풍 보도 날짜와 맞춰 봄).
- 2026-11 검증(claude/벚꽃_단풍_유입효과_시험.md): 관광형 시군구에서 단풍 절정 주(21곳 중 17곳 증가, 중앙값 +9.8%),
  벚꽃 만발 주(14곳 중 12곳 증가, 중앙값 +12.6%)에 관외 유입이 늘었다. 효과가 보인 곳만 SPOTS에 넣었다.
- 판정은 바꾸지 않는다. '그 주에 있었던 일'과 '이 지역의 지난 기록'으로만 보여 준다. 원인 단정 금지.
"""
import csv
import datetime as dt
from pathlib import Path
from statistics import mean

import safe

SOURCE = "벚꽃 만발·단풍 절정(기상청 계절관측)"

# (시도, 시군구) → (종류, 명소 이름, 계절관측 구분, 관측 지점, 날짜로 쓸 단계)
SPOTS = {
    # 단풍: 유명산 단풍 절정
    ("경상북도", "청송군"): ("단풍", "주왕산", "유명산 단풍", "안동", "단풍절정"),
    ("전라남도", "구례군"): ("단풍", "지리산", "유명산 단풍", "남원", "단풍절정"),
    ("강원특별자치도", "속초시"): ("단풍", "설악산", "유명산 단풍", "속초", "단풍절정"),
    ("전북특별자치도", "정읍시"): ("단풍", "내장산", "유명산 단풍", "정읍", "단풍절정"),
    ("충청북도", "보은군"): ("단풍", "속리산", "유명산 단풍", "보은", "단풍절정"),
    ("강원특별자치도", "평창군"): ("단풍", "오대산", "유명산 단풍", "대관령", "단풍절정"),
    ("충청북도", "제천시"): ("단풍", "월악산", "유명산 단풍", "충주", "단풍절정"),
    ("제주특별자치도", "제주시"): ("단풍", "한라산", "유명산 단풍", "제주", "단풍절정"),
    ("전북특별자치도", "무주군"): ("단풍", "덕유산", "유명산 단풍", "전주", "단풍절정"),
    ("강원특별자치도", "원주시"): ("단풍", "치악산", "유명산 단풍", "원주", "단풍절정"),
    ("경상북도", "영주시"): ("단풍", "소백산", "유명산 단풍", "영주", "단풍절정"),
    ("경상남도", "합천군"): ("단풍", "가야산", "유명산 단풍", "합천", "단풍절정"),
    ("전라남도", "순천시"): ("단풍", "조계산", "유명산 단풍", "순천", "단풍절정"),
    ("전라남도", "해남군"): ("단풍", "두륜산", "유명산 단풍", "해남", "단풍절정"),
    ("전라남도", "영암군"): ("단풍", "월출산", "유명산 단풍", "목포", "단풍절정"),
    ("대구광역시", "동구"): ("단풍", "팔공산", "유명산 단풍", "대구", "단풍절정"),
    ("울산광역시", "울주군"): ("단풍", "가지산", "유명산 단풍", "울산", "단풍절정"),
    # 벚꽃: 명소 가까운 관측 지점의 만발
    ("경상남도", "하동군"): ("벚꽃", "하동 십리벚꽃길", "벚나무 표준목", "창원", "만발"),
    ("경상남도", "창원시 진해구"): ("벚꽃", "진해", "벚나무 표준목", "창원", "만발"),
    ("경상북도", "경주시"): ("벚꽃", "경주 보문", "벚나무 표준목", "포항", "만발"),
    ("충청남도", "공주시"): ("벚꽃", "공주", "벚나무 표준목", "대전", "만발"),
    ("서울특별시", "영등포구"): ("벚꽃", "여의도 윤중로", "군락단지(봄꽃)", "서울", "만발"),
    ("서울특별시", "송파구"): ("벚꽃", "석촌호수", "벚나무 표준목", "서울", "만발"),
    ("충청북도", "청주시 상당구"): ("벚꽃", "청주 무심천", "벚나무 표준목", "청주", "만발"),
    ("대전광역시", "대덕구"): ("벚꽃", "대청호", "벚나무 표준목", "대전", "만발"),
    ("전북특별자치도", "군산시"): ("벚꽃", "군산 은파", "벚나무 표준목", "전주", "만발"),
}
# 벚꽃과 단풍이 둘 다 있는 곳
EXTRA = {
    ("전라남도", "구례군"): [("벚꽃", "구례 섬진강", "벚나무 표준목", "광주", "만발")],
    ("전라남도", "영암군"): [("벚꽃", "영암 벚꽃길", "벚나무 표준목", "목포", "만발")],
    ("제주특별자치도", "제주시"): [("벚꽃", "제주 전농로", "벚나무 표준목", "제주", "만발")],
}
_D = None


def _dates():
    global _D
    if _D is None:
        _D = {}
        try:
            with open(Path(__file__).with_name("kma_season.csv"), encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    try:
                        _D[(r["구분"], r["지점"], r["단계"], int(r["연도"]))] = dt.date.fromisoformat(r["날짜"])
                    except Exception as e:
                        safe.bad_row("kma_season.csv", r, e)
        except FileNotFoundError:
            pass
    return _D


def spots(sido, sigungu):
    sd = "전라남도" if sido == "전남광주통합특별시" else sido      # 시도 이름이 바뀐 곳
    k = (sd, sigungu)
    out = [SPOTS[k]] if k in SPOTS else []
    return out + EXTRA.get(k, [])


def in_week(sido, sigungu, mon):
    """그 주(월요일 mon)에 든 벚꽃 만발·단풍 절정. [{종류, 명소, 날짜, 기준}]"""
    out, D = [], _dates()
    for kind, name, grp, stn, step in spots(sido, sigungu):
        d = D.get((grp, stn, step, mon.year))
        if d and mon <= d <= mon + dt.timedelta(days=6):
            word = "만발" if kind == "벚꽃" else "절정"
            out.append({"종류": f"{kind} {word}", "명소": name, "날짜": d.isoformat(),
                        "기준": f"기상청 {stn} 관측({grp})"})
    return out


def history(P, sido, sigungu, skip_year=None):
    """이 지역의 지난 기록: 만발·절정이 든 주의 평소 대비 변화 평균 − 같은 계절 3주 이상 떨어진 주 평균.
    P는 analysis.prepare 결과. 판정과 같은 계산을 쓰고, 묻는 해는 뺀다."""
    from analysis import judge_week, week_dates, START
    D, res = _dates(), []
    for kind, name, grp, stn, step in spots(sido, sigungu):
        months = (3, 4, 5) if kind == "벚꽃" else (9, 10, 11)
        pk, ot, years = [], [], set()
        for wo in P["me"]:
            if wo < START:
                continue
            mon = dt.date.fromisoformat(week_dates(wo)[:10])
            d = D.get((grp, stn, step, mon.year))
            if mon.month not in months or not d or mon.year == skip_year:
                continue
            j = judge_week(P, wo)
            if not j or j["dev"] is None or j["h"][1] > 0:
                continue
            gap = abs((mon + dt.timedelta(days=3) - d).days)
            if gap <= 3:
                pk.append(j["dev"]); years.add(mon.year)
            elif gap >= 21:
                ot.append(j["dev"])
        if pk and ot:
            word = "만발" if kind == "벚꽃" else "절정"
            res.append({"명소": name, "종류": f"{kind} {word}",
                        "지난 기록": f"{min(years)}~{max(years)}년 {len(pk)}번의 {kind} {word} 주에 같은 계절 다른 주보다 "
                                     f"평균 {round((mean(pk) - mean(ot)) * 100):+d}%",
                        "평균 차이(%)": round((mean(pk) - mean(ot)) * 100, 1)})
    return res
