"""server.py의 도구를 인터넷 주소(streamable HTTP, 경로 /mcp)로 띄운다.
안내 페이지(/)와 조회 화면용 주소(/api/...)는 web.py가 맡는다.

포트는 환경변수 PORT(Azure App Service가 넣어 줌)를 쓰고, 없으면 8000.
응답은 SSE 대신 JSON으로 돌려줌(프록시를 거치는 클라이언트와의 호환을 위해).
REQUEST_LOG=1(기본)이면 요청·응답 요약을 로그에 남긴다(연결 문제 진단용, 개인정보 없음).
"""
import datetime as dt
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import uvicorn

from server import mcp
import feedback
import guard
import timing
import web

PORT = int(os.environ.get("PORT", 8000))
REQUEST_LOG = os.environ.get("REQUEST_LOG", "1") == "1"
# 세션 방식: 기본은 요청마다 독립 처리(stateless). MCP_STATELESS=0이면 세션 ID를 주는 방식
# (2026-11-14 세션 방식으로 바꿔도 커넥터 추가 실패가 같아서 원래대로 되돌림)
STATELESS = os.environ.get("MCP_STATELESS", "1") == "1"
# 자동 갱신: Azure에서는 기본으로 켬(AUTO_UPDATE=0이면 끔). 매일 UPDATE_HOUR시(한국 시간)에 pipeline/update.py 실행
# Azure '항상 설정(Always On)'이 꺼져 있으면 요청이 없을 때 서버가 잠들어 갱신도 멈춘다
AUTO_UPDATE = os.environ.get("AUTO_UPDATE", "1" if os.environ.get("WEBSITE_SITE_NAME") else "0") == "1"
UPDATE_HOUR = int(os.environ.get("UPDATE_HOUR", 5))
KST = dt.timezone(dt.timedelta(hours=9))
UPDATE = Path(__file__).resolve().parents[1] / "pipeline" / "update.py"


def _seconds_to_next_run():
    n = dt.datetime.now(KST)
    t = n.replace(hour=UPDATE_HOUR, minute=0, second=0, microsecond=0)
    if t <= n:
        t += dt.timedelta(days=1)
    return (t - n).total_seconds()


def _update_loop():
    time.sleep(300)  # 켜진 직후 한 번(밀린 단계만 따라잡음). 배포 직후 바로 돌지 않게 5분 뒤
    while True:
        print("[update] 시작", flush=True)
        r = subprocess.run([sys.executable, str(UPDATE)], cwd=UPDATE.parents[1])
        print(f"[update] 끝 (코드 {r.returncode})", flush=True)
        web.warm()                     # 새 자료로 지역·주차 목록 다시 만들기
        guard.mark_reload()            # 이 직후 느린 요청은 cold로 표시(속도 모니터링)
        time.sleep(_seconds_to_next_run())


def _summary(body: bytes) -> str:
    try:
        j = json.loads(body or b"null")
    except Exception:
        return f"body={body[:120]!r}"
    items = j if isinstance(j, list) else [j]
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        p = it.get("params") or {}
        extra = ""
        if it.get("method") == "initialize":
            extra = f" proto={p.get('protocolVersion')} client={(p.get('clientInfo') or {}).get('name')}"
        out.append(f"{it.get('method', 'response')}{extra}")
    return ",".join(out)


def _db_ok():
    """DB가 열리고 나우캐스트 표가 있으면 정상."""
    try:
        from server import db
        db.execute("SELECT 1 FROM moves LIMIT 1").fetchone()
        return True
    except Exception:
        return False


