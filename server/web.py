"""안내 페이지(/)와 지역 조회 화면이 쓰는 JSON 주소(/api/...).

원칙(2026-11-14 결정):
- 숫자로 보여 주는 것은 정부가 공표한 값(주민등록인구·생활인구)뿐.
- 나우캐스트 흐름은 '참고'로, 눈금 없는 막대와 문장 판정으로만. 이동 건수·퍼센트·출렁임 배수는 내보내지 않는다.
- 원인은 단정하지 않는다. 그 주에 있었던 일(공휴일·날씨·축제 계획)은 판단 재료로만 붙인다.
- 판정은 MCP 도구(analyze_region_week)와 같은 계산(analysis.prepare/judge_week)을 쓴다.
"""
import datetime as dt
import json
import math
import os
import re
import threading
import time
from pathlib import Path
from statistics import median

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.routing import Route

import admin as ADM
import analysis as A
import feedback
import festival as F
import guard
import lifepop
import perf as PF
import baseball as BB
import safe
import bigevents as BE

STATIC = Path(__file__).with_name("static")
MAX_WEEKS = 56           # 지역 탭: 최대 12개월(웹 화면에서 막음) 넘는 요청만 자름
FEST_PAD = 2            # 축제 탭: 축제가 든 달 앞뒤로 보여 줄 달 수(휴대폰은 화면에서 1달씩 줄임)
_db = None


def _conn():
    global _db
    if _db is None:
        from server import db
        _db = db
    return _db


def _label(wo):
    y, m, n = wo // 1000, (wo // 10) % 100, wo % 10
    return f"{y}년 {m}월 {n}주"


def _short(wo):
    return f"{(wo // 10) % 100}월 {wo % 10}주"


def _monday(wo):
    """주차 번호 → 그 주 월요일(목요일이 속한 달, 2022년만 수요일)."""
    y, m, n = wo // 1000, (wo // 10) % 100, wo % 10
    k = 2 if y == 2022 else 3                       # 기준 요일(수=2, 목=3)
    d = dt.date(y, m, 7 * (n - 1) + 1)
    while d.weekday() != k:
        d += dt.timedelta(days=1)
    return d - dt.timedelta(days=k)


def _json(obj, status=200):
    return JSONResponse(obj, status_code=status, headers={"cache-control": "no-store"})


# ---------- 지역·주차 목록 ----------
_cache = {}


def _cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    v = fn()
    _cache[key] = (time.time(), v)
    return v


def _regions():
    rows = _conn().execute("SELECT sido, sigungu, MAX(week_order) FROM moves WHERE direction='유입' "
                           "AND category='시군구' GROUP BY sido, sigungu").fetchall()
    best = {}
    for sd, sg, last in rows:                       # 시도 이름이 바뀐 곳은 가장 최근 이름 하나로
        k = A.canon(sd, sg)
        if k not in best or last > best[k][2]:
            best[k] = (sd, sg, last)
    out = {}
    for sd, sg, _ in sorted(best.values()):
        out.setdefault(sd, []).append(sg)
    return out


def _weeks():
    rows = _conn().execute("SELECT DISTINCT week_order FROM moves WHERE direction='유입' AND category='시군구' "
                           "ORDER BY week_order DESC").fetchall()
    return [[w, _label(w)] for (w,) in rows]


# 지역 목록은 큰 표 전체를 훑어 처음 한 번 40초 넘게 걸린다(2026-11-15 확인).
# 그래서 서버가 켜질 때 미리 만들어 두고, 6시간이 지나면 이용자를 기다리게 하지 않고 뒤에서 다시 만든다.
_lists = {}
_lists_lock = threading.Lock()
REFRESH_SEC = 6 * 3600


_hol_days = {}     # 날짜 → 공휴일 이름(한국천문연구원 특일 정보 holidays 표)


def _load_hol_days():
    out = {}
    try:
        for day, name in _conn().execute("SELECT day, name FROM holidays"):
            d = dt.date.fromisoformat(day)
            out[d] = (out[d] + "," + name) if d in out else name
    except Exception:
        pass
    return out


def _holiday_detail(wo):
    """연휴가 든 주의 '설 연휴 3일, 징검다리 1일 더하면 최대 4일' 문구. 주말과 붙은 쉬는 날을 이어 세고,
    공휴일·주말 사이에 하루씩 낀 평일(징검다리)은 따로 센다(이틀 연속으로 낀 평일은 세지 않음)."""
    H = _hol_days or _load_hol_days()
    mon = _monday(wo)
    off = lambda d: d in H or d.weekday() >= 5
    days = [mon + dt.timedelta(days=i) for i in range(7) if (mon + dt.timedelta(days=i)) in H]
    if not days:
        return ""
    a = b = days[0]
    while off(a - dt.timedelta(days=1)):
        a -= dt.timedelta(days=1)
    while off(b + dt.timedelta(days=1)):
        b += dt.timedelta(days=1)
    base = (b - a).days + 1
    names = []
    d = a
    while d <= b:
        for n in H.get(d, "").split(","):
            n = n.strip()
            if n and n not in names:
                names.append(n)
        d += dt.timedelta(days=1)
    bridge, lo, hi = 0, a, b
    for step in (1, -1):                              # 뒤로, 앞으로 징검다리를 이어 봄
        edge = hi if step == 1 else lo
        while True:
            gap = edge + dt.timedelta(days=step)
            nxt = gap + dt.timedelta(days=step)
            if off(gap) or not off(nxt):
                break
            bridge += 1
            edge = nxt
            while off(edge + dt.timedelta(days=step)):
                edge += dt.timedelta(days=step)
        if step == 1:
            hi = edge
        else:
            lo = edge
    if any("설" in n for n in names):
        title = "설 연휴"
    elif any("추석" in n for n in names):
        title = "추석 연휴"
    else:
        main = [n for n in names if "대체" not in n and "임시" not in n] or names
        title = "·".join(main[:2]) + " 연휴"
    txt = f"{title} {base}일"
    if bridge:
        txt += f", 징검다리 {bridge}일 더하면 최대 {(hi - lo).days + 1}일"
    return txt


