"""지역 일정 + 그 주 유입(get_region_calendar), 전국 벚꽃·단풍 날짜 + 명소 유입(get_bloom_foliage). 2026-11-15.

- 이용자가 '그때 무슨 일이 있었나', '올해 단풍 절정 언제였나'를 물으면 대개 '그래서 사람이 많이 왔나'로 이어진다.
  그래서 일정마다 그 주의 유입 판정(analyze_region_week와 같은 계산)을 함께 준다.
- 일정은 '같은 시기에 있었던 일'이다. 원인으로 단정하지 않는다.
- 일정 하나가 실패해도 나머지는 나온다(safe.call).
"""
import datetime as dt

import analysis as A
import baseball
import bigevents
import festival
import manual
import perf
import safe
import season
import facility
import tourism

MAX_DAYS = 371


def _date(s):
    s = (s or "").strip().replace(".", "-").replace("/", "-")
    if len(s) == 7:                      # YYYY-MM
        return dt.date.fromisoformat(s + "-01"), True
    return dt.date.fromisoformat(s), False


def _month_end(d):
    n = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return n - dt.timedelta(days=1)


def _label(mon):
    """이동 자료에 없는 주의 주차 이름(목요일이 속한 달로 셈. 2022년 수요일 기준은 지난 일이라 여기선 안 씀)."""
    thu = mon + dt.timedelta(days=3)
    return f"{thu.year}.{thu.month:02d}.{(thu.day - 1) // 7 + 1}주차"


def _inflow(P, wo):
    j = A.judge_week(P, wo) if (P and wo in P["me"]) else None
    if not j:
        return None
    out = {"판정": j["verdict"], "평소 출렁임의 몇 배": j["ratio"], "평소 대비 변화(%)": A._pct(j["dev"]),
           "관외 유입(일평균 건수)": round(j["v"])}
    if j.get("baseball") is not None:
        out["프로야구 홈경기 몫 추정(%)"] = A._pct(j["baseball"])
    return out


def _weather(db, sido, sigungu, wos):
    out, r = {}, None
    try:
        for sd, sg in A.variants(sido, sigungu):
            r = db.execute("SELECT stn, stn_name FROM station_map WHERE sido=? AND sigungu=?", (sd, sg)).fetchone() or r
    except Exception:      # 날씨 표가 없는 DB(첫 적재 전)
        return out, None
    if not r or not wos:
        return out, None
    q = f"SELECT week_order, rn_sum, sd_max FROM weather_week WHERE stn=? AND week_order IN ({','.join('?' * len(wos))})"
    for wo, rn, sd_ in db.execute(q, (r[0], *wos)):
        if sd_ is not None and sd_ >= 5:
            out.setdefault(wo, []).append(f"눈 {sd_:g}cm(그 주 최고 적설)")
        if rn is not None and rn >= 80:
            out.setdefault(wo, []).append(f"비 {round(rn)}mm(그 주 합계)")
    return out, r[1]


