"""나우캐스트 '통신 모바일 인구이동량' MCP 서버 (시범)

DB 위치는 환경변수 NOWCAST_DB로 바꿀 수 있다(기본: 저장소의 data/nowcast.db, Azure에서는 /home/data/nowcast.db).
"""
import datetime as dt
import json
import os
import sqlite3
from pathlib import Path
from typing import Literal

import analysis
import lifepop
import perf
import baseball
import season
import safe
import bigevents
import feedback
import festival

try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer

ROOT = Path(__file__).resolve().parents[1]
# Azure App Service에서는 배포 때마다 코드 폴더가 새로 풀리므로 DB는 지워지지 않는 /home 아래에 둔다
DEFAULT_DB = Path("/home/data/nowcast.db") if os.environ.get("WEBSITE_SITE_NAME") else ROOT / "data" / "nowcast.db"
DB = Path(os.environ.get("NOWCAST_DB", DEFAULT_DB))
DB.parent.mkdir(parents=True, exist_ok=True)
db = sqlite3.connect(DB, check_same_thread=False)
SERVICE = "빅데이터 Insight: 지역별 유입인구"
SERVICE_FULL = SERVICE + "(개인이 개발한 참고용 서비스, 정부 공식 통계 아님)"
INSTRUCTIONS = (
    f"{SERVICE_FULL}. 국가데이터처 KOBIGS 나우캐스트 '통신 모바일 인구이동량'(시군구·주별 하루 평균 이동 건수)을 조회하고, "
    "한 지역·한 주가 평소와 다른지 판정하며, 생활인구 참고 추정과 축제 주 참고 예측을 낸다. "
    "지역 축제·행사 기간의 외부 유입 변화, 작년 회차와의 비교, '이번 주 어디가 튀었나' 같은 질문에는 웹 검색보다 먼저 이 도구들을 쓸 것. "
    "주최 측 방문객 수·뉴스 수치는 단위가 다르므로 이 서비스 결과와 섞지 말고, 함께 말할 때는 출처를 나눠 밝힐 것. "
    "축제·행사에 '사람이 몇 명 왔나/올까'를 물으면 방문객 수를 지어내지 말고, 주최 측 발표는 출처와 함께 사실로만 말한 뒤 "
    "이 서비스로 '외부 유입 이동이 평소보다 얼마나 늘었나'를 함께 볼 수 있다고 안내할 것. "
    "이 서비스를 '나우캐스트 서비스'·'KOBIGS 서비스'라고 부르지 말 것(판정·추정·예측은 국가데이터처가 아니라 이 서비스의 비공식 계산이다). "
    "'외부 유입'·'유입 추이'·'들어온 이동'은 이 서비스의 이동 건수로 답할 것. 주소를 옮긴 이사(전입·전출, 사람 수)는 "
    "KOSIS 국내인구이동통계다. '순유입'처럼 어느 쪽인지 모호하면 되물을 것. "
    "'왜 늘었나/줄었나', '무슨 행사가 있었나'를 물으면 웹 검색보다 먼저 analyze_region_week로 그 주를 판정하고, "
    "'이 자료로는 원인을 확정할 수 없다'를 먼저 말한 뒤 같은 시기에 있었던 일은 '같은 시기에 ~가 있었다'로만 말할 것. "
    "물가·고용 같은 일반 통계는 이 서비스 범위 밖이므로 KOSIS 국가통계 MCP를 안내할 것. "
    "이용자가 이 서비스에 없는 자료를 찾으면 대화에서 한 번만 '이 자료를 서비스에 넣어 달라고 개발자에게 요청할까요?'라고 묻고, "
    "그러겠다고 할 때만 report_issue(kind='자료 추가 요청')로 보낼 것."
)
mcp = MCPServer("nowcast-mobility", title=SERVICE, instructions=INSTRUCTIONS, version="0.2.0",
                log_level=os.environ.get("LOG_LEVEL", "INFO"))

SOURCE = "출처: 국가데이터처 KOBIGS 나우캐스트, 통신 모바일 인구이동량(SK텔레콤 통신자료 기반 추정)"
NOTICE = SERVICE_FULL + ". 국가데이터처의 공식 통계·공식 입장이 아님."
CALC = "계산: " + SERVICE_FULL