_WD = "월화수목금토일"


def _holiday_tip(wo):
    """막대 설명창용 공휴일 한 줄. 주말과 이어 3일 이상 쉬면 '추석 연휴 7일(, 징검다리 …)',
    아니면 '현충일(목)(, 징검다리 1일 더하면 최대 4일)'."""
    H = _hol_days or _load_hol_days()
    mon = _monday(wo)
    days = [mon + dt.timedelta(days=i) for i in range(7) if (mon + dt.timedelta(days=i)) in H]
    if not days:
        return ""
    off = lambda d: d in H or d.weekday() >= 5
    a = b = days[0]
    while off(a - dt.timedelta(days=1)):
        a -= dt.timedelta(days=1)
    while off(b + dt.timedelta(days=1)):
        b += dt.timedelta(days=1)
    if (b - a).days + 1 >= 3:
        return _holiday_detail(wo)
    names = []
    for d in days:
        for n in H.get(d, "").split(","):
            n = n.strip()
            if n and n not in [x[0] for x in names]:
                names.append((n, d))
    txt = ", ".join(f"{n}({_WD[d.weekday()]})" for n, d in names[:2])
    full = _holiday_detail(wo)                        # 징검다리 계산은 같은 함수로
    i = full.find(", 징검다리")
    return txt + (full[i:] if i >= 0 else "")


def refresh_lists():
    if not _lists_lock.acquire(blocking=False):
        return
    try:
        _lists.update(regions=_regions(), weeks=_weeks(), at=time.time())
        print("[web] 지역·주차 목록 준비", flush=True)
        t = time.time()
        n = A.load_all(_conn())
        _hol_days.clear()
        _hol_days.update(_load_hol_days())
        print(f"[web] 지역별 시계열 {n}곳 메모리에 올림 ({time.time() - t:.1f}초)", flush=True)
        # find_unusual_regions의 가장 최근 주 전국 순위를 미리 계산해 둠(처음 부를 때 약 3.7초 걸리던 것).
        # rank_week는 (주차, 최신 주차)로 캐시하므로 새 자료가 들어오면 저절로 다시 계산된다.
        try:
            t = time.time()
            wo = _conn().execute("SELECT MAX(week_order) FROM moves WHERE direction='유입' AND category='시군구'").fetchone()[0]
            if wo:
                A.rank_week(_conn(), wo)
                print(f"[web] 최근 주 전국 순위 미리 계산 ({time.time() - t:.1f}초)", flush=True)
        except Exception as e:
            print("[web] 최근 주 전국 순위 미리 계산 실패", repr(e), flush=True)
        # 기간 순위(find_unusual_regions의 start_week·end_week)에 쓰는 전국×전체 주 판정 표도 미리 계산해 둠.
        # 최신 주차로 캐시하므로 새 자료가 들어오면 저절로 다시 계산된다.
        try:
            t = time.time()
            A.period_table(_conn())
            print(f"[web] 기간 순위 표 미리 계산 ({time.time() - t:.1f}초)", flush=True)
        except Exception as e:
            print("[web] 기간 순위 표 미리 계산 실패", repr(e), flush=True)
    except Exception as e:
        print("[web] 목록 만들기 실패", repr(e), flush=True)
    finally:
        _lists_lock.release()


def warm():
    """서버 시작·자동 갱신 뒤 부른다. 뒤에서 목록을 새로 만든다."""
    threading.Thread(target=refresh_lists, daemon=True).start()


def _list(key):
    if key not in _lists:
        refresh_lists()                              # 아직 없으면(시작 직후) 만들어질 때까지 기다림
        with _lists_lock:
            pass
    elif time.time() - _lists.get("at", 0) > REFRESH_SEC:
        warm()
    return _lists.get(key, {} if key == "regions" else [])


async def api_regions(request: Request):
    from starlette.concurrency import run_in_threadpool
    return _json(await run_in_threadpool(_list, "regions"))


async def api_weeks(request: Request):
    from starlette.concurrency import run_in_threadpool
    return _json(await run_in_threadpool(_list, "weeks"))


async def api_status(request: Request):
    try:
        rows = _conn().execute("SELECT name, unit, latest, fetched_ok, last_failed FROM source_status").fetchall()
    except Exception:
        rows = []
    return _json([{"자료": n, "단위": u, "최신": l, "받아 온 날": f, "최근 실패": bool(x)} for n, u, l, f, x in rows])


