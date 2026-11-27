"""생활인구 참고 추정: 생활인구가 아직 공표되지 않은 달을 나우캐스트 관외 유입으로 가늠한다.

방법(2026-11 검증, 오차 중앙값 약 5.3%):
- 체류인구 = 생활인구 − 주민등록인구 − 등록외국인 (생활인구 정의)
- 비율 r = ln(체류인구 ÷ 그 달 나우캐스트 관외 유입 주 평균)
- 추정 비율 = '작년 같은 달 r'과 '공표된 최근 12개월 r 평균'의 평균.
  설·추석이 든 달이 작년과 어긋나면(명절이 달을 옮김) 12개월 평균만 쓴다.
- 추정 생활인구 = 관외 유입 × exp(추정 비율) + 주민등록인구 + 등록외국인
- 범위: 이 지역의 공표된 달들을 하나씩 '그 전까지만 알았다면' 추정해 본 오차의 중앙값(둘 중 한 번)·80%(열 중 여덟).
  되짚어 본 달이 6개 미만이면 전국 오차(5.3%)를 쓴다.
- 대상: 생활인구 공표 지역(인구감소·관심지역)만. 타지역 통근 비율 30% 이상이면 신뢰도 낮음.
"""
import datetime as dt
import math
from statistics import mean

import analysis as A
import feedback

ERR_HALF = 0.053      # 전국 과거 오차 중앙값. 지역 자체 채점 건수가 모자랄 때만 씀
MIN_BACKTEST = 6      # 지역별 범위를 쓰려면 되짚어 본 달이 이만큼 있어야 함


# 생활인구 공표 일정(국가데이터처 보도계획, 2026-11-14 확인): 분기마다 '○분기 생활인구 산정 결과'
# 2025년 3분기 → 2026-02-24, 4분기 → 05-28, 2026년 1분기 → 07-30, 2분기 → 10-29, 3분기 → 12-29 (분기 끝나고 3~5개월 뒤)
SCHEDULE_URL = "https://mods.go.kr/newsPln.es?mid=a10305000000&bid="
KNOWN_RELEASES = {"2024Q1": "2024-07-25", "2024Q2": "2024-10-31", "2024Q3": "2025-03-27",   # 지난 공표는 실제 공표일
                  "2025Q1": "2025-09-25", "2025Q2": "2025-12-09",                            # (보도, 2026-11-25 확인)
                  "2025Q3": "2026-02-24", "2025Q4": "2026-05-28", "2026Q1": "2026-07-30", "2026Q2": "2026-10-29",
                  "2026Q3": "2026-12-28"}   # 2026Q3: 보도계획 12월분 12-28(2026-11-25 확인, 전엔 12-29로 적혀 있었음)


ROUGH_RELEASES = {"2024Q4": "2025년 6월 하순"}     # 6-27 보도에 결과가 나옴, 정확한 날은 행안부 게시판으로 확인할 것


def _planned(db, key):
    """국가데이터처 보도계획에서 읽어 둔 공표일(pipeline/load_release_plan.py → release_plan 표). 없으면 None."""
    if db is None:
        return None
    try:
        r = db.execute("SELECT date FROM release_plan WHERE key=?", (key,)).fetchone()
        return r[0] if r else None
    except Exception:
        return None


def release_info(ym, db=None):
    """그 달이 든 분기의 생활인구가 언제쯤 공표되는지(보도계획 → 적어 둔 날짜 → 과거 간격으로 어림)."""
    y, m = int(ym[:4]), int(ym[4:])
    q = (m - 1) // 3 + 1
    key = f"{y}Q{q}"
    plan = _planned(db, key)
    if plan:
        done = plan <= dt.date.today().isoformat()
        return {"분기": f"{y}년 {q}분기", "공표일": plan,
                "근거": "실제 공표일(행정안전부·국가데이터처 보도)" if done else "국가데이터처 보도계획(바뀔 수 있음)"}
    if key in KNOWN_RELEASES:
        d = KNOWN_RELEASES[key]
        done = d <= dt.date.today().isoformat()
        return {"분기": f"{y}년 {q}분기", "공표일": d,
                "근거": "실제 공표일(행정안전부·국가데이터처 보도)" if done else "국가데이터처 공표 일정(바뀔 수 있음)"}
    if key in ROUGH_RELEASES:
        return {"분기": f"{y}년 {q}분기", "공표일": ROUGH_RELEASES[key],
                "근거": "실제 공표 시기(보도로 확인, 정확한 날짜는 확인 전)"}
    end = y * 12 + q * 3          # 분기 마지막 달(1부터 센 달 번호)
    a, b = end + 3, end + 5
    fmt = lambda t: f"{(t - 1) // 12}년 {(t - 1) % 12 + 1}월"
    return {"분기": f"{y}년 {q}분기", "공표 예상": f"{fmt(a)}–{fmt(b)} 말경",
            "근거": "지난 공표 간격(분기 끝나고 3–5개월 뒤)으로 어림. 확정 일정은 국가데이터처 보도계획에서 확인",
            "보도계획": SCHEDULE_URL}