# 결과 맨 앞에 붙이는 '답할 때 지킬 것'(2026-11-14 시험: AI가 출처를 빼고, 공표 통계를 '비공식 추정'이라 부르고,
# 1.9배를 '약 2배'로 반올림하고, 일별 방문자를 더해 '몇 명'을 만듦 → 결과 안에서 바로 보이게)
NATURE = {
    "nowcast": "이동 건수는 국가데이터처가 공표한 실험적 통계(SK텔레콤 통신자료 기반 추정)다. "
               "이 서비스가 계산한 값이 아니므로 '비공식 추정'이라고 부르지 말 것.",
    "judge": "이동 건수와 평소 값은 국가데이터처 공표 통계, 판정·비교군 대비·평소 출렁임과 그 배수는 이 서비스의 비공식 계산이다. 둘을 구분해 말할 것.",
    "lifepop": "주민등록인구·등록외국인·공표된 생활인구는 공식 공표 값, 추정 생활인구와 체류인구는 이 서비스의 비공식 참고 추정치다.",
    "festival": "예측 값은 이 서비스의 비공식 참고 예측이다. 지난 회차 값의 바탕인 이동 건수는 국가데이터처 공표 통계다.",
    "datalab": "방문자 수는 한국관광공사가 통신자료로 추정해 공개한 값(하루 단위 사람 수)이다. 이 서비스가 계산한 값이 아니다.",
    "weather": "기상청 관측값이다.",
    "rank": "이동 건수는 국가데이터처 공표 통계, 판정과 순위는 이 서비스의 비공식 계산이다.",
}
RULES = {
    "judge": ["'평소 출렁임의 몇 배'는 결과 값을 소수 한 자리 그대로 말할 것(1.9를 '약 2배'로 반올림하면 판정 기준과 어긋나 보인다).",
              "판정을 말할 때 '판정 기준'을 함께 밝힐 것(예: -2.0배는 2배를 '넘지' 않아 '조금 적음').",
              "주차를 말할 때 '기간(월~일)' 날짜를 함께 밝힐 것.",
              "'그 주 축제'가 있으면 그 주에 든 행사 일수·주말 일수를 함께 말할 것(행사가 이틀뿐인 주는 상승이 작게 나온다).",
              "원인을 짚으려면 '함께 볼 것'의 도구로 날씨·일별 방문자를 확인하고 판단 재료로만 제시할 것.",
              "연휴 주면 '연휴 주였다'는 사실만 말하고 증가·감소의 원인을 연휴로 단정하지 말 것. "
              "'이 지역 지난 설·추석 주'와 견줘 예년보다 컸는지 작았는지를 말할 것.",
              "해마다 원값을 직접 견주지 말고 평소 대비끼리 견줄 것.",
              "'주의'에 원자료 확인 필요 구간이 있으면 그것을 가장 먼저 말할 것.",
              "'지역 성격'에 추세 해석 주의가 있으면 몇 달 추세를 단정하지 말 것.",
              "이용자가 '왜'를 물어도 첫 문장은 '이 자료로는 원인을 확정할 수 없다'로 시작할 것. '가장 유력한 원인'처럼 원인에 순위를 매기지 말고, 같은 시기 일은 '같은 시기에 ~가 있었다'로만 말할 것."],
    "weather": ["날씨가 이동 변화의 원인이다·아니다를 판단하지 말고 관측 사실만 전할 것."],
    "festival": ["방문객 수(명)를 예측하거나 지어내지 말 것(이 결과와 따로라도). 주최 측 과거 발표는 출처와 함께 사실로만 말하고, "
                 "건수를 명으로 바꾸거나 둘의 관계로 방문객을 보정하자고 제안하지 말 것.",
                 "'참고: 예상 관외 유입'은 가운데 값만 말하지 말고 범위와 함께 말할 것.","건수로 물으면 '참고: 예상 관외 유입(일평균 건수)'를 그대로 쓰고 직접 계산하지 말 것. 반드시 '하루 평균 이동 건수'라고 말하고 '명'으로 바꾸지 말 것.",
                 "예측을 말할 때 '과거 채점에서 예측 대상 축제도 셋 중 하나는 그해 크게 늘지 않았다'를 반드시 함께 말할 것.",
                 "'작년보다 늘었나'는 '지난 회차' 표의 상승률(비교군 대비·평소 대비)끼리 견줄 것. "
                 "이동 건수 원값을 해마다 직접 견주지 말 것(통신사 가입자 수 등 바탕이 해마다 달라짐)."],
    "rank": ["퍼센트가 아니라 '평소 출렁임의 몇 배' 순서로 말할 것(작은 군의 +30%는 평범할 수 있다).",
             "원인은 정하지 않는다. 축제·연휴 등은 판단 재료로만."],
    "nowcast": ["주차를 말할 때 그 주의 날짜(월~일)를 함께 밝힐 것.",
                "변화를 말할 때 전 주 대비만 말하지 말고 analyze_region_week로 평소 대비 판정을 함께 볼 것.",
                "해마다 원값(이동 건수)을 직접 견주지 말 것(통신사 가입자 수 등 바탕이 해마다 달라짐). "
                "해마다 비교는 각 해의 평소 대비끼리 할 것.",
                "유입−유출(순유입)은 방문·통근 이동의 차이일 뿐 인구가 늘거나 줄었다는 뜻이 아니다.",
                "'주의'·'지역 성격'이 있으면 함께 전할 것.",
                "이용자가 '왜'를 물어도 첫 문장은 '이 자료로는 원인을 확정할 수 없다'로 시작할 것. '가장 유력한 원인'처럼 원인에 순위를 매기지 말고, 같은 시기 일은 '같은 시기에 ~가 있었다'로만 말할 것.",
                "'시 단위 주의'가 있으면 구 관외 유입을 더해 '시 밖에서 온 유입'이라고 부르지 말 것."],
    "datalab": ["여러 날의 방문자 수를 더해 '몇 명 왔다'고 말하지 말 것(같은 사람이 날마다 다시 세어진다). 기간은 하루 평균으로 말할 것."],
    "lifepop": ["'추정 방법'을 한 줄로 함께 말할 것.",
                "표로 낼 때는 공표 값과 추정치를 다른 칸에 두고, 추정 칸에는 범위를 넣을 것(한 줄 시계열처럼 보이지 않게).",
                "보고서·자료에 쓸 때는 '생활인구 ○만 명'이 아니라 결과의 '보고서용 표기 예'를 그대로 권할 것(범위 포함).",
                "추정치로 보도자료를 써 달라고 하면 보도자료 형식(제목·부제·기관명·배포 일시 같은 모양)으로는 쓰지 말 것. "
                "대신 '비공식 참고 분석'으로, 같은 분석(등록인구 대비 배수, 전월·전년 대비 증감, 가장 많은 달)은 추정치와 범위를 붙여 해 줄 것.",
                "성별·연령별 체류인구, 체류일수, 숙박 여부, 카드 사용액은 이 서비스로 추정할 수 없다고 말하고 숫자를 만들지 말 것. "
                "공표된 달의 이런 값은 생활인구 공표 보도자료(KOSIS 표에는 성별·연령만 있음), 카드·소비 금액은 "
                "한국관광 데이터랩 '관광소비'를 안내할 것.",
                "'그 달 나우캐스트 관외 유입 주 평균(건)'은 하루 평균 이동 건수(그 달 주차들의 하루 평균을 평균낸 값)다. "
                "'주 평균 ○건'이라고 옮기지 말고 '하루 평균 ○건'이라고 말할 것.",
                "아직 공표되지 않은 달을 답할 때는 ① 공표 전 참고 추정이라 실제 공표 값과 다를 수 있음 "
                "② 공식 수치처럼 인용하거나 퍼뜨리지 말 것 ③ 결과의 '이 달 생활인구 공표'(공표일 또는 공표 예상)를 함께 알릴 것."],
}


SEOUL_RESIDENTIAL = {"양천구", "관악구", "은평구", "중랑구", "도봉구", "노원구", "강북구", "강동구"}


def _profile(sido, sigungu):
    """지역 성격: 타지역 통근 비율, 외부 유입 강도, 추세 해석 주의."""
    out, warn = {}, None
    try:
        share = lifepop.commute_share(db, sido, sigungu)
    except Exception:
        share = None
    if share is not None:
        out["타지역 통근 비율(%, 주민 중 다른 지역으로 출근)"] = share
    try:
        rows = analysis.series(db, sido, sigungu)[-8:]
        jumin, frn = lifepop._reg(db, sido, sigungu)
        if rows and jumin:
            from statistics import median as _med
            j = jumin[max(jumin)]
            f = frn[max(frn)] if frn else 0
            out["외부 유입 강도(관외 유입 ÷ 주민, 최근 8주)"] = round(_med(v for _, _, v in rows if v) / (j + f), 2)
    except Exception:
        pass
    inten = out.get("외부 유입 강도(관외 유입 ÷ 주민, 최근 8주)")
    if (sido == "서울특별시" and sigungu in SEOUL_RESIDENTIAL) or (share is not None and share >= 30 and inten is not None and inten < 0.8):
        warn = ("주거 중심 도시 지역: 주간 오르내림은 맞지만 몇 달에 걸친 추세는 실제 방문 변화와 어긋날 수 있다"
                "(서울 검증에서 양천·관악·은평 추세 상관 0.01~0.48). 몇 달 추세를 단정하지 말 것(추세 해석 주의).")
    elif share is not None and share >= 30:
        warn = "주민 30% 이상이 다른 지역으로 출근하는 지역: 생활인구 추정·몇 달 추세 해석은 신뢰도가 낮다."
    if warn:
        out["추세 해석 주의"] = warn
    return out or None