def region_calendar(db, sido, sigungu, start, end=""):
    try:
        a, is_month = _date(start)
        z = _date(end)[0] if end else (_month_end(a) if is_month else a + dt.timedelta(days=6))
        if end and len(end.strip()) == 7:
            z = _month_end(z)
    except Exception:
        return {"error": "날짜는 'YYYY-MM-DD' 또는 'YYYY-MM'(예: '2025-10', '2025-10-01')."}
    if z < a:
        a, z = z, a
    if (z - a).days > MAX_DAYS:
        return {"error": "한 번에 1년까지 볼 수 있다. 기간을 줄일 것."}
    P = A.prepare(db, sido, sigungu)
    if not P:
        return {"error": "지역을 찾지 못함. list_regions로 시도·시군구 이름을 확인할 것."}
    mon_wo = {dt.date.fromisoformat(A.week_dates(wo)[:10]): wo for wo in P["me"]}
    mons, m = [], a - dt.timedelta(days=a.weekday())
    while m <= z:
        mons.append(m)
        m += dt.timedelta(days=7)
    wos = [mon_wo[m] for m in mons if m in mon_wo]
    weather, stn = safe.call("날씨(일정)", _weather, db, sido, sigungu, wos, default=({}, None))
    try:
        hd_all = {d for (d,) in db.execute("SELECT day FROM holidays WHERE day BETWEEN ? AND ?",
                                           (mons[0].isoformat(), (mons[-1] + dt.timedelta(days=6)).isoformat()))}
    except Exception:
        hd_all = set()
    weeks, n_ev = [], 0
    for mon in mons:
        sun = mon + dt.timedelta(days=6)
        wo = mon_wo.get(mon)
        ev = []
        h = P["hol"].get(wo) if wo else None
        if h and h[2]:
            ev.append({"종류": "공휴일·연휴", "이름": ", ".join(sorted(set(h[2].split(",")))),
                       "출처": "한국천문연구원 특일 정보"})
        for f in safe.call("축제(일정)", festival.in_week, sido, sigungu, mon, hd_all, default=[]) or []:
            ev.append({"종류": "축제", "이름": f["축제"], "기간": f["계획 기간"], "출처": "문화체육관광부 지역축제 개최 계획"})
        for b in safe.call("대형 행사(일정)", bigevents.in_range, sido, sigungu, mon, sun, default=[]) or []:
            ev.append({"종류": f"대형 행사({b['종류']})", "이름": b["이름"], "기간": f"{b['시작']}~{b['끝']}",
                       "출처": "정부·주최 측 발표와 보도"})
        bl, bc = safe.call("프로야구(일정)", baseball.week, sido, sigungu, mon, default=([], 0))
        if bl:
            ev.append({"종류": "프로야구 홈경기", "경기": bl, "관중 합계(명)": bc, "출처": "한국야구위원회(KBO) 경기 기록"})
        for s in safe.call("벚꽃·단풍(일정)", season.in_week, sido, sigungu, mon, default=[]) or []:
            ev.append({"종류": s["종류"], "이름": s["명소"], "날짜": s["날짜"], "날짜 성격": "기상청 관측일(예보 아님)",
                       "출처": s["기준"]})
        for f in safe.call("대형 시설(일정)", facility.in_range, sido, sigungu, mon, sun, default=[]) or []:
            ev.append({"종류": "대형 시설 " + ("개점" if "개점" in f else "폐점"), "이름": f["시설"], "날짜": f.get("개점") or f.get("폐점"),
                       "유입 시험": f["유입 시험"], "출처": "회사 발표·보도(이 서비스가 확인), 원인 단정 금지"})
        for p in safe.call("공연(일정)", perf.in_range, db, sido, sigungu, mon, sun, default=[]) or []:
            ev.append({"종류": "큰 공연장 공연", **p, "출처": "KOPIS 공연예술통합전산망(발권 기준, 원인 단정 금지)"})
        for w in weather.get(wo, []):
            ev.append({"종류": "날씨", "이름": w, "출처": f"기상청 {stn} 관측소"})
        n_ev += bool(ev)
        inflow = safe.call("그 주 유입", _inflow, P, wo) if wo else "이동 자료에 없는 주"
        if isinstance(inflow, dict):
            ao = safe.call("개점 뒤 안내(일정)", facility.after_open_short, sido, sigungu, mon)
            if ao:
                inflow["개점 뒤 안내"] = ao
        weeks.append({"주차": P["me"][wo][0] if wo else _label(mon), "기간(월~일)": f"{mon}~{sun}", "일정": ev,
                      "그 주 유입": inflow})
    hist = safe.call("벚꽃·단풍 지난 기록(일정)", season.history, P, sido, sigungu,
                     skip_year=range(a.year, z.year + 1), default=[]) if season.spots(sido, sigungu) else []
    out = {"지역": f"{sido} {sigungu}", "기간": f"{a}~{z}", "일정 있는 주": n_ev, "주별": weeks,
           "자료 기준": {"이동 자료": (P["me"][max(P["me"])][0] if P["me"] else None),
                     "손으로 넣는 자료": [{k: s[k] for k in ("자료", "자료 기준(가장 늦은 날짜)", "받아서 넣은 날")}
                                    for s in safe.call("손 자료 상태(일정)", manual.status, default=[])]},
           "안내": ["일정은 '같은 시기에 있었던 일'이다. 유입 변화의 원인으로 단정하지 말 것.",
                  "'이동 자료에 없는 주'는 유입을 추정하거나 앞뒤 주로 미루어 '많이 왔을 것'이라고 결론 내지 말 것. 자료가 없다고만 말할 것.",
                  "벚꽃·단풍 날짜는 기상청 관측일이다. 웹 기사의 예보·예상 날짜보다 이 날짜를 먼저 말하고, 웹 내용은 출처를 나눠 덧붙일 것.",
                  "지난 기록은 조회한 해를 빼고 계산한다(get_bloom_foliage와 같은 숫자).",
                  "'그 주 유입'은 analyze_region_week와 같은 계산이다. 더 자세한 판정(비교군·평소 값)은 그 도구로 볼 것.",
                  "축제는 문체부 연초 계획 일정이라 실제와 다를 수 있다. 공연은 큰 공연장(3천 석 이상)만, 날씨는 큰 눈·비만 있다.",
                  "자료 기준 뒤의 일정·유입은 없다. 그 뒤를 물으면 '이 서비스에는 ○○까지 있다'고 말하고 원출처를 안내할 것."]}
    if hist:
        out["이 지역 지난 기록(벚꽃·단풍)"] = hist
    tm = safe.call("관광지 방문객(일정)", tourism.months, db, sido, sigungu, a, z)
    if tm:
        out["그 달 주요 유료관광지 방문객"] = tm
    if any(e["종류"].startswith("대형 시설") for w in weeks for e in w["일정"]):
        out["안내"].append("대형 시설 개점·폐점은 '유입 시험' 결과와 함께 말할 것. 시험에서 시군구 유입에 드러나지 않은 시설은 "
                         "그 주 유입 변화와 연결하지 말 것.")
    return out