def _codes(db, sido, sigungu, col):
    """지역 연결표의 코드. 시도 이름이 바뀐 지역(전남광주통합특별시·군위)은 옛 이름·새 이름 줄을 모두 합친다
    (2026-11-14 시험 E6: 강진을 '전라남도'로 찾으면 7월 이후가 비어 추정이 막혔음)."""
    out = []
    mem = _MEM.get("region_map")
    for sd, sg in A.variants(sido, sigungu):
        if mem is not None and col in mem["cols"]:
            v = mem["rows"].get((sd, sg), {}).get(col)
            r = (v,) if v is not None else None
        else:
            try:
                r = db.execute(f"SELECT {col} FROM region_map WHERE sido=? AND sigungu=?", (sd, sg)).fetchone()
            except Exception:
                r = None
        if r and r[0]:
            out += [c for c in str(r[0]).split(",") if c]
    return sorted(set(out))


# ── KOSIS 표·지역 연결표를 메모리에 (0.6.4, 2026-11-23) ─────────────────────────────────────
# kosis_* 표에는 색인이 없어(pipeline이 to_sql로 통째로 바꿔 씀) 느린 서버 저장소에서는 읽을 때마다 표 전체를 훑는다.
# 인구감소지역 순위(top=100)가 순위 줄마다 주민·외국인 수를 다시 읽어 37~49초, '지역 성격'도 한 번에 2~5초 걸렸음(B.Box 11-23).
# 서버 시작·자동 갱신 뒤(web.refresh_lists) 한 번에 읽어 표 → 지역 코드 → 항목 → {시점: 합계}로 두고, _by_period는 여기서 꺼낸다.
# 값은 DB에서 읽던 것과 같다(같은 SUM을 코드·항목별로 미리 해 둔 것). 아직 안 올라왔거나 모르는 조건이면 전처럼 DB에서 읽는다.
KOSIS_TABLES = ("kosis_life_pop", "kosis_reg_pop", "kosis_foreigners", "kosis_commute", "kosis_migration")
_MEM = {}


def load_memory(db):
    """KOSIS 표 5개와 지역 연결표를 한 번에 읽어 메모리에 둔다. 자료가 바뀌면 다시 부른다. 읽은 표 수를 돌려준다."""
    _fit_mem.clear()                 # 0.6.5: 새 자료로 미공표 지역 공식을 다시 맞추게
    new = {}
    for t in KOSIS_TABLES:
        try:
            agg = {}
            for c1, itm, prd, v in db.execute(f"SELECT C1, ITM_ID, PRD_DE, SUM(CAST(DT AS REAL)) FROM {t} "
                                              "GROUP BY C1, ITM_ID, PRD_DE"):
                if v is not None:
                    agg.setdefault(str(c1), {}).setdefault(str(itm), {})[str(prd)] = v
            new[t] = agg
        except Exception as e:          # 표가 없거나 모양이 다르면 그 표만 DB에서 읽게 둠
            print(f"[lifepop] {t} 메모리 올리기 건너뜀", repr(e), flush=True)
    try:
        cur = db.execute("SELECT * FROM region_map")
        cols = [d[0] for d in cur.description]
        rows = {}
        for r in cur.fetchall():
            d = dict(zip(cols, r))
            rows.setdefault((d.get("sido"), d.get("sigungu")), d)   # DB의 fetchone처럼 처음 줄
        new["region_map"] = {"cols": set(cols), "rows": rows}
    except Exception as e:
        print("[lifepop] region_map 메모리 올리기 건너뜀", repr(e), flush=True)
    _MEM.clear()
    _MEM.update(new)
    return len(new)


def _item_of(extra):
    """_by_period의 extra 조건 → 메모리에서 쓸 항목(None=모든 항목 합). 모르는 조건이면 False(DB에서 읽기)."""
    e = " ".join(extra.split())
    if not e:
        return None
    if e.startswith("AND ITM_ID='") and e.endswith("'") and e.count("'") == 2:
        return e[len("AND ITM_ID='"):-1]
    return False


def _by_period(db, table, codes, extra=""):
    if not codes:
        return {}
    mem, itm = _MEM.get(table), _item_of(extra)
    if mem is not None and itm is not False:
        out = {}
        for c1 in set(map(str, codes)):
            for it, ser in mem.get(c1, {}).items():
                if itm is None or it == itm:
                    for p, v in ser.items():
                        out[p] = out.get(p, 0.0) + v
        return out
    q = (f"SELECT PRD_DE, SUM(CAST(DT AS REAL)) FROM {table} WHERE C1 IN ({','.join('?' * len(codes))}) {extra} "
         "GROUP BY PRD_DE")
    return {str(p): v for p, v in db.execute(q, codes).fetchall() if v is not None}


def _nc_month(db, sido, sigungu):
    """월별(주차 라벨의 달) 관외 유입 주 평균. 옛 이름·새 이름 자료를 이어 붙인다(주차가 겹치면 하나만)."""
    seen = {}
    for _, w, v in A.series(db, sido, sigungu):
        if v:
            seen[w] = v
    m = {}
    for w, v in seen.items():
        m.setdefault(w[:4] + w[5:7], []).append(v)
    return {k: (mean(v), len(v)) for k, v in m.items()}


def _lunar_months(db):
    try:
        rows = db.execute("SELECT day FROM holidays WHERE name LIKE '%설날%' OR name LIKE '%추석%'").fetchall()
    except Exception:
        return set()
    return {str(d[0]).replace("-", "")[:6] for d in rows}


def _prev(ym, n):
    y, m = int(ym[:4]), int(ym[4:])
    t = y * 12 + m - 1 - n
    return f"{t // 12}{t % 12 + 1:02d}"


def _latest_le(d, key):
    ks = [k for k in d if k <= key]
    return d[max(ks)] if ks else None


