"""문제 알리기(의견 보내기): 이용자가 잘못된 값·설명·오류를 알리면 저장한다(한 방향 전송, 2026-11-15부터 상태 확인 없음).

규칙(2026-11-14 결정):
- 공백만이면 거절, 1,000자 이하
- 같은 곳에서 1분에 1번(보낸 곳은 IP를 알아볼 수 없게 바꾼 값만, 하루 지나면 지움)
- 서비스 전체 하루 FEEDBACK_DAILY_MAX(기본 200)개. 넘으면 받지 않고 운영자에게 메일 1번
- 이름·연락처·IP 원본은 받지도 저장하지도 않는다
- 저장 위치: DB와 같은 폴더의 feedback.db(서비스 자료와 분리)
- 운영자는 SSH에서 상태를 바꾼다: python server/feedback.py set <접수번호> <확인 중|반영|반려> "메모"
"""
import contextvars
import datetime as dt
import hashlib
import os
import re
import secrets
import sqlite3
import sys
import threading
from pathlib import Path

KST = dt.timezone(dt.timedelta(hours=9))
MAX_LEN = 1000
DAILY_MAX = int(os.environ.get("FEEDBACK_DAILY_MAX", 200))
PER_SOURCE_SECONDS = 60
STATUSES = ("접수", "확인 중", "반영", "반려")

# 요청을 보낸 곳(IP를 알아볼 수 없게 바꾼 값). server_http.py가 요청마다 넣어 준다
client_tag = contextvars.ContextVar("client_tag", default="")
_salt = secrets.token_hex(16)        # 서버가 켜질 때마다 바뀜 → 같은 IP라도 날이 지나면 이어 볼 수 없음


def tag_for(ip: str) -> str:
    day = dt.datetime.now(KST).strftime("%Y%m%d")
    return hashlib.sha256(f"{_salt}|{day}|{ip}".encode()).hexdigest()[:16] if ip else ""


def _db_path():
    from server import DB          # 서비스 DB와 같은 폴더
    return Path(DB).with_name("feedback.db")


_lock = threading.Lock()


def _con():
    con = sqlite3.connect(_db_path(), timeout=30)
    con.execute("CREATE TABLE IF NOT EXISTS feedback(id TEXT PRIMARY KEY, key_hash TEXT, created TEXT, kind TEXT, "
                "region TEXT, period TEXT, tool TEXT, message TEXT, status TEXT, note TEXT, updated TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS sender(tag TEXT, at TEXT)")
    if "link" not in [r[1] for r in con.execute("PRAGMA table_info(feedback)")]:
        con.execute("ALTER TABLE feedback ADD COLUMN link TEXT DEFAULT ''")
    return con


def clean_link(link):
    """자료 추가 요청에 붙는 링크: http(s) 주소 하나만, 300자 이하. 아니면 빈 값."""
    u = (link or "").strip().split()[0] if (link or "").strip() else ""
    return u[:300] if re.match(r"^https?://[^\s<>\"']+$", u) else ""


def _now():
    return dt.datetime.now(KST)


def _notify(subject, body):
    def run():
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
            from notify import send
            send(subject, body)
        except Exception as e:
            print("[feedback] 메일 실패", e, flush=True)
    threading.Thread(target=run, daemon=True).start()


