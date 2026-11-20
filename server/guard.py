"""서비스 보호: 요청 횟수 제한과 이용 기록.

요청 횟수 제한
- Claude·ChatGPT 같은 AI 서비스는 여러 이용자의 요청을 같은 서버(IP)에서 보낸다.
  그래서 IP별 제한은 넉넉히 두고(한 곳이 서비스를 독차지하는 것만 막음), 서비스 전체 상한으로 비용·부하를 지킨다.
- RATE_PER_MIN(기본 300): 같은 곳(하루짜리 해시)에서 1분에 보낼 수 있는 요청 수
- RATE_GLOBAL_PER_HOUR(기본 6000): 서비스 전체 1시간 요청 수
- 넘으면 HTTP 429와 '잠시 뒤 다시' 안내. 전체 상한에 처음 걸리면 운영자에게 메일 1번(시간마다)

이용 기록(usage.db, DB와 같은 폴더)
- 도구 호출마다: 시각, 도구 이름, 입력값(지역·기간 등), 결과 상태, 걸린 시간, 연결한 곳(Claude·ChatGPT·PlayMCP·사이트 등
  짧은 이름만). IP·접속 정보 원본은 남기지 않는다
- 운영자 화면: /admin (앱 설정 ADMIN_PW가 있을 때만 열림, 비밀번호만 넣는 로그인 화면)
- 1년 지난 기록은 지움
- 속도 측정(GitHub Actions, User-Agent에 insight-monitor)의 요청은 연결한 곳을 '측정'으로 남기고, 이용 집계에서는 뺀다

느린 요청(속도 모니터링, 2026-11-21)
- 이용자 요청(측정 제외) 가운데 SLOW_MS(기본 10000ms) 넘게 걸린 것을 usage.db의 slow 표에 따로 남긴다:
  시각, 도구, 입력값, 걸린 시간, 연결한 곳, 단계별 시간(timing.py), 서버가 막 켜졌거나 자료를 다시 읽은 직후인지(cold)
- /api/perf(web.py, MONITOR_KEY가 있을 때만)로 GitHub Actions가 받아 가서 매일 다시 잰다
- 보기: python server/guard.py [일수]   → 도구별·지역별 호출 수
"""
import collections
import datetime as dt
import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

KST = dt.timezone(dt.timedelta(hours=9))
RATE_PER_MIN = int(os.environ.get("RATE_PER_MIN", 300))
RATE_GLOBAL_PER_HOUR = int(os.environ.get("RATE_GLOBAL_PER_HOUR", 6000))
KEEP_DAYS = 365
SLOW_MS = int(os.environ.get("SLOW_MS", 10000))
COLD_SEC = 300                      # 서버 시작·자료 다시 읽기 뒤 이 시간 안의 요청은 cold로 표시
MONITOR_CLIENT = "측정"
_started = time.time()
_reloaded = [_started]


def mark_reload():
    """자동 갱신 뒤 자료를 다시 읽을 때 server_http가 부른다(그 직후 느린 요청은 cold로 표시)."""
    _reloaded[0] = time.time()