def estimate(db, sido, sigungu, ym, force=False):
    """ym: 'YYYYMM'. force=True면 이미 공표된 달도 그 전 달까지만 알았다고 보고 추정한다(여러 달 보기의 with_estimate)."""
    lp_codes = _codes(db, sido, sigungu, "life_pop")
    if not lp_codes:
        return estimate_unpublished(db, sido, sigungu, ym)
    live = _by_period(db, "kosis_life_pop", lp_codes, "AND ITM_ID='T001'")
    jumin = _by_period(db, "kosis_reg_pop", _codes(db, sido, sigungu, "reg_pop"))
    frn = _by_period(db, "kosis_foreigners", _codes(db, sido, sigungu, "foreigners"))
    nc = _nc_month(db, sido, sigungu)
    if ym in live and not force:
        return {"지역": f"{sido} {sigungu}", "달": ym, "공표된 생활인구": round(live[ym]), "공표": release_info(ym, db),
                "안내": "이미 공표된 달이라 추정하지 않는다. 공표 값(국가데이터처 생활인구, KOSIS)을 그대로 쓸 것."}
    if ym not in nc or nc[ym][1] < 3:
        return {"error": "그 달의 나우캐스트 주 자료가 3주 미만이라 추정할 수 없다."}

    def ratio(m):
        if m not in live or m not in nc:
            return None
        j, f = _latest_le(jumin, m), _latest_le(frn, m[:4])
        if j is None or f is None:
            return None
        stay = live[m] - j - f
        return math.log(stay / nc[m][0]) if stay > 0 else None

    pub = sorted(m for m in live if m < ym)
    rs = {m: ratio(m) for m in pub}
    lunar = _lunar_months(db)

    def predict(target, known):
        """known(공표된 달 목록, 오름차순)만 보고 target 달의 비율을 정한다."""
        recent = [rs[m] for m in known[-12:] if rs.get(m) is not None]
        if len(recent) < 6:
            return None, None
        r12 = mean(recent)
        ly = _prev(target, 12)
        rly = rs.get(ly) if ly in known else None
        moved = (target in lunar) != (ly in lunar)
        if rly is not None and not moved:
            return (r12 + rly) / 2, "작년 같은 달 비율과 최근 12개월 평균 비율의 평균"
        return r12, "최근 12개월 평균 비율(" + ("설·추석이 작년과 다른 달이라" if moved else "작년 같은 달 공표값이 없어") + ")"

    r_est, how = predict(ym, pub)
    if r_est is None:
        return feedback.cant({"error": "공표된 달이 부족해 비율을 정할 수 없다."}, "공표 달 부족")

    # 이 지역 자체 채점: 공표된 각 달을 그 전 달까지만 보고 추정했을 때의 체류인구 오차(로그)
    errs = []
    for i, m in enumerate(pub):
        if rs.get(m) is None:
            continue
        r_hat, _ = predict(m, pub[:i])
        if r_hat is not None:
            errs.append(abs(r_hat - rs[m]))
    if len(errs) >= MIN_BACKTEST:
        errs.sort()
        q = lambda p: errs[min(len(errs) - 1, int(p * len(errs)))]
        e50, e80 = q(0.5), q(0.8)
        basis = f"이 지역의 과거 {len(errs)}개월 되짚어 보기"
    else:
        e50 = math.log(1 + ERR_HALF)
        e80 = e50 * 2
        basis = "전국 과거 오차(이 지역 자료가 모자람)"

    j, f = _latest_le(jumin, ym), _latest_le(frn, ym[:4])
    stay = nc[ym][0] * math.exp(r_est)
    est = stay + j + f
    rng = lambda e: [round(stay * math.exp(-e) + j + f, -2), round(stay * math.exp(e) + j + f, -2)]

    notes = []
    if ym[4:] == "05" or ym in lunar:
        notes.append("연휴가 많은 달(5월, 설·추석 달)은 오차가 컸다(2026년 5월 시험에서 오차 중앙값 약 15%). 범위를 넓게 볼 것.")
    cm = _by_period(db, "kosis_commute", _codes(db, sido, sigungu, "commute"), "AND ITM_ID='T30'")
    ce = _by_period(db, "kosis_commute", _codes(db, sido, sigungu, "commute"), "AND ITM_ID='T10'")
    if cm and ce:
        p = max(set(cm) & set(ce))
        share = cm[p] / ce[p] * 100 if ce[p] else None
        if share is not None and share >= 30:
            notes.append(f"타지역 통근 비율이 {share:.0f}%(30% 이상)라 신뢰도가 낮다.")
    return {
        "지역": f"{sido} {sigungu}",
        "달": ym,
        "참고 추정 생활인구(명)": round(est, -2),
        "참고 추정 체류인구(명, 비공식)": round(stay, -2),
        "과거 둘 중 한 번은 이 범위 안": rng(e50),
        "과거 열 번 중 여덟 번은 이 범위 안": rng(e80),
        "범위를 정한 근거": basis,
        "등급": "A(공표 지역의 아직 공표 전 달)",
        "추정 방법": "체류인구 = 이 지역의 과거 '체류인구 ÷ 나우캐스트 관외 유입' 비율 × 그 달 관외 유입. "
                   "생활인구 = 체류인구 + 주민등록인구 + 등록외국인",
        "추정에 쓴 비율": how,
        "그 달 나우캐스트 관외 유입 주 평균(건)": round(nc[ym][0]),
        "관외 유입 단위": "하루 평균 이동 건수(그 달 주차들의 하루 평균을 평균낸 값). 일주일 합계가 아님",
        "주민등록인구(명, 공표)": round(j),
        "등록외국인(명, 공표, 연)": round(f),
        "보고서용 표기 예": (f"{sido} {sigungu} {ym[:4]}년 {int(ym[4:])}월 생활인구 비공식 참고 추정치 "
                       f"약 {rng(e50)[0] / 10000:.1f}만–{rng(e50)[1] / 10000:.1f}만 명(과거 둘 중 한 번 범위, "
                       "빅데이터 Insight: 지역별 유입인구 계산, 공표 전 값)"),
        "가장 최근 공표 달": pub[-1],
        "이 달 생활인구 공표": release_info(ym, db),
        "주의": notes + [
            "이 값은 서비스가 계산한 비공식 참고 추정치다. 공표된 생활인구가 아니다. 공표되면 공표 값을 쓸 것"
            "('이 달 생활인구 공표'의 날짜를 함께 알려 줄 것).",
            "'신뢰구간'·'오차범위'라고 부르지 말고 '과거 둘 중 한 번'·'열 번 중 여덟 번은 이 범위 안'이라고 말할 것.",
            "주민등록인구·등록외국인은 공표 통계, 체류인구 몫만 추정이다. 체류인구를 말할 때도 '참고 추정'이라고 밝힐 것.",
            "일반적인 한계: 잠깐 들르는 이동이 많은 길목 지역은 높게 나올 수 있다(생활인구는 하루 3시간 이상 체류, "
            "나우캐스트는 30분). 이 지역이 그렇다는 뜻은 아니니 지역 사정을 지어내 붙이지 말 것.",
        ],
    }


