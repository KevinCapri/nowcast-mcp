"""문화체육관광부 '연도별 지역축제 개최 계획' 엑셀을 하나의 축제 일정 표로 푼다.

파일은 data/raw/mcst/ 아래에 mcst_festival_{연도}.xlsx 이름으로 둔다.
- 2025년 이후: '조사표' 시트, 시작·종료가 연·월·일 칸으로 나뉨
- 2023·2024년: '세부현황' 시트, 기간이 글자("5.25~5.26")라 풀어 읽음
날짜는 연초에 조사한 '계획 일정'이라 실제 개최일과 다를 수 있음.
결과: data/fest_all.pkl
"""
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "raw" / "mcst"
OUT = ROOT / "data" / "fest_all.pkl"

# 연도별 시트 구조: (시트, 머리줄 건너뛰기, 칸 위치)
LAYOUT_NEW = {2026: 8, 2025: 7}                       # 조사표, 머리줄 수
LAYOUT_OLD = {2024: (6, 10, 14), 2023: (7, 13, 14)}   # 세부현황, (머리줄 수, 예산 칸, 전년 방문객 칸)


def clean(s):
    return re.sub(r"^\d+\.\s*", "", str(s)).strip()


def parse_period(t, y):
    """'5.25~5.26', '5.25(목)~28(일)', '5.25' 같은 글자 기간 → (시작, 종료)"""
    t = str(t).replace(" ", "").replace("\n", "")
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.?\(?[가-힣]?\)?[~\-∼～](\d{1,2})\.(\d{1,2})", t)
    if m:
        a, b, c, d_ = map(int, m.groups())
    else:
        m = re.search(r"(\d{1,2})\.(\d{1,2})\.?\(?[가-힣]?\)?[~\-∼～](\d{1,2})\.?\(?[가-힣]?\)?$", t)
        if m:
            a, b, d_ = map(int, m.groups())
            c = a
        else:
            m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.?(\([가-힣]\))?", t)
            if not m:
                return pd.NaT, pd.NaT
            a, b = int(m.group(1)), int(m.group(2))
            c, d_ = a, b
    try:
        return pd.Timestamp(y, a, b), pd.Timestamp(y if c >= a else y + 1, c, d_)
    except Exception:
        return pd.NaT, pd.NaT


def parse_new(y, path, hdr):
    d = pd.read_excel(path, sheet_name="조사표", header=None, skiprows=hdr)
    rows = []
    for _, r in d.iterrows():
        if pd.isna(r[4]):
            continue

        def ymd(a, b, c):
            try:
                return pd.Timestamp(int(r[a]), int(r[b]), int(r[c]))
            except Exception:
                return pd.NaT
        sgg = clean(r[3])
        if sgg in ("-", "nan", ""):
            sgg = clean(r[9])
        rows.append(dict(y=y, sido=clean(r[2]), sgg=sgg, name=str(r[4]).strip(), type=clean(r[5]),
                         s=ymd(11, 12, 13), e=ymd(14, 15, 16),
                         first=r[20] if y >= 2026 else r[21], budget=r[21] if y >= 2026 else r[22],
                         note=str(r[18])))
    return rows


def parse_old(y, path, hdr, bc, vc):
    d = pd.read_excel(path, sheet_name="세부현황", header=None, skiprows=hdr)
    rows = []
    for _, r in d.iterrows():
        if pd.isna(r[4]):
            continue
        s, e = parse_period(r[6], y)
        rows.append(dict(y=y, sido=clean(r[2]), sgg=clean(r[3]), name=str(r[4]).strip(), type=clean(r[5]),
                         s=s, e=e, first=r[9], budget=r[bc], vis_prev=r[vc], note=str(r[6])))
    return rows


def main():
    rows = []
    for y, hdr in LAYOUT_NEW.items():
        p = SRC / f"mcst_festival_{y}.xlsx"
        if p.exists():
            rows += parse_new(y, p, hdr)
    for y, (hdr, bc, vc) in LAYOUT_OLD.items():
        p = SRC / f"mcst_festival_{y}.xlsx"
        if p.exists():
            rows += parse_old(y, p, hdr, bc, vc)
    f = pd.DataFrame(rows)
    f["days"] = (f.e - f.s).dt.days + 1
    f.to_pickle(OUT)
    print(f.groupby("y").agg(n=("name", "size"), dated=("s", lambda x: x.notna().sum())))


if __name__ == "__main__":
    main()
