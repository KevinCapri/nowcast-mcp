"""곁가지 정보 보호막.

판정(본 결과)에 덧붙이는 일정·지난 기록·추정 몫 같은 곁가지는 계속 늘어난다. 곁가지 하나가 고장 나도
판정과 다른 곁가지는 그대로 나가야 한다. 곁가지는 반드시 extra()/guard() 안에서 붙인다.
- 고장 나면 그 항목만 빠지고, 결과의 '주의'에 한 줄을 남기고, 운영자에게 메일(같은 곁가지는 하루 한 번)을 보낸다.
"""
import datetime as dt
import sys
import threading
import traceback
from pathlib import Path

_sent = set()


def alert(label, exc):
    print(f"[safe] 곁가지 오류 {label}: {exc!r}", flush=True)
    k = (label, dt.date.today())
    if k in _sent:
        return
    _sent.add(k)
    body = f"{label}\n\n{''.join(traceback.format_exception(exc))[-3000:]}"

    def run():
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
            from notify import send
            send(f"곁가지 정보 오류: {label}", body)
        except Exception as e:
            print("[safe] 메일 실패", e, flush=True)
    threading.Thread(target=run, daemon=True).start()


def guard(label, fn, default=None):
    """fn()을 돌리고, 실패하면 default를 돌려준다(사이트 화면 등 '주의' 칸이 없는 곳)."""
    try:
        return fn()
    except Exception as e:
        alert(label, e)
        return default


def extra(r, label, fn):
    """fn(r)로 결과 r에 곁가지를 붙인다. 실패하면 그 곁가지만 빼고 '주의'에 한 줄."""
    try:
        fn(r)
    except Exception as e:
        alert(label, e)
        note = f"일부 정보({label})를 불러오지 못해 빠졌다. 판정에는 영향이 없다."
        if isinstance(r.get("주의"), list):
            r["주의"].append(note)
        else:
            r["주의"] = [note]
