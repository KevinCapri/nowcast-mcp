"""자료 자동 갱신: 단계마다 '마지막 성공'에서 정해진 날짜가 지났으면 다시 돌린다.

- 나우캐스트: 매일 확인(파일 이름이 바뀌었을 때만 받으므로 가벼움)
- 데이터랩(최근 3개월 다시 받기)·날씨: 6일마다
- KOSIS·공휴일·지역 연결표: 27일마다
- 날짜가 아니라 '마지막 성공'을 기준으로 하므로, 서버가 꺼져 하루 건너뛰어도 다음 실행 때 따라잡는다.
- 결과는 /home/data/update_status.json(단계별 마지막 시도·성공·오류)에 남고, 출력은 update.log에 쌓인다.
- 여러 개가 동시에 돌지 않게 잠금 파일을 쓴다.
- 실패한 단계가 있으면 운영자에게 메일(notify.py).

쓰는 법:
  python pipeline/update.py            # 때가 된 단계만
  python pipeline/update.py --all      # 모두
  python pipeline/update.py datalab    # 이름을 준 단계만
  python pipeline/update.py --status   # 자료별 최신 시점 표(source_status)만 다시 계산
"""
import datetime as dt
import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path

import sqlite3

from common import DB
from notify import send

HERE = Path(__file__).resolve().parent
DATA = DB.parent
STATUS = DATA / "update_status.json"
LOG = DATA / "update.log"
LOCK = DATA / "update.lock"
KST = dt.timezone(dt.timedelta(hours=9))

# (이름, 실행할 명령, 며칠마다, 제한 시간(초))
STEPS = [
    ("nowcast", ["fetch_nowcast.py"], 1, 3600),
    ("datalab", ["load_datalab.py"], 6, 3600),
    ("weather", ["build_weather.py"], 6, 3600),
    ("holidays", ["load_holidays.py"], 27, 600),
    ("kosis", ["load_kosis.py"], 27, 3600),
    ("region_map", ["build_region_map.py"], 27, 3600),
    ("rhythm", ["build_rhythm.py"], 27, 3600),
    ("kopis", ["load_kopis.py"], 6, 7200),
]


def now():
    return dt.datetime.now(KST)


def load_status():
    try:
        return json.loads(STATUS.read_text())
    except Exception:
        return {}


def save_status(st):
    tmp = STATUS.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1))
    tmp.replace(STATUS)


def due(st, name, days):
    ok = (st.get(name) or {}).get("last_ok")
    if not ok:
        return True
    return now() - dt.datetime.fromisoformat(ok) >= dt.timedelta(days=days) - dt.timedelta(hours=2)


def run_step(name, cmd, timeout, log):
    log.write(f"\n===== {now():%Y-%m-%d %H:%M} {name} =====\n")
    log.flush()
    try:
        r = subprocess.run([sys.executable, "-u", str(HERE / cmd[0]), *cmd[1:]], stdout=log,
                           stderr=subprocess.STDOUT, timeout=timeout, cwd=HERE.parent)
        return None if r.returncode == 0 else f"종료 코드 {r.returncode}"
    except subprocess.TimeoutExpired:
        return f"{timeout}초 넘어 멈춤"


def tail(path, n=5):
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-n:])
    except Exception:
        return ""


# 자료별 최신 시점: (이름, 단계, 설명, 최신 시점을 구하는 SQL). 단계 이름은 STEPS와 같게(받아 온 날을 붙이려고)
SOURCES = [
    ("나우캐스트 인구이동(유입)", "nowcast", "주 단위", "SELECT week FROM moves WHERE direction='유입' ORDER BY week_order DESC LIMIT 1"),
    ("나우캐스트 인구이동(유출)", "nowcast", "주 단위", "SELECT week FROM moves WHERE direction='유출' ORDER BY week_order DESC LIMIT 1"),
    ("한국관광 데이터랩 방문자 수", "datalab", "일 단위, 약 한 달 늦게 나오고 최근 값은 소급 수정될 수 있음",
     "SELECT MAX(day) FROM datalab_daily"),
    ("기상청 날씨(ASOS)", "weather", "주 단위로 묶음", "SELECT week FROM weather_week ORDER BY week_order DESC LIMIT 1"),
    ("공휴일(한국천문연구원)", "holidays", "날짜", "SELECT MAX(day) FROM holidays"),
    ("생활인구(KOSIS)", "kosis", "월", "SELECT MAX(PRD_DE) FROM kosis_life_pop"),
    ("주민등록인구(KOSIS)", "kosis", "월", "SELECT MAX(PRD_DE) FROM kosis_reg_pop"),
    ("등록외국인(KOSIS)", "kosis", "연", "SELECT MAX(PRD_DE) FROM kosis_foreigners"),
    ("타지역 통근(KOSIS 지역별고용조사)", "kosis", "반기", "SELECT MAX(PRD_DE) FROM kosis_commute"),
    ("전입전출(KOSIS)", "kosis", "월", "SELECT MAX(PRD_DE) FROM kosis_migration"),
]