async def api_version(request: Request):
    """사이트 '연결 주소' 아래 '최신 버전' 표시용(2026-11-17). 서버 VERSION·TOOLS_CHANGED를 그대로 줌."""
    try:
        import server as S
        return _json({"버전": S.VERSION, "날짜": S.TOOLS_CHANGED})
    except Exception:
        return _json({}, 500)


# ---------- 공식 공표 통계 ----------
def _codes(sido, sigungu, col):
    out = []
    for sd, sg in A.variants(sido, sigungu):
        try:
            r = _conn().execute(f"SELECT {col} FROM region_map WHERE sido=? AND sigungu=?", (sd, sg)).fetchone()
        except Exception:
            return []
        if r and r[0]:
            out += [c for c in r[0].split(",") if c]
    return sorted(set(out))


def _by_month(table, codes, extra=""):
    if not codes:
        return {}
    try:
        return lifepop._by_period(_conn(), table, codes, extra)
    except Exception:
        return {}


def _ym_label(ym):
    return f"{ym[:4]}년 {int(ym[4:6])}월"


def _official(sido, sigungu, start_wo, end_wo):
    out = {}
    reg = _by_month("kosis_reg_pop", _codes(sido, sigungu, "reg_pop"))
    if reg:
        m = max(reg)
        out["주민등록인구"] = {"달": _ym_label(m), "값": round(reg[m]),
                         "출처": "행정안전부(mois.go.kr), 「주민등록인구통계」"}
    lp_codes = _codes(sido, sigungu, "life_pop")
    live = _by_month("kosis_life_pop", lp_codes, "AND ITM_ID='T001'")
    if not lp_codes or not live:
        out["생활인구"] = {"공표": False,
                       "설명": "이 지역은 인구감소지역·인구감소관심지역이 아니어서 생활인구가 공표되지 않습니다."}
        return out
    a, b = f"{start_wo // 1000}{(start_wo // 10) % 100:02d}", f"{end_wo // 1000}{(end_wo // 10) % 100:02d}"
    in_range = sorted(m for m in live if a <= m <= b)
    latest = max(live)
    show = in_range[-1] if in_range else latest
    peak = max(in_range, key=lambda m: live[m]) if in_range else None
    item = {"공표": True, "달": _ym_label(show), "값": round(live[show]),
            "출처": "국가데이터처(mods.go.kr), 「생활인구」", "최근 공표 달": _ym_label(latest)}
    if peak and peak != show:
        item["기간 중 가장 많은 달"] = {"달": _ym_label(peak), "값": round(live[peak])}
    if not in_range:                                 # 고른 기간 밖의 달을 보여 줄 때는 그 이유를 적는다
        span = _ym_label(a) + (f"~{int(b[4:])}월" if a[:4] == b[:4] else f"~{_ym_label(b)}") if a != b else _ym_label(a)
        item["안내"] = (f"조회 기간({span})은 아직 공표 전이라 가장 최근 공표 달을 보여 줍니다." if a > latest
                      else f"조회 기간({span})에는 공표 자료가 없어 가장 최근 공표 달을 보여 줍니다.")
    if b > latest:
        y, m = int(latest[:4]), int(latest[4:])
        nxt = f"{y + (m == 12)}{(m % 12) + 1:02d}"
        rel = lifepop.release_info(nxt)
        day = rel.get("공표일") or rel.get("공표 예상")
        if day:
            try:
                d = dt.date.fromisoformat(day)
                day = f"{d.year}년 {d.month}월 {d.day}일"
            except ValueError:
                pass
            q = re.search(r"(\d)분기", rel.get("분기", ""))
            if q:
                k = int(q.group(1))
                day += f" ({3 * k - 2}~{3 * k}월분)"
            item["다음 공표"] = day
    out["생활인구"] = item
    return out


