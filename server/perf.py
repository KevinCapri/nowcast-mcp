"""큰 공연장(KOPIS 좌석 3천 석 이상)의 공연을 '그 주에 있었던 일' 판단 재료로 꺼낸다.

- 자료: pipeline/load_kopis.py가 쌓는 kopis_venue·kopis_perf 표(없으면 빈 목록).
- 2주 넘게 이어지는 공연(상설·장기 공연)은 그 주에 '바뀐 일'이 아니라서 뺀다.
- 관객 수는 쓰지 않는다. 원인으로 단정하지 않는다.
- 2026-11-28: 공연장 지역 칸은 처음 받을 때 주소에서 뽑아 그대로라, 행정구역이 바뀐 곳(인천 중구 → 영종구 등)은 새 이름으로
  물으면 안 걸렸다. 법정동 연계표(region_link.csv: 옛 시군구 + 법정동 → 지금 시군구, insight-work 분석/2026-11-28_법정동_연계표)로
  주소의 법정동·읍면을 이어 지금 시군구에서도 찾는다. 옛 이름으로 물으면 지금처럼 그대로 나온다(더하기만).
"""
import datetime as dt
import re
import csv
import os
import sqlite3

SOURCE = "공연(KOPIS 공연예술통합전산망, 좌석 3천 석 이상 공연장, 2주 이내 공연)"
CREDIT = "출처: (재)예술경영지원센터 공연예술통합전산망(www.kopis.or.kr)"   # KOPIS OPEN API 이용 조건의 출처 표시 문구(지키지 않으면 서비스 중단될 수 있음)
NOTE = (CREDIT + ". KOPIS 자료는 공연예술통합전산망 연계기관의 티켓판매시스템에서 발권된 분량을 기준으로 하므로 "
        "해당 공연의 전체 관객 수와 차이가 있을 수 있음(이 서비스는 관객 수는 쓰지 않음)")
MAX_DAYS = 14
_ALIAS = [{"강원특별자치도", "강원도"}, {"전북특별자치도", "전라북도"},
          {"광주광역시", "광주", "전남광주통합특별시"}, {"전라남도", "전남광주통합특별시"}]


def _sidos(sido):
    s = {sido}
    for g in _ALIAS:
        if sido in g:
            s |= g
    return s


LINK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "region_link.csv")
_link_cache = []
_DONG_IN_PAREN = re.compile(r"\(([^),]+?[동가로])[,)]")
_DONG_TOKEN = re.compile(r"\S+[읍면동가]")


def _link():
    """(옛 시도, 옛 시군구, 법정동) → (지금 시도, 지금 시군구), 그리고 지금 → 옛 (시도, 시군구) 묶음."""
    if not _link_cache:
        fwd, back = {}, {}
        try:
            with open(LINK_FILE, encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    now = (r["지금 시도"], r["지금 시군구"])
                    fwd[(r["시도"], r["옛 시군구"], r["법정동"])] = now
                    back.setdefault(now, set()).add((r["시도"], r["옛 시군구"]))
        except OSError:
            pass
        _link_cache.append((fwd, back))
    return _link_cache[0]


def _addr_dongs(adres):
    """주소에서 법정동·읍면 후보: 괄호 안 '(운서동)'을 먼저, 그다음 '향남읍' 같은 낱말."""
    a = adres or ""
    m = _DONG_IN_PAREN.search(a)
    toks = [t for t in a.split() if _DONG_TOKEN.fullmatch(t) and not t.endswith("시")]
    return ([m.group(1)] if m else []) + toks


def now_region(sido, sigungu, adres):
    """지역 칸이 옛 이름인 공연장의 지금 (시도, 시군구). 연계표에 없으면 None."""
    fwd, _ = _link()
    for d in _addr_dongs(adres):
        hit = fwd.get((sido, sigungu, d))
        if hit:
            return hit
    return None


def _clean(name):
    name = re.sub(r"\s*\[[^\]]*\]\s*$", "", name or "").strip()   # 끝의 '[서울 (앵콜) ]' 같은 꼬리
    return name


def in_range(con, sido, sigungu, a, z):
    """기간 [a, z](date)와 겹치는 큰 공연장 공연. [{이름, 공연장, 시작, 끝}]"""
    sd = sorted(_sidos(sido))
    try:
        rows = con.execute(
            f"SELECT p.name, p.venue, p.s, p.e FROM kopis_perf p JOIN kopis_venue v ON p.mt10id=v.mt10id "
            f"WHERE v.sigungu=? AND v.sido IN ({','.join('?' * len(sd))}) AND p.s<=? AND p.e>=? ORDER BY p.s",
            (sigungu, *sd, z.isoformat(), a.isoformat())).fetchall()
        # 지역 칸이 옛 이름인 공연장 중 연계표로 이 시군구에 이어지는 곳(인천 중구 운서동 → 영종구 등)
        _, back = _link()
        olds = [o for s_ in sd for o in back.get((s_, sigungu), ())]
        for osd, osg in olds:
            for name, venue, s, e, vsd, vsg, adres in con.execute(
                    "SELECT p.name, p.venue, p.s, p.e, v.sido, v.sigungu, v.adres FROM kopis_perf p "
                    "JOIN kopis_venue v ON p.mt10id=v.mt10id WHERE v.sido=? AND v.sigungu=? AND p.s<=? AND p.e>=?",
                    (osd, osg, z.isoformat(), a.isoformat())):
                hit = now_region(vsd, vsg, adres)
                if hit and hit[1] == sigungu and hit[0] in sd:
                    rows.append((name, venue, s, e))
        rows.sort(key=lambda r: r[2])
    except sqlite3.OperationalError:
        return []
    out, seen = [], set()
    for name, venue, s, e in rows:
        try:
            ds, de = dt.date.fromisoformat(s), dt.date.fromisoformat(e)
        except ValueError:
            continue
        if (de - ds).days >= MAX_DAYS:
            continue
        nm = _clean(name)
        if (nm, venue) in seen:
            continue
        seen.add((nm, venue))
        out.append({"이름": nm, "공연장": venue, "시작": s, "끝": e})
    return out
