"""원자료 이상 자동 검사: 한 지역만 몇 주째 크게 튀면 운영자에게 메일만 보낸다(이용자 화면에는 아무것도 붙이지 않음).

2026-11-16 결정: 지난 자료(2023.03~2026.10)에 돌려 보니 경고 열 건 가운데 원자료 문제는 한두 건이고
나머지는 축제 날짜 변화·개강·가뭄처럼 실제로 있었던 일이었다. 그래서 자동으로는 메일만 보내고,
운영자가 원자료 문제로 확인한 건만 server/analysis.py의 SUSPECT에 넣어(PR) 이용자 결과에 경고를 붙인다.
시험 기록: 프로젝트 문서 claude/원자료_자동검사_시험결과_20261116.md

검사(관외 유입, 시군구·주):
1. 평소 대비 = log(그 주 / 직전 8주 중앙값)
2. 같은 시도 다른 시군구들의 평소 대비 중앙값을 뺌(연휴·계절처럼 다 같이 움직이는 것 제거)
3. 지난 해들 같은 철(±1주, 2021년과 2023년 이후, 2022년 제외) 값의 중앙값을 또 뺌(해마다 되풀이되는 축제·개강 제거)
4. 남은 값이 그 지역 평소 흔들림(2023.03 이후 MAD×1.4826)의 3배 넘고 10% 넘게 다른 주가
   같은 방향으로 3주 이상 이어지고, 그 묶음이 가장 최근 2주 안까지 이어지면 '새 건'
- 개편 지역(analysis.SUCCESSOR)은 개편 주 앞뒤 12주를 뺀다. 이미 SUSPECT에 있는 지역·기간은 뺀다.
- 한 묶음(지역·방향·시작 주)은 한 번만 메일. 결과는 표 raw_check에 남는다(관리 화면에서 봄).

쓰는 법: python pipeline/check_raw.py [--dry]   (--dry: 메일·기록 없이 출력만)
"""
import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from common import connect
from notify import send

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import analysis as A  # noqa: E402

RUN, ZMIN, PMIN = 3, 3.0, np.log(1.10)


def _label(wo):
    return f"{wo // 1000}.{(wo // 10) % 100:02d}.{wo % 10}주차"


def _key(sd, sg):
    c = A.canon(sd, sg)
    return c[0], c[1]


def detect(con):
    q = con.execute("SELECT sido, sigungu, week_order, outside FROM moves "
                    "WHERE direction='유입' AND category='시군구'").fetchall()
    df = pd.DataFrame(q, columns=["sido", "sigungu", "wo", "v"])
    df[["csd", "csg"]] = [_key(a, b) for a, b in zip(df.sido, df.sigungu)]
    p = df.pivot_table(index="wo", columns=["csd", "csg"], values="v", aggfunc="sum").sort_index()
    p = p.where(p > 0)
    base = p.shift(1).rolling(8, min_periods=6).median()
    lr = np.log(p / base)
    rel = lr.copy()
    for c in p.columns:
        others = [o for o in p.columns if o[0] == c[0] and o != c]
        rel[c] = lr[c] - lr[others].median(axis=1) if others else np.nan
    wos = list(p.index)
    ok_ref = [(2021000 <= w < 2022000) or w >= 2023000 for w in wos]
    seas = pd.DataFrame(np.nan, index=p.index, columns=p.columns)
    arr = rel.to_numpy()
    for i in range(len(wos)):
        refs = [i - 52 * y + k for y in (1, 2, 3, 4) for k in (-1, 0, 1)]
        refs = [j for j in refs if 0 <= j < i and ok_ref[j]]
        if len(refs) >= 3:
            seas.iloc[i] = np.nanmedian(arr[refs], axis=0)
    rs = rel - seas
    ref = rs.loc[rs.index >= 2023031]
    noise = ref.sub(ref.median()).abs().median() * 1.4826
    zz = rs / noise
    last = wos[-1]
    found = []
    for c in p.columns:
        s, r = zz[c].to_numpy(), rs[c].to_numpy()
        hit = (np.abs(s) >= ZMIN) & (np.abs(r) >= PMIN)
        i = len(wos) - 1
        # 가장 최근 2주 안에서 끝나는 묶음만 본다
        while i >= len(wos) - 2 and not hit[i]:
            i -= 1
        if i < len(wos) - 2:
            continue
        sg = np.sign(s[i])
        j = i
        while j - 1 >= 0 and hit[j - 1] and np.sign(s[j - 1]) == sg:
            j -= 1
        if i - j + 1 < RUN:
            continue
        others = [o for o in p.columns if o[0] == c[0] and o != c]
        found.append(dict(sido=c[0], sigungu=c[1], start=wos[j], end=wos[i], sign="+" if sg > 0 else "-",
                          peak=round(100 * (np.exp(np.nanmax(np.abs(r[j:i + 1]))) - 1)),
                          own=round(100 * (np.exp(lr[c].iloc[i]) - 1)),
                          sido_med=round(100 * (np.exp(lr[others].iloc[i].median()) - 1)) if others else None,
                          latest=last))
    return [f for f in found if not _skip(f)]


