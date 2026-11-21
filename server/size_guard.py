"""응답 길이 제한(0.5.2, 2026-11-20 확정 — 기준 문서 claude/PlayMCP_재심사_20261120.md).

카카오 PlayMCP는 도구 응답이 24,000자를 넘으면 에러로 처리한다. 다른 연결(Claude·ChatGPT)에서도 긴 결과는
대화 공간을 차지해 답이 느려지거나 앞 내용을 잊게 만든다. 그래서 실제로 전송되는 응답 길이(JSON으로 바뀐 형태,
결과 글자 수보다 약 12% 김)가 LIMIT을 넘으면 결과 대신 '무엇을 얼마로 줄여 다시 물을지' 안내를 돌려준다.
짧은 결과는 그대로 나간다(결과 칸 이름·모양도 그대로).

줄일 것: 기간이 있는 도구는 기간, find_unusual_regions는 top(기간은 그대로 — 전체 기간 순위가 핵심),
compare_same_week는 years_back, 둘 다 없는 도구는 '결과가 너무 길다'만(서버 기록에 남김).
"""
import datetime as dt
import functools
import json
import math
import re

import pydantic_core

LIMIT = 20000          # PlayMCP 24,000자에 여유를 둔 값(운영하며 조정)
MARGIN = 0.9           # 권장 값은 여유를 두고 계산

# 도구 → (줄일 것 종류, 시작 인자, 끝 인자)
PERIOD = {
    "get_movement": ("week", "start_week", "end_week"),
    "get_weather": ("week", "start_week", "end_week"),
    "get_region_calendar": ("date", "start", "end"),
    "get_datalab_daily": ("date", "start_date", "end_date"),
}
COUNT = {
    "find_unusual_regions": "top",
    "compare_same_week": "years_back",
}


def wire_length(result):
    """SDK가 결과를 글로 바꾸는 방식(pydantic_core.to_json, indent=2)대로 바꾼 뒤, JSON-RPC 안에 들어갈 때
    따옴표·줄바꿈이 바뀌는 만큼까지 센 길이. 목록은 항목마다 따로 바뀌므로 더한다."""
    items = result if isinstance(result, (list, tuple)) else [result]
    n = 0
    for it in items:
        text = it if isinstance(it, str) else pydantic_core.to_json(it, fallback=str, indent=2).decode()
        n += len(json.dumps(text, ensure_ascii=False))
    return n


def _week_date(s):
    """'YYYY.MM.N주차' → 그 주 대략의 날짜(길이 계산용)."""
    m = re.match(r"\s*(\d{4})\.(\d{1,2})\.(\d)\s*주차", str(s or ""))
    if not m:
        return None
    y, mo, n = map(int, m.groups())
    try:
        return dt.date(y, mo, 1) + dt.timedelta(days=7 * (n - 1))
    except ValueError:
        return None


def _date(s):
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def advice(name, kwargs, length):
    """넘었을 때 돌려줄 안내."""
    ratio = LIMIT / length * MARGIN
    out = {"error": f"결과가 너무 길다({length:,}자, 한 번에 {LIMIT:,}자까지). ",
           "결과 길이(자)": length, "한도(자)": LIMIT}
    if name in PERIOD:
        kind, a, b = PERIOD[name]
        conv = _week_date if kind == "week" else _date
        d0, d1 = conv(kwargs.get(a)), conv(kwargs.get(b))
        if d0 and d1 and d1 >= d0:
            days = (d1 - d0).days + (7 if kind == "week" else 1)
            if kind == "week":
                n = max(1, math.floor(days / 7 * ratio))
                want = f"{n}주"
            else:
                n = max(1, math.floor(days * ratio))
                want = f"{n}일"
            out["error"] += f"기간을 {want} 이하로 줄여 나눠서 다시 물을 것."
            out["줄일 것"] = f"기간({a}~{b})"
            out["권장"] = want + " 이하"
        else:
            out["error"] += "기간을 지금의 절반 이하로 줄여 나눠서 다시 물을 것."
            out["줄일 것"] = f"기간({a}~{b})"
        return out
    if name in COUNT:
        p = COUNT[name]
        cur = kwargs.get(p)
        if isinstance(cur, int) and cur > 1:
            n = max(1, math.floor(cur * ratio))
            out["error"] += f"{p}를 {n} 이하로 줄여 다시 물을 것." + (" 기간은 그대로 둘 것." if p == "top" else "")
            out["권장"] = f"{p} {n} 이하"
        else:
            out["error"] += f"{p}를 줄여 다시 물을 것."
        out["줄일 것"] = p
        return out
    out["error"] = out["error"].strip()
    return out


def guard(name, fn, log=print):
    """도구 함수를 감싸 길이를 재고, 넘으면 안내로 바꾼다. 결과 안의 어떤 칸도 바꾸지 않는다."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        result = fn(*args, **kwargs)
        try:
            n = wire_length(result)
        except Exception as e:  # noqa: BLE001 — 재다 실패하면 결과를 그대로 보냄
            log(f"[size_guard] {name} 길이 재기 실패: {e!r}")
            return result
        if n <= LIMIT:
            return result
        if name not in PERIOD and name not in COUNT:
            log(f"[size_guard] {name} 결과 {n:,}자 — 한도 넘음(줄일 인자 없음) {kwargs}")
        return advice(name, kwargs, n)
    return wrapper


def install(tool_manager, log=print):
    """등록된 도구마다 guard를 씌운다. 비동기 도구는 건너뛴다(지금은 없음)."""
    done = []
    for name, t in tool_manager._tools.items():
        if getattr(t, "is_async", False):
            continue
        t.fn = guard(name, t.fn, log)
        done.append(name)
    return done
