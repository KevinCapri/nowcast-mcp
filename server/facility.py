"""대형 시설 개점·폐점(손 자료 facilities.csv, 2026-11-16).

- 시험(claude/시설목록_계획.md): 9곳 중 스타필드 수원만 시군구 관외 유입에 드러남(개점 주 4.6배, 뒤로 약 30% 계단).
- big='Y'인 곳(효과 확인)만 판정에 '개점 뒤 8주' 안내를 붙이고, 나머지는 일정에 시험 결과와 함께 판단 재료로만.
"""
import csv
import datetime as dt
from pathlib import Path

import analysis
import safe

FILE = Path(__file__).with_name("facilities.csv")
AFTER_DAYS = 56          # 평소 값(직전 8주)이 따라잡기까지
_ROWS = None


def _load():
    global _ROWS
    if _ROWS is None:
        out = []
        with open(FILE, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    r["_open"] = dt.date.fromisoformat(r["open"]) if r["open"] else None
                    r["_close"] = dt.date.fromisoformat(r["close"]) if r["close"] else None
                    r["_key"] = analysis.canon(r["sido"], r["sigungu"])
                    out.append(r)
                except Exception as e:
                    safe.bad_row(FILE.name, r, e)
        _ROWS = out
    return _ROWS


def in_range(sido, sigungu, a, b):
    """a~b 사이에 이 지역에서 문을 열거나 닫은 시설."""
    key = analysis.canon(sido, sigungu)
    out = []
    for r in _load():
        if r["_key"] != key:
            continue
        for kind, d in (("개점", r["_open"]), ("폐점", r["_close"])):
            if d and a <= d <= b:
                out.append({"시설": r["name"], "종류": r["kind"], kind: d.isoformat(),
                            "유입 시험": r["effect"] or "확인 전"})
    return out


def after_open_short(sido, sigungu, mon):
    """after_open의 짧은 안내(일정·전국 순위·해마다 비교의 주별 줄에 붙임)."""
    key = analysis.canon(sido, sigungu)
    end = mon + dt.timedelta(days=6)
    for r in _load():
        o = r["_open"]
        if r["_key"] == key and r.get("big") == "Y" and o and end - dt.timedelta(days=AFTER_DAYS) < o <= end:
            return (f"{r['name']} 개점({o.month}월 {o.day}일) 뒤 8주 안이다. 평소 값이 개점 전 수준이라 '많음'이 이어질 수 있다. "
                    "개점 때문이라고 단정하지 말 것.")
    return None


def after_open(sido, sigungu, mon):
    """효과가 확인된 큰 시설이 그 주 또는 그 전 8주 안에 문을 열었으면 안내."""
    key = analysis.canon(sido, sigungu)
    end = mon + dt.timedelta(days=6)
    for r in _load():
        o = r["_open"]
        if r["_key"] == key and r.get("big") == "Y" and o and end - dt.timedelta(days=AFTER_DAYS) < o <= end:
            return (f"같은 시기에 {r['name']} 개점({o.month}월 {o.day}일). 큰 시설이 문을 연 뒤에는 지역 유입 수준이 올라가 "
                    "몇 주 동안 '많음'이 이어질 수 있다(평소 값이 직전 8주라 따라잡는 중). 개점 때문이라고 단정하지는 말 것.")
    return None
