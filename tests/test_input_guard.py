"""범위 밖 입력 안내 장치 시험(자료 DB 없이 — PR마다 기본 검사에서 돎).

실제 서버의 Tool을 범위 밖 입력으로 불러, 영어 검사 오류 대신 {"error": 한국어 안내}가 나오는지 본다.
입력 검사는 Tool 함수를 부르기 전에 일어나므로 자료 DB가 없어도 시험할 수 있다.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
os.environ.setdefault("NOWCAST_DB", "/tmp/input_guard_none.db")
os.chdir(ROOT / "server")
import server as S  # noqa: E402

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)


def text(r):
    return json.loads(r.content[0].text)


async def main():
    cases = [
        ("compare_same_week", {"sido": "경상북도", "sigungu": "경주시", "week": "2025.10.2주차", "years_back": 5},
         "입력값이 받을 수 있는 범위를 벗어났다. years_back은 1–3만 받는다(받은 값: 5). 고쳐서 다시 물을 것."),
        ("get_movement", {"start_week": "2025.10.2주차", "direction": "들어옴", "category": "성별"},
         "입력값이 받을 수 있는 범위를 벗어났다. direction은 '유입', '유출' 중 하나만 받는다(받은 값: '들어옴'). "
         "고쳐서 다시 물을 것."),
        ("compare_same_week", {"sigungu": "경주시", "week": "2025.10.2주차"},
         "sido가 빠졌다. 넣어서 다시 물을 것."),
        ("find_unusual_regions", {"top": "많이"},
         "top은 숫자로 넣을 것(받은 값: '많이')."),
        ("find_unusual_regions", {"top": 0},
         "입력값이 받을 수 있는 범위를 벗어났다. top은 1–30만 받는다(받은 값: 0). 고쳐서 다시 물을 것."),
    ]
    for name, args, want in cases:
        r = await S.mcp.call_tool(name, args)
        check(not r.is_error, f"{name}{args}: 오류로 나감")
        got = text(r).get("error")
        check(got == want, f"{name}{args}:\n  기대 {want}\n  실제 {got}")
    # 없는 Tool은 원래대로 오류
    try:
        await S.mcp.call_tool("no_such_tool", {})
        fails.append("없는 Tool이 오류 없이 지나감")
    except Exception:  # noqa: BLE001
        pass
    # 범위 안 입력은 검사를 지나 Tool까지 감(자료 DB가 없어 결과는 비어도 됨, 범위 안내가 아니어야 함)
    r = await S.mcp.call_tool("get_metadata", {})
    check("입력값이 받을 수 있는 범위" not in r.content[0].text, "정상 입력이 범위 안내로 바뀜")


asyncio.run(main())
if fails:
    print("실패", len(fails))
    for f in fails:
        print(" -", f)
    sys.exit(1)
print("범위 밖 입력 안내 시험 통과")
