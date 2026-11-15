"""회귀 시험: 대표 질문의 핵심 값이 코드 변경 뒤에도 그대로인지 확인한다.

  NOWCAST_DB=<DB> python tests/regression.py          # 기준과 비교(다르면 종료 코드 1)
  NOWCAST_DB=<DB> python tests/regression.py --save   # 지금 결과를 새 기준으로 저장(바뀐 이유를 PR에 적을 것)

- 비교하는 것: 판정·배수·평소 대비·최종 변화, 곁가지 항목 이름, 오류 여부, 걸린 시간(2배 넘게 느려지면 경고).
- DB는 운영 서버 자료를 받은 사본을 쓴다. DB가 바뀌면(새 주차 추가) 같은 주의 값도 조금 바뀔 수 있으니,
  기준을 다시 저장할 때는 DB 날짜를 함께 적는다.
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
BASE = Path(__file__).with_name("baseline.json")

CASES = [  # (설명, 시도, 시군구, 주차)
    ("화천 산천어축제", "강원특별자치도", "화천군", "2026.01.2주차"),
    ("진해 군항제·벚꽃", "경상남도", "창원시 진해구", "2025.04.1주차"),
    ("구례 벚꽃·지리산", "전라남도", "구례군", "2026.04.1주차"),
    ("정읍 내장산 단풍", "전북특별자치도", "정읍시", "2025.11.2주차"),
    ("창원 마산회원 야구 6연전", "경상남도", "창원시 마산회원구", "2025.08.3주차"),
    ("송파 잠실 시즌 중", "서울특별시", "송파구", "2025.06.2주차"),
    ("대구 수성 포스트시즌", "대구광역시", "수성구", "2026.10.5주차"),
    ("경주 APEC", "경상북도", "경주시", "2025.10.5주차"),
    ("목포 전국체전", "전라남도", "목포시", "2023.10.3주차"),
    ("구로 공연(고척)", "서울특별시", "구로구", "2025.12.1주차"),
    ("추석 연휴 주", "강원특별자치도", "강릉시", "2025.10.2주차"),
    ("설 연휴 주", "충청북도", "진천군", "2026.02.3주차"),
    ("평일 공휴일 하루", "서울특별시", "종로구", "2025.06.1주차"),
    ("행정구역 개편 직후", "경기도", "화성시 동탄구", "2026.03.1주차"),
    ("시도 이름 바뀐 곳(광주)", "광주광역시", "북구", "2026.08.2주차"),
    ("군위군 편입", "대구광역시", "군위군", "2024.05.1주차"),
    ("평범한 주", "충청남도", "청양군", "2025.03.3주차"),
    ("없는 주차", "서울특별시", "중구", "2018.01.1주차"),
    ("없는 지역", "서울특별시", "없는구", "2025.01.1주차"),
    ("잘못된 주차 형식", "서울특별시", "중구", "2025-01-01"),
]
KEYS = ["판정", "평소 출렁임의 몇 배", "평소 대비 변화(%)", "최종 변화(%)", "판정에 쓴 방식", "error"]


def run():
    import server as S
    out = {}
    for name, sd, sg, wk in CASES:
        t = time.time()
        r = S.analyze_region_week(sd, sg, wk)
        sec = round(time.time() - t, 3)
        body = r.get("result", r) if isinstance(r, dict) else r
        out[name] = {"값": {k: body.get(k) for k in KEYS if k in body},
                     "항목": sorted(k for k in body if k not in ("주의", "자료 기준")),
                     "초": sec}
    return out


def main():
    cur = run()
    if "--save" in sys.argv:
        BASE.write_text(json.dumps({"DB": os.environ.get("NOWCAST_DB"), "결과": cur}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
        print(f"기준 저장: {len(cur)}개")
        return 0
    base = json.loads(BASE.read_text(encoding="utf-8"))["결과"]
    bad = 0
    for name, c in cur.items():
        b = base.get(name)
        if not b:
            print(f"[새 사례] {name}")
            continue
        if c["값"] != b["값"]:
            bad += 1
            print(f"[값 바뀜] {name}\n  전: {b['값']}\n  후: {c['값']}")
        lost = set(b["항목"]) - set(c["항목"])
        if lost:
            bad += 1
            print(f"[항목 사라짐] {name}: {sorted(lost)}")
        new = set(c["항목"]) - set(b["항목"])
        if new:
            print(f"[항목 생김] {name}: {sorted(new)}")
        if c["초"] > max(0.5, 2 * b["초"]):
            print(f"[느려짐] {name}: {b['초']}초 → {c['초']}초")
    print("통과" if not bad else f"다른 곳 {bad}개 → 의도한 변화면 이유를 적고 --save")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
