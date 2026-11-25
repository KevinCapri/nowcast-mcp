"""큰 공연장(KOPIS 좌석 3천 석 이상)의 공연을 '그 주에 있었던 일' 판단 재료로 꺼낸다.

- 자료: pipeline/load_kopis.py가 쌓는 kopis_venue·kopis_perf 표(없으면 빈 목록).
- 2주 넘게 이어지는 공연(상설·장기 공연)은 그 주에 '바뀐 일'이 아니라서 뺀다.
- 관객 수는 쓰지 않는다. 원인으로 단정하지 않는다.
"""
import datetime as dt
import re
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
