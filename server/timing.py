"""임시 속도 측정(2026-11-20, PlayMCP 심사 대비). analyze_region_week가 16~18초 걸리는 원인을 찾으려고
단계별 걸린 시간을 메모리에 최근 30건만 담아 두고 /api/timing으로 보여 준다. 원인을 찾아 고친 뒤 지운다.
결과 내용·칸은 바꾸지 않는다."""
import time
from collections import deque
from contextlib import contextmanager

LOG = deque(maxlen=30)
_cur = None


def start(tool, **info):
    global _cur
    _cur = {"tool": tool, "at": time.strftime("%Y-%m-%d %H:%M:%S"), **info, "steps": []}
    _cur["_t0"] = time.perf_counter()


@contextmanager
def step(name):
    t = time.perf_counter()
    try:
        yield
    finally:
        if _cur is not None:
            _cur["steps"].append((name, round(time.perf_counter() - t, 2)))


def end():
    global _cur
    if _cur is not None:
        _cur["total"] = round(time.perf_counter() - _cur.pop("_t0"), 2)
        LOG.append(_cur)
    _cur = None


def dump():
    return list(LOG)
