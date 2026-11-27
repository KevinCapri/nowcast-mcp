"""날짜별 방문자(0.6.0, 2026-11-22): 데이터랩 값 + 데이터랩이 아직 없는 최근 날의 추정 범위 + 평소 같은 요일 대비 + 이 지역 평소 방문 모습 + 날씨.

계산 기준(사람이 읽는 원본은 Blanc '일별 추정', 결정 기록은 프로젝트 문서 Tool_0.6.0_설계.md·건명_Backtest_20261122.md 4.1):
- 주 추정(명/일) = 그 주 나우캐스트 관외 유입(건/일) × 비율. 비율은 데이터랩이 다 있는 최근 '평범한 주' 12개(KT 부풀림 구간·설·추석·연말연시·
  축제·행사 주 뺌)의 '외지인 ÷ 관외 유입' 로그 평균(ⓐ). 설·추석·연말연시 주만 출근 몫을 뗀 비율(ⓑ, 외지인 ÷ (관외 − 통근 인원 × 1건 × 근무일/7)).
- 하루 나누기: 그 지역 요일 몫(최근 52주 평범한 주, KT 구간 주도 씀), 평일 공휴일은 일요일 몫. 합은 주 평균 × 7에 맞춤.
- 날짜별로 나누지 않는 주(하루 값 범위가 ±20%를 넘는 종류): 설·추석 주, 연말연시 주, 샌드위치가 낀 주, 크게 튄 주(관외 유입이 직전 12주
  가운데값보다 30% 넘게 많음). 이런 주는 그 주 하루 평균 범위만.
- 범위 폭: Backtest '열 번 중 여덟 번' 값(log 대칭: 추정 ÷ (1+w) – 추정 × (1+w)).
- 평소 같은 요일: 그 날 앞쪽 같은 요일 데이터랩 실제 값 8개의 가운데값(공휴일·샌드위치·연휴 주·연말연시 주·그 지역 축제·행사 날 뺌,
  12주까지만 거슬러 감, 4개보다 적으면 내지 않음, 수준이 바뀐 경계 2025-05-01·2026-05-01을 넘지 않음).
기능 끄기: Azure 앱 설정 PAUSE_FEATURES에 daily_estimate가 있으면 추정 범위를 붙이지 않음(데이터랩 값은 그대로).
"""
import csv
import datetime as dt
import math
import os
import threading
import time
from pathlib import Path
from statistics import median

import analysis

HERE = Path(__file__).resolve().parent
WD = "월화수목금토일"
LEVEL_CUTS = (dt.date(2025, 5, 1), dt.date(2026, 5, 1))   # 데이터랩 수준이 바뀐 경계(Blanc 2.2)
KT_FROM, KT_TO = dt.date(2025, 4, 28), dt.date(2026, 4, 27)   # 이 사이 월요일인 주는 비율 계산에서 뺌(2025-05~2026-04가 걸친 주)
RATIO_WEEKS = 12
RATIO_MIN_NEW = 4          # 새로 생긴 구(인천)는 쌓인 주가 적어 4주부터 씀
RHYTHM_WEEKS = 52
RHYTHM_MIN = 8
USUAL_N, USUAL_BACK, USUAL_MIN = 8, 12, 4
SURGE = 0.30               # 직전 12주 가운데값보다 이만큼 넘게 많으면 '크게 튄 주'
MAX_DAYS = 31              # 날짜별은 한 번에 한 달
# 범위 폭(Backtest '열 번 중 여덟 번', insight-work 분석/2026-11-22_건명Backtest summary_final.txt·summary.txt)
W_DAY = {"평범한 날": 0.130, "축제·행사 주의 날": 0.149, "평일 공휴일": 0.191}
W_WEEK = {"평범한 주": 0.085, "평일 공휴일 낀 주": 0.086, "축제·행사 주": 0.087, "설·추석 주": 0.121,
          "연말연시 주": 0.121, "샌드위치가 낀 주": 0.086, "크게 튄 주": 0.137}
