"""운영자 화면(/admin): 이용 현황과 들어온 의견을 한 화면에서 본다.

- 앱 설정 ADMIN_PW가 없으면 이 주소는 없는 것처럼(404) 답한다
- 브라우저 로그인 창(Basic 인증)에 아무 이름과 ADMIN_PW를 넣으면 열린다
- 검색엔진에 잡히지 않게 noindex. 사이트 어디에도 링크를 두지 않는다
- 보여 주는 것: 기간별 콜 수(MCP 도구 / 사이트 조회), 도구별·연결한 곳별 콜 수와 실패, 많이 물은 지역,
  최근 호출, 들어온 의견. 이름·IP는 원래 남기지 않으므로 보여 줄 것도 없다
"""
import base64
import datetime as dt
import hmac
import html
import json
import os
from collections import Counter, defaultdict

from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse

import feedback
import guard

KST = dt.timezone(dt.timedelta(hours=9))
TOOL_KO = {
    "analyze_region_week": "한 주 판정", "find_unusual_regions": "튄 지역 찾기", "get_movement": "이동량 조회",
    "get_breakdown": "구성 나눠 보기", "get_weather": "날씨", "get_datalab_daily": "날짜별 방문자",
    "estimate_life_pop": "생활인구 추정", "forecast_festival": "축제 예측", "get_metadata": "자료 설명",
    "list_regions": "지역 이름 찾기", "get_data_status": "자료 상태", "report_issue": "의견 신고",
    "web:view": "사이트 지역 조회", "web:fest": "사이트 축제 조회", "web:feedback": "사이트 의견 보내기",
}


def _authorized(request: Request) -> bool:
    pw = os.environ.get("ADMIN_PW", "")
    h = request.headers.get("authorization", "")
    if not h.lower().startswith("basic "):
        return False
    try:
        given = base64.b64decode(h[6:]).decode("utf-8", "replace").split(":", 1)[1]
    except Exception:
        return False
    return hmac.compare_digest(given.encode(), pw.encode())


def _e(x):
    return html.escape(str(x if x is not None else ""))


def _region(args):
    try:
        a = json.loads(args or "{}")
    except Exception:
        return ""
    sd, sg = a.get("sido", ""), a.get("sigungu") or a.get("item", "")
    return (sd + " " + sg).strip() if (sd or sg) else ""