def write_source_status(st):
    """자료별 최신 시점을 작은 표 source_status에 적는다(서버 도구는 이 표만 읽어 빠름).
    데이터랩처럼 큰 표는 훑는 데 오래 걸리므로 갱신 뒤 여기서 한 번만 계산한다."""
    con = sqlite3.connect(DB, timeout=60)
    rows = []
    for name, step, unit, sql in SOURCES:
        try:
            latest = con.execute(sql).fetchone()[0]
        except Exception:
            latest = None
        s = st.get(step) or {}
        rows.append((name, unit, None if latest is None else str(latest), s.get("last_ok"), s.get("last_try"),
                     1 if s.get("last_error") else 0))
    con.execute("CREATE TABLE IF NOT EXISTS source_status(name TEXT, unit TEXT, latest TEXT, fetched_ok TEXT, "
                "tried TEXT, last_failed INT)")
    con.execute("DELETE FROM source_status")
    con.executemany("INSERT INTO source_status VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


def manual_due(st):
    """손으로 넣는 자료 중 '다음 갱신' 날짜가 지난 것이 있으면 운영자에게 메일(7일에 한 번)."""
    sys.path.insert(0, str(HERE.parent / "server"))
    import manual_data
    today = now().date().isoformat()
    due_ = [(f, m) for f, m in manual_data.manifest().items() if m.get("다음 갱신") and m["다음 갱신"] <= today]
    last = st.get("_manual_alert")
    if not due_ or (last and (now().date() - dt.date.fromisoformat(last)).days < 7):
        return
    send("자료 확인할 때 됐음: " + ", ".join(m.get("이름", f) for f, m in due_),
         "\n\n".join(f"{m.get('이름', f)} ({f})\n- 자료 기준: {m.get('자료 기준')}\n- 받은 날: {m.get('받은 날')}\n"
                     f"- 출처: {m.get('출처')}\n- 갱신 방법: {m.get('갱신 방법')}" for f, m in due_)
         + "\n\n받아 넣은 뒤 server/manual_data.json의 '받은 날'·'다음 갱신'을 고칠 것.")
    st["_manual_alert"] = today
    save_status(st)


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    with open(LOCK, "w") as lk:
        try:
            fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("다른 갱신이 돌고 있음 → 건너뜀")
            return 0
        if LOG.exists() and LOG.stat().st_size > 5_000_000:      # 로그가 너무 커지면 하나만 남기고 새로
            LOG.replace(LOG.with_suffix(".log.1"))
        st, failed = load_status(), []
        if "--status" in sys.argv:
            write_source_status(st)
            print("source_status 다시 계산함")
            return 0
        with open(LOG, "a") as log:
            for name, cmd, days, timeout in STEPS:
                if args and name not in args:
                    continue
                if not args and "--all" not in sys.argv and not due(st, name, days):
                    continue
                s = st.setdefault(name, {})
                s["last_try"] = now().isoformat(timespec="seconds")
                err = run_step(name, cmd, timeout, log)
                if err:
                    log.flush()
                    s["last_error"] = f"{err}\n{tail(LOG)}"
                    failed.append(name)
                else:
                    s["last_ok"] = s["last_try"]
                    s.pop("last_error", None)
                save_status(st)
                print(name, "실패: " + err if err else "성공", flush=True)
        try:
            write_source_status(st)
        except Exception as e:
            print("source_status 계산 실패:", e, flush=True)
        try:
            manual_due(st)
        except Exception as e:
            print("수작업 자료 확인 실패:", e, flush=True)
        if failed:
            send(f"자료 갱신 실패: {', '.join(failed)}",
                 "\n\n".join(f"[{n}]\n{st[n].get('last_error', '')}" for n in failed)
                 + f"\n\n상태 파일: {STATUS}\n로그: {LOG}")
        return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
