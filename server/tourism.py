"""주요 유료관광지 월별 방문객(한국문화관광연구원 관광자원통계, 표 tourism_monthly, 2026-11-16).

- 시험(claude/시설목록_계획.md): 하루 방문객이 관외 유입의 약 4% 이상인 지역은 월별로 함께 움직임(상관 대체로 0.6 이상),
  2% 미만은 안 보임. 관광 지역끼리 성수기를 함께 타서 상관이 조금 부풀 수 있으므로 '함께 움직이는 편'까지만 말한다.
- 시군구가 시 단위(예: 수원특례시)라 구로 나뉜 시는 시 전체 값으로만 보여 준다.
- 약 5개월 늦게 나온다. 매달 빠지는 관광지가 있어 합계는 실제보다 작을 수 있다.
"""
import datetime as dt
from collections import defaultdict

import analysis

SHARE_MIN = 4.0
NOTE = ("한국문화관광연구원 관광자원통계(주요 유료관광지 방문객, 내외국인 합계)다. 약 5개월 늦게 나오고, 달마다 빠지는 "
        "관광지가 있어 합계가 실제보다 작을 수 있다. 사람 수(명)이며 이동 건수와 단위가 다르니 더하거나 나누지 말 것.")


def _norm(g):
    return g.replace("특례시", "시").strip()


def _match(sido, sigungu):
    """이 지역에 맞는 (시도 후보, 시군구 이름, 시 전체인지)."""
    sidos = {s for s, _ in analysis.variants(sido, sigungu)} | {sido}
    if " " in sigungu:                       # 수원시 장안구 → 수원시 전체
        return sidos, sigungu.split(" ")[0], True
    return sidos, sigungu, False


def _rows(db, sido, sigungu, ym_from=None, ym_to=None):
    sidos, g, city = _match(sido, sigungu)
    q = (f"SELECT ym, name, cnt, gungu FROM tourism_monthly WHERE sido IN ({','.join('?' * len(sidos))})")
    args = list(sidos)
    if ym_from:
        q += " AND ym >= ?"
        args.append(ym_from)
    if ym_to:
        q += " AND ym <= ?"
        args.append(ym_to)
    try:
        rows = db.execute(q, args).fetchall()
    except Exception:          # 표가 아직 없음(첫 갱신 전)
        return [], city, g
    return [(ym, n, c) for ym, n, c, gg in rows if _norm(gg) == g], city, g


def _days(ym):
    y, m = int(ym[:4]), int(ym[4:])
    return ((dt.date(y + (m == 12), m % 12 + 1, 1)) - dt.date(y, m, 1)).days


def share(db, sido, sigungu):
    """최근 12개월(자료 있는 달) 하루 방문객 ÷ 하루 관외 유입. 구로 나뉜 시·자료 없음이면 None."""
    rows, city, _ = _rows(db, sido, sigungu)
    if city or not rows:
        return None
    by = defaultdict(int)
    names = defaultdict(int)
    for ym, n, c in rows:
        by[ym] += c
        names[n] += c
    yms = sorted(by)[-12:]
    inflow = defaultdict(list)
    for wo, _, v in analysis.series(db, sido, sigungu):
        if v:
            mon = dt.date.fromisoformat(analysis.week_dates(wo)[:10])
            t = mon + dt.timedelta(days=3)
            inflow[f"{t.year}{t.month:02d}"].append(v)
    ok = [ym for ym in yms if inflow.get(ym)]
    if len(ok) < 6:
        return None
    vis = sum(by[ym] / _days(ym) for ym in ok) / len(ok)
    inf = sum(sum(inflow[ym]) / len(inflow[ym]) for ym in ok) / len(ok)
    pct = round(vis / inf * 100, 1)
    if pct < SHARE_MIN:
        return None
    top = [n for n, _ in sorted(names.items(), key=lambda x: -x[1])[:3]]
    return {"관광지 방문객 몫(%)": pct,
            "설명": f"하루 관광지 방문객이 관외 유입의 약 {pct}%(주요: {', '.join(top)}). 이 지역 관외 유입은 관광지 방문과 "
                  "함께 움직이는 편이다(관광지 때문이라고 단정하지 말 것).",
            "기준": f"{ok[0][:4]}.{ok[0][4:]}~{ok[-1][:4]}.{ok[-1][4:]} 주요 유료관광지 방문객(한국문화관광연구원)"}


def months(db, sido, sigungu, a, b):
    """a~b(date) 달마다 그 지역 주요 유료관광지 방문객 상위 5곳."""
    yf, yt = f"{a.year}{a.month:02d}", f"{b.year}{b.month:02d}"
    rows, city, g = _rows(db, sido, sigungu, yf, yt)
    try:
        last = db.execute("SELECT MAX(ym) FROM tourism_monthly").fetchone()[0]
    except Exception:
        return None
    if not last:
        return None
    by = defaultdict(list)
    for ym, n, c in rows:
        by[ym].append((c, n))
    out = []
    y, m = a.year, a.month
    while f"{y}{m:02d}" <= yt:
        ym = f"{y}{m:02d}"
        if by.get(ym):
            top = sorted(by[ym], reverse=True)
            out.append({"달": f"{y}-{m:02d}", "주요 관광지(명)": {n: c for c, n in top[:5]},
                        "자료 있는 관광지 합계(명)": sum(c for c, _ in top)})
        elif ym > last:
            out.append({"달": f"{y}-{m:02d}", "주요 관광지(명)": "아직 안 나옴(약 5개월 늦게 나옴, 자료 끝 "
                        f"{last[:4]}-{last[4:]})"})
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    if not any(isinstance(x["주요 관광지(명)"], dict) for x in out):
        if not _rows(db, sido, sigungu)[0]:      # 이 지역엔 통계 대상 관광지가 없음
            return None
        pending = [x for x in out if isinstance(x["주요 관광지(명)"], str)]
        return {"안내": pending[0]["주요 관광지(명)"]} if pending else None
    res = {"달별": out, "자료 안내": NOTE}
    if city:
        res["범위"] = f"{g} 전체 기준(관광지 통계가 시 단위라 구로 나눌 수 없음)"
    return res