def _skip(f):
    if A.suspect(f["sido"], f["sigungu"], f["start"], f["end"]):
        return True
    for (sd, old), (wo, news, _) in A.SUCCESSOR.items():
        if A.canon(sd, old)[0] == f["sido"] and f["sigungu"] in [old] + list(news):
            y, m = wo // 1000, (wo // 10) % 100
            k = dt.date(y, m, 1)
            s = dt.date(f["start"] // 1000, (f["start"] // 10) % 100, 1)
            if abs((s - k).days) <= 84 + 31:
                return True
    return False


def main():
    dry = "--dry" in sys.argv
    con = connect()
    found = detect(con)
    if dry:
        for f in found:
            print(f)
        return 0
    con.execute("CREATE TABLE IF NOT EXISTS raw_check(sido TEXT, sigungu TEXT, start INT, last INT, sign TEXT, "
                "peak INT, first_seen TEXT, PRIMARY KEY(sido, sigungu, start, sign))")
    new = []
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat(timespec="seconds")
    for f in found:
        row = con.execute("SELECT 1 FROM raw_check WHERE sido=? AND sigungu=? AND start=? AND sign=?",
                          (f["sido"], f["sigungu"], f["start"], f["sign"])).fetchone()
        if row:
            con.execute("UPDATE raw_check SET last=?, peak=MAX(peak, ?) WHERE sido=? AND sigungu=? AND start=? AND sign=?",
                        (f["end"], f["peak"], f["sido"], f["sigungu"], f["start"], f["sign"]))
        else:
            con.execute("INSERT INTO raw_check VALUES (?,?,?,?,?,?,?)",
                        (f["sido"], f["sigungu"], f["start"], f["end"], f["sign"], f["peak"], today))
            new.append(f)
    con.commit()
    con.close()
    if new:
        names = ", ".join(f"{f['sido']} {f['sigungu']}" for f in new[:5]) + (" 등" if len(new) > 5 else "")
        body = "\n\n".join(
            f"- {f['sido']} {f['sigungu']}: {_label(f['start'])}~{_label(f['end'])} (최근 주까지 이어짐)\n"
            f"  같은 시도 다른 지역·지난 해 같은 철을 빼고도 평소보다 {'많음' if f['sign'] == '+' else '적음'}(최대 {f['peak']}% 차이)\n"
            f"  최근 주 이 지역 평소 대비 {f['own']:+d}%"
            + (f", 같은 시도 다른 지역 중앙값 {f['sido_med']:+d}%" if f['sido_med'] is not None else "")
            for f in new)
        body += ("\n\n이용자 화면에는 아무것도 붙지 않았습니다(판정 그대로).\n"
                 "할 일: 그 시기 축제·행사·사고·개점 같은 실제 일이 있었는지 보고, 원자료 문제(행정구역 배정·집계 방식)로 보이면 "
                 "server/analysis.py의 SUSPECT에 넣어 PR. 실제 일이면 그냥 두면 됨.\n"
                 "기록: 관리 화면 '원자료 자동 검사'")
        send(f"원자료 확인 필요할 수 있음 {len(new)}건: {names}", body)
    print(f"검사: 해당 {len(found)}건, 새 건 {len(new)}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
