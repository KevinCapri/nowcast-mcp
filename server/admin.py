"""운영자 화면(/admin): 이용 현황과 들어온 의견을 한 화면에서 본다.

- 앱 설정 ADMIN_PW가 없으면 이 주소는 없는 것처럼(404) 답한다
- 비밀번호(ADMIN_PW) 하나만 넣는 로그인 화면. 로그아웃 버튼, 30분 동안 쓰지 않으면 저절로 로그아웃, 브라우저를 닫아도 로그아웃
- 검색엔진에 잡히지 않게 noindex. 사이트 어디에도 링크를 두지 않는다
- 보여 주는 것: 기간별 콜 수(MCP 도구 / 사이트 조회), 도구별·연결한 곳별 콜 수와 실패, 많이 물은 지역,
  최근 호출, 들어온 의견. 이름·IP는 원래 남기지 않으므로 보여 줄 것도 없다
"""
import datetime as dt
import hashlib
import hmac
import html
import json
import os
import time
from collections import Counter, defaultdict
from urllib.parse import parse_qs

from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse

import feedback
import guard

KST = dt.timezone(dt.timedelta(hours=9))
TOOL_KO = {
    "analyze_region_week": "한 주 판정", "find_unusual_regions": "튄 지역 찾기", "get_movement": "이동량 조회",
    "get_breakdown": "구성 나눠 보기", "get_weather": "날씨", "get_datalab_daily": "날짜별 방문자",
    "estimate_life_pop": "생활인구 추정", "forecast_festival": "축제 예측", "get_region_calendar": "지역 일정", "get_bloom_foliage": "벚꽃·단풍", "compare_same_week": "작년 같은 주", "get_metadata": "자료 설명",
    "list_regions": "지역 이름 찾기", "get_data_status": "자료 상태", "report_issue": "의견 신고",
    "web:view": "사이트 지역 조회", "web:fest": "사이트 축제 조회", "web:feedback": "사이트 의견 보내기",
}


COOKIE = "nc_admin"
IDLE_MIN = 30          # 아무것도 안 하고 30분 지나면 로그아웃


def _sign(exp: str) -> str:
    return hmac.new(os.environ.get("ADMIN_PW", "").encode(), f"admin|{exp}".encode(), hashlib.sha256).hexdigest()


def _authorized(request: Request) -> bool:
    """로그인 쿠키(만료 시각 + 비밀번호로 만든 서명)가 맞는지. 화면을 열 때마다 만료를 30분 뒤로 미룬다.
    비밀번호를 바꾸면 기존 로그인은 모두 풀린다. 브라우저를 닫아도 풀린다(세션 쿠키)."""
    v = request.cookies.get(COOKIE, "")
    exp, _, sig = v.partition(".")
    if not exp.isdigit() or int(exp) < int(time.time()):
        return False
    return hmac.compare_digest(sig, _sign(exp))


def _cookie(r, exp):
    r.set_cookie(COOKIE, f"{exp}.{_sign(exp)}", httponly=True, secure=True, samesite="strict", path="/admin")


