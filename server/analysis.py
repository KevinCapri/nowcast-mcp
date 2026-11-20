"""지역 효과 분석: 한 지역·한 주의 관외 유입이 평소와 얼마나 다른지.

방법(2026-11 검증):
1) 평소 값 = 직전 8주(연휴 주 제외) 관외 유입의 중앙값. 변화 = ln(이번 주 / 평소 값)
2) 비교군 대비 = 변화 − 같은 주 '비슷한 지역 10곳'(인구·30km·100km 주변 인구가 비슷한 곳) 변화의 중앙값
   → 전국이 함께 움직인 몫(계절·날씨·연휴 앞뒤)을 뺀다. 평소 대비만 보면 축제 효과가 부풀려진다.
3) 평일 공휴일이 하루 든 주는 비교군도 같은 공휴일을 겪으므로, 평소 대비에서 그 지역의
   '공휴일 하루당 반응'(지난 공휴일 주들의 중앙값)을 빼서 판정한다.
4) 평소 출렁임 = 2023년 3월 이후 평범한 주(연휴·공휴일 없음)의 같은 방식 값의 MAD×1.4826.
   판정은 '평소 출렁임의 몇 배'로: 1·2·3배 넘으면 조금·(많음·적음)·아주, 아니면 평소 수준.
연휴 주(설·추석, 평일 공휴일 2일 이상)는 평소 값 계산에서 빼고, 그 주 자체는 비교 신뢰도가 낮다고 표시한다.
"""
import csv
import math
import threading
from pathlib import Path
from statistics import median

PEERS_CSV = Path(__file__).with_name("region_peers.csv")
START = 2023031
# 시가 구로 나뉘거나 구가 개편된 주차. 이 주부터 관외 유입이 계단처럼 뛰므로 8주는 판정 보류
REFORM = {
    ("경기도", "부천시"): 2024021,
    ("경기도", "화성시"): 2026021,
    ("인천광역시", "중구"): 2026071, ("인천광역시", "동구"): 2026071, ("인천광역시", "서구"): 2026071,
}
REFORM_CITY = {"부천시": 2024021, "화성시": 2026021}   # 'OO시 OO구' 이름에 붙임
REFORM_NEW_GU = {("인천광역시", g): 2026071 for g in ("제물포구", "영종구", "서해구", "검단구")}


def reform_week(sido, sigungu):
    if (sido, sigungu) in REFORM:
        return REFORM[(sido, sigungu)]
    if (sido, sigungu) in REFORM_NEW_GU:
        return REFORM_NEW_GU[(sido, sigungu)]
    city = sigungu.split(" ")[0]
    return REFORM_CITY.get(city) if " " in sigungu else None      # 출렁임은 2023년 3월 이후로 계산(코로나 시기 제외)
BASE_WEEKS = 8


def new_start(sido, sigungu):
    """경계가 바뀌어 판정을 새로 시작한 지역(인천 새 구, 부천·화성의 구)이면 개편 주차. 사라진 옛 이름·이어서 판정하는 곳은 None.
    (군위군·전남광주통합특별시·특별자치도는 경계 그대로라 이어서 판정 — 전국순위_기간확장_작업.md 3번)"""
    if (sido, sigungu) in REFORM:
        return None
    return reform_week(sido, sigungu)


def start_note(first_wo, rw):
    """새로 시작한 지역 줄에 붙이는 칸 문구."""
    return f"이 지역은 {label(first_wo)}부터 판정({rw // 1000}년 {(rw // 10) % 100}월 행정구역 개편)"


def first_judged(P, rw):
    """개편 주차 rw 뒤 처음 판정이 나온 주(판정 보류 뺌)."""
    for wo in sorted(P["me"]):
        if wo < rw:
            continue
        J = judge_week(P, wo)
        if J and J["ratio"] is not None and not J["verdict"].startswith("판정 보류"):
            return wo
    return None


GWANGJU_GU = {"동구", "서구", "남구", "북구", "광산구"}


def canon(sido, sigungu):
    """시도 이름이 바뀐 지역을 하나의 열쇠로(비교군 표 찾기용)."""
    if sido in ("전남광주통합특별시", "광주광역시", "광주", "전라남도"):
        return ("광주" if (sigungu in GWANGJU_GU and sido != "전라남도") else "전라남도", sigungu)
    if sigungu == "군위군":
        return ("대구광역시", sigungu)
    return (sido, sigungu)


def variants(sido, sigungu):
    """같은 곳이 시기에 따라 다른 시도 이름으로 적힌 경우를 모두."""
    c = canon(sido, sigungu)
    if c[0] == "광주":
        return [("광주광역시", sigungu), ("전남광주통합특별시", sigungu)]
    if c[0] == "전라남도":
        return [("전라남도", sigungu), ("전남광주통합특별시", sigungu)]
    if sigungu == "군위군":
        return [("경상북도", sigungu), ("대구광역시", sigungu)]
    return [(sido, sigungu)]