# ---------- 그 주에 있었던 일 ----------
def _events(sido, sigungu, P, wos):
    hol = P["hol"]
    ev = {wo: [] for wo in wos}
    nhol = {wo: 0 for wo in wos}                      # ev 앞쪽 몇 개가 공휴일 이름인지
    for wo in wos:
        h = hol.get(wo)
        if h and h[2]:
            names = sorted(set(h[2].split(",")))
            if A.is_long_holiday(h) and any("설" in n for n in names):
                ev[wo].append("설 연휴")
            elif A.is_long_holiday(h) and any("추석" in n for n in names):
                ev[wo].append("추석 연휴")
            else:
                ev[wo] += names[:2]
            nhol[wo] = len(ev[wo])
    stn = None
    try:
        r = None
        for sd, sg in A.variants(sido, sigungu):
            r = _conn().execute("SELECT stn, stn_name FROM station_map WHERE sido=? AND sigungu=?", (sd, sg)).fetchone() or r
        if r:
            stn = r[1]
            q = (f"SELECT week_order, rn_sum, sd_max FROM weather_week WHERE stn=? AND week_order IN "
                 f"({','.join('?' * len(wos))})")
            for wo, rn, sd_ in _conn().execute(q, (r[0], *wos)):
                if sd_ is not None and sd_ >= 5:
                    ev[wo].append(f"눈 {sd_:g}cm")
                if rn is not None and rn >= 80:
                    ev[wo].append(f"비 {round(rn)}mm")
    except Exception:
        pass
    c = A.canon(sido, sigungu)
    fest = [r for r in F.FEST if A.canon(r["sido"], r["sigungu"]) == c and r.get("s") and r.get("e")]
    fest_weeks = {}
    for wo in wos:
        mon = _monday(wo)
        sun = mon + dt.timedelta(days=6)
        names = []
        for r in fest:
            try:
                s, e = dt.date.fromisoformat(r["s"]), dt.date.fromisoformat(r["e"])
            except ValueError:
                continue
            nm = re.sub(r"^\s*(\d{4}\s*년?|제?\s*\d+\s*회)\s*", "", r["name"]).strip() or r["name"]
            if s <= sun and e >= mon and nm not in names:
                names.append(nm)
        if names:
            fest_weeks[wo] = names
            ev[wo] += names   # 다 넘김. 줄이는 건 화면에서
    # 대형 행사(전국체전·국제행사). 축제 뒤에 붙임
    try:
        a, z = _monday(wos[0]), _monday(wos[-1]) + dt.timedelta(days=6)
        for b in BE.in_range(sido, sigungu, a, z):
            for wo in wos:
                mon = _monday(wo)
                if b["시작"] <= (mon + dt.timedelta(days=6)).isoformat() and b["끝"] >= mon.isoformat():
                    ev[wo].append(b["이름"])
    except Exception:
        pass
    # 큰 공연장 공연(KOPIS). 축제 뒤에 붙임
    has_perf = False
    try:
        a, z = _monday(wos[0]), _monday(wos[-1]) + dt.timedelta(days=6)
        perfs = PF.in_range(_conn(), sido, sigungu, a, z)
        has_perf = _conn().execute("SELECT 1 FROM kopis_perf LIMIT 1").fetchone() is not None
        for wo in wos:
            mon = _monday(wo)
            sun = mon + dt.timedelta(days=6)
            for p in perfs:
                if p["시작"] <= sun.isoformat() and p["끝"] >= mon.isoformat() and p["이름"] not in ev[wo]:
                    ev[wo].append(p["이름"])
    except Exception:
        pass
    return ev, fest_weeks, stn, nhol, has_perf


# ---------- 조회 ----------
LEVEL = [("아주 많음", "up3"), ("조금 많음", "up1"), ("많음", "up2"),
         ("아주 적음", "down3"), ("조금 적음", "down1"), ("적음", "down2"),
         ("평소 수준", "normal"), ("판정 보류", "hold")]
WORD = {"up3": "평소보다 아주 많았습니다", "up2": "평소보다 많았습니다",
        "down3": "평소보다 아주 적었습니다", "down2": "평소보다 적었습니다"}


def _level(verdict):
    for k, v in LEVEL:
        if verdict.startswith(k):
            return v
    return "na"


def _iga(word):
    """받침에 따라 '이'/'가'."""
    c = word[-1:] if word else ""
    if "가" <= c <= "힣":
        return "이" if (ord(c) - 0xAC00) % 28 else "가"
    return "이(가)"


def _span(a, b):
    """'1월 2주~5주'처럼 같은 달이면 달을 한 번만."""
    return f"{a['label']}~{b['label'].split(' ')[1]}" if a["label"].split(" ")[0] == b["label"].split(" ")[0] \
        else f"{a['label']}~{b['label']}"


def _sentences(weeks, fest_weeks, inside_flat, area_word, key="level"):
    # 원자료 확인이 필요한 주는 요약에서 뺌(많고 적음을 말하지 않음). 앞뒤 주가 이어 붙지 않게 자리는 남김
    sus = [w for w in weeks if w.get("suspect")]
    if weeks and len(sus) == len(weeks):
        return ["이 기간은 모두 원자료 확인이 필요한 구간이라 요약하지 않습니다."]
    weeks = [dict(w, level="sus" if w.get("suspect") else w[key]) for w in weeks]
    out = []
    i = 0
    strong = lambda w: w["level"] in ("up2", "up3", "down2", "down3")
    sign = lambda w: w["level"][:2]
    while i < len(weeks):
        w = weeks[i]
        if not strong(w):
            i += 1
            continue
        j = i
        while j + 1 < len(weeks) and strong(weeks[j + 1]) and sign(weeks[j + 1]) == sign(w):
            j += 1
        run = weeks[i:j + 1]
        if len(run) == 1:
            s = f"{w['label']}는 {WORD[w['level']]}."
        else:
            much = "많았습니다" if sign(w) == "up" else "적었습니다"
            very = [x for x in run if x["level"].endswith("3")]
            if len(very) == len(run):
                s = f"{_span(run[0], run[-1])}, {len(run)}주 연속 평소보다 아주 {much}."
            else:
                s = f"{_span(run[0], run[-1])}, {len(run)}주 연속 평소보다 {much}."
                if very:
                    s += f" 그중 {'·'.join(x['label'] for x in very)}는 아주 {much}."
        names = []
        for x in run:
            for n in fest_weeks.get(x["wo"], []):
                if n not in names:
                    names.append(n)
        if names:
            txt = ", ".join(names[:3]) + (" 등" if len(names) > 3 else "")
            s += f" 같은 기간 문체부 축제 개최 계획에 {txt}{_iga(txt)} 있었습니다."
        hol = [x for x in run if x["long_holiday"]]
        if hol:
            s += " 연휴가 낀 주는 연휴 때문에 늘거나 줄었을 수 있습니다."
        out.append(s)
        i = j + 1
    if not out:
        out.append("조회한 기간에 평소보다 많이 늘어나거나 줄어든 주는 없었습니다.")
    if sus:
        out.append("원자료 확인이 필요한 주는 빼고 보았습니다.")
    for w in weeks:
        if w["long_holiday"] and not strong(w) and w["level"] != "sus":
            d = w.get("holiday_detail")
            out.append(f"{w['label']}는 연휴가 있는 주{'(' + d + ')' if d else ''}라 평소와 비교하기 어렵습니다.")
    if inside_flat:
        out.append(f"{area_word} 안 이동은 기간 내내 거의 그대로였습니다.")
    out.append("증가 또는 감소 이유는 이 자료만으로 알 수 없습니다. 그래프 아래 적힌 공휴일·축제·날씨를 함께 참고하세요.")
    return out