class Limiter:
    def __init__(self):
        self.per = collections.defaultdict(collections.deque)
        self.glob = collections.deque()
        self.lock = threading.Lock()
        self.alerted_hour = None

    def check(self, tag):
        """허용이면 None, 막으면 이유 문자열."""
        now = time.time()
        with self.lock:
            g = self.glob
            while g and now - g[0] > 3600:
                g.popleft()
            if len(g) >= RATE_GLOBAL_PER_HOUR:
                hour = int(now // 3600)
                if self.alerted_hour != hour:
                    self.alerted_hour = hour
                    _alert(f"요청 전체 상한 도달({RATE_GLOBAL_PER_HOUR}/시간)")
                return "서비스 전체 요청이 많아 잠시 받지 않는다. 몇 분 뒤 다시 시도할 것."
            if tag:
                q = self.per[tag]
                while q and now - q[0] > 60:
                    q.popleft()
                if len(q) >= RATE_PER_MIN:
                    return "같은 곳에서 요청이 너무 많다. 1분쯤 뒤 다시 시도할 것."
                q.append(now)
                if len(self.per) > 5000:          # 오래된 칸 정리
                    for k in [k for k, v in self.per.items() if not v or now - v[-1] > 120]:
                        del self.per[k]
            g.append(now)
        return None


limiter = Limiter()


def _alert(subject):
    def run():
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
            from notify import send
            send(subject, subject)
        except Exception as e:
            print("[guard] 메일 실패", e, flush=True)
    threading.Thread(target=run, daemon=True).start()


def _db_path():
    from server import DB
    return Path(DB).with_name("usage.db")


_wlock = threading.Lock()


def _con():
    con = sqlite3.connect(_db_path(), timeout=30)
    con.execute("CREATE TABLE IF NOT EXISTS usage(at TEXT, tool TEXT, args TEXT, ok INT, ms INT)")
    if "client" not in [r[1] for r in con.execute("PRAGMA table_info(usage)")]:
        con.execute("ALTER TABLE usage ADD COLUMN client TEXT DEFAULT ''")
    con.execute("CREATE TABLE IF NOT EXISTS slow(at TEXT, tool TEXT, args TEXT, ms INT, client TEXT, cold INT, "
                "steps TEXT)")
    return con


def client_name(user_agent: str) -> str:
    """요청을 보낸 AI 서비스를 짧은 이름으로(원래 문자열은 남기지 않음)."""
    u = (user_agent or "").lower()
    if "insight-monitor" in u:
        return MONITOR_CLIENT
    for key, name in (("claude", "Claude"), ("anthropic", "Claude"), ("openai", "ChatGPT"), ("chatgpt", "ChatGPT"),
                      ("playmcp", "PlayMCP"), ("kakao", "PlayMCP"), ("cursor", "Cursor"), ("gemini", "Gemini"),
                      ("google", "Gemini"), ("copilot", "Copilot"), ("vscode", "VS Code")):
        if key in u:
            return name
    return "기타" if u else "알 수 없음"


def tool_calls(body: bytes):
    """요청 본문에서 tools/call의 (도구 이름, 입력값)을 꺼낸다."""
    try:
        j = json.loads(body or b"null")
    except Exception:
        return []
    out = []
    for it in (j if isinstance(j, list) else [j]):
        if isinstance(it, dict) and it.get("method") == "tools/call":
            p = it.get("params") or {}
            args = p.get("arguments") or {}
            if p.get("name") == "report_issue":           # 의견 본문은 이용 기록에 남기지 않음(feedback.db에만)
                args = {k: v for k, v in args.items() if k != "message"}
            out.append((str(p.get("name", ""))[:60], json.dumps(args, ensure_ascii=False)[:500]))
    return out


def record(calls, ok, ms, client="", steps=None):
    if not calls:
        return
    cold = int(time.time() - _reloaded[0] < COLD_SEC + ms / 1000)
    slow = ms >= SLOW_MS and client != MONITOR_CLIENT

    def run():
        try:
            with _wlock:
                con = _con()
                now = dt.datetime.now(KST)
                at = now.isoformat(timespec="seconds")
                con.executemany("INSERT INTO usage(at, tool, args, ok, ms, client) VALUES (?,?,?,?,?,?)",
                                [(at, t, a, int(ok), int(ms),
                                  client or ("사이트" if t.startswith("web:") else "")) for t, a in calls])
                if slow:
                    con.executemany("INSERT INTO slow(at, tool, args, ms, client, cold, steps) VALUES (?,?,?,?,?,?,?)",
                                    [(at, t, a, int(ms), client, cold,
                                      json.dumps((steps or {}).get("steps", []), ensure_ascii=False)) for t, a in calls])
                if now.minute == 0:     # 가끔 오래된 기록 정리
                    old = (now - dt.timedelta(days=KEEP_DAYS)).isoformat()
                    con.execute("DELETE FROM usage WHERE at < ?", (old,))
                    con.execute("DELETE FROM slow WHERE at < ?", (old,))
                con.commit()
                con.close()
        except Exception as e:
            print("[guard] 기록 실패", e, flush=True)
    threading.Thread(target=run, daemon=True).start()


def perf(since_slow, since_monitor):
    """/api/perf용: 느린 이용자 요청과 측정 요청 기록."""
    con = _con()
    try:
        slow = [dict(zip(("at", "tool", "args", "ms", "client", "cold", "steps"), r)) for r in con.execute(
            "SELECT at, tool, args, ms, client, cold, steps FROM slow WHERE at>=? ORDER BY at", (since_slow,))]
        for r in slow:
            r["steps"] = json.loads(r["steps"] or "[]")
        mon = [dict(zip(("at", "tool", "args", "ok", "ms"), r)) for r in con.execute(
            "SELECT at, tool, args, ok, ms FROM usage WHERE client=? AND at>=? ORDER BY at",
            (MONITOR_CLIENT, since_monitor))]
        return {"slow_ms": SLOW_MS, "slow": slow, "monitor": mon}
    finally:
        con.close()


if __name__ == "__main__":      # 운영자용 요약
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    con = _con()
    since = (dt.datetime.now(KST) - dt.timedelta(days=days)).isoformat()
    print(f"최근 {days}일 도구별 호출(성공/전체, 평균 ms)")
    for row in con.execute("SELECT tool, SUM(ok), COUNT(*), CAST(AVG(ms) AS INT) FROM usage WHERE at>=? AND client!=? "
                           "GROUP BY tool ORDER BY 3 DESC", (since, MONITOR_CLIENT)):
        print(" ", *row)
    print("많이 물은 입력값 상위 15")
    for row in con.execute("SELECT args, COUNT(*) FROM usage WHERE at>=? AND client!=? GROUP BY args ORDER BY 2 DESC "
                           "LIMIT 15", (since, MONITOR_CLIENT)):
        print(" ", row[1], row[0][:150])