REASON = {   # 2026-11-22 Kevin 확정 문장(생활 속 까닭)
    "설·추석 주": "명절엔 고향에 오가는 날이 사람마다 달라서, 날짜별로 몇 명인지 가늠하기 어려워요",
    "연말연시 주": "성탄절과 새해 첫날이 해마다 다른 요일에 걸려서, 날짜별로 몇 명인지 가늠하기 어려워요",
    "크게 튄 주": "그 주에 사람이 갑자기 몰렸는데, 어느 날에 몰렸는지는 이동 건수만으로 알 수 없어요",
    "샌드위치가 낀 주": "앞뒤가 쉬는 날이라 연차를 내고 놀러 오는 사람이 많아서, 다른 평일과 같다고 보기 어려워요",
}
NEW_GU_NOTE = "새로 생긴 구라 맞혀 본 기간이 짧아요(2026년 가을 다섯 주). 겨울까지 지켜본 뒤 다시 확인할 예정이에요."


def paused(name):
    return name in {s.strip() for s in os.environ.get("PAUSE_FEATURES", "").split(",") if s.strip()}


def _d(s):
    return dt.date.fromisoformat(str(s)[:10])


def _monday(d):
    return d - dt.timedelta(days=d.weekday())


def _era(d):
    return sum(d >= c for c in LEVEL_CUTS)


def ko_num(v):
    """사람 수를 읽기 쉽게: 24,300 → '2만 4천', 3,280 → '3,300', 874 → '870'."""
    v = int(round(v))
    if v >= 10000:
        man, rest = divmod(v, 10000)
        cheon = int(round(rest / 1000))
        if cheon == 10:
            man, cheon = man + 1, 0
        return f"{man}만" + (f" {cheon}천" if cheon else "")
    if v >= 1000:
        return f"{int(round(v, -2)):,}"
    return str(int(round(v, -1)) if v >= 100 else v)


def rng(est, w):
    lo, hi = est / (1 + w), est * (1 + w)
    return lo, hi, f"약 {ko_num(lo)}–{ko_num(hi)} 명"


def week_name(W, hol):
    """'추석 주'·'설 주'·'연말연시 주'처럼 이용자에게 말하는 주 이름."""
    if W["kind"] == "설·추석 주":
        names = {hol.get(W["mon"] + dt.timedelta(days=i), "") for i in range(7)}
        return "추석 주" if any("추석" in n for n in names) else "설 주"
    return W["kind"]


def date_ko(d):
    return f"{d.month}월 {d.day}일({WD[d.weekday()]})"


# ---------- 자료 읽기 ----------
_CACHE, _LOCK = {}, threading.Lock()
TTL = 600


def _cached(key, fn):
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < TTL:
            return hit[1]
    v = fn()
    with _LOCK:
        _CACHE[key] = (time.time(), v)
    return v


def clear():
    with _LOCK:
        _CACHE.clear()


def codes(db, sido, sigungu):
    out = []
    for sd, sg in analysis.variants(sido, sigungu):
        r = db.execute("SELECT datalab FROM region_map WHERE sido=? AND sigungu=?", (sd, sg)).fetchone()
        if r and r[0]:
            out += r[0].split(",")
    return sorted(set(out))


def visitors(db, cs, div=2, a=None, b=None):
    """날짜 → 방문자(명). a·b가 없으면 전체."""
    q = f"SELECT day, SUM(touNum) FROM datalab_daily WHERE signguCode IN ({','.join('?' * len(cs))}) AND touDivCd=?"
    args = [*cs, div]
    if a:
        q += " AND day >= ?"
        args.append(a.isoformat())
    if b:
        q += " AND day <= ?"
        args.append(b.isoformat())
    return {_d(d): v for d, v in db.execute(q + " GROUP BY day", args) if v is not None}


def holidays(db):
    def f():
        try:
            return {_d(d): n for d, n in db.execute("SELECT day, name FROM holidays")}
        except Exception:
            return {}
    return _cached("hol", f)