KIND = {"벚꽃": [("벚나무 표준목", ("개화", "만발")), ("군락단지(봄꽃)", ("개화", "만발"))],
        "단풍": [("유명산 단풍", ("첫단풍", "단풍절정")), ("단풍나무 표준목", ("단풍시작", "단풍절정"))]}


def bloom_foliage(db, year, kind):
    kind = "벚꽃" if "벚" in kind or "꽃" in kind or "봄" in kind else "단풍" if ("단풍" in kind or "가을" in kind) else kind
    if kind not in KIND:
        return {"error": "kind는 '벚꽃' 또는 '단풍'."}
    D = season._dates()
    have_years = sorted({k[3] for k in D if k[0] in [g for g, _ in KIND[kind]]})
    if year not in have_years:
        st = [s for s in manual.status() if s["자료"].startswith("벚꽃")]
        return {"error": f"{year}년 {kind} 관측 자료가 이 서비스에 없다(있는 해: {have_years[0]}~{have_years[-1]}). "
                         f"자료 기준: {st[0]['자료 기준(가장 늦은 날짜)'] if st else '?'}. "
                         "최신·올해 현황과 예보는 기상청 날씨누리 '봄꽃·단풍'을 안내할 것."}
    # 관측 지점 → 연결된 명소 시군구
    link = {}
    for (sd, sg), v in list(season.SPOTS.items()) + [(k, x) for k, xs in season.EXTRA.items() for x in xs]:
        if v[0] == kind:
            link.setdefault((v[2], v[3]), []).append((sd, sg, v[1], v[4]))
    groups = []
    for grp, steps in KIND[kind]:
        rows = []
        for stn in sorted({k[1] for k in D if k[0] == grp and k[3] == year}):
            r = {"관측 지점": stn}
            if grp == "유명산 단풍":
                r["산"] = season.MOUNTAIN.get(stn)
            for s in steps:
                d = D.get((grp, stn, s, year))
                r[s] = d.isoformat() if d else None
            spots = []
            for sd, sg, name, step in link.get((grp, stn), []):
                d = D.get((grp, stn, step, year))
                if not d:
                    continue
                mon = d - dt.timedelta(days=d.weekday())
                P = safe.call("명소 유입", A.prepare, db, sd, sg)
                wo = next((w for w in (P or {}).get("me", {}) if A.week_dates(w)[:10] == mon.isoformat()), None)
                fest = [f"{x['축제']}({x['계획 기간']})" for x in
                        (safe.call("명소 같은 주 축제", festival.in_week, sd, sg, mon, default=[]) or [])]
                spots.append({"명소": name, "지역": f"{sd} {sg}", "기준 날짜": f"{step} {d}",
                              **({"같은 주 축제(문체부 계획)": fest,
                                  "축제 안내": "같은 주에 축제가 있어 벚꽃·단풍 몫과 축제 몫을 이 자료로 가를 수 없다."} if fest else {}),
                              "그 주 유입": (safe.call("명소 그 주 유입", _inflow, P, wo) if wo else "이동 자료에 없는 주"),
                              "이 지역 지난 기록": [h["지난 기록"] for h in (safe.call("명소 지난 기록", season.history, P, sd, sg,
                                                                             skip_year=year, default=[]) or [])
                                              if h["명소"] == name]})
            if spots:
                r["연결된 명소(유입)"] = spots
            rows.append(r)
        groups.append({"구분": grp, "지점": rows})
    st = [s for s in manual.status() if s["자료"].startswith("벚꽃")]
    return {"해": year, "종류": kind, "관측": groups,
            "자료 기준": st[0] if st else None,
            "안내": ["기상청 계절관측 관측일이다(예보 아님). 관측이 끝난 뒤 넣는 자료라 '자료 기준' 뒤의 일은 없다.",
                   "이 관측일이 있는 해·지점은 웹 기사의 예보·예상 날짜보다 이 날짜를 먼저 말할 것. 웹 내용은 출처를 나눠 덧붙일 것.",
                   "'이동 자료에 없는 주'는 유입을 추정하거나 앞뒤 주로 미루어 결론 내지 말 것. 자료가 없다고만 말할 것.",
                   "'연결된 명소'는 이 서비스가 유입 효과를 확인한 명소만 붙였다. 명소 날짜는 가까운 기상청 지점 날짜로 대신한 것이다.",
                   "'그 주 유입'은 analyze_region_week와 같은 계산이다. 유입 변화를 벚꽃·단풍 때문이라고 단정하지 말 것(같은 주 축제가 겹치는 경우가 많다).",
                   "유명산 단풍의 산 이름은 관측 지점 이름을 바탕으로 이 서비스가 붙였다."]}
