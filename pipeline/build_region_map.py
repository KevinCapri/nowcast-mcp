"""나우캐스트 시군구(시도 이름 + 시군구 이름)를 다른 자료의 지역 코드와 잇는 연결표(region_map)를 만든다.

표마다 지역을 적는 방식이 다르다(2026-11-14 확인):
- 나우캐스트 moves        : 시도 전체 이름 + 시군구 이름('수원시 장안구'처럼 일반구는 시 이름을 앞에 붙임)
- 주민등록 kosis_reg_pop  : 5자리 행정 코드(41111). 일반구 이름은 '장안구'만 → 상위 시 코드(41110)로 시 이름을 붙임
- 데이터랩 datalab_daily  : 5자리 코드. 강원·전북은 옛 코드(42·45)와 새 코드(51·52)가 기간에 따라 섞임
- 생활인구 kosis_life_pop : KOSIS 옛 코드(강원 32570), 이름은 '강원특별자치도 화천군'
- 통근 kosis_commute      : 4자리 코드(1101), 이름 '서울 종로구'. 일반구 없이 시 단위 → 일반구는 시 값을 씀(commute_city=1)
- 등록외국인 kosis_foreigners: KOSIS 자체 코드(AC149), 이름에 시도 없음 → 코드 순서로 시도를 정함

잇는 열쇠는 (짧은 시도 이름, 공백을 뺀 시군구 이름). 전남광주통합특별시는 광주 5개 구면 '광주', 나머지는 '전남'.
결과 표 region_map: 나우캐스트 지역 한 줄마다 각 자료의 코드(여러 개면 쉼표), 시 단위로 대신 이은 경우 표시.
마지막에 자료별로 못 이은 지역을 출력한다 → 이름 표기 차이를 찾아 ALIAS에 더하면 됨.

쓰는 법: python pipeline/build_region_map.py   (적재가 끝난 DB에서)
"""
import os
import re
import shutil
import sqlite3

import pandas as pd

from common import DB, connect

SIDO = {
    "서울": "서울", "부산": "부산", "대구": "대구", "인천": "인천", "광주": "광주", "대전": "대전", "울산": "울산",
    "세종": "세종", "경기": "경기", "강원": "강원", "충청북": "충북", "충북": "충북", "충청남": "충남", "충남": "충남",
    "전라북": "전북", "전북": "전북", "전라남": "전남", "전남": "전남", "경상북": "경북", "경북": "경북",
    "경상남": "경남", "경남": "경남", "제주": "제주",
}
# 5자리 행정 코드 앞 두 자리(새·옛) / KOSIS 옛 코드 앞 두 자리
CODE5 = {"11": "서울", "26": "부산", "27": "대구", "28": "인천", "29": "광주", "30": "대전", "31": "울산", "36": "세종",
         "41": "경기", "42": "강원", "51": "강원", "43": "충북", "44": "충남", "45": "전북", "52": "전북", "46": "전남",
         "47": "경북", "48": "경남", "50": "제주"}
OLD = {"11": "서울", "21": "부산", "22": "대구", "23": "인천", "24": "광주", "25": "대전", "26": "울산", "29": "세종",
       "31": "경기", "32": "강원", "33": "충북", "34": "충남", "35": "전북", "36": "전남", "37": "경북", "38": "경남",
       "39": "제주"}
GWANGJU_GU = {"동구", "서구", "남구", "북구", "광산구"}
ALIAS = {("세종", "세종시"): ("세종", "세종특별자치시")}   # (짧은 시도, 다른 표의 이름) → 나우캐스트 열쇠
SAME_PLACE = {"군위군"}   # 시도가 바뀐 곳: 어느 시도로 적힌 자료든 같은 곳으로 봄


def short_sido(name):
    if name.startswith("전남광주"):
        return None
    for k, v in SIDO.items():
        if name.startswith(k):
            return v
    return None


def norm(s):
    return re.sub(r"\s+", "", str(s))


def key(sd, name):
    n = norm(name)
    return ALIAS.get((sd, n), (sd, n))


def nowcast_keys(con):
    m = pd.read_sql("SELECT sido, sigungu, MIN(week_order) a, MAX(week_order) b FROM moves "
                    "WHERE category='시군구' AND direction='유입' GROUP BY 1, 2", con)
    def sd(r):
        if r.sido.startswith("전남광주"):
            return "광주" if r.sigungu in GWANGJU_GU else "전남"
        return short_sido(r.sido)
    m["sd"] = m.apply(sd, axis=1)
    m["k"] = [key(a, b) for a, b in zip(m.sd, m.sigungu)]
    return m


def from_code5(df, code_col, name_col):
    """5자리 코드 표 → {열쇠: 코드들}. 일반구는 상위 시(코드 끝자리 0) 이름을 앞에 붙임."""
    d = df[[code_col, name_col]].drop_duplicates()
    d = d[d[code_col].astype(str).str.fullmatch(r"\d{5}")]
    names = dict(zip(d[code_col].astype(str), d[name_col]))
    out = {}
    for c, n in names.items():
        sd = CODE5.get(c[:2]) or ("광주" if c.startswith("12") and n in GWANGJU_GU else "전남" if c.startswith("12") else None)
        parent = c[:4] + "0"
        if c[4] != "0" and parent in names and names[parent].endswith("시") and not n.startswith(names[parent]):
            n = names[parent] + n
        out.setdefault(key(sd, n), set()).add(c)
    return out