def bounds(db, cs, div=2):
    """그 지역 방문자 자료의 첫 날·마지막 날. 지역 코드로 좁혀 색인(signguCode, touDivCd, day)을 탐
    (표 전체 MIN·MAX는 색인을 못 써 Azure 저장소에서 느림)."""
    if not cs:
        return None, None
    r = db.execute(f"SELECT MIN(day), MAX(day) FROM datalab_daily WHERE signguCode IN ({','.join('?' * len(cs))}) "
                   "AND touDivCd=?", (*cs, div)).fetchone()
    return (_d(r[0]) if r and r[0] else None), (_d(r[1]) if r and r[1] else None)


_EVENT_DAYS = None


def event_days(region):
    """지역(canon) → 축제·대형 행사 날짜들."""
    global _EVENT_DAYS
    if _EVENT_DAYS is None:
        ev = {}
        for name in ("festivals.csv", "events.csv"):
            try:
                with open(HERE / name, encoding="utf-8") as f:
                    for r in csv.DictReader(f):
                        try:
                            s, e = _d(r["s"]), _d(r["e"])
                        except (ValueError, TypeError, KeyError):
                            continue
                        k = analysis.canon(r["sido"], r["sigungu"])
                        while s <= e:
                            ev.setdefault(k, set()).add(s)
                            s += dt.timedelta(days=1)
            except OSError:
                pass
        _EVENT_DAYS = ev
    return _EVENT_DAYS.get(region, set())


_COMMUTE = None