# ---------- 등급 B: 생활인구가 공표되지 않은 지역 ----------
# 방법(등급 B): 공표 지역(통근 비율 30% 미만)의 최근 12개월
# log 체류인구 = a + b·log 관외 유입 + c·log 등록인구 를 함께 맞춘 공식으로 추정.
# 범위는 지역 하나씩 빼고 맞혀 본 오차(둘 중 한 번·열 번 중 여덟 번)로 서버가 매번 계산한다(검증 때 중앙값 약 12%).
COMMUTE_LIMIT = 30.0
_fit_cache = {}


def commute_share(db, sido, sigungu):
    """타지역 통근 비율(%, 지역별고용조사 최근 반기). 없으면 None."""
    codes = _codes(db, sido, sigungu, "commute")
    try:
        cm = _by_period(db, "kosis_commute", codes, "AND ITM_ID='T30'")
        ce = _by_period(db, "kosis_commute", codes, "AND ITM_ID='T10'")
    except Exception:
        return None
    common = set(cm) & set(ce)
    if not common:
        return None
    p = max(common)
    return round(cm[p] / ce[p] * 100, 1) if ce[p] else None


def _reg(db, sido, sigungu):
    jumin = _by_period(db, "kosis_reg_pop", _codes(db, sido, sigungu, "reg_pop"))
    frn = _by_period(db, "kosis_foreigners", _codes(db, sido, sigungu, "foreigners"))
    return jumin, frn


# ── 0.6.5 ⑤ 미공표 지역 공식(2026-11-24 Kevin 확정, 근거 insight-work 분석/2026-11-23_생활인구_기준 run2) ──────────
# 'dl'  관광공사 외지인 방법: log 체류 = a + b·log(그 달 하루 평균 외지인) + c·log(주민+외국인). 관광공사 값이 다 찬 달에만.
#       통근 30% 미만 오차 중앙 9.4 → 8.2%, 관광지에서 크게 나아짐(평창 31 → 13%).
# 'sub' 출근 몫 빼기: 관외 유입에서 출근 몫(통근 유입 인원 × 그 달 근무일 몫)을 빼고 공식, 출근하는 사람은 체류에 따로 더함.
#       관광공사 값이 아직 없는 최근 달·광역시(특별시) 구. 통근 30% 미만 9.4 → 8.3%, 원도심 13곳 20 → 14%.
# 'nc'  옛 방법(나우캐스트 관외 유입 그대로). 출근 인원 자료가 없을 때만.
# 광역시 구는 'dl'을 쓰지 않는다: 시험에서 원도심 구가 외지인 방법으로 나빠짐(부산 중구 33 → 58%, 동구 17 → 41%).
METHOD_NAME = {"dl": "관광공사 외지인 방법", "sub": "출근 몫 빼기 방법", "nc": "관외 유입 방법(옛 방법)"}


def _metro_gu(sido, sigungu):
    return (sido.endswith("광역시") or sido == "서울특별시") and sigungu.endswith("구")


def _commute_in(sido, sigungu):
    try:
        import daily
        return daily.commute(sido, sigungu)
    except Exception:
        return None


def _wd_ratio(db, ym):
    """그 달 날짜 중 근무일(주말·공휴일 뺌) 몫."""
    import calendar
    import datetime as dt
    try:
        import daily
        hol = daily.holidays(db)
    except Exception:
        hol = {}
    y, m = int(ym[:4]), int(ym[4:])
    n = calendar.monthrange(y, m)[1]
    w = sum(1 for d in range(1, n + 1)
            if dt.date(y, m, d).weekday() < 5 and dt.date(y, m, d) not in hol)
    return w / n