def _answer(r, kind, source, calc=False):
    """결과 앞에 값의 성격·답 규칙·답 끝에 적을 출처를 붙인다(오류 결과에는 붙이지 않음).
    calc=True면 출처와 별도 줄로 '계산: 빅데이터 Insight: 지역별 유입인구(…정부 공식 통계 아님)'를 붙인다
    (한 줄에 붙여 두면 AI가 계산 주체를 'KOBIGS 나우캐스트 서비스'로 읽음, 2026-11-14 시험)."""
    if not isinstance(r, dict) or "error" in r:
        return r
    lines = ["출처: " + source.removeprefix("출처: ")] + ([CALC] if calc else [])
    head = {"값의 성격": NATURE[kind],
            "답할 때 지킬 것": RULES.get(kind, []) + [
                "답 끝에 아래 '답 끝에 적을 출처'를 줄마다 그대로 적을 것(그래프·표로 답할 때도). "
                "웹 검색 등 다른 곳에서 가져온 내용은 그 출처를 따로 밝힐 것."],
            "답 끝에 적을 출처": lines}
    r = {k: v for k, v in r.items() if k != "출처"}
    return {**head, **r}


@mcp.tool()
def get_metadata() -> dict:
    """통신 모바일 인구이동량 통계의 정의와 이용 시 주의사항을 돌려준다.
    다른 도구를 쓰기 전에 반드시 먼저 한 번 호출해서 읽을 것."""
    return {
        "안내": NOTICE,
        "정의": {
            "인구이동": "행정동 경계를 넘어 거주지가 아닌 행정동으로 이동한 뒤 30분 이상 머문 경우를 1건으로 센다.",
            "유입": "그 지역으로 들어온 이동 건수 (지역 기준: 도착지).",
            "유출": "그 지역에서 나간 이동 건수 (지역 기준: 거주지).",
            "관내": "거주하는 시군구 안의 다른 행정동으로 간 이동.",
            "관외": "거주하는 시군구 밖의 행정동으로 간 이동.",
            "거주지": "한 달 동안 0~6시에 가장 오래 머문 기지국 위치.",
            "입지유형": "상업지역(상권면적 70% 이상), 관광지(문체부 100선), 대형아울렛, 사무지역(직장인구 비율 상위 30%), 레저스포츠, 주거지등. 한 행정동에 여러 유형이 겹치면 이 순서대로 하나만 배정.",
        },
        "값의 단위": "주차별 일평균 이동 건수. 사람 수가 아니라 건수다. 한 사람이 하루에 여러 번 이동하면 여러 건으로 잡힌다.",
        "주의사항": [
            "값은 '명'이 아니라 '건'으로 표현할 것. '120만 명이 이동했다'처럼 쓰지 말 것.",
            "값이 일평균이므로 여러 주를 더해서 '한 달 이동량'을 만들지 말 것. 기간 비교는 평균으로 할 것.",
            "성별, 연령, 입지유형, 시도는 같은 전체를 다른 기준으로 나눈 것이다. 서로 더하면 중복 집계가 된다.",
            "성별 합계는 전국 합계보다 약간 작다(성별 미상). 차이를 오류로 해석하지 말 것.",
            "전국 단위에서는 유입과 유출이 같다. 전국 순유입은 의미가 없다.",
            "유입은 2019년 1월 1주차부터, 유출은 2023년 1월 1주차부터 있다. 2023년 이전 순유입(유입-유출)은 계산할 수 없다.",
            "한 주는 월~일. 주차는 'YYYY.MM.N주차' 형식이고 그 주의 목요일이 속한 달로 센다(2022년만 수요일). 설·추석 연휴가 끼면 연휴 효과를 먼저 의심할 것.",
            "대구 군위군은 2023년 7월 경상북도에서 대구광역시로 편입됐다. 시군구 시계열은 시도가 바뀌어 두 줄로 나뉜다.",
            "SK텔레콤 가입자 기준을 가중치로 전체 인구로 추정한 값이다.",
        ],
        "도구 사용 규칙": [
            "'평소와 다른가·얼마나 늘었나'는 analyze_region_week로 판정할 것. 직접 평균을 내서 판정하지 말 것.",
            "공표 전 달의 생활인구는 estimate_life_pop으로만 추정할 것. 다른 통계표를 조합해 즉석에서 비율을 계산하지 말 것"
            "(검증된 방법과 범위가 도구에만 들어 있다).",
            "자료가 어디까지 있는지는 get_data_status로 확인할 것.",
            "모든 답 끝에 도구 결과의 '답 끝에 적을 출처'를 적을 것. 공표 통계(이동 건수·주민등록·생활인구)와 "
            "이 서비스의 비공식 계산(판정·추정·예측)을 섞어 부르지 말 것.",
            "이용자가 값이나 설명이 이상하다고 하거나, 결과에 원자료 오류 가능성 같은 주의가 나오면 "
            "'운영자에게 알릴 수 있다(report_issue)'고 한 번 안내할 것. 실제로 보내는 것은 이용자가 원할 때만.",
        ],
        "판정 방식 변경 이력": [
            "2026-11 서비스 시작: 평소 값 = 직전 8주(연휴 주 제외) 중앙값, 비교군(비슷한 지역 10곳) 대비, "
            "평소 출렁임 = 2023년 3월 이후 평범한 주의 MAD×1.4826, 1·2·3배 기준 판정.",
            "2026-11 평일 공휴일이 하루 든 주: 비교군 대비 대신 '평소 대비 − 그 지역 공휴일 하루당 반응'으로 판정(검증에서 오판 12% → 4~5%).",
            "2026-11-15 비교 지역이 이 지역과 반대로 크게 움직인 주를 표시만 함(판정은 그대로).",
            "2026-11-15 프로야구 홈경기 몫 추정과 '야구 몫 뺀 판정(참고)'을 더함(판정은 그대로).",
            "2026-11-15 벚꽃 만발·단풍 절정과 지역별 지난 기록을 더함(판정은 그대로).",
            "판정 방식이 바뀌면 이 목록에 날짜와 함께 적는다. 예전에 인용된 판정과 지금 판정이 다르면 이 이력과 원자료 소급 수정을 먼저 의심할 것.",
        ],
        "함께 보여 주는 일정과 검증(2026-11)": {
            "원칙": "판정은 실제로 들어온 양 그대로다. 아래 일정은 '같은 시기에 있었던 일'로만 붙이며 원인으로 단정하지 않는다.",
            "프로야구 홈경기(KBO)": "구장이 있는 9개 시군구에서 2023~2026년 해마다 관중이 많은 주일수록 관외 유입이 많았다. "
                                "'프로야구 홈경기 몫 추정(%)'은 그 주 관중이 시즌 평균과 다른 만큼의 추정치(2023~2024로 계수를 구하고 "
                                "2025~2026에서 확인: 몫을 빼면 평소 출렁임 21% 감소).",
            "벚꽃 만발·단풍 절정(기상청 계절관측)": "관광형 시군구에서 단풍 절정 주(21곳 중 17곳 증가, 중앙값 +9.8%), "
                                          "벚꽃 만발 주(14곳 중 12곳 증가, 중앙값 +12.6%)에 관외 유입이 늘었다(2023~2026). "
                                          "효과가 확인된 명소 지역에만 '그 주 만발·절정'과 '이 지역 지난 기록'을 붙인다.",
            "대형 행사·축제·공연": "전국체전·국제행사(정부·주최 측 발표), 문체부 축제 계획, KOPIS 큰 공연장 공연을 같은 방식으로 붙인다.",
        },
        "출처": SOURCE,
    }