def from_old(df, nm_has_sido):
    d = df[["C1", "C1_NM"]].drop_duplicates()
    out = {}
    for c, n in zip(d.C1.astype(str), d.C1_NM):
        sd = OLD.get(c[:2])
        if nm_has_sido and " " in n:
            n = n.split(" ", 1)[1]
        out.setdefault(key(sd, n), set()).add(c)
    return out


SIDO_FULL = {"서울특별시", "부산광역시", "대구광역시", "인천광역시", "광주광역시", "대전광역시", "울산광역시",
             "세종특별자치시", "경기도", "강원도", "강원특별자치도", "충청북도", "충청남도", "전라북도", "전북특별자치도",
             "전라남도", "경상북도", "경상남도", "제주도", "제주특별자치도"}


def from_foreigners(df):
    """이름에 시도가 없음 → 코드 번호 순서로 훑으며 바로 앞의 시도 줄을 시도로 씀(세종은 시도이자 시군구)."""
    d = df[["C1", "C1_NM"]].drop_duplicates().copy()
    d["num"] = d.C1.str.extract(r"(\d+)", expand=False).astype(int)
    out, sd = {}, None
    for c, n in d.sort_values("num")[["C1", "C1_NM"]].itertuples(index=False):
        if norm(n) in SIDO_FULL:
            sd = short_sido(n)
            if sd != "세종":
                continue
        if sd:
            out.setdefault(key(sd, n), set()).add(c)
    return out


def main():
    # Azure의 /home은 네트워크 저장소라 큰 표를 훑으면 매우 느림 → 임시 디스크로 복사해서 읽고, 결과만 원래 DB에 씀
    out_con = connect()
    if os.environ.get("WEBSITE_SITE_NAME"):
        tmp = "/tmp/region_src.db"
        shutil.copy(DB, tmp)
        con = sqlite3.connect(tmp)
    else:
        con = out_con
    m = nowcast_keys(con)
    name_count = m.groupby(m.k.str[1]).sd.nunique().to_dict()
    src = {
        "reg_pop": from_code5(pd.read_sql("SELECT DISTINCT C1, C1_NM FROM kosis_reg_pop", con), "C1", "C1_NM"),
        "datalab": from_code5(pd.read_sql("SELECT DISTINCT signguCode, signguNm FROM datalab_daily", con),
                              "signguCode", "signguNm"),
        "life_pop": from_old(pd.read_sql("SELECT DISTINCT C1, C1_NM FROM kosis_life_pop", con), True),
        "commute": from_old(pd.read_sql("SELECT DISTINCT C1, C1_NM FROM kosis_commute", con), True),
        "foreigners": from_foreigners(pd.read_sql("SELECT DISTINCT C1, C1_NM FROM kosis_foreigners", con)),
    }
    for s, mp in src.items():
        codes, via_city = [], []
        by_name = {}
        for kk in mp:
            by_name.setdefault(kk[1], []).append(kk)
        for k, nm in zip(m.k, m.sigungu):
            c = mp.get(k)
            city = None
            cand = by_name.get(k[1], [])
            if k[1] in SAME_PLACE and cand:               # 시도가 바뀐 곳: 어느 시도로 적혔든 후보 코드를 모두 합침
                c = set().union(*(mp[x] for x in cand))   # (등록외국인은 새 코드가 맨 뒤에 붙어 엉뚱한 시도로 읽힘)
            elif not c:                                   # 시도가 다르게 적힌 경우: 이름이 전국에 하나뿐이면 이름으로
                if len(cand) == 1 and name_count[k[1]] == 1:
                    c = mp[cand[0]]
            if not c and s == "commute" and " " in nm:    # 통근은 시 단위뿐 → 일반구는 시 값으로 대신
                city = key(k[0], nm.split(" ")[0])
                c = mp.get(city)
            codes.append(",".join(sorted(c)) if c else None)
            via_city.append(1 if (c and city) else 0)
        m[s] = codes
        m[s + "_city"] = via_city
    out = m.drop(columns="k")
    out.to_sql("region_map", out_con, if_exists="replace", index=False)
    out_con.commit()

    print("나우캐스트 지역", len(m))
    for s, mp in src.items():
        miss = m[m[s].isna()]
        used = {k for k in m.k} | {key(k[0], n.split(" ")[0]) for k, n in zip(m.k, m.sigungu) if " " in n}
        extra = sorted(f"{k[0]} {k[1]}" for k in mp if k not in used)
        print(f"\n== {s}: 이음 {m[s].notna().sum()} / 시 단위로 대신 {int(m[s + '_city'].sum())} / 못 이음 {len(miss)}")
        print("  못 이은 나우캐스트 지역:", ", ".join(f"{a} {b}" for a, b in zip(miss.sido, miss.sigungu))[:1500] or "없음")
        print("  쓰이지 않은 자료 쪽 이름:", ", ".join(extra)[:1500] or "없음")


if __name__ == "__main__":
    main()