def _dl_month(db, sido, sigungu, months=None):
    """관광공사 외지인 하루 평균(명), 그 달 날짜가 다 찬 달만. {YYYYMM: 값}
    months(YYYYMM 목록)를 주면 그 기간만 읽는다(0.6.5 배포 뒤: 2018년부터 통째로 읽어 첫 추정이 4분 넘게 걸렸음)."""
    import calendar
    codes = _codes(db, sido, sigungu, "datalab")
    if not codes:
        return {}
    try:
        q = (f"SELECT day, SUM(touNum) FROM datalab_daily WHERE signguCode IN "
             f"({','.join('?' * len(codes))}) AND touDivCd=2")
        args = list(codes)
        if months:
            a, b = min(months), max(months)
            q += " AND day >= ? AND day <= ?"
            args += [f"{a[:4]}-{a[4:]}-01", f"{b[:4]}-{b[4:]}-31"]
        agg = {}
        for day, v in db.execute(q + " GROUP BY day", args):
            d = str(day).replace("-", "")
            agg.setdefault(d[:6], []).append(v or 0)
    except Exception:
        return {}
    return {ym: sum(vs) / len(vs) for ym, vs in agg.items()
            if len(vs) == calendar.monthrange(int(ym[:4]), int(ym[4:]))[1]}


def _feat(method, db, sido, sigungu, ym, nc, dl, jf, stay=None):
    """(x들, y) — y는 학습 때만. 못 만들면 None."""
    if method == "dl":
        if ym not in dl or dl[ym] <= 0:
            return None
        x = [math.log(dl[ym]), math.log(jf)]
        return x, (math.log(stay) if stay is not None and stay > 0 else None)
    if ym not in nc or nc[ym][1] < 3:
        return None
    if method == "sub":
        cm = _commute_in(sido, sigungu)
        if not cm:
            return None
        vis = nc[ym][0] - cm * _wd_ratio(db, ym)
        if vis <= 0:
            return None
        y = None
        if stay is not None:
            if stay - cm <= 0:
                return None
            y = math.log(stay - cm)
        return [math.log(vis), math.log(jf)], y
    return [math.log(nc[ym][0]), math.log(jf)], (math.log(stay) if stay is not None and stay > 0 else None)


_FIT_TTL = 6 * 3600
_fit_mem = {}      # method → (시각, 결과). 공표 지역마다 표를 읽어 만드는 일이라(외지인 방법은 지역마다 관광공사 표) 6시간 재사용,
                   # 서버 시작·자동 갱신 뒤(load_memory) 비움


_fit_locks = {}


def _fit(db, method="nc", wait=True):
    """wait=False면 다른 요청이 만드는 중일 때 기다리지 않고 None(그 요청은 다음 방법으로)."""
    import threading
    import time
    hit = _fit_mem.get(method)
    if hit and time.time() - hit[0] < _FIT_TTL:
        return hit[1]
    lock = _fit_locks.setdefault(method, threading.Lock())
    if not lock.acquire(blocking=wait):
        return None
    try:
        hit = _fit_mem.get(method)
        if hit and time.time() - hit[0] < _FIT_TTL:
            return hit[1]
        out = _fit_build(db, method)
        _fit_mem[method] = (time.time(), out)
        return out
    finally:
        lock.release()


def warm_fits(db):
    """서버 시작·자동 갱신 뒤 미공표 지역 공식을 미리 맞춰 둠(첫 이용자가 기다리지 않게, 0.6.5)."""
    for m in ("dl", "sub", "nc"):
        try:
            _fit(db, m)
        except Exception as e:
            print("[lifepop] 공식 미리 맞추기 실패", m, repr(e), flush=True)


def _fit_build(db, method="nc"):
    """공표 지역으로 공식을 맞추고, 지역 하나씩 빼고 맞혀 오차 분포를 낸다(최신 공표 달이 바뀔 때만 다시).
    method: 'dl'·'sub'·'nc'(0.6.5, 위 설명)."""
    import numpy as np
    regions = db.execute("SELECT sido, sigungu FROM region_map WHERE life_pop IS NOT NULL AND life_pop<>''").fetchall()
    seen, rows = set(), []
    for sd, sg in regions:
        k = A.canon(sd, sg)
        if k in seen:
            continue
        seen.add(k)
        share = commute_share(db, sd, sg)
        if share is None or share >= COMMUTE_LIMIT:
            continue
        live = _by_period(db, "kosis_life_pop", _codes(db, sd, sg, "life_pop"), "AND ITM_ID='T001'")
        jumin, frn = _reg(db, sd, sg)
        nc = _nc_month(db, sd, sg)
        last12 = sorted(live)[-12:]
        dl = _dl_month(db, sd, sg, last12) if method == "dl" and last12 else {}
        for m in last12:
            j, f = _latest_le(jumin, m), _latest_le(frn, m[:4])
            if j is None or f is None:
                continue
            stay = live[m] - j - f
            if stay <= 0:
                continue
            ft = _feat(method, db, sd, sg, m, nc, dl, j + f, stay)
            if ft is None or ft[1] is None:
                continue
            rows.append((k, ft[1], ft[0][0], ft[0][1]))
    if len(seen) < 20 or len(rows) < 100:
        return None
    key = (method, len(rows), round(sum(r[1] for r in rows), 3))
    if key in _fit_cache:
        return _fit_cache[key]
    ks = np.array([r[0][0] + "|" + r[0][1] for r in rows])
    y = np.array([r[1] for r in rows])
    X = np.column_stack([np.ones(len(rows)), [r[2] for r in rows], [r[3] for r in rows]])
    coef = np.linalg.lstsq(X, y, rcond=None)[0]
    errs = []
    for k in set(ks):
        tr, te = ks != k, ks == k
        c = np.linalg.lstsq(X[tr], y[tr], rcond=None)[0]
        errs += list(np.abs(X[te] @ c - y[te]))
    errs.sort()
    q = lambda p: float(errs[min(len(errs) - 1, int(p * len(errs)))])
    out = {"coef": [float(x) for x in coef], "e50": q(0.5), "e80": q(0.8),
           "n_regions": int(len(set(ks))), "n_rows": len(rows)}
    if len(_fit_cache) > 5:
        _fit_cache.clear()
    _fit_cache[key] = out
    return out


