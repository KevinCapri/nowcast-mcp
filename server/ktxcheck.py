"""KTX 역별 이용객과 관외 유입 견주기(관리자 전용 시험, 2026-11-16).

질문: KTX 역 이용객(한국철도공사 월별 역별 승하차, 천 명 단위, 2019.01~2022.12)이 늘고 줄 때
그 역이 있는 시군구, 그리고 역에서 내려 실제로 가는 곳(목적지) 시군구의 관외 유입도 함께 움직이나?
- 전국 바탕 변화(코로나 등)를 빼려고 '몫'끼리 견준다: 역 몫 = 그 역 ÷ 모든 역 합, 지역 몫 = 그 지역 관외 유입 ÷ 전국(48개월 다 있는 시군구 합)
- 월은 그 주 목요일이 든 달. 달마다 주별 일평균 관외 유입의 평균
- (1) 몫의 전월 대비 변화끼리 상관(47개월) (2) 몫의 전년 같은 달 대비 변화끼리 상관(2020~2022, 36개월)
- 특정성: 같은 역 몫 변화와 전국 모든 시군구 몫 변화의 상관을 구해, 역 시군구·목적지 시군구가 몇 위인지
- 원값(몫 아님) 전월 대비 상관도 참고로(코로나 때문에 어디나 높게 나옴)
자료: server/ktx_monthly.csv(공공데이터포털 '한국철도공사_KTX 월별 역별 승하차', 2019~2022만, 평균 1만 명 이상 역)
"""
import csv
import datetime as dt
import math
from collections import defaultdict
from pathlib import Path

import analysis

HERE = Path(__file__).resolve().parent

# 역 → (역이 있는 시군구, [목적지 시군구...]). 시도는 앞 두 글자로 맞춤(옛 이름 강원도·전라북도 등도 잡히게)
STATIONS = {
    "대전": (("대전", "동구"), [("대전", "중구")]),
    "서대전": (("대전", "중구"), []),
    "오송": (("충청", "청주시 흥덕구"), [("세종", "세종특별자치시")]),
    "천안아산": (("충청", "아산시"), [("충청", "천안시 서북구"), ("충청", "천안시 동남구")]),
    "동대구": (("대구", "동구"), [("대구", "중구")]),
    "부산": (("부산", "동구"), [("부산", "중구")]),
    "구포": (("부산", "북구"), []),
    "울산": (("울산", "울주군"), [("울산", "남구")]),
    "광주송정": (("광주", "광산구"), [("광주", "동구")]),
    "전주": (("전북", "전주시 덕진구"), [("전북", "전주시 완산구")]),
    "경주": (("경상", "경주시"), []),
    "포항": (("경상", "포항시 북구"), []),
    "목포": (("전라", "목포시"), []),
    "순천": (("전라", "순천시"), []),
    "여수엑스포": (("전라", "여수시"), []),
    "익산": (("전북", "익산시"), []),
    "정읍": (("전북", "정읍시"), []),
    "남원": (("전북", "남원시"), []),
    "나주": (("전라", "나주시"), []),
    "김천구미": (("경상", "김천시"), [("경상", "구미시")]),
    "밀양": (("경상", "밀양시"), []),
    "진주": (("경상", "진주시"), []),
    "마산": (("경상", "창원시 마산회원구"), []),
    "창원중앙": (("경상", "창원시 의창구"), [("경상", "창원시 성산구")]),
    "진영": (("경상", "김해시"), []),
    "공주": (("충청", "공주시"), []),
    "논산": (("충청", "논산시"), []),
    "계룡": (("충청", "계룡시"), []),
    "수원": (("경기", "수원시 팔달구"), []),
    "광명": (("경기", "광명시"), []),
    "행신": (("경기", "고양시 덕양구"), []),
}
# 시도 앞 두 글자 묶음(전라·경상·충청은 남북 모두)
_SIDO = {"충청": ("충청",), "경상": ("경상",), "전라": ("전라", "전남"), "전북": ("전북", "전라북")}


def _corr(xs, ys):
    p = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(p)
    if n < 8:
        return None
    mx, my = sum(x for x, _ in p) / n, sum(y for _, y in p) / n
    sxy = sum((x - mx) * (y - my) for x, y in p)
    sx = math.sqrt(sum((x - mx) ** 2 for x, _ in p))
    sy = math.sqrt(sum((y - my) ** 2 for _, y in p))
    return round(sxy / (sx * sy), 3) if sx and sy else None


