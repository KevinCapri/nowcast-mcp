"""큰 관광지 일별 방문객과 시군구 관외 유입 견주기(관리자 전용 시험, 2026-11-16).

질문: 국립중앙박물관(서울 용산구) 방문객이 평소보다 많았던 주에 용산구 관외 유입(이 서비스 판정)과
데이터랩 외지인도 평소보다 많았나? 박물관은 그 구 유입의 몇 % 규모인가?
- 주(월~일) 일평균 방문객의 '평소 대비' = ln(이번 주 / 직전 8주 중앙값), 휴관 주(일평균 100명 미만)는 빼고
- 이 서비스 쪽은 analysis.judge_week의 평소 대비(dev)와 판정 값(final, 비교군 대비 등)
- 데이터랩 외지인도 같은 방식의 주 평소 대비
- 2019.01~2025.12, 공휴일·연휴 주 포함/제외 둘 다
자료: server/nmk_daily.csv(국립중앙박물관 일별 관람객, 공공데이터포털, 2019~2025만)
"""
import csv
import datetime as dt
import math
from pathlib import Path
from statistics import median

import analysis

HERE = Path(__file__).resolve().parent
SITES = {"국립중앙박물관": ("서울특별시", "용산구", "nmk_daily.csv")}


def _corr(xs, ys):
    p = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(p)
    if n < 10:
        return None
    mx, my = sum(x for x, _ in p) / n, sum(y for _, y in p) / n
    sxy = sum((x - mx) * (y - my) for x, y in p)
    sx = math.sqrt(sum((x - mx) ** 2 for x, _ in p))
    sy = math.sqrt(sum((y - my) ** 2 for _, y in p))
    return round(sxy / (sx * sy), 3) if sx and sy else None


def _devs(weeks, val):
    """주 순서 목록과 {월요일: 값} → {월요일: ln(값 / 직전 8주 중앙값)}."""
    out = {}
    for i, m in enumerate(weeks):
        prev = [val[w] for w in weeks[max(0, i - 8):i] if val.get(w)]
        if val.get(m) and len(prev) >= 6:
            out[m] = math.log(val[m] / median(prev))
    return out


def _one(db, name, sido, sigungu, fn):
    day = {}
    with open(HERE / fn, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["n"]:
                day[dt.date.fromisoformat(r["day"])] = float(r["n"])
    P = analysis.prepare(db, sido, sigungu)
    if not P:
        return "지역 없음"
    mon_wo = {dt.date.fromisoformat(analysis.week_dates(wo)[:10]): wo for wo in P["me"]}
    weeks = sorted(m for m in mon_wo if dt.date(2019, 1, 1) <= m <= dt.date(2025, 12, 22))
    site = {}
    for m in weeks:
        v = [day.get(m + dt.timedelta(days=k)) for k in range(7)]
        v = [x for x in v if x is not None]
        if len(v) == 7 and sum(v) / 7 >= 100:
            site[m] = sum(v) / 7
    sdev = _devs(weeks, site)

    # 데이터랩 외지인 주 합계
    codes = []
    for sd, sg in analysis.variants(sido, sigungu):
        r = db.execute("SELECT datalab FROM region_map WHERE sido=? AND sigungu=?", (sd, sg)).fetchone()
        if r and r[0]:
            codes += r[0].split(",")
    dl_day = {}
    codes = sorted(set(codes))  # 옛 이름·새 이름이 같은 코드를 가져올 때 중복 빼기
    if codes:
        q = (f"SELECT day, SUM(touNum) FROM datalab_daily WHERE signguCode IN ({','.join('?' * len(codes))}) "
             "AND touDivCd=2 AND day BETWEEN '2019-01-01' AND '2025-12-31' GROUP BY day")
        dl_day = {dt.date.fromisoformat(d): v for d, v in db.execute(q, codes)}
    dl = {m: sum(dl_day.get(m + dt.timedelta(days=k), 0) for k in range(7)) / 7 for m in weeks
          if all((m + dt.timedelta(days=k)) in dl_day for k in range(7))}
    ddev = _devs(weeks, dl)

    rows = []
    for m in weeks:
        wo = mon_wo[m]
        J = analysis.judge_week(P, wo)
        rows.append(dict(m=m, wo=wo, hol=bool(P["hol"].get(wo)), s=sdev.get(m), dl=ddev.get(m),
                         dev=J["dev"] if J else None, final=J["final"] if J else None,
                         ratio=J.get("ratio") if J else None, verdict=J.get("verdict") if J else None,
                         site=site.get(m), out=P["me"][wo][1] if wo in P["me"] else None))

    def block(rs):
        s = [r["s"] for r in rs]
        return {"주 수": sum(1 for r in rs if r["s"] is not None and r["dev"] is not None),
                "박물관 평소 대비 ↔ 이 서비스 평소 대비": _corr(s, [r["dev"] for r in rs]),
                "박물관 평소 대비 ↔ 이 서비스 판정 값(비교군 대비 등)": _corr(s, [r["final"] for r in rs]),
                "박물관 평소 대비 ↔ 데이터랩 외지인 평소 대비": _corr(s, [r["dl"] for r in rs]),
                "데이터랩 외지인 ↔ 이 서비스 판정 값(참고)": _corr([r["dl"] for r in rs], [r["final"] for r in rs])}

    plain = [r for r in rows if not r["hol"]]
    by_year = {y: block([r for r in plain if r["m"].year == y]) for y in range(2019, 2026)}
    share = [r["site"] / r["out"] * 100 for r in rows if r["site"] and r["out"]]
    top = sorted((r for r in plain if r["s"] is not None), key=lambda r: -r["s"])[:12]
    yearly = {}
    for y in range(2019, 2026):
        v = [r for r in rows if r["m"].year == y and r["site"] and r["out"]]
        if v:
            yearly[y] = {"박물관 일평균(명)": round(sum(r["site"] for r in v) / len(v)),
                         "용산구 관외 유입 일평균(건)": round(sum(r["out"] for r in v) / len(v))}
    return {
        "지역": f"{sido} {sigungu}",
        "박물관 ÷ 관외 유입(%, 주 일평균 기준) 중앙값": round(median(share), 2) if share else None,
        "공휴일·연휴 주 뺀 전체": block(plain),
        "공휴일·연휴 주 포함 전체": block(rows),
        "해마다(공휴일 주 뺌)": by_year,
        "해마다 평균(원값, 참고)": yearly,
        "박물관이 평소보다 가장 많았던 주 12개(공휴일 주 뺌)": [
            {"주": analysis.week_dates(r["wo"]), "박물관 평소 대비(%)": round((math.exp(r["s"]) - 1) * 100, 1),
             "데이터랩 외지인 평소 대비(%)": None if r["dl"] is None else round((math.exp(r["dl"]) - 1) * 100, 1),
             "이 서비스 평소 대비(%)": None if r["dev"] is None else round((math.exp(r["dev"]) - 1) * 100, 1),
             "이 서비스 판정": r["verdict"], "평소 출렁임의 몇 배": r["ratio"]} for r in top],
    }


def run(db):
    return {"설명": __doc__.strip().splitlines()[0],
            **{name: _one(db, name, sd, sg, fn) for name, (sd, sg, fn) in SITES.items()}}