def _suspect_notes(sido, sigungu, weeks):
    """원자료 확인이 필요한 주를 이어진 묶음마다 한 문장으로(그래프 카드 맨 위에 보임)."""
    runs, cur = [], []
    for w in weeks:
        if w.get("suspect"):
            cur.append(w)
        elif cur:
            runs.append(cur); cur = []
    if cur:
        runs.append(cur)
    out = []
    tail = "행정구역 배정이나 집계 방식에 바뀐 점이 있는지 확인이 필요한 구간입니다. 막대 아래 빗금 줄로 표시했습니다."
    act = "이 기간의 증가·감소는 근거로 사용하지 마세요."
    for run in runs:
        a, b = run[0], run[-1]
        if len(run) == len(weeks):
            head = "조회한 기간 전체가"
        elif len(run) == 1:
            head = f"{a['full']}는"
        elif b is weeks[-1] and A.is_suspect(sido, sigungu, 2099125):
            head = f"{a['full']}부터는"
        elif a["wo"] // 10 == b["wo"] // 10:
            head = f"{a['full']}~{b['wo'] % 10}주는"
        else:
            head = f"{a['full']}~{b['full'] if a['wo'] // 1000 != b['wo'] // 1000 else b['label']}는"
        out.append([f"{head} {tail}", act])   # 둘째 문장은 사이트에서 굵게
    return out


def _area_word(sigungu):
    last = sigungu.split(" ")[-1][-1:]
    return {"군": "군", "구": "구", "시": "시"}.get(last, "지역")


def view(sido, sigungu, start_wo, end_wo):
    db = _conn()
    if start_wo > end_wo:
        start_wo, end_wo = end_wo, start_wo
    P = A.prepare(db, sido, sigungu)
    if not P:
        return {"error": "지역을 찾지 못했습니다."}
    wos = [wo for wo in sorted(P["me"]) if start_wo <= wo <= end_wo]
    if not wos:
        return {"error": "그 기간 자료가 없습니다."}
    cut = len(wos) > MAX_WEEKS
    wos = wos[-MAX_WEEKS:]
    js = {wo: A.judge_week(P, wo) for wo in wos}
    opp = []
    ev, fest_weeks, stn, nhol, has_perf = _events(sido, sigungu, P, wos)
    weeks = []
    for wo in wos:
        j = js[wo]
        lvl_adj = _level(j["verdict"]) if j else "na"
        lvl = _level(j["verdict_plain"]) if j else "na"
        r = j["ratio_plain"] if (j and j["ratio_plain"] is not None) else 0
        ra = j["ratio"] if (j and j["ratio"] is not None) else 0
        h = P["hol"].get(wo)
        mon = _monday(wo)
        sun = mon + dt.timedelta(days=6)
        if j and j.get("opposite"):
            opp.append(_short(wo))
        lh = A.is_long_holiday(h)
        weeks.append({"wo": wo, "label": _short(wo), "full": _label(wo), "level": lvl, "level_adj": lvl_adj,
                      "dates": f"{mon.month}.{mon.day}~{sun.month}.{sun.day}",
                      "dir": 1 if r > 0 else (-1 if r < 0 else 0),
                      "dir_adj": 1 if ra > 0 else (-1 if ra < 0 else 0),
                      "long_holiday": lh, "holiday_detail": _holiday_detail(wo) if lh else "",
                      "events": ev[wo],
                      "games": safe.call("프로야구(사이트)", BB.week, sido, sigungu, mon, default=([], 0))[0],
                      "hol_tip": _holiday_tip(wo) if nhol[wo] else "",
                      "ev_other": ev[wo][nhol[wo]:],
                      "suspect": A.is_suspect(sido, sigungu, wo)})

    # 관내(지역 안 이동)가 거의 그대로였나: 기간 중앙값에서 ±10% 안
    inside_flat = False
    try:
        vals = []
        for sd, sg in A.variants(sido, sigungu):
            vals += [v for (v,) in db.execute(
                "SELECT within FROM moves WHERE direction='유입' AND category='시군구' AND item=? AND sido=? "
                "AND week_order BETWEEN ? AND ?", (sg, sd, wos[0], wos[-1])) if v]
        if len(vals) >= 3:
            m = median(vals)
            inside_flat = max(abs(math.log(v / m)) for v in vals) < 0.10
    except Exception:
        pass

    notes = []
    if cut:
        notes.append(f"한 번에 {MAX_WEEKS}주까지 보여 줍니다. 앞쪽 주는 잘렸습니다.")
    rw = A.reform_week(sido, sigungu)
    if rw and wos[0] <= rw + 100 and rw <= wos[-1] + 100:
        notes.append(f"{_label(rw)}에 행정구역이 바뀌어 이 주부터 집계 방식이 달라졌습니다. "
                     "바뀐 뒤 두 달 정도 자료가 쌓일 때까지는 비교 결과를 보여 주지 않습니다.")
    if len(A.variants(sido, sigungu)) > 1:
        notes.append("시도 이름이 바뀐 지역이라 옛 이름 자료를 이어 붙였습니다.")
    notes_adj = []
    if opp:
        notes_adj.append(f"{', '.join(opp)}에는 비슷한 지역들이 이 지역과 반대로 크게 늘거나 줄었습니다. 휴가철처럼 "
                         "출퇴근 지역과 관광지가 반대로 움직이는 때에는 결과가 실제보다 크게 나올 수 있습니다.")
    srcs = ["공휴일(한국천문연구원 특일 정보)"]
    if stn:
        srcs.append(f"날씨(기상청 {stn} 관측소, 눈 5cm·비 80mm 이상만 표시)")
    srcs.append("축제(문화체육관광부 지역축제 개최 계획, 언론 보도)")
    srcs.append(BE.SOURCE)
    if has_perf:
        srcs.append(PF.SOURCE)
    if any(w["games"] for w in weeks):
        srcs.append(BB.SOURCE)
    try:
        r = db.execute("SELECT latest, fetched_ok FROM source_status WHERE name LIKE '나우캐스트%유입%'").fetchone()
        as_of = {"최신 주차": r[0], "받아 온 날": r[1]} if r else None
    except Exception:
        as_of = None
    return {
        "지역": f"{sido} {sigungu}", "기간": f"{_label(wos[0])} ~ {_label(wos[-1])}",
        "공식": _official(sido, sigungu, wos[0], wos[-1]),
        "weeks": weeks,
        "문장": _sentences(weeks, fest_weeks, inside_flat, _area_word(sigungu)),
        "문장_비교": _sentences(weeks, fest_weeks, inside_flat, _area_word(sigungu), "level_adj"),
        "비교 가능": bool(P["peers"]),
        "주의": notes, "주의_비교": notes_adj, "일정 출처": srcs, "자료 기준": as_of,
        "원자료 확인": _suspect_notes(sido, sigungu, weeks),
    }


