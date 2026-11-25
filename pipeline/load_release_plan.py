"""생활인구 공표일을 국가데이터처 보도계획에서 읽어 둔다(2026-11-25 Kevin 확정).

- 보도계획(https://mods.go.kr/newsPln.es?mid=a10305000000&bid=)은 달마다 표가 올라오고, 올해 안의 달을 골라 부를 수 있다
  (oa_mm=MM). 이번 달과 다음 달(같은 해일 때만)을 읽어 '생활인구 산정 결과' 줄의 날짜를 release_plan 표에 쓴다.
- 쓰는 곳: lifepop.release_info(이용자에게 알리는 공표일), update.py(공표일부터 새 달이 들어올 때까지 KOSIS를 매일 받기).
- 이용 조건: 국가데이터처 누리집은 공공누리 기준, robots.txt에서 보도계획 페이지는 막혀 있지 않음(11-25 확인).
  하루 두 번 부르고 날짜만 쓴다.
- 페이지 모양이 바뀌어 표를 못 읽으면 실패로 끝낸다(update.py가 운영자 메일). 그동안 서비스는 적어 둔 날짜나 어림을 쓴다.
- 그달 표에 생활인구 줄이 없는 건 정상(분기마다 한 번).
"""
import datetime as dt
import html
import re
import sys

import requests

from common import connect

URL = "https://mods.go.kr/newsPln.es?mid=a10305000000&bid="
UA = {"User-Agent": "nowcast-mcp (+https://nowcast-mcp.azurewebsites.net)"}
KST = dt.timezone(dt.timedelta(hours=9))


def fetch_month(mm):
    r = requests.post(URL, data={"oa_mm": f"{mm:02d}", "mid": "a10305000000", "bid": "", "act": "list"},
                      headers=UA, timeout=30)
    r.raise_for_status()
    r.encoding = r.encoding or "utf-8"
    return r.text


def parse(text):
    """→ (표의 연, 표의 달, [(분기 키 'YYYYQn', 'YYYY-MM-DD', 제목)]). 표를 못 알아보면 ValueError."""
    m = re.search(r"(\d{4})년\s*(\d{1,2})월\s*보도계획", text)
    if not m:
        raise ValueError("보도계획 제목('YYYY년 M월 보도계획')을 찾지 못함 — 페이지 모양이 바뀌었을 수 있음")
    year, month = int(m.group(1)), int(m.group(2))
    rows = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", tr))).strip()
            for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.S | re.I)]
    dated = [r for r in rows if re.match(r"\d{2}\.\d{2}\.", r)]
    if not dated:
        raise ValueError(f"{year}년 {month}월 보도계획 표에 날짜 줄이 없음 — 페이지 모양이 바뀌었을 수 있음")
    out = []
    for r in dated:
        if "생활인구" not in r or "산정" not in r:
            continue
        d = re.match(r"(\d{2})\.(\d{2})\.", r)
        q = re.search(r"(\d{4})년\s*(\d)\s*(?:/\s*4\s*)?분기", r)
        if not q:
            continue
        mo, day = int(d.group(1)), int(d.group(2))
        out.append((f"{q.group(1)}Q{q.group(2)}", dt.date(year, mo, day).isoformat(), r))   # 표의 날짜는 그해 안
    return year, month, out


def main():
    today = dt.datetime.now(KST).date()
    months = [today.month] + ([today.month + 1] if today.month < 12 else [])
    found = []
    for mm in months:
        y, m, rows = parse(fetch_month(mm))
        if (y, m) != (today.year, mm):
            raise SystemExit(f"{today.year}년 {mm}월을 불렀는데 {y}년 {m}월 표가 옴 — 페이지 모양이 바뀌었을 수 있음")
        found += rows
    con = connect()
    with con:
        con.execute("CREATE TABLE IF NOT EXISTS release_plan(key TEXT PRIMARY KEY, date TEXT, title TEXT, fetched TEXT)")
        for k, d, t in found:
            con.execute("INSERT OR REPLACE INTO release_plan VALUES (?, ?, ?, ?)",
                        (k, d, t[:200], dt.datetime.now(KST).isoformat(timespec="seconds")))
    print(f"보도계획 {months}월: 생활인구 공표 {len(found)}건", *[f"{k} {d}" for k, d, _ in found], flush=True)


if __name__ == "__main__":
    sys.exit(main())
