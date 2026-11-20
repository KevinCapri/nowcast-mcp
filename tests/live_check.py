"""실제 서비스 회귀 확인(2026-11-16).

배포된 서비스(/mcp)에 대표 질문(tests/regression.py의 CASES)을 보내, 실제 서비스 기준값(tests/live_baseline.json)과 견준다.
샌드박스 DB로 만든 baseline.json은 실제 서비스 DB와 자료가 달라 숫자가 어긋나므로, 배포 뒤 확인은 이 파일로 한다.
  python tests/live_check.py                 # 비교. 문제 있으면 끝 코드 1
  python tests/live_check.py --save out.json # 지금 실제 결과를 기준값 파일로 저장(바뀐 이유를 확인한 뒤에만 커밋)
문제로 치는 것: 오류·예외가 새로 생김, 결과 항목이 사라짐, 판정 문구가 바뀜, 배수가 0.3 넘게 바뀜, 도구 목록이 다름.
새 항목이 생긴 것은 알리기만 한다(더하기는 원칙상 허용).
"""
import json
import os
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from regression import CASES, core  # noqa: E402

URL = os.environ.get("NOWCAST_URL", "https://nowcast-mcp.azurewebsites.net").rstrip("/") + "/mcp"
BASE = HERE / "live_baseline.json"
NAMES = {
    "analyze_region_week": ("sido", "sigungu", "week"),
    "find_unusual_regions": ("week", "top"),
    "estimate_life_pop": ("sido", "sigungu", "year_month"),
    "forecast_festival": ("sido", "sigungu", "festival_name"),
    "get_region_calendar": ("sido", "sigungu", "start"),
    "get_bloom_foliage": ("year", "kind"),
    "compare_same_week": ("sido", "sigungu", "week"),
}
TOL = 0.3


def call(name, args, tries=3):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": dict(zip(NAMES[name], args))}}
    for i in range(tries):
        try:
            r = requests.post(URL, json=body, timeout=120,
                              headers={"Accept": "application/json, text/event-stream"})
            j = r.json()
            res = j.get("result") or {}
            text = (res.get("content") or [{}])[0].get("text", "")
            if res.get("isError"):
                return {"예외": text}
            return json.loads(text)
        except Exception as e:  # noqa: BLE001
            if i == tries - 1:
                return {"예외": f"{type(e).__name__}: {e}"}
            time.sleep(10 * (i + 1))


def tools():
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    r = requests.post(URL, json=body, timeout=60, headers={"Accept": "application/json, text/event-stream"})
    return sorted(t["name"] for t in r.json()["result"]["tools"])


def run():
    out = {}
    for name, args in CASES:
        out[f"{name}{args}"] = core(name, call(name, args))
    out["_tools"] = tools()
    return out


def compare(base, now):
    bad, info = [], []
    for k, b in base.items():
        n = now.get(k)
        if k == "_tools":
            if n != b:
                bad.append(f"도구 목록 다름: 빠짐 {sorted(set(b) - set(n or []))}, 새로 {sorted(set(n or []) - set(b))}")
            continue
        if n is None:
            bad.append(f"{k}: 사례 없음")
            continue
        if "예외" in n and "예외" not in b:
            bad.append(f"{k}: 예외 {n['예외'][:120]}")
            continue
        gone = set(b.get("keys", [])) - set(n.get("keys", []))
        new = set(n.get("keys", [])) - set(b.get("keys", []))
        if gone:
            bad.append(f"{k}: 항목 사라짐 {sorted(gone)}")
        if new:
            info.append(f"{k}: 새 항목 {sorted(new)}")
        if b.get("판정") != n.get("판정"):
            bad.append(f"{k}: 판정 {b.get('판정')} → {n.get('판정')}")
        bx, nx = b.get("평소 출렁임의 몇 배"), n.get("평소 출렁임의 몇 배")
        if isinstance(bx, (int, float)) and isinstance(nx, (int, float)) and abs(bx - nx) > TOL:
            bad.append(f"{k}: 배수 {bx} → {nx}")
        for f in ("error", "요약", "주별", "관측"):
            if f in b and b.get(f) != n.get(f):
                info.append(f"{k}: {f} 바뀜")
    for k in now:
        if k not in base:
            info.append(f"{k}: 새 사례(기준값 없음)")
    return bad, info


# 응답 길이(0.5.2): 가장 길게 나올 만한 입력으로 불러 전송 길이가 한도 안인지 본다(PlayMCP 24,000자 에러).
LIMIT = 20000
LONG_CASES = [
    ("get_movement", {"direction": "유입", "category": "시군구", "item": "중구", "sido": "서울특별시",
                      "start_week": "2019.01.1주차", "end_week": "2030.12.4주차"}),
    ("get_weather", {"sido": "서울특별시", "sigungu": "중구", "start_week": "2019.01.1주차", "end_week": "2030.12.4주차"}),
    ("get_region_calendar", {"sido": "서울특별시", "sigungu": "송파구", "start": "2025-01-01", "end": "2025-12-31"}),
    ("get_datalab_daily", {"sido": "강원특별자치도", "sigungu": "화천군", "start_date": "2025-01-01", "end_date": "2026-01-31"}),
    ("find_unusual_regions", {"start_week": "2023.06.1주차", "end_week": "2030.12.4주차", "top": 30}),
    ("find_unusual_regions", {"top": 30}),
    ("compare_same_week", {"sido": "강원특별자치도", "sigungu": "화천군", "week": "2026.01.3주차", "years_back": 3}),
    ("list_regions", {"keyword": "구"}),
    ("get_metadata", {}),
    ("get_bloom_foliage", {"year": 2026, "kind": "단풍"}),
    ("get_data_status", {}),
]


def length_check():
    bad = []
    for name, args in LONG_CASES:
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
        try:
            r = requests.post(URL, json=body, timeout=180, headers={"Accept": "application/json, text/event-stream"})
            raw = r.text
            for line in raw.splitlines():          # SSE로 오면 data: 줄만
                if line.startswith("data:"):
                    raw = line[5:]
            res = json.loads(raw).get("result") or {}
            n = sum(len(json.dumps(c.get("text", ""), ensure_ascii=False)) for c in res.get("content") or [])
            if n > LIMIT:
                bad.append(f"응답 길이 {name}{args}: {n:,}자 > {LIMIT:,}자")
        except Exception as e:  # noqa: BLE001
            bad.append(f"응답 길이 {name}: {type(e).__name__}: {e}")
    return bad


def main():
    t = time.time()
    now = run()
    if "--save" in sys.argv:
        path = Path(sys.argv[sys.argv.index("--save") + 1]) if len(sys.argv) > sys.argv.index("--save") + 1 else BASE
        path.write_text(json.dumps(now, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"저장: {path} ({len(now) - 1}건, {time.time() - t:.0f}초)")
        return
    if not BASE.exists():
        print("기준값(live_baseline.json)이 아직 없음 — --save로 먼저 만들 것")
        return
    bad, info = compare(json.loads(BASE.read_text(encoding="utf-8")), now)
    bad += length_check()
    for x in info:
        print("  참고:", x)
    for x in bad:
        print("  문제:", x)
    print(f"실제 서비스 확인: 문제 {len(bad)}건, 참고 {len(info)}건 ({time.time() - t:.0f}초)")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