def _months():
    return [f"{y}-{m:02d}" for y in range(2019, 2023) for m in range(1, 13)]


def _mom(s):
    return [math.log(s[i] / s[i - 1]) if s[i] and s[i - 1] else None for i in range(1, len(s))]


def _yoy(s):
    return [math.log(s[i] / s[i - 12]) if s[i] and s[i - 12] else None for i in range(12, len(s))]


def _match(want, key):
    sd, sg = key
    pre, name = want
    return sg == name and any(sd.startswith(p) for p in _SIDO.get(pre, (pre,)))


def run(db):
    M = _months()
    idx = {m: i for i, m in enumerate(M)}
    # KTX
    with open(HERE / "ktx_monthly.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    ktx = {c: [0.0] * 48 for c in rows[0] if c != "년월"}
    for r in rows:
        if r["년월"] in idx:
            for c in ktx:
                ktx[c][idx[r["년월"]]] = float(r[c] or 0)
    tot = [sum(ktx[c][i] for c in ktx) for i in range(48)]
    # 나우캐스트 월 평균 관외 유입
    acc = defaultdict(lambda: defaultdict(list))
    for sd, sg, wo, v in db.execute("SELECT sido, item, week_order, outside FROM moves WHERE direction='유입' "
                                    "AND category='시군구' AND outside IS NOT NULL AND week_order BETWEEN 2018120 AND 2023020"):
        thu = dt.date.fromisoformat(analysis.week_dates(wo)[:10]) + dt.timedelta(days=3)
        m = f"{thu.year}-{thu.month:02d}"
        if m in idx:
            acc[analysis.canon(sd, sg)][idx[m]].append(v)
    reg = {k: [sum(d[i]) / len(d[i]) if d.get(i) else None for i in range(48)] for k, d in acc.items()}
    full = {k: s for k, s in reg.items() if all(s)}
    nat = [sum(s[i] for s in full.values()) for i in range(48)]
    rsh = {k: [s[i] / nat[i] for i in range(48)] for k, s in full.items()}
    rmom = {k: _mom(s) for k, s in rsh.items()}

    def find(want):
        ks = [k for k in full if _match(want, k)]
        return ks[0] if ks else None

    out, summ = {}, []
    for st, (home, dests) in STATIONS.items():
        s = ktx.get(st)
        if not s or not all(s):
            out[st] = "역 자료가 48개월 다 있지 않음"
            continue
        ssh = [s[i] / tot[i] for i in range(48)]
        smom, syoy = _mom(ssh), _yoy(ssh)
        allc = sorted(((c, k) for k, v in rmom.items() if (c := _corr(smom, v)) is not None), reverse=True)
        rank = {k: i + 1 for i, (_, k) in enumerate(allc)}
        med = allc[len(allc) // 2][0] if allc else None
        res = {"역 하루 평균(천 명, 2019~2022 월 평균)": round(sum(s) / 48, 1),
               "전국 시군구와의 몫 변화 상관 중앙값": med, "견준 시군구 수": len(allc)}
        for role, want in [("역 시군구", home)] + [("목적지", d) for d in dests]:
            k = find(want)
            if not k:
                res[f"{role} {want[1]}"] = "지역 없음"
                continue
            row = {"몫 전월 대비 상관": _corr(smom, rmom[k]),
                   "몫 전년 같은 달 대비 상관": _corr(syoy, _yoy(rsh[k])),
                   "원값 전월 대비 상관(참고)": _corr(_mom(s), _mom(full[k])),
                   "전국 순위(몫 전월 대비)": f"{rank.get(k)}/{len(allc)}"}
            res[f"{role} {k[1]}"] = row
            summ.append((st, role, k[1], row["몫 전월 대비 상관"], rank.get(k)))
        res["상관 높은 시군구 5곳"] = [f"{k[0]} {k[1]} {c}" for c, k in allc[:5]]
        out[st] = res
    top10 = [x for x in summ if x[1] == "역 시군구" and x[4] and x[4] <= 10]
    return {
        "설명": __doc__.strip().splitlines()[0],
        "요약": {"역 시군구가 전국 10위 안": f"{len(top10)}/{sum(1 for x in summ if x[1] == '역 시군구')}",
               "역별(역, 구분, 시군구, 몫 전월 대비 상관, 전국 순위)": [list(x) for x in summ]},
        "역별": out,
    }