# 0.6.5 ⑥: 지역별고용조사 시군 표에 없는 곳의 타지역 통근 비율(%)과 출처. 모두 30% 넘어 추정 안 함(등급 C) 이유를 바로잡음
SHARE_FALLBACK = {
    ("세종특별자치시", "세종특별자치시"): (32.3, "인구총조사 2020, 다른 시도로 출근"),   # 지역 목록의 이름
    ("세종특별자치시", "세종시"): (32.3, "인구총조사 2020, 다른 시도로 출근"),
    ("인천광역시", "제물포구"): (60.0, "옛 인천 동구 값(지역별고용조사, 2026-07 구 개편 전)"),
    ("인천광역시", "영종구"): (33.0, "옛 인천 중구 값(지역별고용조사, 2026-07 구 개편 전)"),
    ("인천광역시", "서해구"): (50.0, "옛 인천 서구 값(지역별고용조사, 2026-07 구 개편 전)"),
    ("인천광역시", "검단구"): (50.0, "옛 인천 서구 값(지역별고용조사, 2026-07 구 개편 전)"),
}


def estimate_unpublished(db, sido, sigungu, ym):
    base = {"지역": f"{sido} {sigungu}", "달": ym, "공표": "이 지역은 생활인구 공표 대상(인구감소·관심지역)이 아니다."}
    share, share_src = commute_share(db, sido, sigungu), "지역별고용조사(최근 반기)"
    if share is None:
        fb = next((v for (sd, sg), v in SHARE_FALLBACK.items() if A.canon(sd, sg) == A.canon(sido, sigungu)), None)
        if fb:
            share, share_src = fb
    if share is None:
        return feedback.cant({**base, "추정": "하지 않음",
                "이유": "타지역 통근 비율 자료가 없어 추정 공식을 써도 되는 지역인지 판단할 수 없다. 숫자를 지어내지 말 것."},
                "통근 비율 자료 없음")
    if share >= COMMUTE_LIMIT:
        out = {**base, "추정": "하지 않음(등급 C)", "타지역 통근 비율(%)": share, "통근 비율 출처": share_src,
                "이용자에게 할 말": f"{sigungu}{'은' if (ord(sigungu[-1]) - 0xAC00) % 28 else '는'} 날마다 다른 지역으로 출퇴근하는 사람이 많아요. 이동 자료는 같은 사람이 "
                                "출근할 때마다 따로 세서, 한 달에 한 번 세는 생활인구로 바꾸면 크게 빗나가요. "
                                "대신 바깥에서 들어온 이동이 평소보다 많았는지는 알려 드릴 수 있어요.",
                "이유": f"주민의 {share:.0f}%가 다른 지역으로 출근하는 지역이라 통근 이동이 방문과 섞인다. "
                       "시험한 대도시 구에서 오차가 23–28%(최대 115%)여서 추정치를 내지 않는다. "
                       "대신 외부 유입 규모와 평소 대비 변화(analyze_region_week)로 답할 것."}
        return feedback.cant(out, "통근 많아 추정 안 함")
    nc = _nc_month(db, sido, sigungu)
    if ym not in nc or nc[ym][1] < 3:
        return {"error": "그 달의 나우캐스트 주 자료가 3주 미만이라 추정할 수 없다(진행 중인 달은 추정하지 않음)."}
    jumin, frn = _reg(db, sido, sigungu)
    j, f = _latest_le(jumin, ym), _latest_le(frn, ym[:4])
    if j is None or f is None:
        return feedback.cant({"error": "주민등록인구나 등록외국인 자료가 없어 추정할 수 없다."}, "주민·외국인 자료 없음")
    # 0.6.5 ⑤: 관광공사 값이 다 찬 달(광역시 구 제외)은 외지인 방법, 아니면 출근 몫 빼기, 출근 인원 자료가 없으면 옛 방법
    dl = {} if _metro_gu(sido, sigungu) else _dl_month(db, sido, sigungu, [ym])
    order = (["dl"] if ym in dl else []) + ["sub", "nc"]
    method, F, ft = None, None, None
    for mth in order:
        ft = _feat(mth, db, sido, sigungu, ym, nc, dl, j + f)
        if ft is None:
            continue
        F = _fit(db, mth, wait=(mth != "dl"))     # 외지인 공식을 다른 요청이 만드는 중이면 기다리지 않고 출근 몫 빼기로
        if F:
            method = mth
            break
    if not F:
        return {"error": "추정 공식을 맞출 공표 지역 자료가 모자란다."}
    a, b, c = F["coef"]
    ls = a + b * ft[0][0] + c * ft[0][1]
    add = (_commute_in(sido, sigungu) or 0.0) if method == "sub" else 0.0     # 출근하는 사람은 따로 더함
    stay = math.exp(ls) + add
    rng = lambda e: [round(math.exp(ls - e) + add + j + f, -2), round(math.exp(ls + e) + add + j + f, -2)]
    notes = [
        "생활인구가 공표되지 않는 지역의 비공식 참고 추정치다. 실제 값이 한 번도 없어 맞았는지 확인할 수 없는 추정이다.",
        "공식은 대부분 인구감소지역인 공표 지역에서 배웠다. 인구가 늘거나 도시 성격이 강한 지역은 오차가 더 클 수 있다.",
        "관광 중심 지역은 방문 한 번당 머무는 방식이 달라 규모 오차가 30–40%까지 커진 적이 있다(평창·태안·보령·울릉 등).",
        "보고서에 '생활인구 ○만 명'으로 쓰지 말 것. 쓰려면 '비공식 참고 추정치'라고 밝히고 범위·계산 방법·오차를 함께 적을 것.",
    ]
    # #116 뒤 dl은 그 달만 읽어서(빈 dict), 옛 조건 `dl and ym not in dl`이 늘 거짓이 돼 이 줄이 빠졌음(11-25 TEST)
    if method != "dl" and not _metro_gu(sido, sigungu) and _codes(db, sido, sigungu, "datalab"):
        notes.insert(0, "관광공사 값이 나오면 더 정확한 방법으로 다시 계산되어 숫자가 달라질 수 있어요"
                        "(관광공사 값은 약 한 달 늦게 나옴).")
    try:
        r = db.execute("SELECT weekend_ratio FROM region_rhythm WHERE sido=? AND sigungu=?", (sido, sigungu)).fetchone()
        if r and r[0] and r[0] >= 1.5:
            notes.insert(2, f"주말 외지인이 평일의 {r[0]:.1f}배인 주말형(관광형) 지역이라 규모 오차가 클 수 있다.")
    except Exception:
        pass
    return {
        **base,
        "등급": "B(미공표 지역, 다른 지역에서 배운 공식)",
        "참고 추정 생활인구(명, 비공식)": round(stay + j + f, -2),
        "참고 추정 체류인구(명, 비공식)": round(stay, -2),
        "과거 둘 중 한 번은 이 범위 안": rng(F["e50"]),
        "과거 열 번 중 여덟 번은 이 범위 안": rng(F["e80"]),
        "범위를 정한 근거": f"공표 지역 {F['n_regions']}곳을 하나씩 빼고 맞혀 본 오차(같은 방법, 통근 비율 30% 미만 지역, 최근 12개월)",
        "추정 방법": METHOD_NAME[method] + ". " + {
            "dl": "log 체류인구 = a + b·log(그 달 하루 평균 외지인, 한국관광공사) + c·log(주민등록인구+등록외국인)",
            "sub": "log(체류인구 − 출근 인원) = a + b·log(그 달 관외 유입 − 출근 몫) + c·log(주민등록인구+등록외국인), "
                   "출근 인원은 인구총조사 2020 통근 유입, 출근 몫은 출근 인원 × 그 달 근무일 몫",
            "nc": "log 체류인구 = a + b·log(그 달 관외 유입 주 평균) + c·log(주민등록인구+등록외국인)",
        }[method] + f", a={a:.3f}, b={b:.3f}, c={c:.3f}. 생활인구 = 체류인구 + 주민등록인구 + 등록외국인",
        "쓴 방법": METHOD_NAME[method],
        "그 달 나우캐스트 관외 유입 주 평균(건)": round(nc[ym][0]),
        "관외 유입 단위": "하루 평균 이동 건수(그 달 주차들의 하루 평균을 평균낸 값). 일주일 합계가 아님",
        "주민등록인구(명, 공표)": round(j),
        "등록외국인(명, 공표, 연)": round(f),
        "타지역 통근 비율(%)": share,
        **({"그 달 하루 평균 외지인(명, 한국관광공사)": round(dl[ym])} if method == "dl" else {}),
        **({"출근 인원(명, 인구총조사 2020 통근 유입)": round(add)} if method == "sub" else {}),
        "보고서용 표기 예": (f"{sido} {sigungu} {ym[:4]}년 {int(ym[4:])}월 생활인구 비공식 참고 추정치 "
                       f"약 {rng(F['e50'])[0] / 10000:.1f}만–{rng(F['e50'])[1] / 10000:.1f}만 명"
                       "(공표되지 않는 지역, 다른 지역에서 배운 공식으로 계산해 맞았는지 확인할 수 없는 추정, 빅데이터 Insight: 지역별 유입인구 계산)"),
        "주의": notes,
    }


