"""'받는 중' 표시 파일 시험(2026-11-25 DB 손상 뒤): 둘째 실행은 건너뛰고, 끝나면 지우고, 오래된 표시는 치운다."""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ["NOWCAST_DB"] = str(Path(tempfile.mkdtemp()) / "t.db")
sys.path.insert(0, str(ROOT / "pipeline"))
import update as U  # noqa: E402

U.DATA.mkdir(parents=True, exist_ok=True)
fails = []
assert U.acquire() is True
if U.acquire() is not False:
    fails.append("이미 받는 중인데 또 잡힘")
ran = []
U._main = lambda: ran.append(1) or 0
if U.main() != 0 or ran:
    fails.append("다른 받기가 도는 중인데 건너뛰지 않음")
U.RUNNING.unlink()
U.main()
if ran != [1] or U.RUNNING.exists():
    fails.append("끝난 뒤 표시가 안 지워짐")
U.RUNNING.write_text("old")
old = time.time() - U.STALE - 60
os.utime(U.RUNNING, (old, old))
if U.acquire() is not True:
    fails.append("오래된 표시를 안 치움")
if fails:
    print("실패:", *fails, sep="\n- ")
    sys.exit(1)
print("받는 중 표시 시험 통과")
