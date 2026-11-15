"""연도별 흐름 검증(관리자 전용, 2026-11-16).

질문: 해마다 원값(이동 건수)은 통신사 가입자 등 바탕이 바뀌어 직접 견줄 수 없다. 대신 '전국 관외 유입 가운데 그 지역 몫'의
해마다 변화가 실제 방문 흐름을 따라가는가?
- 비교 대상: 한국관광 데이터랩 외지인(KT) 연간 합계의 '전국 가운데 그 지역 몫'
- 경계가 바뀐 지역(부천·화성·인천 개편·군위 등, 연도 사이에 자료가 끊기거나 새로 생긴 곳)은 빼고, 전국 합계도 남은 지역끼리
- 연도 평균은 그 해 월요일이 든 주들의 일평균(나우캐스트), 그 해 날짜 합계(데이터랩)
- 견주는 것: (1) 해마다 몫 변화(%)끼리 상관 (2) 2019→2025 몫 변화끼리 상관 (3) 방향 일치율
  비교용으로 원값 변화끼리의 같은 수치도 낸다(몫이 원값보다 나은지 보려고)
"""
import datetime as dt
import math
from collections import defaultdict

import analysis

YEARS = list(range(2019, 2026))


def _corr(xs, ys):
    n = len(xs)
    if n < 5:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return round(sxy / (sx * sy), 3) if sx and sy else None


def _rank(v):
    o = sorted(range(len(v)), key=lambda i: v[i])
    r = [0] * len(v)
    for k, i in enumerate(o):
        r[i] = k
    return r


def _spear(xs, ys):
    return _corr(_rank(xs), _rank(ys)) if len(xs) >= 5 else None


def _agree(xs, ys):
    p = [(x > 0) == (y > 0) for x, y in zip(xs, ys) if x and y]
    return round(sum(p) / len(p) * 100, 1) if p else None


def run(db):
    # 나우캐스트: 지역(이름 바뀐 곳은 한 열쇠로) × 연도 일평균 관외 유입
    nc = defaultdict(lambda: defaultdict(list))
    for sd, sg, wo, v in db.execute("SELECT sido, item, week_order, outside FROM moves "
                                    "WHERE direction='유입' AND category='시군구' AND outside IS NOT NULL"):
        mon = dt.date.fromisoformat(analysis.week_dates(wo)[:10])
        if 2019 <= mon.year <= 2025:
            nc[analysis.canon(sd, sg)][mon.year].append(v)
    nc_y = {k: {y: sum(v) / len(v) for y, v in d.items() if len(v) >= 40} for k, d in nc.items()}
    stable = {k for k, d in nc_y.items() if all(y in d for y in YEARS)}

    # 데이터랩 외지인: 지역 코드 → 연도 합계
    codes = {}
    for sd, sg, c in db.execute("SELECT sido, sigungu, datalab FROM region_map WHERE datalab IS NOT NULL AND datalab != ''"):
        k = analysis.canon(sd, sg)
        if k in stable:
            codes.setdefault(k, set()).update(c.split(","))
    dl_code = defaultdict(dict)
    for code, y, s in db.execute("SELECT signguCode, substr(day,1,4), SUM(touNum) FROM datalab_daily "
                                 "WHERE touDivCd=2 AND day BETWEEN '2019-01-01' AND '2025-12-31' GROUP BY signguCode, substr(day,1,4)"):
        dl_code[code][int(y)] = s
    dl_y = {}
    for k, cs in codes.items():
        d = defaultdict(float)
        ok = True
        for c in cs:
            if not all(y in dl_code.get(c, {}) for y in YEARS):
                ok = False
                break
            for y in YEARS:
                d[y] += dl_code[c][y]
        if ok:
            dl_y[k] = dict(d)
    both = sorted(stable & set(dl_y))

    nat_nc = {y: sum(nc_y[k][y] for k in both) for y in YEARS}
    nat_dl = {y: sum(dl_y[k][y] for k in both) for y in YEARS}
    sh_nc = {k: {y: nc_y[k][y] / nat_nc[y] for y in YEARS} for k in both}
    sh_dl = {k: {y: dl_y[k][y] / nat_dl[y] for y in YEARS} for k in both}

    def pct(a, b):
        return (b / a - 1) * 100 if a else None

    yy_sh_nc, yy_sh_dl, yy_raw_nc, yy_raw_dl = [], [], [], []
    by_year = {}
    for y in YEARS[1:]:
        a, b, c, d = [], [], [], []
        for k in both:
            a.append(pct(sh_nc[k][y - 1], sh_nc[k][y]))
            b.append(pct(sh_dl[k][y - 1], sh_dl[k][y]))
            c.append(pct(nc_y[k][y - 1], nc_y[k][y]))
            d.append(pct(dl_y[k][y - 1], dl_y[k][y]))
        yy_sh_nc += a; yy_sh_dl += b; yy_raw_nc += c; yy_raw_dl += d
        by_year[f"{y - 1}→{y}"] = {"몫 변화 상관": _corr(a, b), "몫 방향 일치(%)": _agree(a, b),
                                   "원값 변화 상관": _corr(c, d), "원값 방향 일치(%)": _agree(c, d),
                                   "전국 원값 변화 나우캐스트(%)": round(pct(nat_nc[y - 1], nat_nc[y]), 1),
                                   "전국 원값 변화 데이터랩(%)": round(pct(nat_dl[y - 1], nat_dl[y]), 1)}
    lr_nc = [pct(sh_nc[k][2019], sh_nc[k][2025]) for k in both]
    lr_dl = [pct(sh_dl[k][2019], sh_dl[k][2025]) for k in both]
    big = sorted(both, key=lambda k: -abs(lr_nc[both.index(k)]))[:15]
    ex = {}
    for k in big + [("충청북도", "청주시 흥덕구"), ("강원특별자치도", "강릉시"), ("경기도", "가평군"), ("대전광역시", "중구")]:
        if k in sh_nc:
            ex[" ".join(k)] = {"나우캐스트 몫(‰)": [round(sh_nc[k][y] * 1000, 3) for y in YEARS],
                               "데이터랩 몫(‰)": [round(sh_dl[k][y] * 1000, 3) for y in YEARS]}
    return {
        "설명": __doc__.strip().splitlines()[0],
        "지역 수": {"나우캐스트 7년 다 있음": len(stable), "데이터랩까지 이어짐": len(both)},
        "해마다 변화(모든 해·지역 묶음)": {"몫 상관": _corr(yy_sh_nc, yy_sh_dl), "몫 순위상관": _spear(yy_sh_nc, yy_sh_dl),
                                     "몫 방향 일치(%)": _agree(yy_sh_nc, yy_sh_dl),
                                     "원값 상관": _corr(yy_raw_nc, yy_raw_dl), "원값 방향 일치(%)": _agree(yy_raw_nc, yy_raw_dl)},
        "해마다": by_year,
        "2019→2025 몫 변화": {"상관": _corr(lr_nc, lr_dl), "순위상관": _spear(lr_nc, lr_dl), "방향 일치(%)": _agree(lr_nc, lr_dl)},
        "예시(몫 변화가 큰 15곳 + 관심 지역, 연도 2019~2025)": ex,
    }