# ---------- 여러 달 한 번에(2026-11-25 Kevin 확정) ----------
MAX_MONTHS = 36
COMPARE_RULE = ("공표 달과 추정 달을 견줄 때는 한쪽은 공표 값, 한쪽은 추정 범위라고 함께 말할 것. "
                "추정 범위(둘 중 한 번)가 견주는 공표 값을 포함하면 늘었다·줄었다고 단정하지 말 것"
                "(예: 6월 공표 19.0만 vs 7월 추정 17–19만 → 늘었는지 줄었는지 말하기 어려움, 22–25만 → 늘었을 가능성이 큼). "
                "추정 달끼리 견줄 때도 두 범위가 겹치면 늘었다·줄었다고 말하지 말 것(예: 8월 17–19만 vs 9월 16–18만 → 말하기 어려움).")


def months_between(a, b):
    """'YYYYMM' a–b(포함) 달 목록."""
    ya, ma, yb, mb = int(a[:4]), int(a[4:]), int(b[:4]), int(b[4:])
    out = []
    t, end = ya * 12 + ma - 1, yb * 12 + mb - 1
    while t <= end:
        out.append(f"{t // 12}{t % 12 + 1:02d}")
        t += 1
    return out


def _dash(ym):
    return f"{ym[:4]}-{ym[4:]}"