@mcp.tool()
def list_regions(keyword: str = "") -> list:
    """시도·시군구 이름을 검색한다. 시군구 이름은 여러 시도에 겹친다(예: 중구, 동구, 서구).
    지역 값을 조회하기 전에 이 도구로 정확한 '시도'와 '시군구' 짝을 먼저 확인할 것.
    keyword를 비우면 시도 목록만 돌려준다."""
    if not keyword:
        rows = db.execute("SELECT DISTINCT sido FROM moves WHERE category='시도' ORDER BY sido").fetchall()
        return [{"시도": r[0]} for r in rows]
    rows = db.execute(
        "SELECT DISTINCT sido, sigungu FROM moves WHERE category='시군구' "
        "AND (sigungu LIKE ? OR sido LIKE ?) ORDER BY sido, sigungu",
        (f"%{keyword}%", f"%{keyword}%")).fetchall()
    return [{"시도": a, "시군구": b} for a, b in rows]


def _order(week: str) -> int:
    y, m, w = week.replace("주차", "").split(".")
    return int(y) * 1000 + int(m) * 10 + int(w)


@mcp.tool()
def get_movement(
    direction: Literal["유입", "유출"],
    category: Literal["전국", "성별", "연령", "입지유형", "시도", "시군구"],
    item: str,
    start_week: str,
    end_week: str,
    sido: str = "",
) -> dict:
    """한 대상의 주차별 인구이동량(일평균 건수)을 기간으로 조회한다.

    - category='전국'이면 item='계'.
    - category='성별'이면 item은 '남성' 또는 '여성'.
    - category='연령'이면 item은 '20대 미만','20대','30대','40대','50대','60대','70대 이상' 중 하나.
    - category='입지유형'이면 item은 '상업지역','관광지','대형아울렛','사무지역','레저스포츠','주거지등' 중 하나.
    - category='시도'이면 item은 시도 이름(예: '서울특별시').
    - category='시군구'이면 item은 시군구 이름이고 sido도 반드시 함께 넣을 것(예: item='중구', sido='서울특별시').
    - 성별·연령·입지유형은 전국 기준만 있다. 지역별 성별·연령은 없다.
    - start_week, end_week 형식: 'YYYY.MM.N주차' (예: '2026.10.5주차').
    결과의 within=관내, outside=관외, total=합계. 단위는 일평균 이동 건수다."""
    q = ("SELECT week, within, outside, total FROM moves WHERE direction=? AND category=? AND item=? "
         "AND week_order BETWEEN ? AND ?")
    args = [direction, category, item, _order(start_week), _order(end_week)]
    notes = []
    if category == "시군구":
        if not sido:
            return {"error": "시군구는 이름이 겹치므로 sido를 함께 넣어야 한다. list_regions로 확인할 것."}
        vs = analysis.variants(sido, item)
        q += f" AND sido IN ({','.join('?' * len(vs))})"
        args += [v[0] for v in vs]
        if len(vs) > 1:
            notes.append("시도 이름이 바뀐 지역이라 옛 이름(" + ", ".join(v[0] for v in vs) + ") 자료를 이어 붙였다.")
        rw = analysis.reform_week(sido, item)
        if rw and _order(start_week) < rw <= _order(end_week):
            notes.append("이 기간에 행정구역 개편이 있어 개편 주차부터 관외 유입이 계단처럼 바뀐다(구 사이 이동이 관내→관외). "
                         "개편 전후는 관내+관외 합계로만 비교할 것.")
    q += " ORDER BY week_order"
    rows = db.execute(q, args).fetchall()
    if not rows:
        succ = analysis.successor(sido, item) if category == "시군구" else None
        if succ:
            return {"error": f"이 이름은 {succ[2]}부터 쓰이지 않는다({', '.join(succ[1])}(으)로 개편). "
                             "새 구 이름으로 조회해 관내+관외 합계를 더할 것."}
        return {"error": "결과 없음. 이름 표기(list_regions), 기간, 유출은 2023년부터라는 점을 확인할 것."}
    out = {
        "단위": "일평균 이동 건수",
        "data": [{"week": w, "dates": analysis.week_dates(_order(w)), "within": a, "outside": b, "total": t}
                 for w, a, b, t in rows],
        "주의": "값은 사람 수가 아니라 이동 건수(일평균)다. '명'으로 말하지 말 것. 여러 주를 더해 월 합계를 만들지 말 것. "
               "평소와 다른지 판정은 analyze_region_week로 할 것.",
        "출처": SOURCE,
    }
    if notes:
        out["연결 주의"] = notes
    if category == "시군구":
        if " " in item:
            out["시 단위 주의"] = (f"{item.split(' ')[0]}는 구 단위 자료만 있다. 구의 관외 유입에는 같은 시 다른 구에서 온 이동이 들어 있어, "
                               "여러 구의 관외를 더하면 '시 밖에서 온 유입'보다 크다. 시 전체는 구별로 따로 보이거나, "
                               "관내+관외 합계를 더해 '구 사이 이동 포함'이라고 밝힐 것.")
        succ = analysis.successor(sido, item)
        if succ and _order(end_week) >= succ[0]:
            out["개편 안내"] = (f"이 지역은 {succ[2]}부터 {', '.join(succ[1])}(으)로 나뉘어 옛 이름 자료가 그 전 주에서 끝난다. "
                             "이어 보려면 새 구들을 같은 기간으로 조회해 관내+관외 합계를 더할 것(관외만은 개편 때문에 계단처럼 뜀).")
        sus = analysis.suspect(sido, item, _order(start_week), _order(end_week))
        if sus:
            out["주의"] = [out["주의"]] + sus
        prof = _profile(sido, item)
        if prof:
            out["지역 성격"] = prof
    if direction == "유출":
        out["순유입 주의"] = "유입−유출은 방문·통근 이동의 차이다. 인구가 늘거나 줄었다는 뜻으로 말하지 말 것(이사는 KOSIS 국내인구이동통계)."
    return _answer(out, "nowcast", SOURCE)