async def api_view(request: Request):
    q = request.query_params
    sido, sigungu = q.get("sido", "")[:30], q.get("sigungu", "")[:30]
    try:
        a, b = int(q.get("start", "0")), int(q.get("end", "0"))
    except ValueError:
        return _json({"error": "기간을 다시 골라 주세요."}, 400)
    t0 = time.time()
    try:
        r = view(sido, sigungu, a, b)
    except Exception as e:
        print("[web] view 오류", repr(e), flush=True)
        r = {"error": "조회 중 오류가 났습니다. 잠시 뒤 다시 시도해 주세요."}
    guard.record([("web:view", json.dumps({"sido": sido, "sigungu": sigungu, "start": a, "end": b},
                                          ensure_ascii=False))], "error" not in r, (time.time() - t0) * 1000)
    return _json(r, 200 if "error" not in r else 404)


# ---------- 축제 기간 보기 ----------
WD = "월화수목금토일"


def _dlabel(d, year=True):
    return (f"{d.year}년 " if year else "") + f"{d.month}월 {d.day}일({WD[d.weekday()]})"


def _fest_name(name):
    return re.sub(r"^\s*(\d{4}\s*년?|제?\s*\d+\s*회)\s*", "", name).strip() or name


def _fest_index():
    """지역 → 축제(fid) → 해마다 (시작, 끝, 이름). 축제 기간 자료가 다 들어온 해만."""
    weeks = _list("weeks")
    last = weeks[0][0] if weeks else 0
    out = {}
    for r in F.FEST:
        try:
            s, e = dt.date.fromisoformat(r["s"]), dt.date.fromisoformat(r["e"])
        except (ValueError, TypeError, KeyError):
            continue
        if e < s or F.week_order(e)[0] > last:
            continue
        f = out.setdefault(A.canon(r["sido"], r["sigungu"]), {}).setdefault(r["fid"], {"years": {}})
        f["years"][s.year] = (s, e, r["name"])
    for fests in out.values():
        for f in fests.values():
            f["name"] = _fest_name(f["years"][max(f["years"])][2])
    return out


def _fests():
    hit = _cache.get("fests")
    weeks = _list("weeks")
    key = weeks[0][0] if weeks else 0
    if not hit or hit[0] != key:
        hit = (key, _fest_index())
        _cache["fests"] = hit
    return hit[1]


def fest_regions():
    idx = _fests()
    out = {}
    for sd, sgs in _list("regions").items():
        has = [sg for sg in sgs if idx.get(A.canon(sd, sg))]
        if has:
            out[sd] = has
    return out