def _bars(rows, width=260):
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
    mcp = [r for r in rows if not r[1].startswith("web:")]
    web = [r for r in rows if r[1].startswith("web:")]

    # 날짜별
    byday = defaultdict(lambda: [0, 0])
    for r in rows:
        byday[r[0][:10]][1 if r[1].startswith("web:") else 0] += 1
    today = dt.datetime.now(KST).date()
    days_list = [(today - dt.timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    mx = max([sum(v) for v in byday.values()] or [1]) or 1
    day_html = ['<div class="days">']
    for d in days_list:
        a, b = byday.get(d, [0, 0])
        ha, hb = int(120 * a / mx), int(120 * b / mx)
        day_html.append(f'<div class="d" title="{d}: MCP {a}, 사이트 {b}"><div class="col">'
                        f'<i class="w" style="height:{hb}px"></i><i class="m" style="height:{ha}px"></i></div>'
                        f'<span>{int(d[8:])}</span></div>')
    day_html.append("</div>")

    def group(rs, key):
        c, bad = Counter(), Counter()
        for r in rs:
            k = key(r)
            c[k] += 1
            if not r[3]:
                bad[k] += 1
        return [(k, v, bad[k]) for k, v in c.most_common()]

    tools = group(mcp, lambda r: TOOL_KO.get(r[1], r[1]) + f" ({r[1]})")
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
        fb = fc.execute("SELECT id, created, kind, region, period, tool, message, status FROM feedback "
                        "ORDER BY created DESC LIMIT 50").fetchall()
        fb_total = fc.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
        fc.close()
    except Exception:
        fb, fb_total = [], 0
    fb_html = ['<table class="list"><tr><th>접수</th><th>시각</th><th>종류</th><th>지역·기간</th><th>내용</th><th>상태</th></tr>']
    for fid, created, kind, region, period, tool, msg, status in fb:
        fb_html.append(f"<tr><td>{_e(fid)}</td><td>{_e((created or '')[5:16].replace('T', ' '))}</td><td>{_e(kind)}</td>"
                       f"<td>{_e(' '.join(x for x in (region, period) if x))}</td><td class='a'>{_e(msg)}</td>"
                       f"<td>{_e(status)}</td></tr>")
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
.days{{display:flex;gap:2px;align-items:flex-end;min-height:140px}} .d{{flex:1;min-width:6px;text-align:center;font-size:10px;color:#9ca3af}}
.d .col{{display:flex;flex-direction:column;justify-content:flex-end;height:122px}} .d i{{display:block;width:100%}}
.d .m{{background:#2563eb}} .d .w{{background:#93c5fd}}
.lg i{{display:inline-block;width:10px;height:10px;margin:0 4px 0 10px;vertical-align:-1px}}
table.bars td{{padding:3px 6px;font-size:14px;white-space:nowrap}} table.bars td.k{{color:#374151}}
table.bars i{{display:inline-block;height:10px;background:#2563eb;border-radius:2px;vertical-align:0}}
table.list{{border-collapse:collapse;width:100%;font-size:13px}} table.list th,table.list td{{border-bottom:1px solid #eef0f3;padding:6px;text-align:left;vertical-align:top;white-space:nowrap}} table.list td.a{{white-space:normal}}
table.list th{{color:#6b7280;font-weight:500}} td.a{{color:#4b5563;word-break:break-all}} .bad{{color:#c2410c}} .none{{color:#9ca3af;font-size:14px}}
</style></head><body><div class="wrap">
<h1>운영 현황</h1><div class="sub">빅데이터 Insight: 지역별 유입인구 · 이름·IP는 남기지 않음 · 한국 시간 기준</div>
<div class="sel">{sel}</div>
<div class="cards">
<div class="c">MCP 콜 수<b>{len(mcp):,}</b></div>
<div class="c">MCP 성공률<b>{ok_rate:.1f}%</b></div>
<div class="c">MCP 평균 응답<b>{avg_ms:,}ms</b></div>
<div class="c">사이트 조회<b>{len(web):,}</b></div>
<div class="c">들어온 의견(전체)<b>{fb_total:,}</b></div>
</div>
<div class="card"><h2>날짜별 <span class="lg sub"><i style="background:#2563eb"></i>MCP 콜<i style="background:#93c5fd"></i>사이트 조회</span></h2>{''.join(day_html)}</div>
<div class="two">
<div class="card"><h2>도구별 MCP 콜</h2>{_bars(tools)}</div>
<div class="card"><h2>어디서 왔나 (MCP)</h2>{_bars(clients)}<p class="sub">요청에 실린 앱 정보로 짐작한 이름. 2026-11-15 이전 기록은 '기록 전'.</p></div>
<div class="card"><h2>사이트 조회</h2>{_bars(webk)}</div>
<div class="card"><h2>많이 물은 지역 (상위 15)</h2>{_bars(reg_rows)}</div>
</div>
<div class="card"><h2>최근 MCP 호출 50건</h2>{''.join(rec_html)}</div>
<div class="card"><h2>최근 사이트 조회 20건</h2>{rec_web}</div>
<div class="card"><h2>들어온 의견 (최근 50건)</h2>{''.join(fb_html)}</div>
</div></body></html>"""


async def admin(request: Request):
    if not os.environ.get("ADMIN_PW"):
        return PlainTextResponse("Not Found", status_code=404)
    if not _authorized(request):
        return PlainTextResponse("로그인이 필요합니다.", status_code=401,
                                 headers={"WWW-Authenticate": 'Basic realm="admin", charset="UTF-8"'})
    try:
        days = int(request.query_params.get("days", "30"))
    except ValueError:
        days = 30
    days = max(1, min(days, 365))
    return HTMLResponse(_page(days), headers={"X-Robots-Tag": "noindex", "Cache-Control": "no-store"})
