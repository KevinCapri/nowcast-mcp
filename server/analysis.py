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
            "오류라고 단정하지도 말고, 이 기간 값을 방문 증가·감소의 근거로 쓰지도 말 것. 행사 후보를 억지로 대지 말 것. "
            "이용자가 원하면 report_issue로 운영자에게 알릴 수 있다."
            for k, a, b, why in SUSPECT if k == c and a <= wo_to and wo_from <= b]


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


def _pass(rows, hol, sig=None, skip=()):
    out, normal = {}, []
    for wo, wk, v in rows:
        base = median(normal[-BASE_WEEKS:]) if len(normal) >= BASE_WEEKS else None
        dev = math.log(v / base) if (base and v and v > 0) else None
        out[wo] = (wk, v, base, dev)
        if is_long_holiday(hol.get(wo)) or not v:
            continue
        if sig and wo in skip and dev is not None and dev > SPIKE_K * sig:
            continue                                # 축제로 크게 늘어난 주는 평소 값에 넣지 않음
        normal.append(v)
    return out


def deviations(rows, hol, region=None):
    """week_order → (주차, 관외, 평소 값, ln 변화)
    2026-11-15: 축제 주가 직전 8주에 들어가 평소 값을 부풀려, 축제 직후 주가 '뚜렷이 적음'으로 잘못 나오던 문제를 고침.
    region(canon)을 주면, 그 지역 축제 계획 기간 중 평소 출렁임의 3배를 넘게 늘어난 주는 평소 값 계산에서 뺀다.
    (학기 시작·새 시설처럼 축제가 아닌 변화는 그대로 평소 값에 들어가 새 수준을 따라간다.)"""
    first = _pass(rows, hol)
    skip = fest_weeks(region) if region else set()
    if not skip:
        return first
    xs = [d for wo, (_, _, _, d) in first.items() if d is not None and wo >= START and not hol.get(wo)]
    sig = _mad(xs) if len(xs) >= 20 else None
    return _pass(rows, hol, sig, skip) if sig else first


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
    # 프로야구 홈경기 몫(구장이 있는 9개 시군구, 시즌 중만). baseball.py 참고
    bb = None
    if final is not None:
        import datetime as _dt
        import baseball
        bb = baseball.adjust(P["sido"], P["sigungu"], _dt.date.fromisoformat(week_dates(week_order)[:10]))
        if bb is not None:
            final -= bb
            method += " · 프로야구 홈경기 몫 뺌"
    ratio = round(final / s, 1) if (final is not None and s) else None
    verdict = judge(ratio)
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
                method=method, ratio=ratio, verdict=verdict, opposite=opposite, baseball=bb)


def analyze(db, sido, sigungu, week_order):
    P = prepare(db, sido, sigungu)
    if not P:
        return {"error": "지역을 찾지 못함. list_regions로 시도·시군구 이름을 확인할 것."}
    rows, me, peers, pdev = P["rows"], P["me"], P["peers"], P["pdev"]
    if week_order not in me:
        return {"error": f"그 주차 자료가 없음(이 지역 자료: {rows[0][1]} ~ {rows[-1][1]})."}
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
        "프로야구 홈경기 보정(%)": _pct(J["baseball"]),
        "판정에 쓴 방식": method,
        "최종 변화(%)": _pct(final),
        "평소 출렁임(%)": _pct(s),
        "평소 출렁임의 몇 배": ratio,
        "판정": verdict,
        "판정 기준": JUDGE_RULE,
        "그 주 공휴일": h[2],
        "비교한 지역": [f"{a} {b}" for a, b in peers],
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


_rank_cache = {}


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
        out.append({"지역": f"{sd} {sg}", "배수": J["ratio"], "판정": J["verdict"],
                    "평소 대비(%)": _pct(J["dev"]), "최종 변화(%)": _pct(J["final"]),
                    "판정에 쓴 방식": J["method"], "관외 유입(일평균 건수)": round(J["v"])})
    out.sort(key=lambda x: -abs(x["배수"]))
    if len(_rank_cache) > 20:
        _rank_cache.clear()
    _rank_cache[key] = out
    return out