def fest_all():
    """축제별로 고르기: 자료가 들어온 축제 전체(지역 표시 포함)."""
    hit = _cache.get("fest_all")
    key = id(_fests())
    if hit and hit[0] == key:
        return hit[1]
    idx, out = _fests(), []
    for sd, sgs in _list("regions").items():
        for sg in sgs:
            for k, v in idx.get(A.canon(sd, sg), {}).items():
                out.append({"sido": sd, "sigungu": sg, "id": k, "name": v["name"], "years": sorted(v["years"])})
    _cache["fest_all"] = (key, out)
    return out


def fest_list(sido, sigungu):
    """시군구를 비우면 그 시도 전체 축제(시군구 가나다순, 그 안에서 축제 이름순)."""
    sgs = [sigungu] if sigungu else fest_regions().get(sido, [])
    out = []
    for sg in sgs:
        for k, v in _fests().get(A.canon(sido, sg), {}).items():
            out.append({"id": k, "name": v["name"], "sigungu": sg, "years": sorted(v["years"], reverse=True)})
    return sorted(out, key=lambda x: (x["sigungu"], x["name"]))


def fest_view(sido, sigungu, fid, year, s=None, e=None):
    f = _fests().get(A.canon(sido, sigungu), {}).get(fid)
    if not f or year not in f["years"]:
        return {"error": "그 축제 기록을 찾지 못했습니다."}
    ps, pe, name = f["years"][year]
    edited = False
    if s and e:
        if e < s or (e - s).days > 120:
            return {"error": "축제 날짜를 다시 확인해 주세요(끝 날이 시작 날보다 앞이거나 120일을 넘음)."}
        edited = (s, e) != (ps, pe)
    else:
        s, e = ps, pe
    # 축제가 든 달과 앞뒤 2달(두 달 이상에 걸치면 앞뒤 1달)을 달 단위로 통째로 보여 줌
    fmon, emon = F.week_order(s)[1], F.week_order(e)[1]
    mo = lambda wo: (wo // 1000) * 12 + (wo // 10) % 100
    m1, m2 = mo(F.week_order(s)[0]), mo(F.week_order(e)[0])
    pad = FEST_PAD if m1 == m2 else FEST_PAD - 1
    lo, hi = m1 - pad, m2 + pad
    wos = []
    for i in range(-40, (emon - fmon).days // 7 + 41):
        wo = F.week_order(fmon + dt.timedelta(days=7 * i + 3))[0]
        if lo <= mo(wo) <= hi and wo not in wos:
            wos.append(wo)
    fs, fe = F.week_order(s)[0], F.week_order(e)[0]
    weeks = _list("weeks")
    last = weeks[0][0] if weeks else 0
    have = [wo for wo in wos if wo <= last]
    if not have:
        return {"error": "그 축제 기간 자료가 아직 없습니다."}
    r = view(sido, sigungu, have[0], have[-1])
    if "error" in r:
        return r
    got = {w["wo"]: w for w in r["weeks"]}
    out = []
    for wo in wos:
        w = got.get(wo)
        if w is None:
            mon = _monday(wo)
            sun = mon + dt.timedelta(days=6)
            w = {"wo": wo, "label": _short(wo), "full": _label(wo), "level": "nodata",
                 "dates": f"{mon.month}.{mon.day}~{sun.month}.{sun.day}", "dir": 0, "dir_adj": 0,
                 "level_adj": "nodata", "long_holiday": False, "holiday_detail": "", "events": [], "hol_tip": "", "ev_other": [],
                 "suspect": False}
        w["fest"] = fs <= wo <= fe
        out.append(w)
    r["weeks"] = out
    r.pop("공식", None)
    if wos[-1] > last:
        nxt = F.week_order(_monday(last) + dt.timedelta(days=7))[0]
        r["주의"].insert(0, f"{_label(nxt)}부터는 자료가 아직 없습니다.")
    r["축제"] = {"이름": _fest_name(name), "시작": s.isoformat(), "끝": e.isoformat(),
               "계획 시작": ps.isoformat(), "계획 끝": pe.isoformat(), "고침": edited,
               "기간": f"{_dlabel(s)} ~ {_dlabel(e, s.year != e.year)}"}
    return r


async def api_fest_regions(request: Request):
    from starlette.concurrency import run_in_threadpool
    return _json(await run_in_threadpool(fest_regions))


async def api_fest_all(request: Request):
    from starlette.concurrency import run_in_threadpool
    return _json(await run_in_threadpool(fest_all))


async def api_fests(request: Request):
    q = request.query_params
    return _json(fest_list(q.get("sido", "")[:30], q.get("sigungu", "")[:30]))


async def api_fest_view(request: Request):
    q = request.query_params
    sido, sigungu, fid = q.get("sido", "")[:30], q.get("sigungu", "")[:30], q.get("id", "")[:120]
    try:
        year = int(q.get("year", "0"))
        s = dt.date.fromisoformat(q["s"]) if q.get("s") else None
        e = dt.date.fromisoformat(q["e"]) if q.get("e") else None
    except ValueError:
        return _json({"error": "날짜를 다시 확인해 주세요."}, 400)
    t0 = time.time()
    try:
        from starlette.concurrency import run_in_threadpool
        r = await run_in_threadpool(fest_view, sido, sigungu, fid, year, s, e)
    except Exception as ex:
        print("[web] fest_view 오류", repr(ex), flush=True)
        r = {"error": "조회 중 오류가 났습니다. 잠시 뒤 다시 시도해 주세요."}
    guard.record([("web:fest", json.dumps({"sido": sido, "sigungu": sigungu, "id": fid, "year": year},
                                          ensure_ascii=False))], "error" not in r, (time.time() - t0) * 1000)
    return _json(r, 200 if "error" not in r else 404)


POLITE = {  # feedback.py의 안내(AI용 반말)를 화면용 존댓말로
    "내용이 비어 있다.": "내용을 적어 주세요.",
    "1000자 이하로 줄여 달라.": "1,000자 이하로 줄여 주세요.",
    "같은 곳에서는 1분에 한 번만 보낼 수 있다. 잠시 뒤 다시 보내 달라.": "1분에 한 번만 보낼 수 있습니다. 잠시 뒤 다시 보내 주세요.",
    "오늘은 의견을 더 받을 수 없다. 내일 다시 보내 달라.": "오늘은 의견을 더 받을 수 없습니다. 내일 다시 보내 주세요.",
}


def _polite(r):
    if "error" in r:
        r = {"error": POLITE.get(r["error"], r["error"])}
    return r


async def api_feedback(request: Request):
    try:
        j = await request.json()
    except Exception:
        return _json({"error": "내용을 읽지 못했습니다."}, 400)
    kind = str(j.get("kind", "기타"))[:20]
    if kind == "작동 오류(조회 안 됨·화면 깨짐)":
        kind = "작동 오류"
    if kind not in ("값이 이상함", "설명이 틀림", "작동 오류", "자료 추가 요청", "제안", "기타"):
        kind = "기타"
    r = _polite(feedback.submit(str(j.get("message", "")), kind, str(j.get("region", ""))[:60],
                                str(j.get("period", ""))[:40], "web", str(j.get("link", ""))[:300]))
    r.pop("안내", None)
    if "error" not in r:
        r = {"받음": True}
    guard.record([("web:feedback", json.dumps({"kind": kind}, ensure_ascii=False))], "error" not in r, 0)
    return _json(r, 200 if "error" not in r else 400)


async def index(request: Request):
    return FileResponse(STATIC / "index.html", media_type="text/html; charset=utf-8",
                        headers={"cache-control": "public, max-age=300"})


SITE = "https://nowcast-mcp.azurewebsites.net/"


async def robots(request: Request):
    return PlainTextResponse(f"User-agent: *\nAllow: /\nDisallow: /api/\nSitemap: {SITE}sitemap.xml\n")


async def sitemap(request: Request):
    body = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f'<url><loc>{SITE}</loc><changefreq>weekly</changefreq></url></urlset>\n')
    return PlainTextResponse(body, media_type="application/xml")


async def og_image(request: Request):
    return FileResponse(STATIC / "og.png", media_type="image/png", headers={"cache-control": "public, max-age=86400"})


async def api_perf(request):
    """속도 모니터링(2026-11-21): 느린 이용자 요청(slow)과 측정 요청(monitor) 기록, 최근 단계별 시간(recent).
    앱 설정 MONITOR_KEY가 있고 머리글 X-Monitor-Key가 같을 때만 답한다(아니면 없는 주소처럼 404).
    since_slow·since_monitor: 이 시각 이후(ISO, 기본 30일 전·1일 전). GitHub Actions(insight-work perf.yml)가 쓴다.
    usage(2026-11-21): 최근 30일 도구별·연결한 곳별 콜·실패 요약(관리 화면 숫자, 지역·입력값 없음). Glama·Tool 합치기 판단용"""
    import hmac
    import timing
    key = os.environ.get("MONITOR_KEY", "")
    if not key or not hmac.compare_digest(request.headers.get("x-monitor-key", "").encode(), key.encode()):
        return PlainTextResponse("Not Found", status_code=404)
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
    q = request.query_params
    out = await run_in_threadpool(guard.perf, q.get("since_slow") or (now - dt.timedelta(days=30)).isoformat(),
                                  q.get("since_monitor") or (now - dt.timedelta(days=1)).isoformat())
    out["recent"] = timing.dump()
    out["usage"] = await run_in_threadpool(guard.usage_summary, (now - dt.timedelta(days=30)).isoformat())
    return JSONResponse(out, headers={"cache-control": "no-store"})


app = Starlette(routes=[
    Route("/", index),
    Route("/robots.txt", robots),
    Route("/sitemap.xml", sitemap),
    Route("/og.png", og_image),
    Route("/admin", ADM.admin, methods=["GET", "POST"]),
    Route("/admin/trend-check", ADM.trend_check),
    Route("/admin/site-check", ADM.site_check),
    Route("/admin/ktx-check", ADM.ktx_check),
    Route("/api/regions", api_regions),
    Route("/api/weeks", api_weeks),
    Route("/api/status", api_status),
    Route("/api/version", api_version),
    Route("/api/view", api_view),
    Route("/api/fest_regions", api_fest_regions),
    Route("/api/fests", api_fests),
    Route("/api/fest_all", api_fest_all),
    Route("/api/fest_view", api_fest_view),
    Route("/api/feedback", api_feedback, methods=["POST"]),
    Route("/api/perf", api_perf),
])


def handles(path: str) -> bool:
    return path in ("/", "/robots.txt", "/sitemap.xml", "/og.png", "/admin", "/admin/trend-check", "/admin/ktx-check", "/admin/site-check") or path.startswith("/api/")
