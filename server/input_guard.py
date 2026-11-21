"""범위 밖 입력 안내(0.5.2, 2026-11-21 확정 — 기준 문서 claude/PlayMCP_재심사_20261120.md 3번).

입력값이 Tool이 정한 범위·형식을 벗어나면 SDK는 영어 검사 오류(pydantic)를 그대로 돌려준다
(예: compare_same_week years_back=5 → 'Input should be less than or equal to 3 ...'). PlayMCP 심사에서
문제가 된 부분이다. 이 장치는 그 오류를 잡아, 다른 Tool이 지역 이름이 틀렸을 때 돌려주는 것과 같은 모양
{"error": "..."}의 한국어 안내로 바꾼다. 허용 범위는 Tool마다 이미 정해 둔 값(입력 형식)에서 읽는다.
정상 입력의 결과·Tool 설명은 바꾸지 않는다.
"""
import functools

from pydantic import ValidationError

RANGE_TYPES = {"less_than_equal", "greater_than_equal", "less_than", "greater_than"}
CHOICE_TYPES = {"literal_error", "enum"}
NUMBER_TYPES = {"int_parsing", "int_type", "int_from_float", "float_parsing", "float_type"}
PREFIX = "입력값이 받을 수 있는 범위를 벗어났다."


# 영어 이름을 한글로 읽을 때 받침이 생기는 끝 글자(top→톱, back→백, item→아이템, direction→디렉션).
# 그 밖(kind→카인드, sido→시도, year→이어)은 받침 없이 끝남.
_BATCHIM = set("bcklmnp")


def _topic(name):
    """영어 입력값 이름 뒤 조사(은/는)."""
    return name + ("은" if name[-1:].lower() in _BATCHIM else "는")


def _subject(name):
    """영어 입력값 이름 뒤 조사(이/가)."""
    return name + ("이" if name[-1:].lower() in _BATCHIM else "가")


def _shown(v):
    return f"'{v}'" if isinstance(v, str) else str(v)


def _prop(schema, name):
    p = (schema or {}).get("properties", {}).get(name, {})
    if "anyOf" in p:                       # 선택 입력값(None 허용)은 anyOf 안에 들어 있음
        for alt in p["anyOf"]:
            if alt.get("type") != "null":
                return {**alt, **{k: v for k, v in p.items() if k != "anyOf"}}
    return p


def message(schema, errors):
    """검사 오류 목록 → 한국어 안내 문장."""
    parts, out_of_range, missing_only = [], False, True
    for e in errors:
        name = str(e.get("loc", ["?"])[-1])
        kind = e.get("type", "")
        got = e.get("input")
        p = _prop(schema, name)
        if kind == "missing":
            parts.append(f"{_subject(name)} 빠졌다.")
            continue
        missing_only = False
        if kind in RANGE_TYPES:
            out_of_range = True
            lo, hi = p.get("minimum"), p.get("maximum")
            if lo is not None and hi is not None:
                parts.append(f"{_topic(name)} {lo}~{hi}만 받는다(받은 값: {_shown(got)}).")
            elif hi is not None:
                parts.append(f"{_topic(name)} {hi} 이하만 받는다(받은 값: {_shown(got)}).")
            elif lo is not None:
                parts.append(f"{_topic(name)} {lo} 이상만 받는다(받은 값: {_shown(got)}).")
            else:
                parts.append(f"{_topic(name)} 받을 수 있는 범위를 벗어났다(받은 값: {_shown(got)}).")
        elif kind in CHOICE_TYPES:
            out_of_range = True
            choices = p.get("enum") or ([p["const"]] if "const" in p else [])
            if choices:
                parts.append(f"{_topic(name)} {', '.join(_shown(c) for c in choices)} 중 하나만 받는다"
                             f"(받은 값: {_shown(got)}).")
            else:
                parts.append(f"{_topic(name)} 정해진 값 중 하나만 받는다(받은 값: {_shown(got)}).")
        elif kind in NUMBER_TYPES:
            parts.append(f"{_topic(name)} 숫자로 넣을 것(받은 값: {_shown(got)}).")
        else:
            parts.append(f"{name}의 형식이 맞지 않다(받은 값: {_shown(got)}).")
    if missing_only:
        tail = "넣어서 다시 물을 것."
    elif out_of_range:
        tail = "고쳐서 다시 물을 것."
    else:
        tail = ""
    text = " ".join(([PREFIX] if out_of_range else []) + parts + ([tail] if tail else []))
    return {"error": text}


def install(tool_manager, log=print):
    """Tool을 부를 때 입력 검사 오류만 잡아 한국어 안내로 바꾼다. 다른 오류는 그대로 둔다."""
    orig = tool_manager.call_tool

    @functools.wraps(orig)
    async def call_tool(name, arguments, *args, convert_result=False, **kwargs):
        try:
            return await orig(name, arguments, *args, convert_result=convert_result, **kwargs)
        except Exception as e:  # noqa: BLE001
            cause = e.__cause__
            tool = tool_manager._tools.get(name)
            if not isinstance(cause, ValidationError) or tool is None:
                raise
            out = message(tool.parameters, cause.errors())
            log(f"[input_guard] {name} 범위 밖 입력 {arguments} → {out['error']}")
            return tool.fn_metadata.convert_result(out) if convert_result else out

    tool_manager.call_tool = call_tool
    return call_tool