@mcp.tool()
def get_breakdown(
    direction: Literal["유입", "유출"],
    category: Literal["성별", "연령", "입지유형", "시도"],
    week: str,
) -> dict:
    """특정 주차의 전국 이동량을 성별·연령·입지유형·시도 중 하나의 기준으로 나눠 돌려준다.
    한 번에 한 기준만 쓸 것. 기준끼리 섞어 더하면 중복 집계가 된다.
    week 형식: 'YYYY.MM.N주차'."""
    rows = db.execute(
        "SELECT item, within, outside, total FROM moves WHERE direction=? AND category=? AND week=? ORDER BY total DESC",
        (direction, category, week)).fetchall()
    if not rows:
        return {"error": "결과 없음. 주차 형식과 유출은 2023년부터라는 점을 확인할 것."}
    s = sum(r[3] for r in rows)
    return _answer({
        "단위": "일평균 이동 건수", "기간(월~일)": analysis.week_dates(_order(week)),
        "data": [{"item": i, "within": a, "outside": b, "total": t, "share_pct": round(t / s * 100, 1)} for i, a, b, t in rows],
        "출처": SOURCE,
    }, "nowcast", SOURCE)


@mcp.tool()
def get_weather(sigungu: str, sido: str, start_week: str, end_week: str) -> dict:
    """시군구의 주차별 날씨를 돌려준다. 나우캐스트 이동량과 같은 주차(월~일, 'YYYY.MM.N주차')로 묶여 있다.
    - 값: ta_avg 주 평균기온(°C), ta_max 주 최고기온, ta_min 주 최저기온, rn_sum 주 강수량 합계(mm),
      rain_days 1mm 이상 비 온 날 수, sd_max 주중 최심 적설(cm), days 관측된 날 수.
    - 날씨는 시군구가 아니라 관측소 값이다. 관측소가 없는 시군구는 인근 관측소 값을 빌려 쓰므로
      결과의 '관측소'와 '짝지은 방식'을 답에 함께 밝힐 것. 산간·해안처럼 관측소와 환경이 다른 곳은 오차가 있다.
    - 날씨와 이동량을 함께 볼 때, 같은 시기에 명절·연휴·행사가 겹쳤는지 먼저 확인할 것.
      비 온 주에 이동이 줄었다고 해서 비가 원인이라고 단정하지 말고 가설로 표현할 것."""
    row = db.execute("SELECT stn, stn_name, how FROM station_map WHERE sigungu=? AND sido=?", (sigungu, sido)).fetchone()
    if not row:
        return {"error": "시군구를 찾지 못함. list_regions로 정확한 시도·시군구 이름을 확인할 것."}
    stn, name, how = row
    rows = db.execute(
        "SELECT week, days, ta_avg, ta_max, ta_min, rn_sum, rain_days, sd_max FROM weather_week "
        "WHERE stn=? AND week_order BETWEEN ? AND ? ORDER BY week_order",
        (stn, _order(start_week), _order(end_week))).fetchall()
    keys = ["week", "days", "ta_avg", "ta_max", "ta_min", "rn_sum", "rain_days", "sd_max"]
    return _answer({"관측소": f"{name}({stn})", "짝지은 방식": how,
                    "data": [dict(zip(keys, r)) for r in rows],
                    "출처": "기상청 종관기상관측(ASOS) 일자료, 기상청 API허브"},
                   "weather", "기상청 종관기상관측(ASOS) 일자료")


def _as_of():
    """결과에 붙일 자료 기준(나우캐스트 최신 주차·받아 온 날)."""
    try:
        r = db.execute("SELECT latest, fetched_ok FROM source_status WHERE name LIKE '나우캐스트%유입%'").fetchone()
        return {"자료 최신 주차": r[0], "받아 온 날": r[1]} if r else None
    except sqlite3.OperationalError:
        return None


