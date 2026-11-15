"""곁가지 보호(2026-11-15, claude/변경_관리_원칙.md).

- 곁가지(축제·행사·공연·야구·벚꽃단풍·지난 기록 등)는 실패해도 그 항목만 빠지고 본 판정은 그대로 나와야 한다.
- 실패는 로그에 남기고, 같은 곁가지는 하루 한 번만 운영자 메일(pipeline/notify.py)로 알린다.
- 손으로 넣는 자료 파일은 줄마다 검사해서 잘못된 줄은 건너뛰고 기록한다(파일 하나가 도구 전체를 멈추지 않게).
"""
import datetime as dt
import logging
import sys
import threading
from pathlib import Path

log = logging.getLogger("nowcast.safe")
FAILS = []          # 최근 곁가지 실패(최대 50개): (시각, 이름, 내용)
BAD_ROWS = {}       # 파일 이름 → 건너뛴 줄 수
_sent = set()


def _notify(name, text):
    key = (name, dt.date.today())
    if key in _sent:
        return
    _sent.add(key)
    try:
        p = str(Path(__file__).resolve().parent.parent / "pipeline")
        if p not in sys.path:
            sys.path.append(p)
        from notify import send
        threading.Thread(target=send, args=(f"곁가지 실패: {name}", text), daemon=True).start()
    except Exception:
        pass


def call(name, fn, *a, default=None, **k):
    """fn(*a, **k)를 부르되 실패하면 default를 돌려준다."""
    try:
        return fn(*a, **k)
    except Exception as e:
        log.exception("곁가지 실패: %s", name)
        FAILS.append((dt.datetime.now().isoformat(timespec="seconds"), name, repr(e)[:200]))
        del FAILS[:-50]
        _notify(name, f"{name}\n{type(e).__name__}: {e}\n(본 판정은 그대로 나가고 이 항목만 빠짐)")
        return default


def bad_row(fname, row, err):
    """잘못된 줄 하나를 건너뛸 때 부른다."""
    BAD_ROWS[fname] = BAD_ROWS.get(fname, 0) + 1
    log.warning("%s 잘못된 줄 건너뜀: %s (%s)", fname, dict(row) if row else row, err)
    if BAD_ROWS[fname] == 1:
        _notify(f"자료 파일 {fname}", f"{fname}에 잘못된 줄이 있어 건너뛰었다.\n{dict(row) if row else row}\n{err}\n"
                "pipeline/check_manual_data.py로 확인할 것.")