def commute(sido, sigungu):
    global _COMMUTE
    if _COMMUTE is None:
        out = {}
        try:
            with open(HERE / "commute.csv", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    out[analysis.canon(r["sido"], r["sigungu"])] = float(r["commute_in"])
        except OSError:
            pass
        _COMMUTE = out
    return _COMMUTE.get(analysis.canon(sido, sigungu))


# ---------- 날·주 종류 ----------
def off(d, hol):
    return d.weekday() >= 5 or d in hol


def is_sandwich(d, hol):
    return d.weekday() < 5 and d not in hol and off(d - dt.timedelta(days=1), hol) and off(d + dt.timedelta(days=1), hol)


def is_lunar(d, hol):
    return "설" in hol.get(d, "") or "추석" in hol.get(d, "")


def week_flags(mon, hol, evd):
    days = [mon + dt.timedelta(days=i) for i in range(7)]
    lunar = any(is_lunar(x, hol) for x in days)
    # 설·추석 연휴에 붙은 대체공휴일도 설·추석 주로
    whol = [x for x in days if x.weekday() < 5 and x in hol]
    yearend = any((x.month, x.day) in ((12, 25), (1, 1)) for x in days)
    return dict(lunar=lunar, yearend=yearend, long=lunar or len(whol) >= 2, whol=whol,
                sandwich=[x for x in days if is_sandwich(x, hol)], event=any(x in evd for x in days))


# ---------- 평소 같은 요일 ----------
def usual(d, vis, hol, evd, wf):
    """그 날 앞쪽 같은 요일 실제 값 8개의 가운데값. wf(월요일 → week_flags)."""
    xs, era = [], _era(d)
    for k in range(1, USUAL_BACK + 1):
        p = d - dt.timedelta(days=7 * k)
        if _era(p) != era:
            break
        if p not in vis or p in hol or is_sandwich(p, hol) or p in evd:
            continue
        f = wf(_monday(p))
        if f["long"] or f["yearend"]:
            continue
        xs.append(vis[p])
        if len(xs) == USUAL_N:
            break
    return median(xs) if len(xs) >= USUAL_MIN else None


# ---------- 주 추정 ----------
def _week_means(vis):
    out = {}
    for d, v in vis.items():
        out.setdefault(_monday(d), []).append(v)
    return {m: sum(xs) / 7 for m, xs in out.items() if len(xs) == 7}


def _nowcast_by_monday(db, sido, sigungu):
    out = {}
    for wo, _, v in analysis.series(db, sido, sigungu):
        if v:
            out[_d(analysis.week_dates(wo)[:10])] = (wo, float(v))
    return out


def _workdays(mon, hol):
    return 5 - sum(1 for i in range(5) if (mon + dt.timedelta(days=i)) in hol)


class Region:
    """한 지역의 추정 재료를 한 번에."""

    def __init__(self, db, sido, sigungu, since=None):
        """since: 이 날부터 방문자를 읽음(없으면 마지막 날 앞 2년 남짓 — 비율·요일 몫·평소 같은 요일·들쭉날쭉에 충분)."""
        self.db, self.sido, self.sigungu = db, sido, sigungu
        self.key = analysis.canon(sido, sigungu)
        self.cs = codes(db, sido, sigungu)
        self.hol = holidays(db)
        self.evd = event_days(self.key)
        self.first, lastd = bounds(db, self.cs) if self.cs else (None, None)
        if since is None and lastd:
            since = lastd - dt.timedelta(days=800)
        self.vis = visitors(db, self.cs, a=since) if self.cs else {}
        self.last = max(self.vis) if self.vis else None
        self._wf = {}
        try:
            self.nc = _nowcast_by_monday(db, sido, sigungu)
        except Exception:      # 이동 자료 표가 없으면 추정 없이 관광공사 값만
            self.nc = {}
        self.rw = analysis.reform_week(sido, sigungu)
        self.new_gu = (sido, sigungu) in analysis.REFORM_NEW_GU
        self.C = commute(sido, sigungu)

    def wf(self, mon):
        if mon not in self._wf:
            self._wf[mon] = week_flags(mon, self.hol, self.evd)
        return self._wf[mon]

    def usual(self, d):
        return usual(d, self.vis, self.hol, self.evd, self.wf)

    def _plain(self, mon):
        f = self.wf(mon)
        return not (f["lunar"] or f["yearend"] or f["event"]) and not (KT_FROM <= mon <= KT_TO)

    def ratios(self):
        """(ⓐ 로그 평균, ⓑ 로그 평균, 쓴 주 수). 데이터랩이 다 있는 주만."""
        if hasattr(self, "_ratios"):
            return self._ratios
        wm = _week_means(self.vis)
        la, lb = [], []
        for mon in sorted(wm, reverse=True):
            if mon not in self.nc or not self._plain(mon):
                continue
            wo, N = self.nc[mon]
            if self.rw and wo < self.rw:
                break                                      # 개편 전 주는 관외 유입 기준이 달라 쓰지 않음
            x = wm[mon]
            la.append(math.log(x / N))
            if self.C:
                visit = N - self.C * _workdays(mon, self.hol) / 7
                if visit > 0:
                    lb.append(math.log(x / visit))
            if len(la) == RATIO_WEEKS:
                break
        need = RATIO_MIN_NEW if self.new_gu else RATIO_WEEKS
        a = sum(la) / len(la) if len(la) >= need else None
        b = sum(lb[:len(la)]) / len(lb[:len(la)]) if (a is not None and len(lb) >= need) else None
        self._ratios = (a, b, len(la))
        return self._ratios

    def rhythm(self):
        """요일별 몫(주 평균 대비). 최근 52주 평범한 주(축제·평일 공휴일·설·추석·연말연시 뺌, KT 구간 주도 씀)."""
        if hasattr(self, "_rh"):
            return self._rh
        wm = _week_means(self.vis)
        sh = {i: [] for i in range(7)}
        n = 0
        for mon in sorted(wm, reverse=True)[:RHYTHM_WEEKS]:
            f = self.wf(mon)
            if f["lunar"] or f["yearend"] or f["event"] or f["whol"]:
                continue
            for i in range(7):
                sh[i].append(self.vis[mon + dt.timedelta(days=i)] / wm[mon])
            n += 1
        self._rh = [sum(sh[i]) / len(sh[i]) for i in range(7)] if n >= RHYTHM_MIN else None
        return self._rh

    def surge(self, mon):
        if mon not in self.nc:
            return False
        prev = [self.nc[m][1] for m in sorted(self.nc) if m < mon][-12:]
        if len(prev) < 8:
            return False
        return self.nc[mon][1] / median(prev) - 1 > SURGE

    def week_estimate(self, mon):
        """그 주 하루 평균 추정과 종류. 없으면 None."""
        if mon not in self.nc:
            return None
        a, b, _ = self.ratios()
        if a is None:
            return None
        wo, N = self.nc[mon]
        f = self.wf(mon)
        if (f["lunar"] or f["yearend"]) and b is not None and self.C:
            visit = N - self.C * _workdays(mon, self.hol) / 7
            est = math.exp(b) * visit if visit > 0 else math.exp(a) * N
        else:
            est = math.exp(a) * N
        if f["lunar"]:
            kind = "설·추석 주"
        elif f["yearend"]:
            kind = "연말연시 주"
        elif self.surge(mon):
            kind = "크게 튄 주"
        elif f["sandwich"]:
            kind = "샌드위치가 낀 주"
        elif f["event"]:
            kind = "축제·행사 주"
        elif f["whol"]:
            kind = "평일 공휴일 낀 주"
        else:
            kind = "평범한 주"
        return dict(mon=mon, wo=wo, est=est, kind=kind, split=kind not in REASON)

    def day_estimates(self, mon):
        """날짜별로 나눌 수 있는 주면 [(날짜, 추정, 날 종류)], 아니면 None."""
        W = self.week_estimate(mon)
        rh = self.rhythm()
        if not W or not W["split"] or not rh:
            return None
        days = [mon + dt.timedelta(days=i) for i in range(7)]
        s = [rh[6] if (x.weekday() < 5 and x in self.hol) else rh[x.weekday()] for x in days]
        tot = sum(s)
        out = []
        for x, si in zip(days, s):
            if x.weekday() < 5 and x in self.hol:
                k = "평일 공휴일"
            elif W["kind"] == "축제·행사 주":
                k = "축제·행사 주의 날"
            else:
                k = "평범한 날"
            out.append((x, W["est"] * 7 * si / tot, k))
        return out


# ---------- 평소 방문 모습 ----------
def _wobble(R, upto):
    """최근 1년 평범한 날이 평소 같은 요일에서 오르내린 정도(가운데값): (%, 명, 평소 하루 가운데값)."""
    pct, ppl, base = [], [], []
    d = upto - dt.timedelta(days=363)
    while d <= upto:
        if d in R.vis and d not in R.hol and d not in R.evd and not is_sandwich(d, R.hol):
            f = R.wf(_monday(d))
            if not (f["long"] or f["yearend"]):
                u = R.usual(d)
                if u:
                    pct.append(abs(R.vis[d] / u - 1))
                    ppl.append(abs(R.vis[d] - u))
                    base.append(u)
        d += dt.timedelta(days=1)
    if len(pct) < 60:
        return None
    return median(pct) * 100, median(ppl), median(base)


_WOBBLE = {"at": 0, "v": None, "busy": False}


def _build_wobble(db):
    out = {}
    try:
        regs = db.execute("SELECT sido, sigungu FROM region_map WHERE datalab IS NOT NULL AND datalab != ''").fetchall()
        for sd, sg in regs:
            k = analysis.canon(sd, sg)
            if k in out:
                continue
            try:
                R = Region(db, sd, sg)
                w = _wobble(R, R.last) if R.last else None
            except Exception:
                w = None
            if w:
                out[k] = w[0]
    finally:
        _WOBBLE.update(at=time.time(), v=out or _WOBBLE["v"], busy=False)


def wobble_table(db, wait=False):
    """전국 시군구의 오르내림 %(하루 한 번, 뒤에서 계산). 아직 없으면 None을 돌려주고 계산을 시작함(이용자 응답을 늦추지 않게)."""
    stale = time.time() - _WOBBLE["at"] > 86400
    if (stale or _WOBBLE["v"] is None) and not _WOBBLE["busy"]:
        _WOBBLE["busy"] = True
        if wait:
            _build_wobble(db)
        else:
            threading.Thread(target=_build_wobble, args=(db,), daemon=True).start()
    return _WOBBLE["v"]


def wobble_text(db, R):
    w = _wobble(R, R.last) if R.last else None
    if not w:
        return None
    pct, ppl, base = w
    table = wobble_table(db) or {}
    vals = sorted(table.values())
    pos, top10 = "중간", False
    if vals:
        rank = sum(v > pct for v in vals) / len(vals)     # 0 = 가장 큼
        if rank < 0.1:
            pos, top10 = "큰 편(열 곳 중 한 곳)", True
        elif rank < 0.3:
            pos = "큰 편"
        elif rank >= 0.7:
            pos = "작은 편"
    t = f"이 지역은 평범한 날에도 같은 요일끼리 ±{round(pct)}% 안팎, 하루 약 {ko_num(ppl)}명씩 오르내려요."
    if vals:
        t += f" 전국 시군구 가운데 오르내림이 {pos}이에요."
    if vals and pos.startswith("큰") and base < 5000:
        t += " 찾는 사람이 적은 곳이라 몇백 명만 달라도 %가 크게 움직여요."
    return t


def weekend_text(ratio):
    r = round(ratio, 1)
    if ratio >= 1.1:
        return f"주말에 외지인이 평일의 약 {r}배 와요."
    if ratio < 0.95:
        return f"평일에 외지인이 주말보다 많이 와요(주말이 평일의 약 {r}배)."
    return f"주말과 평일에 오는 외지인 수가 비슷해요(주말이 평일의 약 {r}배)."


# ---------- 날씨 ----------
def station(db, sido, sigungu):
    for sd, sg in analysis.variants(sido, sigungu):
        try:
            r = db.execute("SELECT stn, stn_name, how FROM station_map WHERE sido=? AND sigungu=?", (sd, sg)).fetchone()
        except Exception:
            return None
        if r:
            return r
    return None


def weather_days(db, stn, a, b):
    try:
        rows = db.execute("SELECT date, ta_min, ta_max, rn_day, sd_max FROM weather_daily WHERE stn=? AND date BETWEEN ? AND ?",
                          (stn, a.isoformat(), b.isoformat())).fetchall()
    except Exception:
        return {}
    out = {}
    for d, lo, hi, rn, sd in rows:
        if lo is None or hi is None:
            continue
        t = f"{round(lo)}–{round(hi)}℃"
        if rn is not None and rn >= 1:
            t += f", {'비·눈' if (lo + hi) / 2 < 3 else '비'} {round(rn)}mm"
        if sd is not None and sd > 0:
            t += f", 눈 쌓임 {round(sd, 1)}cm"
        out[_d(d)] = t
    return out


def week_weather(db, stn, wos):
    """주차 번호 → 그 주 날씨 한 줄(관측소 값)."""
    if not wos:
        return {}
    try:
        q = (f"SELECT week_order, ta_avg, ta_min, ta_max, rn_sum, rain_days, sd_max FROM weather_week WHERE stn=? "
             f"AND week_order IN ({','.join('?' * len(wos))})")
        rows = db.execute(q, (stn, *wos)).fetchall()
    except Exception:
        return {}
    out = {}
    for wo, av, lo, hi, rn, rd, sd in rows:
        if av is None:
            continue
        t = f"평균 {round(av)}℃(최저 {round(lo)}℃·최고 {round(hi)}℃)" if (lo is not None and hi is not None) else f"평균 {round(av)}℃"
        if rn is not None and rn >= 1:
            t += f", {'비·눈' if av < 3 else '비'} {round(rn)}mm" + (f"(1mm 넘게 온 날 {int(rd)}일)" if rd else "")
        else:
            t += ", 비 거의 없음"
        if sd is not None and sd > 0:
            t += f", 쌓인 눈 최대 {sd:g}cm"
        out[wo] = t
    return out


WEATHER_RULE = "날씨는 관측 사실을 전하고, 이동이 늘거나 준 영향이 있었을 가능성은 근거(지난 해 비슷한 날씨 때의 반응)와 한계를 밝혀 말해도 되지만 원인으로 확정하지 말 것(같은 시기 명절·행사가 겹쳤는지 먼저 볼 것)."