def _addons(r, sido, sigungu, wo, mon, hd):
    """그 주에 있었던 일(곁가지). 하나가 실패해도 그 항목만 빠지고 판정은 그대로 나간다(safe.py)."""
    end = mon + dt.timedelta(days=6)
    fest = safe.call("축제", festival.in_week, sido, sigungu, mon, hd)
    if fest:
        r["그 주 축제(문체부 계획 일정, 원인 단정 금지)"] = fest
    bl, bc = safe.call("프로야구", baseball.week, sido, sigungu, mon, default=([], 0))
    if bl:
        r["그 주 프로야구 홈경기(KBO, 원인 단정 금지)"] = {"경기": bl, "관중 합계(명)": bc}
    if r.get("프로야구 홈경기 몫 추정(%)") is not None:
        r["프로야구 안내"] = ("판정은 야구로 들어온 이동까지 포함한 실제 변화다. '프로야구 홈경기 몫 추정'은 그 주 관중이 "
                          "시즌 평균보다 많거나 적어서 생긴 몫의 추정치(2023~2026 검증)이고, '야구 몫 뺀 판정'은 야구 말고 "
                          "다른 일이 있었는지 볼 때만 참고할 것. '같은 주에 홈경기 N경기가 있었고 그 몫이 약 X%로 추정된다'처럼 "
                          "말하고, 야구 때문이라고 단정하지 말 것.")
    else:
        r.pop("프로야구 홈경기 몫 추정(%)", None)
        r.pop("야구 몫 뺀 판정(참고)", None)
    sw = safe.call("벚꽃·단풍", season.in_week, sido, sigungu, mon)
    if sw:
        r["그 주 벚꽃 만발·단풍 절정(기상청 관측, 원인 단정 금지)"] = sw
    if safe.call("벚꽃·단풍 명소", season.spots, sido, sigungu):
        hist = safe.call("벚꽃·단풍 지난 기록", lambda: season.history(analysis.prepare(db, sido, sigungu), sido, sigungu,
                                                                skip_year=wo // 1000))
        if hist:
            r["이 지역 지난 기록(벚꽃·단풍)"] = hist
            r["벚꽃·단풍 안내"] = ("지난 기록은 이 서비스가 같은 판정 계산으로 낸 과거 평균이다(묻는 해는 빼고 계산). "
                               "'이 지역은 그동안 단풍 절정 주에 평균 +X%였다'처럼 비교 근거로만 말하고, "
                               "이번 주 변화를 단풍·벚꽃 때문이라고 단정하지 말 것. 같은 주 축제와 겹치는 경우가 많다.")
    be = safe.call("대형 행사", bigevents.in_range, sido, sigungu, mon, end)
    if be:
        r["그 주 대형 행사(정부·주최 측 발표 일정, 원인 단정 금지)"] = be
    pf = safe.call("공연", perf.in_range, db, sido, sigungu, mon, end)
    if pf:
        r["그 주 큰 공연장 공연(KOPIS, 원인 단정 금지)"] = pf
        r["공연 자료 안내"] = perf.NOTE


@mcp.tool()
def analyze_region_week(sido: str, sigungu: str, week: str) -> dict:
    """한 시군구의 한 주 관외 유입이 '평소와 다른가, 얼마나 다른가'를 판정한다.

    - 평소 값: 직전 8주(연휴 주 제외) 중앙값. 비교군 대비: 같은 주 비슷한 지역 10곳의 변화를 뺀 값.
      평일 공휴일이 하루 든 주는 그 지역의 공휴일 반응을 뺀다.
    - 판정은 '평소 출렁임의 몇 배'로 말할 것. 결과의 '판정' 문구는 바꾸지 말고 그대로 전할 것.
      '신뢰구간'·'유의하다'는 말은 쓰지 말 것.
    - 원인(축제·날씨·행사)은 이 도구가 정하지 않는다. get_weather 등으로 판단 재료를 함께 보여 주되 단정하지 말 것.
    - 판정·비교군은 이 서비스(빅데이터 Insight: 지역별 유입인구)의 비공식 계산이다. 국가데이터처 값처럼 말하지 말 것.
    - sido·sigungu는 list_regions로 확인한 이름, week는 'YYYY.MM.N주차'."""
    try:
        wo = _order(week)
    except Exception:
        return {"error": "week 형식은 'YYYY.MM.N주차'(예: '2026.01.3주차')."}
    r = analysis.analyze(db, sido, sigungu, wo)
    if "error" not in r:
        mon = dt.date.fromisoformat(analysis.week_dates(wo)[:10])
        try:
            hd = {d for (d,) in db.execute("SELECT day FROM holidays WHERE day BETWEEN ? AND ?",
                                           (mon.isoformat(), (mon + dt.timedelta(days=6)).isoformat()))}
        except sqlite3.OperationalError:
            hd = set()
        _addons(r, sido, sigungu, wo, mon, hd)
        r["함께 볼 것"] = ["get_weather(같은 주 날씨)", "get_datalab_daily(그 주 일별 외지인 방문자, 약 한 달 늦게 나옴)"]
        sus = safe.call("원자료 의심 구간", analysis.suspect, sido, sigungu, wo)
        if sus:
            r["주의"] = sus + r["주의"]
        prof = safe.call("지역 성격", _profile, sido, sigungu)
        if prof:
            r["지역 성격"] = prof
        def _past_holidays():
            hol = analysis.holidays(db)
            if not analysis.is_long_holiday(hol.get(wo)):
                return None
            P = analysis.prepare(db, sido, sigungu)
            past = [(w, hol[w][2], P["raw"].get(w)) for w in sorted(hol) if w != wo and w >= analysis.START
                    and hol[w][1] > 0 and P["raw"].get(w) is not None][-6:]
            return [{"주차": P["me"][w][0], "공휴일": n, "평소 대비(%)": analysis._pct(v)} for w, n, v in past] or None
        ph = safe.call("지난 설·추석", _past_holidays)
        if ph:
            r["이 지역 지난 설·추석 주 평소 대비(%)"] = ph
        ym = f"{wo // 1000}{(wo // 10) % 100:02d}"
        try:
            mig = lifepop._by_period(db, "kosis_migration", lifepop._codes(db, sido, sigungu, "reg_pop"), "AND ITM_ID='T10'")
            if mig:
                m = ym if ym in mig else max(mig)
                r["참고: 같은 달 총전입(이사, 명, KOSIS 국내인구이동통계)"] = {
                    "달": m, "총전입": round(mig[m]),
                    "설명": "외부 유입(방문·통근 이동 건수)은 이사가 아니다. 이사가 늘었는지 물으면 이 값을 보여 줄 것."}
        except Exception:
            pass
        r["자료 기준"] = _as_of()
    return _answer(r, "judge", SOURCE, calc=True)


@mcp.tool()
def find_unusual_regions(week: str = "", top: int = 15) -> dict:
    """한 주에 전국 시군구 중 관외 유입이 평소와 가장 크게 달랐던 곳을 '평소 출렁임의 몇 배' 순으로 돌려준다.
    '이번 주 어디가 튀었나', '전국에서 사람이 갑자기 몰린 곳' 같은 질문에 쓸 것. 직접 여러 지역을 조회해 순위를 만들지 말 것.

    - week를 비우면 가장 최근 주차. 형식 'YYYY.MM.N주차'. top은 늘어난 곳·줄어든 곳 각각 몇 곳(최대 30).
    - 순위는 퍼센트가 아니라 평소 출렁임의 배수다. 원인은 정하지 않는다.
    - 연휴 주에는 전국이 함께 움직여 판정이 흔들린다(결과의 '주의')."""
    try:
        if week:
            wo = _order(week)
        else:
            wo = db.execute("SELECT MAX(week_order) FROM moves WHERE direction='유입' AND category='시군구'").fetchone()[0]
    except Exception:
        return {"error": "week 형식은 'YYYY.MM.N주차'(예: '2026.10.5주차')."}
    top = max(1, min(int(top or 15), 30))
    rows = analysis.rank_week(db, wo)
    if not rows:
        return {"error": "그 주차 자료가 없거나 판정할 수 없다. get_data_status로 최신 주차를 확인할 것."}
    up = [x for x in rows if x["배수"] > 0][:top]
    down = [x for x in rows if x["배수"] < 0][:top]
    h = analysis.holidays(db).get(wo)
    notes = []
    if analysis.is_long_holiday(h):
        notes.append(f"연휴 주({h[2]})라 지역마다 연휴 반응이 달라 판정이 흔들린다. 순위는 참고만 할 것.")
    n_strong = sum(1 for x in rows if abs(x["배수"]) > 2)
    return _answer({
        "주차": f"{wo // 1000}.{(wo // 10) % 100:02d}.{wo % 10}주차", "기간(월~일)": analysis.week_dates(wo),
        "판정한 지역 수": len(rows), "평소 출렁임 2배 넘게 달라진 지역 수": n_strong,
        "평소보다 늘어난 곳": up, "평소보다 줄어든 곳": down,
        "판정 기준": analysis.JUDGE_RULE,
        "주의": notes + [
            "평소 출렁임 2배를 넘는 지역은 평범한 주에도 전국에서 몇 곳씩 나온다(공휴일 없는 주에 위로 약 5%, 아래로 약 3%).",
            "한 지역에서만 크게 튀고 이웃 지역이 반대로 움직였으면 원자료 처리 오류일 수도 있다. 이용자가 원하면 report_issue로 알릴 수 있다.",
        ],
    }, "rank", SOURCE, calc=True)


@mcp.tool()
def estimate_life_pop(sido: str, sigungu: str, year_month: str) -> dict:
    """생활인구가 아직 공표되지 않은 달의 생활인구를 나우캐스트로 가늠한 '참고 추정치'를 돌려준다.
    생활인구 추정 질문에는 반드시 이 도구를 쓸 것. KOSIS 표 등을 조합해 직접 계산하지 말 것.

    - 이미 공표된 달이면 공표 값을 돌려준다(추정하지 않음).
    - 결과의 범위는 '과거 둘 중 한 번은 이 범위 안'이라고 말할 것. '신뢰구간'·'오차범위'라고 부르지 말 것.
    - 공표된 생활인구처럼 말하지 말 것. 반드시 '비공식 참고 추정치'라고 밝힐 것.
    - 공표되지 않은 지역은 타지역 통근 비율 30% 미만이면 공표 지역에서 배운 공식으로 범위를 준다(등급 B, 확인 불가능한 추정).
      30% 이상(대도시 구 등)은 추정하지 않는다.
    - year_month 형식: 'YYYYMM' (예: '202610')."""
    ym = year_month.replace("-", "").replace(".", "")[:6]
    if not (len(ym) == 6 and ym.isdigit()):
        return {"error": "year_month 형식은 'YYYYMM'(예: '202610')."}
    r = lifepop.estimate(db, sido, sigungu, ym)
    if "error" not in r:
        r["자료 기준"] = _as_of()
        return _answer(r, "lifepop", "생활인구·주민등록인구·등록외국인: 국가데이터처·행정안전부·법무부(KOSIS) / "
                       + SOURCE.removeprefix("출처: "), calc=True)
    return r


@mcp.tool()
def forecast_festival(sido: str, sigungu: str, festival_name: str = "", start_date: str = "", end_date: str = "") -> dict:
    """지역 축제가 열리는 주의 관외 유입이 평소보다 얼마나 늘지 '비공식 참고 예측'을 범위로 돌려준다.

    - 축제 방문객 수(명)를 예측하지 않는다. 관외 유입 이동 건수의 상승률(%)이다. '명'으로 바꿔 말하지 말 것.
    - festival_name을 비우면 그 지역의 축제 목록을 돌려준다. start_date·end_date(YYYY-MM-DD)에 올해 일정을 넣으면
      연휴·공휴일 겹침 경고를 붙인다.
    - 결과의 범위는 '과거 둘 중 한 번'·'열 번 중 여덟 번은 이 안'으로 말하고, '신뢰구간·오차범위'라고 부르지 말 것.
    - '예측: 하지 않음'이면 그 이유를 그대로 전하고 숫자를 지어내지 말 것.
    - 예측값은 '비교군 대비' 상승률이다. '평소보다 몇 %' 또는 '평소의 몇 배'로 바꿔 말하지 말 것
      (평소 대비는 '참고: 지난 회차 평소 대비 평균'을 따로 인용할 것).
    - 축제 질문에 직접 계산하지 말고 이 도구를 쓸 것. '작년보다 늘었나'처럼 지난 회차끼리 견주는 질문에도 쓸 것
      (결과의 '지난 회차' 표).
    - 지난 회차가 없는(처음 여는) 축제면 비슷한 지역 축제들의 '낮은 신뢰도 참고 범위'를 돌려준다."""
    r = festival.forecast(db, sido, sigungu, festival_name, start_date, end_date)
    if "error" not in r:
        r["자료 기준"] = _as_of()
        return _answer(r, "festival", "축제 일정: 문화체육관광부 연도별 지역축제 개최 계획 / "
                       + SOURCE.removeprefix("출처: "), calc=True)
    return r


def _load_markets():
    import csv
    out = {}
    with open(Path(__file__).with_name("markets.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["cycle"] != "매일" and r["sido"] and r["sigungu"]:
                out.setdefault(analysis.canon(r["sido"], r["sigungu"]), []).append(
                    f"{r['name']}({r['cycle'].replace('+', '·')}, 점포 {int(float(r['stores'] or 0))}개)")
    return out


MARKETS = _load_markets()


def _rhythm(sido, sigungu):
    try:
        r = None
        for sd, sg in analysis.variants(sido, sigungu):
            r = db.execute("SELECT weekend_ratio, dow_profile, market_amp, market_days, period FROM region_rhythm "
                           "WHERE sido=? AND sigungu=?", (sd, sg)).fetchone() or r
    except sqlite3.OperationalError:
        return None
    mk = MARKETS.get(analysis.canon(sido, sigungu))
    if not r:
        return {"공식 정기장": mk} if mk else None
    ratio, prof, amp, mdays, period = r
    kind = "주말형(관광지·쇼핑 등)" if ratio >= 1.5 else ("평일형(평일에 외부 방문이 많은 곳)" if ratio < 1.1 else "중간")
    out = {"주말/평일 외지인 방문 비율": ratio, "성격": kind, "요일별 상대 수준(1=평균)": json.loads(prof),
           "계산 기간": period}
    if mdays:
        out["장날 리듬"] = f"평일 중 날짜 끝자리 {mdays}에 외지인이 많음(평소의 약 {round((amp - 1) * 100)}% 위)"
    if mk:
        out["공식 정기장(전국전통시장표준데이터)"] = mk[:10]
        if not mdays:
            out["장날 리듬"] = "정기장은 있지만 이 지역 규모에서는 장날 효과가 외지인 방문에 드러나지 않음"
    return out


@mcp.tool()
def get_datalab_daily(sido: str, sigungu: str, start_date: str, end_date: str,
                      kind: Literal["외지인", "현지인", "외국인"] = "외지인") -> dict:
    """한국관광 데이터랩 시군구별 '일별' 방문자 수(사람 수 추정, 단위 '명')와 그 지역의 요일·장날 리듬을 돌려준다.

    - 나우캐스트(주 단위, 이동 '건')와 단위가 다르다. 둘을 더하거나 섞지 말 것.
    - 방문 지표로는 외지인을 쓸 것. 현지인은 그 지역 상주자(거주·근무·통학)라 방문 지표가 아니다.
    - 외지인에는 근무자가 빠져 있어 출퇴근이 많은 지역(업무지구)은 이 자료로 가릴 수 없다.
    - 일 단위라 하루짜리 행사(야구 경기, 불꽃축제, 태풍)를 확인하는 데 쓸 것. 최소 단위는 시군구(읍면동 없음).
    - start_date·end_date: YYYY-MM-DD, 최대 400일. 자료는 약 한 달 늦게 나오고 최근 값은 소급 수정될 수 있다."""
    try:
        a, b = dt.date.fromisoformat(start_date), dt.date.fromisoformat(end_date)
    except Exception:
        return {"error": "날짜 형식은 YYYY-MM-DD."}
    if (b - a).days > 400 or b < a:
        return {"error": "기간은 400일 이하로."}
    codes = []
    for sd, sg in analysis.variants(sido, sigungu):
        r = db.execute("SELECT datalab FROM region_map WHERE sido=? AND sigungu=?", (sd, sg)).fetchone()
        if r and r[0]:
            codes += r[0].split(",")
    if not codes:
        return {"error": "데이터랩과 이어지지 않은 지역. list_regions로 이름을 확인할 것."}
    div = {"현지인": 1, "외지인": 2, "외국인": 3}[kind]
    q = (f"SELECT day, SUM(touNum) FROM datalab_daily WHERE signguCode IN ({','.join('?' * len(codes))}) "
         "AND touDivCd=? AND day BETWEEN ? AND ? GROUP BY day ORDER BY day")
    rows = db.execute(q, (*sorted(set(codes)), div, a.isoformat(), b.isoformat())).fetchall()
    hol = dict(db.execute("SELECT day, name FROM holidays WHERE day BETWEEN ? AND ?", (a.isoformat(), b.isoformat())))
    wd = "월화수목금토일"
    notes = ["값은 KT 통신자료 기반 추정 사람 수(일자별 순방문자, 2박3일이면 3명)다. 나우캐스트 '건'과 다르다."]
    if kind == "외국인":
        notes.append("외국인은 SKT 로밍 단기 방문자만이다(한국 유심·장기 체류 외국인 제외).")
    if a <= dt.date(2026, 4, 30) and b >= dt.date(2025, 5, 1):
        notes.append("2025년 5월~2026년 4월은 데이터랩 전년 대비가 다른 자료보다 8~13%p 높게 나왔다. "
                     "이 기간을 걸친 전년 대비 비교는 하지 말 것.")
    if not rows:
        return {"error": "그 기간 자료가 없음(약 한 달 늦게 나옴). get_data_status로 최신 날짜를 확인할 것."}
    return _answer({
        "지역": f"{sido} {sigungu}", "구분": kind, "단위": "명(추정, 일별)",
        "data": [{"day": d, "요일": wd[dt.date.fromisoformat(d).weekday()], "value": round(v),
                  **({"공휴일": hol[d]} if d in hol else {})} for d, v in rows],
        "이 지역 리듬": _rhythm(sido, sigungu),
        "주의": notes,
        "출처": "한국관광공사 한국관광 데이터랩 지역별 방문자 수(KT·SKT 통신자료 기반 추정)"
                "; 정기장 목록: 소상공인시장진흥공단 전국전통시장표준데이터(공공데이터포털)",
    }, "datalab", "한국관광공사 한국관광 데이터랩 지역별 방문자 수(통신자료 기반 추정)")


@mcp.tool()
def report_issue(message: str,
                 kind: Literal["값이 이상함", "설명이 틀림", "작동 오류", "자료 추가 요청", "제안", "기타"] = "기타",
                 region: str = "", period: str = "", tool: str = "", link: str = "") -> dict:
    """이용자가 이 서비스의 잘못된 값·설명·오류를 운영자에게 알리거나, 서비스에 넣었으면 하는 자료를 요청한다.
    - 보내는 것은 이용자가 '알려 달라·제보하겠다·운영자에게 전해 달라'고 할 때만. 스스로 판단해서 보내지 말 것.
    - 다만 이용자가 이상하다고 말하거나 결과에 원자료 오류 가능성 주의가 있으면, 알릴 수 있다는 사실은 먼저 안내할 것.
    - message는 이용자가 말한 내용을 그대로(1,000자 이하). 이름·연락처·개인정보는 넣지 말 것(받지 않음).
    - region·period·tool에는 문제가 된 지역·기간·도구를 적으면 확인이 빨라진다.
    - 자료 추가 요청: 이용자가 이 서비스에 없는 자료를 찾거나 다른 자료를 따로 찾아 쓰면, 대화에서 한 번만
      '이 자료를 서비스에 넣어 달라고 개발자에게 요청할까요?'라고 묻고, 이용자가 그러겠다고 할 때만 kind='자료 추가 요청'으로 보낼 것.
      message에는 어떤 자료가 왜 필요한지, link에는 그 자료가 있는 공개 페이지 주소(http로 시작, 하나)를 넣을 것. 파일은 받지 않음.
    - 보낸 뒤에는 '받았다, 따로 답장은 없고 반영된 내용은 서비스에 바로 적용된다'고만 전할 것."""
    return feedback.submit(message, kind, region, period, tool, link)


@mcp.tool()
def get_data_status() -> dict:
    """이 서비스가 가진 자료별 최신 시점과 마지막으로 받아 온 날을 돌려준다.
    '언제 자료까지 있나', '최신 주차가 뭐냐'는 질문이나, 다른 도구 결과가 비어 있을 때 기간을 확인하는 데 쓴다.
    - latest: 자료가 담고 있는 가장 최근 시점(주차·날짜·월·반기·연).
    - fetched_ok: 이 서비스가 원천에서 마지막으로 받아 오는 데 성공한 시각(한국 시간).
    - last_failed=1이면 가장 최근 받기 시도가 실패한 상태다. 답할 때 '최신이 아닐 수 있음'을 밝힐 것.
    - 원자료는 소급 수정될 수 있으므로, 수치를 인용할 때 자료 시점과 받아 온 날을 함께 밝힐 것."""
    try:
        rows = db.execute("SELECT name, unit, latest, fetched_ok, last_failed FROM source_status").fetchall()
    except sqlite3.OperationalError:
        rows = []
    import manual
    return {
        "안내": NOTICE,
        "data": [{"자료": n, "단위": u, "latest": l, "fetched_ok": f, "last_failed": bool(x)}
                 for n, u, l, f, x in rows],
        "손으로 넣는 자료": safe.call("손 자료 상태", manual.status, default=[]),
        "손으로 넣는 자료 안내": "자동으로 받지 않고 시즌·관측이 끝난 뒤 운영자가 넣는 자료다. '자료 기준' 뒤의 일은 아직 없다. "
                         "그 뒤 일을 물으면 '이 서비스에는 ○○까지 있다'고 말하고 원출처(KBO·기상청 날씨누리 등)를 안내할 것.",
    }


if __name__ == "__main__":
    mcp.run()  # 로컬 시험은 stdio. 인터넷 주소로 띄울 때는 server_http.py