def _load_peers():
    out = {}
    with open(PEERS_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[canon(r["sido"], r["sigungu"])] = [tuple(p.split(" ", 1)) for p in r["peers"].split(";")]
    return out


PEERS = _load_peers()


def _mad(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) < 10:
        return None
    m = median(xs)
    return 1.4826 * median(abs(x - m) for x in xs)


JUDGE_RULE = ("평소 출렁임의 몇 배인지(절댓값)로 판정: 1배 이하 '평소 수준', 1배 넘음 '조금 많음/조금 적음', "
              "2배 넘음 '많음/적음', 3배 넘음 '아주 많음/아주 적음'. 정확히 2.0배는 2배를 넘지 않아 '조금'.")


def judge(ratio):
    """판정 문구. 이 문구를 그대로 전하게 한다."""
    if ratio is None:
        return "판정 못함(비교 자료 부족)"
    a = abs(ratio)
    # 사이트와 같은 일곱 단계 말(번역투 '뚜렷한 증가' 대신 평소 쓰는 말)
    if a > 3:
        return "아주 많음" if ratio > 0 else "아주 적음"
    if a > 2:
        return "많음" if ratio > 0 else "적음"
    if a > 1:
        return "조금 많음(평소 출렁임 범위 안팎)" if ratio > 0 else "조금 적음(평소 출렁임 범위 안팎)"
    return "평소 수준"


def _pct(logv):
    return None if logv is None else round((math.exp(logv) - 1) * 100, 1)


# ── 판정을 말하는 방식(2026-11-19, 전국순위_기간확장_작업.md 6번) ───────────────────────────────
# '평소 출렁임의 3.5배'는 '3.5배로 늘었다'로 오해하기 쉬워, % 먼저 말하고 배수는 판정 단계별 정해진 말로 푼다.
# 순위 기준은 배수 그대로. 이용자가 배수를 직접 물으면 소수 한 자리 그대로 말해도 된다.
STAGE_WORDS = {
    "조금": "평소 오르내림 폭을 조금 넘는 정도입니다.",
    "많음": "평소 오르내림 폭의 두 배가 넘어, 이 지역에서는 흔치 않은 일입니다.",
    "아주": "평소 오르내림 폭의 세 배가 넘어, 이 지역에서는 아주 드문 일입니다.",
    "평소": "평소 오르내림 폭 안이라 평소 수준입니다.",
}
STAGE_RULE = ("판정 단계별 말: 조금 많음·조금 적음 → '" + STAGE_WORDS["조금"] + "' / 많음·적음 → '" + STAGE_WORDS["많음"]
              + "' / 아주 많음·아주 적음 → '" + STAGE_WORDS["아주"] + "' / 평소 수준 → '" + STAGE_WORDS["평소"] + "'")
SAME_PCT = 3.0   # 평소 대비와 판정에 쓴 값의 차이가 이 %p 안이면 '거의 같음'(숫자 하나로만 말함)


def _stage(verdict):
    if verdict.startswith("아주"):
        return STAGE_WORDS["아주"]
    if verdict.startswith("조금"):
        return STAGE_WORDS["조금"]
    if verdict in ("많음", "적음"):
        return STAGE_WORDS["많음"]
    return STAGE_WORDS["평소"] if verdict == "평소 수준" else None


def _topic(name):
    """이름 뒤 '은/는'."""
    c = name[-1]
    return name + ("은" if "가" <= c <= "힣" and (ord(c) - 0xAC00) % 28 else "는")


def speak(J, place, when):
    """판정을 % 먼저, 배수는 단계별 말로 푼 문장들. 판정 못 한 주는 None."""
    if not J or J.get("ratio") is None or J.get("dev") is None:
        return None
    stage = _stage(J["verdict"])
    if not stage:
        return None
    p, f, s = _pct(J["dev"]), _pct(J["final"]), _pct(J["s"])
    first = (f"{_topic(place)} {when} 외부 유입이 평소보다 {abs(round(p))}% {'늘었' if p > 0 else '줄었'}습니다."
             if abs(p) >= 0.5 else f"{_topic(place)} {when} 외부 유입이 평소와 거의 같았습니다({p:+.1f}%).")
    lines = [first]
    if f is not None and abs(f - p) >= SAME_PCT:
        if J.get("hol_adj") is not None:
            h = _pct(J["hol_adj"])
            who = f"공휴일 낀 주에 보통 {'늘어나는' if h > 0 else '줄어드는'} 몫을 빼면"
        else:
            c = p - f   # 비교 지역 몫(대략)
            who = (f"비슷한 지역들{'도' if c * p > 0 else '이'} 함께 {'늘어난' if c > 0 else '줄어든'} 몫을 빼면")
        lines.append(f"{who} {abs(round(f))}% {'많은' if f > 0 else '적은'} 셈인데,")
    lines.append(f"이 지역은 평소 한 주에 {max(1, round(s))}% 안팎 오르내리니, {stage}")
    return " ".join(lines)


# 순위 줄용 짧은 판정 풀이(TEST_수정안_20261120.md 3+5번). 지역·날짜·평소 출렁임은 같은 줄 칸에 있으니 뺀다.
SHORT_STAGE = {
    "조금": "평소 오르내림 폭을 조금 넘는 정도입니다.",
    "많음": "평소 오르내림 폭의 두 배가 넘는 흔치 않은 일입니다.",
    "아주": "평소 오르내림 폭의 세 배가 넘는 아주 드문 일입니다.",
    "평소": "평소 오르내림 폭 안이라 평소 수준입니다.",
}


def speak_short(w):
    """w: dev·final·verdict·hol_adj(없어도 됨). 판정 못 한 줄은 None."""
    v = w.get("verdict") or ""
    key = ("아주" if v.startswith("아주") else "조금" if v.startswith("조금")
           else "많음" if v in ("많음", "적음") else "평소" if v == "평소 수준" else None)
    if not key or w.get("dev") is None:
        return None
    stage = SHORT_STAGE[key]
    p, f = _pct(w["dev"]), _pct(w.get("final"))
    if f is None or abs(f - p) < SAME_PCT:
        if abs(p) < 0.5:
            return f"평소와 거의 같아, {stage}"
        return f"평소보다 {abs(round(p))}% {'늘어' if p > 0 else '줄어'}, {stage}"
    head = (f"평소와 거의 같았지만" if abs(p) < 0.5
            else f"평소보다 {abs(round(p))}% {'늘었' if p > 0 else '줄었'}지만")
    if w.get("hol_adj") is not None:
        h = _pct(w["hol_adj"])
        who = f"공휴일 낀 주에 보통 {'늘어나는' if h > 0 else '줄어드는'} 몫을 빼면"
    else:
        c = p - f
        who = f"비슷한 지역들{'도' if c * p > 0 else '이'} 함께 {'늘어난' if c > 0 else '줄어든'} 몫을 빼면"
    return f"{head}, {who} {abs(round(f))}% {'많은' if f > 0 else '적은'} 셈이라, {stage}"


# 원자료 처리 문제가 의심되는 구간(2026-11-13 점검). 이 지역만 크게 튀고
# 같은 시도 합계는 그대로이거나 이웃 구가 반대로 움직였으며, 데이터랩 외지인에는 변화가 없었던 경우.
SUSPECT = [
    (("대전광역시", "중구"), 2025111, 2025115, "2025년 11월: 중구만 크게 늘고 대전 전체는 그대로, 이웃 구가 그만큼 줄었고 데이터랩 외지인은 변화 없음"),
    (("경상남도", "창원시 의창구"), 2024011, 2024025, "2024년 1~2월: 의창구만 크게 튀고 이웃 구가 반대로 움직임"),
    (("경기도", "가평군"), 2025071, 2099125, "2025년 하반기부터: 데이터랩 등 다른 자료와 수준이 어긋남"),
    (("광주", "남구"), 2025091, 2099125, "2025년 9월부터: 남구와 북구가 서로 반대로 크게 움직임(행정구역 배정 착오 가능성)"),
    (("광주", "북구"), 2025091, 2099125, "2025년 9월부터: 남구와 북구가 서로 반대로 크게 움직임(행정구역 배정 착오 가능성)"),
]


SUCCESSOR = {  # 개편으로 사라진 이름 → (새 이름이 시작된 주, 새 이름들, 표기)
    ("경기도", "부천시"): (2024021, ["원미구", "소사구", "오정구"], "2024.02.1주차"),
    ("경기도", "화성시"): (2026021, ["동탄구", "만세구", "병점구", "효행구"], "2026.02.1주차"),
    ("인천광역시", "서구"): (2026071, ["서해구", "검단구"], "2026.07.1주차"),
    ("인천광역시", "중구"): (2026071, ["제물포구", "영종구"], "2026.07.1주차"),
    ("인천광역시", "동구"): (2026071, ["제물포구"], "2026.07.1주차"),
}


def successor(sido, sigungu):
    return SUCCESSOR.get((sido, sigungu))


def suspect(sido, sigungu, wo_from, wo_to=None):
    """조회 기간이 의심 구간에 걸치면 안내 문구 목록."""
    wo_to = wo_to or wo_from
    c = canon(sido, sigungu)
    return [f"원자료 확인 필요 구간: {why}. 자료 처리 과정의 오류나 행정구역 배정 착오일 가능성이 있다(확인되지 않음). "
            "답할 때 이 점을 먼저 말하고, 이용자에게는 '이 기간은 행정구역 배정이나 집계 방식에 바뀐 점이 있는지 확인이 필요한 구간'이라는 뜻으로 전할 것. "
            "오류라고 단정하지도 말고, 이 기간 값을 방문 증가·감소의 근거로 쓰지도 말 것. 행사 후보를 억지로 대지 말 것. "
            "이용자가 원하면 report_issue로 운영자에게 알릴 수 있다."
            for k, a, b, why in SUSPECT if k == c and a <= wo_to and wo_from <= b]


SUSPECT_FLAG = "이 값을 방문 증가·감소의 근거로 쓰지 말 것('주의' 참고)"   # 결과의 줄마다 붙이는 짧은 칸 '원자료 확인 필요 구간'


def is_suspect(sido, sigungu, wo):
    """그 지역·그 주가 원자료 확인 필요 구간인가."""
    c = canon(sido, sigungu)
    return any(k == c and a <= wo <= b for k, a, b, _ in SUSPECT)


def week_dates(wo):
    """주차 번호 → '2026-10-26~2026-11-01'(월~일). 목요일이 속한 달로 세고 2022년만 수요일."""
    import datetime as dt
    y, m, n = wo // 1000, (wo // 10) % 100, wo % 10
    k = 2 if y == 2022 else 3
    d = dt.date(y, m, 7 * (n - 1) + 1)
    while d.weekday() != k:
        d += dt.timedelta(days=1)
    mon = d - dt.timedelta(days=k)
    return f"{mon}~{mon + dt.timedelta(days=6)}"


def date_ko(wo):
    """주차 번호 → '2025년 11월 10일~16일'(이용자에게 말하는 날짜, TEST_수정안_20261120.md 8번).
    달이 바뀌면 '2025년 9월 29일~10월 5일', 해가 바뀌면 '2025년 12월 29일~2026년 1월 4일'."""
    import datetime as dt
    a, b = (dt.date.fromisoformat(x) for x in week_dates(wo).split("~"))
    if a.year != b.year:
        return f"{a.year}년 {a.month}월 {a.day}일~{b.year}년 {b.month}월 {b.day}일"
    if a.month != b.month:
        return f"{a.year}년 {a.month}월 {a.day}일~{b.month}월 {b.day}일"
    return f"{a.year}년 {a.month}월 {a.day}일~{b.day}일"


def holidays(db):
    """week_order → (평일 공휴일 수, 설·추석 날 수, 이름)"""
    try:
        rows = db.execute("SELECT week_order, weekday_holidays, lunar_days, names FROM holiday_week").fetchall()
    except Exception:
        return {}
    return {w: (int(a or 0), int(b or 0), n) for w, a, b, n in rows}


def is_long_holiday(h):
    return bool(h) and (h[1] > 0 or h[0] >= 2)


_ALL = None   # (시도, 시군구) → [(주차 번호, 주차, 관외 유입)]. 서버 저장소가 느려 한 번에 읽어 메모리에 둠


def load_all(db):
    """시군구 관외 유입 전체를 한 번에 읽어 둔다. 자료가 바뀌면 다시 부른다."""
    global _ALL
    out = {}
    for sd, sg, wo, wk, v in db.execute("SELECT sido, item, week_order, week, outside FROM moves "
                                        "WHERE direction='유입' AND category='시군구' ORDER BY week_order"):
        out.setdefault((sd, sg), []).append((wo, wk, v))
    _ALL = out
    return len(out)


def series(db, sido, sigungu):
    """시도 이름이 바뀐 지역은 옛 이름 자료까지 이어 붙인다(주차가 겹치면 먼저 찾은 것)."""
    seen = {}
    for sd, sg in variants(sido, sigungu):
        if _ALL is not None:
            for row in _ALL.get((sd, sg), ()):
                seen.setdefault(row[0], row)
            continue
        for wo, wk, v in db.execute("SELECT week_order, week, outside FROM moves WHERE direction='유입' "
                                    "AND category='시군구' AND item=? AND sido=? ORDER BY week_order", (sg, sd)):
            seen.setdefault(wo, (wo, wk, v))
    return [seen[k] for k in sorted(seen)]


SPIKE_K = 3.0      # 축제 주 가운데 평소 출렁임의 몇 배를 넘게 늘어난 주를 평소 값 계산에서 뺄지
_FEST_WEEKS = None


def fest_weeks(region):
    """지역(canon) → 문체부 축제 계획 기간에 걸친 주차 번호들. festival.py와 서로 부르지 않도록 표를 직접 읽음."""
    global _FEST_WEEKS
    if _FEST_WEEKS is None:
        import datetime as dt
        _FEST_WEEKS = {}
        try:
            with open(Path(__file__).with_name("festivals.csv"), encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    try:
                        d, e = dt.date.fromisoformat(r["s"]), dt.date.fromisoformat(r["e"])
                    except (ValueError, TypeError, KeyError):
                        continue
                    ws = _FEST_WEEKS.setdefault(canon(r["sido"], r["sigungu"]), set())
                    while d <= e:
                        mon = d - dt.timedelta(days=d.weekday())
                        k = mon + dt.timedelta(days=2 if (mon + dt.timedelta(days=2)).year == 2022 else 3)
                        ws.add(k.year * 1000 + k.month * 10 + (k.day - 1) // 7 + 1)
                        d += dt.timedelta(days=1)
        except OSError:
            pass
    return _FEST_WEEKS.get(region, set())


def _pass(rows, hol, sig_at=None):
    """sig_at: 축제 주 → 그 주 전 자료로 낸 평소 출렁임. 이 값의 3배를 넘게 늘어난 축제 주는 평소 값에 넣지 않는다."""
    sig_at = sig_at or {}
    out, normal = {}, []
    for wo, wk, v in rows:
        base = median(normal[-BASE_WEEKS:]) if len(normal) >= BASE_WEEKS else None
        dev = math.log(v / base) if (base and v and v > 0) else None
        out[wo] = (wk, v, base, dev)
        if is_long_holiday(hol.get(wo)) or not v:
            continue
        sig = sig_at.get(wo)
        if sig and dev is not None and dev > SPIKE_K * sig:
            continue                                # 축제로 크게 늘어난 주는 평소 값에 넣지 않음
        normal.append(v)
    return out


def deviations(rows, hol, region=None):
    """week_order → (주차, 관외, 평소 값, ln 변화)
    2026-11-15: 축제 주가 직전 8주에 들어가 평소 값을 부풀려, 축제 직후 주가 '뚜렷이 적음'으로 잘못 나오던 문제를 고침.
    region(canon)을 주면, 그 지역 축제 계획 기간 중 평소 출렁임의 3배를 넘게 늘어난 주는 평소 값 계산에서 뺀다.
    (학기 시작·새 시설처럼 축제가 아닌 변화는 그대로 평소 값에 들어가 새 수준을 따라간다.)
    2026-11-19(전국순위_기간확장_작업.md 8번): 이 기준의 평소 출렁임을 2023년 3월 이후 전체 자료가 아니라 '그 주 전 자료만'으로
    낸다(자료가 쌓여도 지난 판정이 바뀌지 않게, 판정 쪽 평소 출렁임과 같은 방식)."""
    first = _pass(rows, hol)
    skip = fest_weeks(region) if region else set()
    if not skip:
        return first
    plain = [(wo, d) for wo, (_, _, _, d) in sorted(first.items()) if d is not None and wo >= START and not hol.get(wo)]
    sig_at = {}
    for wo in skip:
        xs = [d for w, d in plain if w < wo]
        if len(xs) >= 20:
            sig_at[wo] = _mad(xs)
    return _pass(rows, hol, sig_at) if sig_at else first


def prepare(db, sido, sigungu, memo=None):
    """한 지역의 판정 재료(시계열·비교군·공휴일)를 한 번만 계산해 둔다. 여러 주를 판정할 때 재사용.
    memo(dict)를 넘기면 지역별 시계열·평소 대비를 나눠 써서 전국을 한꺼번에 볼 때 빨라진다."""
    memo = {} if memo is None else memo
    if "hol" not in memo:
        memo["hol"] = holidays(db)
    hol = memo["hol"]

    def dev_of(a, b):
        k = canon(a, b)
        if k not in memo:
            r = series(db, a, b)
            memo[k] = (r, deviations(r, hol, k))
        return memo[k]

    rows, me = dev_of(sido, sigungu)
    if not rows:
        return None
    peers = PEERS.get(canon(sido, sigungu), [])
    pdev = [dev_of(a, b)[1] for a, b in peers]
    pdev = [p for p in pdev if p]

    def common(wo):
        xs = [p[wo][3] for p in pdev if wo in p and p[wo][3] is not None]
        return median(xs) if len(xs) >= 5 else None

    com = {wo: (common(wo) if pdev else None) for wo in me}
    adj = {}
    for wo, (_, _, _, d) in me.items():
        c = com[wo]
        adj[wo] = None if d is None else (d - c if c is not None else (d if not pdev else None))
    raw = {wo: x[3] for wo, x in me.items()}
    return dict(sido=sido, sigungu=sigungu, hol=hol, rows=rows, me=me, peers=peers, pdev=pdev,
                com=com, adj=adj, raw=raw)


def judge_week(P, week_order):
    """prepare() 결과로 한 주를 판정. 자료가 모자라면 None."""
    hol, me, adj, raw, pdev = P["hol"], P["me"], P["adj"], P["raw"], P["pdev"]
    if week_order not in me:
        return None
    wk, v, base, dev = me[week_order]
    if dev is None:
        return None
    # 평일 공휴일 주(연휴 아님): 비교군도 같은 공휴일을 겪으므로 비교군 대비가 아니라
    # '평소 대비 − 그 지역 공휴일 하루당 반응 × 공휴일 수'로 판정한다(검증한 방식, 오판 12% → 4~5%).
    h = hol.get(week_order, (0, 0, None))
    one_day = h[0] > 0 and not is_long_holiday(h)
    resp = [raw[wo] / hh[0] for wo, hh in hol.items()
            if raw.get(wo) is not None and wo != week_order and wo >= START
            and hh[0] > 0 and not is_long_holiday(hh)]
    per_day = median(resp) if len(resp) >= 3 else None

    def plain(src):
        return [src[wo] for wo in src if START <= wo < week_order and src[wo] is not None and not hol.get(wo)]

    hol_adj = None
    if one_day and per_day is not None:
        hol_adj = per_day * h[0]
        final, s = dev - hol_adj, _mad(plain(raw))
        method = "평소 대비 − 이 지역 공휴일 반응(평일 공휴일 주라 비교군 대비는 참고만)"
    elif adj[week_order] is not None and pdev:
        final, s = adj[week_order], _mad(plain(adj))
        method = "비교군 대비"
    else:
        final, s = dev, _mad(plain(raw))
        method = "평소 대비(비교군 없음)"
    ratio = round(final / s, 1) if (final is not None and s) else None
    verdict = judge(ratio)
    # 프로야구 홈경기 몫(구장이 있는 9개 시군구, 시즌 중만). 판정은 그대로 두고(야구로 들어온 것도 실제 유입)
    # 몫과 '야구 몫 뺀 판정'을 참고로만 붙인다. baseball.py 참고
    bb = ratio_nobb = verdict_nobb = None
    if final is not None and s:
        import datetime as _dt
        import baseball
        import safe
        bb = safe.call("야구 몫", baseball.adjust, P["sido"], P["sigungu"],
                       _dt.date.fromisoformat(week_dates(week_order)[:10]))
        if bb is not None:
            ratio_nobb = round((final - bb) / s, 1)
            verdict_nobb = judge(ratio_nobb)
    # 웹 기본 화면용: 아무것도 빼지 않은 평소 대비 그대로(실제로 들어온 양)
    s_raw = _mad(plain(raw))
    ratio_plain = round(dev / s_raw, 1) if s_raw else None
    verdict_plain = judge(ratio_plain)
    # 2026-11-15: 비교 지역이 이 지역과 반대 방향으로 크게 움직인 주(예: 통근형 지역의 휴가철에 관광지가 섞인 비교군)는
    # 판정이 실제보다 크게 나올 수 있어 표시만 한다(판정은 바꾸지 않음).
    c = P["com"].get(week_order)
    opposite = bool(method == "비교군 대비" and c is not None and s and ratio is not None and abs(ratio) > 2
                    and dev * c < 0 and abs(c) > 1.5 * s)
    rw = reform_week(P["sido"], P["sigungu"])
    if rw and rw <= week_order < rw + 100:      # 대략 개편 뒤 두 달 안
        n_after = sum(1 for wo in me if rw <= wo < week_order)
        if n_after < BASE_WEEKS:
            verdict = verdict_plain = "판정 보류(행정구역 개편 직후)"
    return dict(ratio_plain=ratio_plain, verdict_plain=verdict_plain, wk=wk, v=v, base=base, dev=dev, h=h, one_day=one_day, hol_adj=hol_adj, final=final, s=s,
                method=method, ratio=ratio, verdict=verdict, opposite=opposite, baseball=bb,
                ratio_nobb=ratio_nobb, verdict_nobb=verdict_nobb)


def _count_word(x):
    """하루 건수를 읽기 쉬운 말로: 310,000 → '31만', 26,000 → '2.6만', 4,830 → '4,800'."""
    if x >= 1e5:
        return f"{round(x / 1e4)}만"
    if x >= 1e4:
        return f"{x / 1e4:.1f}".rstrip("0").rstrip(".") + "만"
    return f"{round(x, -2):,.0f}"


SCALE_NOTE = (
    "이 기준은 지역마다 다르다. 평소 이동이 많은 큰 지역은 행사가 있어도 '평소 수준'으로 나오기 쉽고, 작은 지역은 조금만 늘어도 "
    "'많음'이 나올 수 있다. '평소 수준'을 '효과가 없었다'로, '많음'을 '사람이 많이 왔다'로 바꿔 말하지 말고 이 기준과 함께 전할 것. "
    "'건'은 이동 건수이고 사람 수가 아니다. 행사 효과가 궁금하다고 하면, get_region_calendar로 행사 날짜를 찾고 get_datalab_daily로 "
    "그 날짜만 떼어 평소 같은 요일과 견줘 볼 수 있다고 안내할 것. 한 주 전체보다 하루 단위가 더 잘 보일 수 있지만, 큰 지역은 하루 "
    "단위로도 묻힐 수 있다. 데이터랩은 외지인 방문자 수(사람 수)라 이 서비스의 이동 건수와 단위가 다르니, 두 숫자를 섞거나 더하지 말 것.")


def scale(base, s):
    """지역별 판정 기준(전국순위_기간확장_작업.md 7번): 평소 값 × 평소 출렁임 × 2('많음' 문턱).
    비교군·공휴일 보정 전 기준이라 '약'."""
    sp = _pct(s)
    if not base or sp is None:
        return None
    return {"이 지역 판정 기준": f"평소보다 하루 약 {_count_word(base * sp / 100 * STRONG_X)} 건 넘게 더 유입돼야 '많음'으로 나옴",
            "이 지역 평소 규모": f"이 지역은 평소 하루 약 {_count_word(base)} 건이 유입되고, 평범한 주에도 {max(1, round(sp))}% 안팎은 변동함",
            "판정 기준 안내": SCALE_NOTE}


STRONG_X = 2   # '많음' 문턱(평소 출렁임의 배수). 기간 순위의 STRONG과 같은 값


def analyze(db, sido, sigungu, week_order):
    import timing
    with timing.step("analysis.prepare(시계열·비교군)"):
        P = prepare(db, sido, sigungu)
    if not P:
        return {"error": "지역을 찾지 못함. list_regions로 시도·시군구 이름을 확인할 것."}
    rows, me, peers, pdev = P["rows"], P["me"], P["peers"], P["pdev"]
    if week_order not in me:
        return {"error": f"그 주차 자료가 없음(이 지역 자료: {rows[0][1]} ~ {rows[-1][1]})."}
    with timing.step("analysis.judge_week(판정)"):
        J = judge_week(P, week_order)
    if J is None:
        return {"error": "평소 값을 계산할 직전 주가 부족함."}
    wk, v, base, dev, h = J["wk"], J["v"], J["base"], J["dev"], J["h"]
    one_day, hol_adj, final, s = J["one_day"], J["hol_adj"], J["final"], J["s"]
    method, ratio, verdict = J["method"], J["ratio"], J["verdict"]

    notes = []
    if is_long_holiday(h):
        notes.append("설·추석이나 평일 공휴일이 2일 이상 든 연휴 주라 평소와 비교하기 어렵다. 판정은 참고만.")
    if not peers:
        notes.append("비슷한 지역 목록이 없는 지역(새로 생긴 구 등)이라 비교군 대비 값을 못 냈다.")
    if verdict.startswith("판정 보류"):
        notes.append("행정구역 개편 직후라 관외 유입이 계단처럼 바뀌었다(구 사이 이동이 관내→관외). 개편 뒤 8주가 쌓일 때까지 판정하지 않는다.")
    if len(variants(sido, sigungu)) > 1:
        notes.append("시도 이름이 바뀐 지역이라 옛 이름 자료를 이어 붙여 계산했다.")
    if one_day and hol_adj is None:
        notes.append("평일 공휴일이 든 주인데 이 지역의 공휴일 반응을 추정할 자료가 부족하다.")
    if J.get("opposite"):
        notes.append(f"이 주에는 비슷한 지역들이 이 지역과 반대 방향으로 크게 움직였다(이 지역 평소 대비 {_pct(dev):+}%, "
                     f"비슷한 지역 {_pct(P['com'][week_order]):+}%). 휴가철·연말연시처럼 지역 성격(통근형·관광형)에 따라 "
                     "반대로 움직이는 시기에는 판정이 실제보다 크게 나올 수 있다. 판정과 함께 '평소 대비 변화'도 전할 것.")

    return {
        "지역": f"{sido} {sigungu}",
        "주차": wk,
        "기간(월~일)": week_dates(week_order),
        "관외 유입(일평균 건수)": v,
        "평소 값(직전 8주 중앙값, 연휴 주 제외)": round(base),
        "평소 대비 변화(%)": _pct(dev),
        "비슷한 지역 10곳의 같은 주 변화(%)": _pct(P["com"][week_order]) if pdev else None,
        "비교군 대비 변화(%)": _pct(P["adj"][week_order]),
        "공휴일 보정(%)": _pct(hol_adj),
        "프로야구 홈경기 몫 추정(%)": _pct(J["baseball"]),
        "야구 몫 뺀 판정(참고)": (f'{J["verdict_nobb"]} ({J["ratio_nobb"]}배)' if J["verdict_nobb"] else None),
        "판정에 쓴 방식": method,
        "최종 변화(%)": _pct(final),
        "평소 출렁임(%)": _pct(s),
        "평소 출렁임의 몇 배": ratio,
        "판정": verdict,
        "판정 말하는 법": speak(J, f"{sido} {sigungu}" if (sigungu.endswith("구") and " " not in sigungu) else sigungu,
                          f"{date_ko(week_order)}에"),
        "판정 기준": JUDGE_RULE,
        **(scale(base, s) or {}),
        "그 주 공휴일": h[2],
        "비교한 지역": [" ".join(current_name(db, a, b)) for a, b in peers],
        "주의": notes + [
            "이 값은 서비스가 계산한 비공식 참고값이다. 공식 통계가 아니다.",
            "원인은 이 도구가 정하지 않는다. 축제·날씨·공휴일 등은 판단 재료로만 제시하고 단정하지 말 것.",
            "'건'은 사람 수가 아니다. '명'으로 바꿔 말하지 말 것.",
            "한 지역에서만 크고 데이터랩에서는 그대로이면 원자료 처리 오류일 수도 있다. "
            "이용자가 원하면 report_issue로 운영자에게 알릴 수 있다고 안내할 것.",
        ],
    }


def all_regions(db):
    """나우캐스트 시군구 목록(시도 이름이 바뀐 곳은 가장 최근 이름 하나로)."""
    rows = db.execute("SELECT sido, sigungu, MAX(week_order) FROM moves WHERE direction='유입' "
                      "AND category='시군구' GROUP BY sido, sigungu").fetchall()
    best = {}
    for sd, sg, last in rows:
        k = canon(sd, sg)
        if k not in best or last > best[k][2]:
            best[k] = (sd, sg, last)
    return sorted((a, b) for a, b, _ in best.values())


_names = {"key": None, "map": {}}


def current_name(db, sido, sigungu):
    """지금 쓰는 시도·시군구 이름(시도 이름이 바뀐 곳은 가장 최근 이름). 보여 주기에만 쓰고 계산은 canon으로 한다.
    region_peers.csv는 개편 전 이름이라 '비교한 지역'에 옛 시도 이름이 나오던 것을 고침(전국순위_기간확장_작업.md 5번)."""
    key = db.execute("SELECT MAX(week_order) FROM moves").fetchone()[0]
    if _names["key"] != key:
        _names.update(key=key, map={canon(a, b): (a, b) for a, b in all_regions(db)})
    return _names["map"].get(canon(sido, sigungu), (sido, sigungu))


_rank_cache = {}

# ── 같은 방향 규칙(2026-11-19, 전국순위_기간확장_작업.md 4번) ─────────────────────────────────
# 비교 대상이 다 같이 크게 움직인 주에는 '평소보다 늘어난 곳'에 실제로는 줄어든 지역이 섞인다(계엄 주 함안 −11% 등).
# '늘어난 곳'은 평소 대비 +1% 넘게 늘어난 곳만, '줄어든 곳'은 −1% 넘게 줄어든 곳만. 엇갈린 곳은 따로 짧은 목록으로.
SAME_DIR_PCT = 1.0


def real_dir(ratio, dev):
    """판정 방향과 실제(평소 대비) 방향이 같은가."""
    p = _pct(dev)
    if p is None or ratio is None:
        return False
    return p > SAME_DIR_PCT if ratio > 0 else p < -SAME_DIR_PCT


def _move(p, stem):
    """+21.3 → '21% 늘어', −26 → '26% 줄어', 거의 0 → None. stem 뒤 말은 부르는 쪽이 붙인다."""
    if p is None or abs(p) <= SAME_DIR_PCT:
        return None
    return f"{abs(round(p))}% {'늘' if p > 0 else '줄'}{stem}"


def mixed_line(name, ratio, dev, com, hol_adj):
    """판정 방향과 실제 방향이 엇갈린 곳의 한 줄. 판정 방식에 맞춰 비교 숫자를 붙인다."""
    p = _pct(dev)
    me = _move(p, "었지만") or f"거의 그대로({p:+.1f}%)였지만"
    if ratio > 0:
        tail = "그보다는 덜 줄었음" if p < -SAME_DIR_PCT else "그보다는 많았음"
    else:
        tail = "그보다는 덜 늘었음" if p > SAME_DIR_PCT else "그보다는 적었음"
    if hol_adj is not None:
        h = _pct(hol_adj)
        hw = _move(h, "던") or None
        cmp = (f"공휴일 낀 주에는 보통 {abs(round(h))}%씩 {'늘' if h > 0 else '줄'}던 곳이라" if hw
               else "공휴일 낀 주에도 보통 거의 그대로이던 곳이라")
    elif com is not None:
        cw = _move(_pct(com), "어")
        cmp = f"비슷한 지역들은 {cw}" if cw else "비슷한 지역들은 거의 그대로여서"
    else:   # 비교군 없음: 평소 대비만으로 판정(드묾)
        cmp = "이 지역은 평소 오르내림이 아주 작은 곳이라"
        tail = "이 정도도 평소보다는 많은 편임" if ratio > 0 else "이 정도도 평소보다는 적은 편임"
    return f"{name}: 이번 주 {me}, {cmp} {tail}"


MIXED_NOTE = ["'사람이 몰렸다/빠졌다'고 말하지 말고 이 문장 그대로 전할 것.",
              "비슷한 지역은 주민 수·30km 안 인구·100km 안 인구가 비슷한 10곳이다(지역 성격은 보지 않음).",
              "어느 지역인지 물으면 analyze_region_week로 그 지역·그 주를 불러 '비교한 지역'을 보여 줄 것."]


def rank_week(db, week_order):
    """한 주의 전국 시군구 판정을 평소 출렁임 배수 순으로(같은 주차는 서버가 켜져 있는 동안 한 번만 계산)."""
    key = (week_order, db.execute("SELECT MAX(week_order) FROM moves").fetchone()[0])
    if key in _rank_cache:
        return _rank_cache[key]
    memo, out = {}, []
    for sd, sg in all_regions(db):
        P = prepare(db, sd, sg, memo)
        if not P:
            continue
        J = judge_week(P, week_order)
        if not J or J["ratio"] is None:
            continue
        row = {"지역": f"{sd} {sg}", "배수": J["ratio"], "판정": J["verdict"],
               "평소 대비(%)": _pct(J["dev"]), "최종 변화(%)": _pct(J["final"]), "평소 출렁임(%)": _pct(J["s"]),
               "판정에 쓴 방식": J["method"], "관외 유입(일평균 건수)": round(J["v"])}
        if J["hol_adj"] is not None:
            row["공휴일 보정(%)"] = _pct(J["hol_adj"])
        elif J["method"] == "비교군 대비" and P["com"].get(week_order) is not None:
            row["비슷한 지역 같은 주 변화(%)"] = _pct(P["com"][week_order])
        row["_dev"], row["_com"], row["_hol"] = J["dev"], P["com"].get(week_order), J["hol_adj"]
        row["_final"] = J["final"]
        rw = new_start(sd, sg)
        fj = first_judged(P, rw) if rw else None
        if fj:
            row["판정 시작"] = start_note(fj, rw)
        out.append(row)
    out.sort(key=lambda x: -abs(x["배수"]))
    if len(_rank_cache) > 20:
        _rank_cache.clear()
    _rank_cache[key] = out
    return out


# ── 기간 순위(2026-11-19, claude/전국순위_기간확장_작업.md 1·2·9번) ──────────────────────────────
# 판정 방식은 한 주 순위와 같고 범위만 넓힌다. 판정할 수 있는 주(평소 출렁임을 낼 평범한 주가 10주 넘게 쌓인 뒤,
# 대부분 2023년 6월부터)만 쓴다. 2019년부터로 계산 방식을 바꾸지 않는다(코로나 시기가 '평범한 때'에 섞임).
STRONG = 2          # '많음·적음' 문턱(평소 출렁임의 배수)
REPEAT_DAYS = 14    # '해마다 같은 때': 서로 다른 두 해 이상, 날짜가 앞뒤 2주 안
_period = {"key": None, "table": None}
_period_lock = threading.Lock()


def label(wo):
    return f"{wo // 1000}.{(wo // 10) % 100:02d}.{wo % 10}주차"


def monday(wo):
    import datetime as dt
    return dt.date.fromisoformat(week_dates(wo)[:10])


def period_table(db):
    """전국 시군구 → 판정할 수 있는 모든 주의 판정 요약. 자료 최신 주차가 바뀔 때만 다시 계산
    (전국 약 250곳 × 약 180주. 서버 시작·자동 갱신 뒤 web.refresh_lists가 미리 불러 둠).
    반환: {canon: {"sido", "sigungu", "weeks": [{wo, ratio, verdict, dev, final, s, method, v, hol, suspect}]}}"""
    key = db.execute("SELECT MAX(week_order) FROM moves").fetchone()[0]
    if _period["key"] == key and _period["table"] is not None:
        return _period["table"]
    with _period_lock:
        if _period["key"] == key and _period["table"] is not None:
            return _period["table"]
        memo, table = {}, {}
        for sd, sg in all_regions(db):
            P = prepare(db, sd, sg, memo)
            if not P:
                continue
            weeks = []
            for wo in sorted(P["me"]):
                if wo < START:
                    continue
                J = judge_week(P, wo)
                if not J or J["ratio"] is None or J["verdict"].startswith("판정 보류"):
                    continue
                weeks.append(dict(wo=wo, ratio=J["ratio"], verdict=J["verdict"], dev=J["dev"], final=J["final"],
                                  s=J["s"], method=J["method"], v=J["v"], hol=P["hol"].get(wo),
                                  com=P["com"].get(wo), hol_adj=J["hol_adj"],
                                  suspect=is_suspect(sd, sg, wo)))
            table[canon(sd, sg)] = dict(sido=sd, sigungu=sg, weeks=weeks)
        _period.update(key=key, table=table)
        return table


def judged_range(table):
    """실제로 판정한 첫 주·마지막 주(어느 지역이든). 글자로 박지 않고 자료에서 계산."""
    ws = [w["wo"] for r in table.values() for w in r["weeks"]]
    return (min(ws), max(ws)) if ws else (None, None)


def repeats(wos):
    """서로 다른 두 해 이상에서 같은 때(앞뒤 2주 안)에 되풀이됐나."""
    ds = [monday(w) for w in wos]
    for i, a in enumerate(ds):
        for b in ds[i + 1:]:
            if a.year == b.year:
                continue
            gap = abs(a.timetuple().tm_yday - b.timetuple().tm_yday)
            if min(gap, 365 - gap) <= REPEAT_DAYS:
                return True
    return False


def _strong(weeks, up, holiday=False):
    """'많음' 이상(up) 또는 '적음' 이하 주. 실제 방향이 엇갈린 주(4번)와 원자료 확인 구간은 뺀다.
    연휴 주(설·추석, 평일 공휴일 2일 이상)는 기본으로 빼고, holiday=True면 연휴 주만 센다(TEST_수정안_20261120.md 1-2번)."""
    return [w for w in weeks if not w["suspect"] and real_dir(w["ratio"], w["dev"])
            and is_long_holiday(w["hol"]) == holiday
            and (w["ratio"] > STRONG if up else w["ratio"] < -STRONG)]


def rank_period(table, a, b):
    """기간 [a, b] 안에서 지역마다 가장 크게 튄 주 하나(늘어난 쪽·줄어든 쪽 따로)와 '많음' 이상 횟수.
    원자료 확인 구간인 주, 실제 방향이 엇갈린 주(4번), 연휴 주(TEST_수정안_20261120.md 1번)는 고르지도 세지도 않는다.
    연휴 주는 비슷한 지역들이 크고 제각각 움직여 배수가 부풀기 때문(예: 안산 상록구 2025 설 주 +3.2%가 16배)."""
    up, down = [], []
    for r in table.values():
        ws = [w for w in r["weeks"] if a <= w["wo"] <= b and not w["suspect"] and not is_long_holiday(w["hol"])]
        if not ws:
            continue
        for side, pick, keep in ((up, max, lambda x: x > 0), (down, min, lambda x: x < 0)):
            cand = [w for w in ws if keep(w["ratio"]) and real_dir(w["ratio"], w["dev"])]   # 같은 방향 규칙(4번)
            if not cand:
                continue
            best = pick(cand, key=lambda w: w["ratio"])
            strong = _strong(ws, side is up)
            side.append(dict(r=r, best=best, n=len(strong), repeat=repeats([w["wo"] for w in strong])))
    up.sort(key=lambda x: -x["best"]["ratio"])
    down.sort(key=lambda x: x["best"]["ratio"])
    return up, down
