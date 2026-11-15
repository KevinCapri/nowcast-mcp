"""손으로 넣는 자료 파일 검사(2026-11-15, claude/변경_관리_원칙.md 4번).

올리기 전에:  python pipeline/check_manual_data.py
- 형식(날짜·숫자·구장·단계)과 앞뒤(시작 ≤ 끝, 연도 = 날짜의 해)를 본다.
- KBO는 해마다 정규시즌 경기 수·관중 합계를 KBO 페이지 값과 대조한다(새 시즌을 넣으면 KBO_TOTAL에 추가).
- NOWCAST_DB가 있으면 시도·시군구 이름이 이동 자료에 있는지도 본다.
잘못된 줄이 하나라도 있으면 끝 코드 1.
"""
import csv
import datetime as dt
import os
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

S = Path(__file__).resolve().parent.parent / "server"
sys.path.insert(0, str(S))
from baseball import STADIUM  # noqa: E402
from analysis import variants  # noqa: E402

# KBO 일자별 관중 현황 페이지의 해마다 정규시즌 (경기 수, 관중 합계)
KBO_TOTAL = {2021: (720, 1228489), 2022: (720, 6076074), 2023: (720, 8100326),
             2024: (720, 10887705), 2025: (720, 12312519), 2026: (720, 12770551)}
STEPS = {"발아", "개화", "만발", "단풍시작", "단풍절정", "첫단풍"}
errs = []


def err(f, i, msg):
    errs.append(f"{f} {i}번째 줄: {msg}")


def d(x):
    return dt.date.fromisoformat(x)


def rows(name):
    with open(S / name, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def kbo():
    tot = defaultdict(lambda: [0, 0])
    for i, r in enumerate(rows("kbo_games.csv"), 2):
        try:
            day = d(r["날짜"])
            if r["구장"] not in STADIUM:
                raise ValueError(f"모르는 구장 {r['구장']}")
            n = int(r["관중수"])
            if n < 0:
                raise ValueError("관중 음수")
            if r["구분"] == "정규시즌":
                tot[day.year][0] += 1
                tot[day.year][1] += n
            elif not r["구분"].startswith("포스트시즌 "):
                raise ValueError(f"구분 {r['구분']}")
        except Exception as e:
            err("kbo_games.csv", i, e)
    for y, (g, n) in sorted(tot.items()):
        want = KBO_TOTAL.get(y)
        mark = "확인" if want == (g, n) else f"KBO 값 {want}와 다름" if want else "KBO_TOTAL에 이 해 값 없음"
        print(f"  KBO {y}: 정규시즌 {g}경기, 관중 {n:,}명 → {mark}")
        if want and want != (g, n):
            errs.append(f"kbo_games.csv {y}: 합계가 KBO 값과 다름")


def kma():
    for i, r in enumerate(rows("kma_season.csv"), 2):
        try:
            day = d(r["날짜"])
            if day.year != int(r["연도"]):
                raise ValueError("연도와 날짜의 해가 다름")
            if r["단계"] not in STEPS:
                raise ValueError(f"모르는 단계 {r['단계']}")
        except Exception as e:
            err("kma_season.csv", i, e)


def ranges(name, s, e, extra=None):
    out = []
    for i, r in enumerate(rows(name), 2):
        try:
            if r.get(s) or r.get(e):
                if d(r[s]) > d(r[e]):
                    raise ValueError("시작이 끝보다 늦음")
            if extra:
                extra(r)
            out.append(r)
        except Exception as ex:
            err(name, i, ex)
    return out


def regions(name, rs):
    p = os.environ.get("NOWCAST_DB")
    if not p or not Path(p).exists():
        return
    have = set(sqlite3.connect(p).execute("SELECT DISTINCT sido, sigungu FROM moves WHERE category='시군구'"))
    for r in rs:
        sido = "광주광역시" if r["sido"] == "광주" else r["sido"]     # 축제 목록은 광주를 '광주'로 적음
        if not any(v in have for v in variants(sido, r["sigungu"])):
            errs.append(f"{name}: 이동 자료에 없는 지역 {r['sido']} {r['sigungu']} ({r.get('name')})")


def main():
    print("자료 파일 검사")
    kbo()
    kma()
    ev = ranges("events.csv", "s", "e")
    fe = ranges("festivals.csv", "s", "e", extra=lambda r: int(r["y"]))
    regions("events.csv", ev)
    regions("festivals.csv", fe)
    if errs:
        print(f"잘못된 곳 {len(errs)}개:")
        for x in errs[:50]:
            print("  -", x)
        return 1
    print("이상 없음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
