"""도구 한 번 처리의 단계별 걸린 시간(속도 모니터링, 2026-11-21).

- server_http.py가 도구 요청마다 start()·end()를 부르고, 도구 안의 `with timing.step("이름")`이 단계 시간을 더한다
  (단계를 넣어 둔 도구만 단계가 남고, 나머지는 전체 시간만).
- 요청마다 따로 담기도록 contextvars를 쓴다(동시에 들어온 요청이 섞이지 않게).
- 끝난 기록은 메모리에 최근 30건만(LOG). 10초 넘은 요청은 guard.py가 usage.db의 slow 표에 남긴다.
결과 내용·칸은 바꾸지 않는다."""
import contextvars
import time
from collections import deque
from contextlib import contextmanager

LOG = deque(maxlen=30)
_cur = contextvars.ContextVar("timing_cur", default=None)


def start(tool, **info):
    _cur.set({"tool": tool, "at": time.strftime("%Y-%m-%d %H:%M:%S"), **info, "steps": [],
              "_t0": time.perf_counter()})


@contextmanager
def step(name):
    t = time.perf_counter()
    try:
        yield
    finally:
        c = _cur.get()
        if c is not None:
            c["steps"].append((name, round(time.perf_counter() - t, 2)))


def mark(key, value):
    """도구 안에서 이번 요청 기록에 값 하나를 남긴다(0.6.5: 못 한 이유 'cant'). 기록 중이 아니면 아무것도 안 함."""
    c = _cur.get()
    if c is not None:
        c[key] = value


def end():
    """끝난 기록을 돌려준다(없으면 None)."""
    c = _cur.get()
    if c is None:
        return None
    c["total"] = round(time.perf_counter() - c.pop("_t0"), 2)
    LOG.append(c)
    _cur.set(None)
    return c


def dump():
    return list(LOG)