def submit(message, kind="", region="", period="", tool="", link=""):
    text = (message or "").strip()
    if not text:
        return {"error": "내용이 비어 있다."}
    if len(text) > MAX_LEN:
        return {"error": f"{MAX_LEN}자 이하로 줄여 달라."}
    now = _now()
    tag = client_tag.get()
    with _lock:
        con = _con()
        # 하루 지난 '보낸 곳' 기록은 지움
        con.execute("DELETE FROM sender WHERE at < ?", ((now - dt.timedelta(days=1)).isoformat(),))
        if tag:
            last = con.execute("SELECT MAX(at) FROM sender WHERE tag=?", (tag,)).fetchone()[0]
            if last and (now - dt.datetime.fromisoformat(last)).total_seconds() < PER_SOURCE_SECONDS:
                con.commit()
                return {"error": "같은 곳에서는 1분에 한 번만 보낼 수 있다. 잠시 뒤 다시 보내 달라."}
        today = now.strftime("%Y-%m-%d")
        n_today = con.execute("SELECT COUNT(*) FROM feedback WHERE substr(created,1,10)=?", (today,)).fetchone()[0]
        if n_today >= DAILY_MAX:
            con.commit()
            if n_today == DAILY_MAX:     # 처음 넘었을 때 한 번만
                con.execute("INSERT INTO feedback(id, created, kind, message, status) VALUES (?,?,?,?,?)",
                            (f"LIMIT-{today}", now.isoformat(timespec='seconds'), "상한", "하루 상한 도달", "반려"))
                con.commit()
                _notify("의견 하루 상한 도달", f"{today} 의견이 {DAILY_MAX}개에 이르러 더 받지 않음.")
            return {"error": "오늘은 의견을 더 받을 수 없다. 내일 다시 보내 달라."}
        rid = "R" + now.strftime("%y%m%d") + "-" + secrets.token_hex(3)
        key = secrets.token_urlsafe(9)
        link = clean_link(link)
        con.execute("INSERT INTO feedback(id, key_hash, created, kind, region, period, tool, message, status, note, "
                    "updated, link) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rid, hashlib.sha256(key.encode()).hexdigest(), now.isoformat(timespec="seconds"),
                     kind[:30], region[:60], period[:40], tool[:40], text, "접수", "", None, link))
        if tag:
            con.execute("INSERT INTO sender VALUES (?,?)", (tag, now.isoformat()))
        con.commit()
    _notify(f"의견 접수 {rid}", f"종류: {kind}\n지역: {region}\n기간: {period}\n도구: {tool}\n링크: {link}\n\n{text}")
    # 2026-11-15: 한 방향 전송만. 접수 번호·확인 키는 운영자 기록용으로만 남기고 이용자에게 돌려주지 않는다.
    return {"받음": True,
            "이용자에게 전할 말": "개발자에게 전달했어요. 따로 답장은 없고, 반영되면 서비스에 바로 적용돼요.",
            "안내": "위 '이용자에게 전할 말'을 존댓말 그대로 전할 것(연락처를 받지 않아 답장은 하지 않는다)."}


# 0.6.5 ③: 기다려도 풀리지 않는 '할 수 없음' 결과 끝에 붙이는 한 줄(AI에게 주는 신호, 서버 안내문 ①과 짝).
# 입력 형식 오류·아직 안 나온 최근 자료에는 붙이지 않는다.
HINT = ("원하면 개발자에게 이 자료·기능을 전달할 수 있다고 이 대화에서 한 번만 안내할 것"
        "(이용자가 그러겠다고 할 때만 report_issue).")


def cant(d, reason=""):
    """'할 수 없음' 결과에 의견 보내기 줄을 붙여 돌려준다. reason은 짧은 이유(못 한 응답 기록용)."""
    d["의견 보내기"] = HINT
    if reason:                       # 0.6.5 ④: 이용 기록의 '못 한 이유' 칸으로(guard.record)
        try:
            import timing
            timing.mark("cant", reason[:40])
        except Exception:
            pass
    return d


def status(report_id, key):
    con = _con()
    r = con.execute("SELECT key_hash, created, status, note, updated FROM feedback WHERE id=?", (report_id,)).fetchone()
    if not r or not key or r[0] != hashlib.sha256(key.encode()).hexdigest():
        return {"error": "접수 번호나 확인 키가 맞지 않는다."}
    return {"접수 번호": report_id, "접수 시각": r[1], "상태": r[2], "운영자 메모": r[3] or None, "바뀐 시각": r[4]}


if __name__ == "__main__":      # 운영자용: 목록 보기·상태 바꾸기
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    con = _con()
    if len(sys.argv) >= 4 and sys.argv[1] == "set":
        st = sys.argv[3]
        if st not in STATUSES:
            sys.exit(f"상태는 {STATUSES} 중 하나")
        cur = con.execute("UPDATE feedback SET status=?, note=?, updated=? WHERE id=?",
                          (st, sys.argv[4] if len(sys.argv) > 4 else "", _now().isoformat(timespec="seconds"), sys.argv[2]))
        con.commit()
        print("바꿈" if cur.rowcount else "그런 접수 번호 없음", sys.argv[2], st, "| DB:", _db_path())
    else:
        for row in con.execute("SELECT id, created, status, kind, region, period, substr(message,1,80) FROM feedback "
                               "ORDER BY created DESC LIMIT 50"):
            print(" | ".join(str(x) for x in row))
