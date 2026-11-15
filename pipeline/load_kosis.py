"""KOSIS 공유서비스 OpenAPI로 지역 기초 자료를 받아 DB에 넣는다(환경변수 KOSIS_KEY).

받는 표:
- life_pop     생활인구           orgId 101, DT_1YL12001E (월, 107개 감소·관심지역, T001 계)
- reg_pop      주민등록인구       orgId 101, DT_1B040A3   (월, T20 총인구수)
- foreigners   등록외국인         orgId 111, DT_1B040A11  (연, 성별 '계'·체류자격 '총계')
- commute      타지역 통근        orgId 101, DT_1ES3A11S  (반기, T10 취업자·T30 거주지외 통근)
- migration    전입·전출          orgId 101, DT_1B26001_A01 (월, T10 총전입·T20 총전출)

KOSIS는 한 번에 4만 셀까지라 기간을 1년씩 나눠 부름.
지역 코드·이름은 표마다 다름(2026-11-14 시험): 생활인구·주민등록·전입전출 5자리, 통근 4자리,
등록외국인은 KOSIS 자체 코드(AC149), 생활인구는 개편 전 이름 →
지역 연결표는 이름이 아니라 코드와 개편 이력으로 따로 만든다.

쓰는 법: KOSIS_KEY=... python pipeline/load_kosis.py [표 이름 ...]
"""
import datetime as dt
import sys
import time

import pandas as pd
import requests

from common import connect, get, key

URL = "https://kosis.kr/openapi/Param/statisticsParameterData.do"

TABLES = {
    "life_pop":   dict(orgId="101", tblId="DT_1YL12001E", itmId="T001", objL1="ALL", objL2="ALL", prdSe="M", start=2024),
    "reg_pop":    dict(orgId="101", tblId="DT_1B040A3", itmId="T20", objL1="ALL", prdSe="M", start=2019),
    # 등록외국인: objL2 성별 '계'(13102870964B.0), objL3 체류자격 '총계'(AAA0000) — 2026-11-14 표 정보로 확인
    "foreigners": dict(orgId="111", tblId="DT_1B040A11", itmId="13103870964T1", objL1="ALL",
                       objL2="13102870964B.0", objL3="AAA0000", prdSe="Y", start=2019),
    # 타지역 통근: 반기 주기 코드는 S, 시점은 YYYY01(상반기)·YYYY02(하반기) — 2026-11-14 확인
    # 분류는 objL1(지역) 하나뿐이라 objL2를 넣으면 오류 21. 지역 코드는 4자리(1101 서울 종로구)라 5자리 체계와 다름
    "commute":    dict(orgId="101", tblId="DT_1ES3A11S", itmId="T10+T30", objL1="ALL", prdSe="S", start=2019),
    "migration":  dict(orgId="101", tblId="DT_1B26001_A01", itmId="T10+T20", objL1="ALL", prdSe="M", start=2019),
}
KEEP = ["PRD_DE", "C1", "C1_NM", "C2", "C2_NM", "C3", "C3_NM", "ITM_ID", "ITM_NM", "UNIT_NM", "DT", "LST_CHN_DE"]


def period(prd, y):
    return {"M": (f"{y}01", f"{y}12"), "Y": (f"{y}", f"{y}"), "S": (f"{y}01", f"{y}02")}[prd]


def fetch(k, spec):
    out = []
    for y in range(spec["start"], dt.date.today().year + 1):
        a, b = period(spec["prdSe"], y)
        p = {k_: v for k_, v in spec.items() if k_ != "start"}
        p.update(method="getList", apiKey=k, format="json", jsonVD="Y", startPrdDe=a, endPrdDe=b)
        r = get(URL, p)
        j = r.json()
        if isinstance(j, dict):     # 오류나 자료 없음은 dict로 옴
            print(spec["tblId"], y, j.get("errMsg", j))
            continue
        out += j
        time.sleep(1)
    df = pd.DataFrame(out)
    df = df[[c for c in KEEP if c in df]]
    df["DT"] = pd.to_numeric(df["DT"], errors="coerce")
    return df


def main():
    k = key("KOSIS_KEY")
    names = sys.argv[1:] or list(TABLES)
    con = connect()
    for n in names:
        df = fetch(k, TABLES[n])
        df.to_sql(f"kosis_{n}", con, if_exists="replace", index=False)
        con.commit()
        print(n, len(df), "행", df["PRD_DE"].min() if len(df) else "", "~", df["PRD_DE"].max() if len(df) else "")


if __name__ == "__main__":
    main()