def _when(info):
    return info.get("공표일") or info.get("공표 예상")


def estimate_range(db, sido, sigungu, a, b, with_estimate=False):
    """a–b('YYYYMM') 여러 달을 한 번에. 공통 안내는 맨 위 한 번, 달마다 짧은 줄 하나(왼쪽 공표 값, 오른쪽 추정 범위)."""
    months = months_between(a, b)
    head = {"지역": f"{sido} {sigungu}", "기간": f"{_dash(a)} – {_dash(b)}({len(months)}개월)"}
    published = bool(_codes(db, sido, sigungu, "life_pop"))
    lines, notes, basis, how, methods = [], [], set(), set(), {}
    for ym in months:
        r = estimate(db, sido, sigungu, ym)
        line = {"달": _dash(ym)}
        if "공표된 생활인구" in r:
            line["공표 값(명)"] = r["공표된 생활인구"]
            line["공표일"] = _when(r["공표"])
            if with_estimate:
                e = estimate(db, sido, sigungu, ym, force=True)
                if "참고 추정 생활인구(명)" in e:
                    line["추정 범위(명, 과거 둘 중 한 번)"] = e["과거 둘 중 한 번은 이 범위 안"]
                    line["추정값(명)"] = e["참고 추정 생활인구(명)"]
                    line["추정과 공표 차이(%)"] = round((e["참고 추정 생활인구(명)"] / r["공표된 생활인구"] - 1) * 100, 1)
                else:
                    line["추정"] = "못 함(" + str(e.get("error") or e.get("이유") or "자료 부족") + ")"
        elif "과거 둘 중 한 번은 이 범위 안" in r:
            line["추정 범위(명, 과거 둘 중 한 번)"] = r["과거 둘 중 한 번은 이 범위 안"]
            line["넓은 범위(명, 열 번 중 여덟 번)"] = r["과거 열 번 중 여덟 번은 이 범위 안"]
            if published:
                line["공표 예정"] = _when(r["이 달 생활인구 공표"])
                basis.add(r["범위를 정한 근거"])
                how.add(r["추정에 쓴 비율"])
                if any("연휴가 많은 달" in n for n in r["주의"]):
                    line["주의"] = "연휴가 많은 달이라 범위를 넓게 볼 것"
                notes.extend(n for n in r["주의"] if "연휴가 많은 달" not in n and n not in notes)
            else:
                line["쓴 방법"] = r["쓴 방법"]
                methods.setdefault(r["쓴 방법"], r)
                notes.extend(n for n in r["주의"] if n not in notes)
        elif not published and str(r.get("추정", "")).startswith("하지 않음"):
            # 등급 C·통근 자료 없음: 달마다 같으므로 한 번만 돌려줌(의견 보내기 줄 포함)
            return {**{k: v for k, v in r.items() if k != "달"}, "기간": head["기간"]}
        else:
            line["추정"] = "못 함(" + str(r.get("error", "자료 부족")) + ")"
        lines.append(line)
    out = dict(head)
    if published:
        out["등급"] = "A(공표 지역)"
        out["추정 방법"] = ("체류인구 = 이 지역의 과거 '체류인구 ÷ 나우캐스트 관외 유입' 비율 × 그 달 관외 유입. "
                         "생활인구 = 체류인구 + 주민등록인구 + 등록외국인")
        if how:
            out["추정에 쓴 비율"] = sorted(how)
        if basis:
            out["범위를 정한 근거"] = sorted(basis)
    else:
        out["공표"] = "이 지역은 생활인구 공표 대상(인구감소·관심지역)이 아니다."
        out["등급"] = "B(미공표 지역, 다른 지역에서 배운 공식)"
        out["맞혀 본 결과"] = [
            f"{m}: 생활인구가 공표된 지역 {_n_regions(F)}곳을 같은 방법으로 하나씩 빼고 맞혀 보니, "
            f"둘 중 한 번은 바깥에서 와 머문 사람(체류인구) 몫이 ±{_pct_from(F)}% 안이었어요."
            for m, F in methods.items()]
        out["추정 방법"] = sorted({F["추정 방법"] for F in methods.values()})
    out["달마다"] = lines
    out["견줄 때 지킬 것"] = COMPARE_RULE
    if with_estimate and published:
        out["추정과 공표 차이 읽는 법"] = ("공표된 달에 붙인 추정은 그 전 달까지만 알았다고 보고 같은 방법으로 낸 값이다. "
                                   "이 차이로 이 지역에서 추정이 얼마나 맞는지 직접 볼 수 있다.")
    out["주의"] = notes
    return out


def _n_regions(r):
    import re
    m = re.search(r"공표 지역 (\d+)곳", r["범위를 정한 근거"])
    return m.group(1) if m else "여러"


def _pct_from(r):
    """미공표 결과의 범위(둘 중 한 번)에서 체류인구 기준 ±%."""
    lo, hi = r["과거 둘 중 한 번은 이 범위 안"]
    add = r.get("출근 인원(명, 인구총조사 2020 통근 유입)", 0)       # 출근 인원은 공식 밖에서 더한 몫이라 뺌
    stay = r["참고 추정 체류인구(명, 비공식)"] - add
    base = r["참고 추정 생활인구(명, 비공식)"] - stay
    if stay <= 0:
        return None
    return round(((hi - base) / stay - 1) * 100)