class RequestLog:
    """요청 본문에서 JSON-RPC 메서드 이름만 기록하고 그대로 넘긴다."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] == "/health":   # Azure 상태 검사용
            ok = _db_ok()
            await send({"type": "http.response.start", "status": 200 if ok else 503,
                        "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
            await send({"type": "http.response.body", "body": b"ok" if ok else b"db error"})
            return
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        hd = {k.decode().lower(): v.decode(errors="replace") for k, v in scope.get("headers", [])}
        ip = (hd.get("x-client-ip") or hd.get("x-forwarded-for", "").split(",")[0]
              or (scope.get("client") or ("",))[0]).split(":")[0].strip()
        tag = feedback.tag_for(ip)          # IP를 알아볼 수 없게 바꾼 값(하루짜리)
        feedback.client_tag.set(tag)

        # 요청 횟수 제한(guard.py)
        why = guard.limiter.check(tag)
        if why:
            body = json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32000, "message": why}},
                              ensure_ascii=False).encode()
            await send({"type": "http.response.start", "status": 429,
                        "headers": [(b"content-type", b"application/json; charset=utf-8"), (b"retry-after", b"60")]})
            await send({"type": "http.response.body", "body": body})
            return

        if web.handles(scope["path"]):     # 안내 페이지·조회 화면(/, /api/...)
            return await web.app(scope, receive, send)

        chunks, more = [], True
        while more:
            m = await receive()
            chunks.append(m)
            more = m["type"] == "http.request" and m.get("more_body", False)
        body = b"".join(c.get("body", b"") for c in chunks)
        calls = guard.tool_calls(body)
        # 세션 번호(guard.py 설명): 처음 연결이면 새 번호를 응답 헤더로 주고, 아니면 들고 온 번호를 기록에 씀
        new_sid = uuid.uuid4().hex if guard.is_initialize(body) and not hd.get("mcp-session-id") else ""
        session = guard.session_tag(new_sid or hd.get("mcp-session-id", "")) or guard.meta_session(body)
        it = iter(chunks)

        async def replay():
            try:
                return next(it)
            except StopIteration:
                return await receive()

        status, resp = [0], [b""]

        async def capture(m):
            if m["type"] == "http.response.start":
                status[0] = m["status"]
                hs = list(m.get("headers") or [])
                if new_sid and m["status"] == 200 and not any(k.lower() == b"mcp-session-id" for k, _ in hs):
                    hs.append((b"mcp-session-id", new_sid.encode()))
                    m = {**m, "headers": hs}
            elif m["type"] == "http.response.body" and len(resp[0]) < 4000:
                resp[0] += m.get("body", b"")
            await send(m)

        if calls:                          # 도구 요청마다 단계별 시간(timing.py)
            timing.start(calls[0][0], args=calls[0][1])
        t0 = time.time()
        try:
            await self.app(scope, replay, capture)
        finally:
            ok = status[0] == 200 and b'"isError":true' not in resp[0] and b'"error"' not in resp[0][:200]
            rec = timing.end() if calls else None
            guard.record(calls, ok, (time.time() - t0) * 1000, guard.client_name(hd.get("user-agent", "")), rec,
                         session=session, meta=guard.meta_keys(body))
            if REQUEST_LOG:
                print(f"[req] {scope['method']} {scope['path']} ua={hd.get('user-agent', '')[:60]!r} sid={session[:4] or '-'} {_summary(body)} "
                      f"-> {status[0]} {resp[0][:300]!r}", flush=True)


def build_app():
    kwargs = dict(stateless_http=STATELESS, json_response=True, host="0.0.0.0")
    try:  # mcp 2.x
        from mcp.server.transport_security import TransportSecuritySettings
        kwargs["transport_security"] = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    except ImportError:
        pass
    return mcp.streamable_http_app(**kwargs)


if __name__ == "__main__":
    if AUTO_UPDATE:
        threading.Thread(target=_update_loop, daemon=True).start()
    web.warm()                         # 지역 목록을 첫 방문 전에 미리 만들어 둠(처음엔 40초 넘게 걸림)
    uvicorn.run(RequestLog(build_app()), host="0.0.0.0", port=PORT, log_level="info", lifespan="on")
