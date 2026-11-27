"""프로야구 홈경기(KBO)와 관외 유입.

- 자료: kbo_games.csv(KBO 일자별 관중 현황·경기 기록, 2021– 정규시즌·포스트시즌 경기별 관중).
  KBO 사이트는 자동 수집을 막아 두어 시즌이 끝나면 손으로 갱신한다.
- 2026-11 검증(claude/프로야구_홈경기_유입효과_시험.md): 구장이 있는 9개 시군구에서 홈경기 관중이 많은 주일수록
  관외 유입이 늘었다(2023–2026 해마다, 9곳 모두). 2023–2024년으로 구한 '관중 1만 명당 변화'를 2025–2026년에
  적용하니 시즌 중 평소 출렁임이 21%, '많음·적음' 이상 주가 86→71로 줄었다.
- 쓰는 법: 판정은 그대로 둔다(야구로 들어온 이동도 실제 유입). 야구 몫 = b × (그 주 관중(만 명) − 시즌 중 평균 주 관중)을
  '추정 몫'으로 보여 주고, 몫을 뺀 판정은 '다른 일이 있었나' 볼 때만 참고로 붙인다. 시즌 기간(그해 첫 경기 주–
  마지막 경기 주) 안에서만. 비시즌까지 하면 겨울 주가 거꾸로 '많음'이 되는 부작용이 있었다.
- 원인으로 단정하지 않는다. '같은 주에 홈경기가 있었다'만 말한다.
"""
import csv
import re

import safe
import datetime as dt
from collections import defaultdict
from pathlib import Path

SOURCE = "프로야구 홈경기(KBO 경기 기록)"

# 구장 → (시도, 시군구). 한밭은 2024년까지 대전 홈구장(같은 중구)
STADIUM = {
    "잠실": ("서울특별시", "송파구"), "고척": ("서울특별시", "구로구"), "문학": ("인천광역시", "미추홀구"),
    "사직": ("부산광역시", "동래구"), "수원": ("경기도", "수원시 장안구"), "대구": ("대구광역시", "수성구"),
    "광주": ("광주", "북구"), "대전": ("대전광역시", "중구"), "한밭": ("대전광역시", "중구"),
    "창원": ("경상남도", "창원시 마산회원구"), "포항": ("경상북도", "포항시 남구"),
    "울산": ("울산광역시", "남구"), "청주": ("충청북도", "청주시 서원구"),
}

# analysis.canon 열쇠 → (b: 관중 1만 명당 ln 변화, 시즌 중 평균 주 관중(만 명)). 2023~2024년 시즌 중·공휴일 없는 주로 구함
PARAMS = {
    ("서울특별시", "송파구"): (0.00149, 7.04),
    ("서울특별시", "구로구"): (0.00405, 2.04),
    ("인천광역시", "미추홀구"): (0.00375, 3.32),
    ("부산광역시", "동래구"): (0.00481, 2.85),
    ("경기도", "수원시 장안구"): (0.01040, 2.20),
    ("대구광역시", "수성구"): (0.00352, 3.26),
    ("광주", "북구"): (0.00382, 3.25),
    ("대전광역시", "중구"): (0.01203, 1.89),
    ("경상남도", "창원시 마산회원구"): (0.01110, 1.88),
}

_W = None      # (canon, 월요일) → [경기 dict]
_SEASON = {}   # 해 → (첫 경기 주 월요일, 마지막 경기 주 월요일)


def _key(sido, sigungu):
    from analysis import canon
    return canon(sido, sigungu)


def _load():
    global _W
    if _W is not None:
        return _W
    _W = defaultdict(list)
    try:
        with open(Path(__file__).with_name("kbo_games.csv"), encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    if r["구장"] not in STADIUM:
                        raise ValueError(f"모르는 구장 {r['구장']}")
                    d = dt.date.fromisoformat(r["날짜"])
                    int(r["관중수"] or 0)
                except Exception as e:
                    safe.bad_row("kbo_games.csv", r, e)
                    continue
                mon = d - dt.timedelta(days=d.weekday())
                _W[(_key(*STADIUM[r["구장"]]), mon)].append(r)
                a, b = _SEASON.get(d.year, (mon, mon))
                _SEASON[d.year] = (min(a, mon), max(b, mon))
    except FileNotFoundError:
        pass
    return _W


def _ps(kind):
    """'포스트시즌 KS1' → 'KS 1차전', '포스트시즌 준PO3 (…)' → '준PO 3차전'"""
    m = re.match(r"포스트시즌 (\D+)(\d+)", kind)
    return f"{m[1]} {m[2]}차전" if m else kind.replace("포스트시즌 ", "")


def week(sido, sigungu, mon):
    """그 주(월요일 mon) 이 지역 구장의 경기. (줄 목록 ['롯데 vs 한화 3경기', ...], 관중 합계)"""
    games = _load().get((_key(sido, sigungu), mon), [])
    cnt, ps = {}, []
    for g in games:
        if g["구분"].startswith("포스트시즌"):
            ps.append(f"{g['홈']} vs {g['방문']} {_ps(g['구분'])}")
        else:
            k = (g["홈"], g["방문"])
            cnt[k] = cnt.get(k, 0) + 1
    out = [f"{h} vs {a} {n}경기" for (h, a), n in cnt.items()] + ps
    return out, sum(int(g["관중수"] or 0) for g in games)


def adjust(sido, sigungu, mon):
    """그 주 야구 몫 추정(ln 단위). 대상 지역·시즌 기간이 아니면 None."""
    p = PARAMS.get(_key(sido, sigungu))
    if not p:
        return None
    _load()
    se = _SEASON.get(mon.year)
    if not se or not (se[0] <= mon <= se[1]):
        return None
    _, crowd = week(sido, sigungu, mon)
    return p[0] * (crowd / 1e4 - p[1])