def _login_page(msg=""):
    m = f'<p class="err">{_e(msg)}</p>' if msg else ""
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="robots" content="noindex, nofollow"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>로그인</title><style>
body{{font-family:Pretendard,'Noto Sans KR',system-ui,sans-serif;background:#f5f6f8;color:#1f2937;margin:0}}
form{{max-width:320px;margin:18vh auto;background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:24px}}
input{{width:100%;box-sizing:border-box;height:42px;border:1px solid #d1d5db;border-radius:8px;padding:0 12px;font:inherit}}
button{{margin-top:12px;width:100%;height:42px;border:0;border-radius:8px;background:#1e3a8a;color:#fff;font:inherit;cursor:pointer}}
.err{{color:#c2410c;font-size:14px;margin:10px 0 0}}
</style></head><body><form method="post" action="/admin">
<input type="password" name="pw" placeholder="비밀번호" autocomplete="current-password" autofocus required>
<button type="submit">들어가기</button>{m}</form></body></html>"""


def _e(x):
    return html.escape(str(x if x is not None else ""))


def _region(args):
    try:
        a = json.loads(args or "{}")
    except Exception:
        return ""
    sd, sg = a.get("sido", ""), a.get("sigungu") or a.get("item", "")
    return (sd + " " + sg).strip() if (sd or sg) else ""


def _bars(rows, width=170):
    """[(이름, 값, 실패)] → 가로 막대 표."""
    if not rows:
        return '<p class="none">기록 없음</p>'
    mx = max(v for _, v, _ in rows) or 1
    out = ['<table class="bars">']
    for name, v, bad in rows:
        w = max(2, int(width * v / mx))
        b = f' <span class="bad">실패 {bad}</span>' if bad else ""
        out.append(f'<tr><td class="k">{_e(name)}</td><td><i style="width:{w}px"></i> {v:,}{b}</td></tr>')
    out.append("</table>")
    return "".join(out)


def _page(days):
    con = guard._con()
    since = (dt.datetime.now(KST) - dt.timedelta(days=days)).isoformat()
    rows = con.execute("SELECT at, tool, args, ok, ms, COALESCE(client,'') FROM usage WHERE at>=? ORDER BY at",
                       (since,)).fetchall()
    con.close()
    allmcp = [r for r in rows if not r[1].startswith("web:")]
    mcp = [r for r in allmcp if r[1] in TOOL_KO]          # 실제 도구 호출만
    probes = len(allmcp) - len(mcp)                        # 없는 도구 이름으로 온 호출(레지스트리·검사기의 점검)
    web = [r for r in rows if r[1].startswith("web:")]

    # 날짜별
    byday = defaultdict(lambda: [0, 0])
    for r in rows:
        byday[r[0][:10]][1 if r[1].startswith("web:") else 0] += 1
    today = dt.datetime.now(KST).date()
    # 30일까지는 하루씩, 90일은 주(월요일 시작)씩, 365일은 달씩 묶음
    if days <= 30:
        unit, key = "일", (lambda d: d)
    elif days <= 90:
        unit, key = "주", (lambda d: d - dt.timedelta(days=d.weekday()))
    else:
        unit, key = "월", (lambda d: d.replace(day=1))
    start = today - dt.timedelta(days=days - 1)
    buckets = []
    for i in range(days):
        k = key(start + dt.timedelta(days=i))
        if not buckets or buckets[-1] != k:
            buckets.append(k)
    agg = defaultdict(lambda: [0, 0])
    for d, (a, b) in byday.items():
        try:
            dd = dt.date.fromisoformat(d)
        except ValueError:
            continue
        if dd >= start:
            k = agg[key(dd)]; k[0] += a; k[1] += b
    mx = max([sum(v) for v in agg.values()] or [1]) or 1
    ttl = {"일": "날짜별", "주": "주별", "월": "달별"}[unit]
    day_html = ['<div class="days">']
    for k in buckets:
        a, b = agg.get(k, [0, 0])
        ha, hb = int(120 * a / mx), int(120 * b / mx)
        if unit == "일":
            # 30일 보기는 1일·5일 간격만 숫자를 적어 겹치지 않게
            lab = f"{k.day}" if days <= 7 or k.day == 1 or k.day % 5 == 0 else ""
            tip = k.isoformat()
        elif unit == "주":
            lab, tip = f"{k.month}/{k.day}", f"{k.isoformat()} 주"
        else:
            lab, tip = f"{k.month}월", f"{k.year}년 {k.month}월"
        day_html.append(f'<div class="d" title="{tip}: MCP {a}, 사이트 {b}"><div class="col">'
                        f'<i class="w" style="height:{hb}px"></i><i class="m" style="height:{ha}px"></i></div>'
                        f'<span>{lab}</span></div>')
    day_html.append("</div>")

    def group(rs, key):
        c, bad = Counter(), Counter()
        for r in rs:
            k = key(r)
            c[k] += 1
            if not r[3]:
                bad[k] += 1
        return [(k, v, bad[k]) for k, v in c.most_common()]

    tools = group(mcp, lambda r: TOOL_KO.get(r[1], r[1]))
    clients = group(mcp, lambda r: r[5] or "기록 전")
    webk = group(web, lambda r: TOOL_KO.get(r[1], r[1]))
    regions = Counter(_region(r[2]) for r in rows if _region(r[2]))
    reg_rows = [(k, v, 0) for k, v in regions.most_common(15)]

    def rec_table(rs, n):
        out = ['<table class="list"><tr><th>시각</th><th>어디서</th><th>도구</th><th>입력</th><th>결과</th><th>걸린 시간</th></tr>']
        for at, tool, args, ok, ms, client in rs[-n:][::-1]:
            out.append(f"<tr><td>{_e(at[5:16].replace('T', ' '))}</td><td>{_e(client or '–')}</td>"
                       f"<td>{_e(TOOL_KO.get(tool, tool))}</td><td class='a'>{_e((args or '')[:120])}</td>"
                       f"<td>{'성공' if ok else '<b class=bad>실패</b>'}</td><td>{ms:,}ms</td></tr>")
        out.append("</table>" if rs else '</table><p class="none">기록 없음</p>')
        return "".join(out)
    rec_html = [rec_table(mcp, 50)]
    rec_web = rec_table(web, 20)

    try:
        fc = feedback._con()
        fb = fc.execute("SELECT id, created, kind, region, period, tool, message, status, COALESCE(link,'') FROM feedback "
                        "ORDER BY created DESC LIMIT 50").fetchall()
        fb_total = fc.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
        fc.close()
    except Exception:
        fb, fb_total = [], 0
    fb_html = ['<table class="list"><tr><th>접수</th><th>시각</th><th>종류</th><th>지역·기간</th><th>내용</th><th>상태</th></tr>']
    for fid, created, kind, region, period, tool, msg, status, link in fb:
        fb_html.append(f"<tr><td>{_e(fid)}</td><td>{_e((created or '')[5:16].replace('T', ' '))}</td><td>{_e(kind)}</td>"
                       f"<td>{_e(' '.join(x for x in (region, period) if x))}</td><td class='a'>{_e(msg)}"
                       + (f"<br><a href='{_e(link)}' rel='noopener noreferrer nofollow' target='_blank'>{_e(link)}</a>" if link else "")
                       + f"</td><td>{_e(status)}</td></tr>")
    fb_html.append("</table>" if fb else '</table><p class="none">들어온 의견 없음</p>')

    ok_rate = (sum(r[3] for r in mcp) / len(mcp) * 100) if mcp else 0
    avg_ms = int(sum(r[4] for r in mcp) / len(mcp)) if mcp else 0
    sel = "".join(f'<a href="?days={d}"{" class=on" if d == days else ""}>{d}일</a>' for d in (7, 30, 90, 365))
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="robots" content="noindex, nofollow"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>운영 현황</title><style>
body{{font-family:Pretendard,'Noto Sans KR',system-ui,sans-serif;background:#f5f6f8;color:#1f2937;margin:0}}
.wrap{{max-width:1080px;margin:0 auto;padding:24px 16px 60px}} h1{{font-size:22px;margin:0 0 4px}}
h2{{font-size:16px;margin:0 0 12px}} .sub{{color:#6b7280;font-size:13px}}
.sel a{{display:inline-block;padding:6px 12px;margin:10px 6px 4px 0;border:1px solid #d1d5db;border-radius:8px;color:#374151;text-decoration:none;font-size:14px}}
.sel a.on{{background:#1e3a8a;color:#fff;border-color:#1e3a8a}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:14px 0}}
.c{{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:14px}} .c b{{display:block;font-size:24px;margin-top:4px}}
.card{{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:18px;margin:12px 0;overflow-x:auto}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:12px}} @media(max-width:760px){{.two{{grid-template-columns:1fr}}}}
.two .card{{margin:0}}
.days{{display:flex;gap:2px;align-items:flex-end;min-height:140px}} .d{{flex:1;min-width:0;text-align:center;font-size:10px;color:#9ca3af;white-space:nowrap;overflow:hidden}}
.d .col{{display:flex;flex-direction:column;justify-content:flex-end;height:122px}} .d i{{display:block;width:100%}}
.d .m{{background:#2563eb}} .d .w{{background:#93c5fd}}
.lg i{{display:inline-block;width:10px;height:10px;margin:0 4px 0 10px;vertical-align:-1px}}
table.bars td{{padding:3px 6px;font-size:14px;white-space:nowrap}} table.bars td.k{{max-width:11em;overflow:hidden;text-overflow:ellipsis}} table.bars td.k{{color:#374151}}
table.bars i{{display:inline-block;height:10px;background:#2563eb;border-radius:2px;vertical-align:0}}
table.list{{border-collapse:collapse;width:100%;font-size:13px}} table.list th,table.list td{{border-bottom:1px solid #eef0f3;padding:6px;text-align:left;vertical-align:top;white-space:nowrap}} table.list td.a{{white-space:normal}}
table.list th{{color:#6b7280;font-weight:500}} td.a{{color:#4b5563;word-break:break-all}} .bad{{color:#c2410c}} .none{{color:#9ca3af;font-size:14px}}
</style></head><body><div class="wrap">
<form method="post" action="/admin" style="float:right;margin:0"><input type="hidden" name="logout" value="1">
<button style="height:34px;padding:0 14px;border:1px solid #d1d5db;border-radius:8px;background:#fff;font:inherit;font-size:14px;cursor:pointer">로그아웃</button></form>
<h1>운영 현황</h1><div class="sub">빅데이터 Insight: 지역별 유입인구 · 이름·IP는 남기지 않음 · 한국 시간 기준 · 30분 동안 아무것도 안 하면 로그아웃</div>
<div class="sel">{sel}</div>
<div class="cards">
<div class="c">MCP 콜 수<b>{len(mcp):,}</b></div>
<div class="c">MCP 성공률<b>{ok_rate:.1f}%</b></div>
<div class="c">MCP 평균 응답<b>{avg_ms:,}ms</b></div>
<div class="c">사이트 조회<b>{len(web):,}</b></div>
<div class="c">들어온 의견(전체)<b>{fb_total:,}</b></div>
</div>
<div class="card"><h2>{ttl} <span class="lg sub"><i style="background:#2563eb"></i>MCP 콜<i style="background:#93c5fd"></i>사이트 조회</span></h2>{''.join(day_html)}</div>
<div class="two">
<div class="card"><h2>도구별 MCP 콜</h2>{_bars(tools)}{f'<p class="sub">외부 점검 호출(없는 도구 이름) {probes:,}건은 뺐습니다.</p>' if probes else ''}</div>
<div class="card"><h2>어디서 왔나 (MCP)</h2>{_bars(clients)}<p class="sub">요청에 실린 앱 정보로 짐작한 이름. 2026-11-15 이전 기록은 '기록 전'.</p></div>
<div class="card"><h2>사이트 조회</h2>{_bars(webk)}</div>
<div class="card"><h2>많이 물은 지역 (상위 15)</h2>{_bars(reg_rows)}</div>
</div>
<div class="card"><h2>최근 MCP 호출 50건</h2>{''.join(rec_html)}</div>
<div class="card"><h2>최근 사이트 조회 20건</h2>{rec_web}</div>
<div class="card"><h2>들어온 의견 (최근 50건)</h2>{''.join(fb_html)}</div>
</div></body></html>"""


_fails = []          # 비밀번호 틀린 시각(서비스 전체 1분에 5번까지)


async def admin(request: Request):
    if not os.environ.get("ADMIN_PW"):
        return PlainTextResponse("Not Found", status_code=404)
    nohead = {"X-Robots-Tag": "noindex", "Cache-Control": "no-store"}
    if request.method == "POST":
        now = time.time()
        _fails[:] = [t for t in _fails if now - t < 60]
        if len(_fails) >= 5:
            return HTMLResponse(_login_page("잠시 뒤 다시 시도해 주세요."), status_code=429, headers=nohead)
        q = parse_qs((await request.body())[:2000].decode("utf-8", "replace"))
        if q.get("logout"):
            r = HTMLResponse(_login_page("로그아웃했습니다."), headers=nohead)
            r.delete_cookie(COOKIE, path="/admin")
            return r
        pw = (q.get("pw") or [""])[0]
        if not hmac.compare_digest(pw.encode(), os.environ["ADMIN_PW"].encode()):
            _fails.append(now)
            return HTMLResponse(_login_page("비밀번호가 맞지 않습니다."), status_code=401, headers=nohead)
        r = RedirectResponse("/admin", status_code=303, headers=nohead)
        _cookie(r, str(int(now) + IDLE_MIN * 60))
        return r
    if not _authorized(request):
        msg = "오래 쓰지 않아 로그아웃됐습니다." if request.cookies.get(COOKIE) else ""
        r = HTMLResponse(_login_page(msg), headers=nohead)
        if msg:
            r.delete_cookie(COOKIE, path="/admin")
        return r
    try:
        days = int(request.query_params.get("days", "30"))
    except ValueError:
        days = 30
    days = max(1, min(days, 365))
    r = HTMLResponse(_page(days) + _IDLE_JS, headers=nohead)
    _cookie(r, str(int(time.time()) + IDLE_MIN * 60))       # 쓸 때마다 30분 연장
    return r


# 화면을 열어 둔 채 30분 지나면 저절로 로그인 화면으로
_IDLE_JS = f"""<script>(function(){{var t;function arm(){{clearTimeout(t);t=setTimeout(function(){{location.href='/admin';}},{IDLE_MIN * 60 * 1000});}}
['click','keydown','scroll','mousemove'].forEach(function(e){{addEventListener(e,arm,{{passive:true}});}});arm();}})();</script>"""
