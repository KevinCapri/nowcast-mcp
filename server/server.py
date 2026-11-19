"""나우캐스트 '통신 모바일 인구이동량' MCP 서버 (시범)

DB 위치는 환경변수 NOWCAST_DB로 바꿀 수 있다(기본: 저장소의 data/nowcast.db, Azure에서는 /home/data/nowcast.db).
"""
import datetime as dt
import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Annotated, Literal

import analysis
import lifepop
import perf
import baseball
import season
import safe
import regioncal
import bigevents
import feedback
import festival
import facility
import tourism
from pydantic import Field
from mcp.types import ToolAnnotations

try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer

ROOT = Path(__file__).resolve().parents[1]
# Azure App Service에서는 배포 때마다 코드 폴더가 새로 풀리므로 DB는 지워지지 않는 /home 아래에 둔다
DEFAULT_DB = Path("/home/data/nowcast.db") if os.environ.get("WEBSITE_SITE_NAME") else ROOT / "data" / "nowcast.db"
DB = Path(os.environ.get("NOWCAST_DB", DEFAULT_DB))
DB.parent.mkdir(parents=True, exist_ok=True)


class _ThreadDB:
    """스레드마다 따로 연결을 쓰는 DB 창구.

    도구는 여러 요청이 동시에 오면 스레드에서 함께 돈다. 연결 하나를 여러 스레드가 같이 쓰면
    sqlite가 'Recursive use of cursors' 등으로 실패해 도구가 'Error executing tool'만 낸다
    (2026-11-16, 동시 4건 요청에서 약 15% 실패 확인). 읽기만 하므로 스레드별 연결로 충분하다."""

    def __init__(self, path):
        self._path = path
        self._local = threading.local()

    def _con(self):
        con = getattr(self._local, "con", None)
        if con is None:
            con = sqlite3.connect(self._path, check_same_thread=False, timeout=30)
            self._local.con = con
        return con

    def execute(self, *args, **kwargs):
        return self._con().execute(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._con(), name)


db = _ThreadDB(DB)
SERVICE = "빅데이터 Insight: 지역별 유입인구"
# 도구 목록이 바뀌면(추가·삭제·입력값·설명) 세 값을 같이 고친다. 회귀 시험이 TOOLS와 실제 도구가 다르면 잡는다.
# 결과마다 붙여서, 예전에 연결한 이용자에게도 AI가 '새 도구가 생겼으니 다시 연결'을 알려 주게 한다(claude/변경_관리_원칙.md).
VERSION = "0.4.8"
TOOLS_CHANGED = "2026-11-17"
TOOLS = ["get_metadata", "list_regions", "get_movement", "get_breakdown", "get_weather", "analyze_region_week",
         "find_unusual_regions", "estimate_life_pop", "forecast_festival", "get_datalab_daily", "report_issue",
         "get_region_calendar", "get_bloom_foliage", "get_data_status", "compare_same_week"]
TOOL_NOTICE = {"버전": VERSION, f"도구 목록({len(TOOLS)}개)": TOOLS, "도구가 바뀐 날": TOOLS_CHANGED,
               "안내": "지금 쓸 수 있는 도구에 이 목록 중 없는 것이 있으면, 이용자에게 대화에서 한 번만 "
                     "'이 서비스에 새 도구(이름)가 생겼다. 커넥터(앱)를 끊었다 다시 연결하면 쓸 수 있다'고 알릴 것."}
SERVICE_FULL = SERVICE + "(개인이 개발한 참고용 서비스, 정부 공식 통계 아님)"

# 도구 정의에 붙이는 동작 표시와 입력값 설명(TDQS·클라이언트가 읽음, 2026-11-16).
# 이 서비스 도구는 report_issue만 바깥(운영자)으로 보내고, 나머지는 모두 읽기만 한다.
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
SENDS = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
Sido = Annotated[str, Field(description="시도 이름. list_regions로 확인한 표기(예: '경상북도', '서울특별시')")]
Sigungu = Annotated[str, Field(description="시군구 이름. list_regions로 확인한 표기(예: '경주시', '수원시 장안구')")]
Week = Annotated[str, Field(description="주차 'YYYY.MM.N주차'(월~일 한 주, 그 주 목요일이 속한 달로 셈. 예: '2025.10.2주차')")]
YearMonth = Annotated[str, Field(description="달 'YYYY-MM'(예: '2026-09')")]
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
    "그러겠다고 할 때만 report_issue(kind='자료 추가 요청')로 보낼 것. "
    "'그 달 그 지역에 무슨 행사·경기가 있었나', '언제였나'는 get_region_calendar로, '올해(그해) 벚꽃·단풍 언제였나'는 "
    "get_bloom_foliage로 답하고, 결과에 함께 나오는 그 주 유입으로 '그래서 사람이 많이 왔나'까지 이어서 답할 것. "
    "'작년보다 많이 왔나', '올해 추석이 작년 추석보다' 같은 해 비교는 compare_same_week로 답할 것(원값끼리 견주지 말 것). "
    "'○○ 인구 유입 분석'처럼 이사인지 방문인지 모호한 포괄 질문은 KOSIS 이사 통계만으로 답하지 말고, 이 서비스의 방문·통근 유입"
    "(최근 흐름·평소와 다른 주·작년 같은 주)도 함께 보여 줄 것. 둘이 다른 자료라는 것을 한 줄로 밝힐 것. 이 서비스에는 지역별 "
    "출발지·연령·성별·시간대·체류 시간이 없으니, 분석 항목을 미리 약속할 때도 이런 항목을 넣지 말 것. "
    "'2019년부터 해마다', '몇 년 새 늘었나'처럼 여러 해 추세를 물으면, 이 서비스로 몇 년 사이 외부 유입이 늘었다·줄었다를 "
    "판단하지 말 것(해마다 원값은 통신사 가입자 등 바탕이 달라 견줄 수 없고, 전국 대비 몫의 변화도 다른 방문 자료와 방향이 "
    "잘 맞지 않음). 대신 이 서비스로는 compare_same_week로 같은 주·명절의 해마다 평소 대비 판정을 보여 주고, 장기 추세는 "
    "이사는 KOSIS 국내인구이동통계, 방문은 한국관광 데이터랩을 안내할 것. "
    "판정·비교·추정·예측은 '빅데이터 Insight'의 계산이라고 부를 것. 커넥터 이름이 nowcast-mcp여도 '나우캐스트 분석', "
    "'나우캐스트 MCP 결과'라고 하지 말 것(나우캐스트는 국가데이터처가 공표한 이동 건수 원자료의 이름이다). "
    "'나우캐스트 MCP'처럼 이 서비스를 나우캐스트 이름으로 부르지 말 것. "
    "모든 답의 맨 끝에는 도구 결과의 '답 끝에 적을 출처'를 줄마다 그대로 붙일 것. 문장 속 설명으로 대신하지 말고, "
    "웹 검색에서 가져온 내용(경기 점수·기사 등)은 그 출처를 따로 한 줄 더 붙일 것. "
    "업데이트 반영 안내는 결과의 '서비스 도구' 안내를 따를 것. "
    "이용자가 'Hello Insight'(또는 '헬로 인사이트'·'안녕 인사이트')라고 하거나 이 서비스로 무엇을 할 수 있는지 물으면, 도구 목록이나 도구 이름을 보고 직접 요약하지 말고 반드시 get_metadata를 먼저 불러 '할 수 있는 일'을 그대로 보여 줄 것. "
    "'Hello Insight'에는 다른 '빅데이터 Insight' 서비스가 함께 연결돼 있으면 각각 불러 함께 소개하고, 'Hello Insight 유입인구'처럼 서비스 이름을 붙이면 그 서비스만 소개할 것."
)
mcp = MCPServer("nowcast-mobility", title=SERVICE, instructions=INSTRUCTIONS, version=VERSION,
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
    "calendar": "일정은 각 기관 자료(천문연구원·문체부·KBO·기상청·KOPIS·정부 발표)이고, 이동 건수는 국가데이터처 공표 통계, "
                "그 주 판정·야구 몫 추정·지난 기록은 이 서비스의 비공식 계산이다. 셋을 구분해 말할 것.",
}
RULES = {
    "judge": ["'평소 출렁임의 몇 배'는 결과 값을 소수 한 자리 그대로 말할 것(1.9를 '약 2배'로 반올림하면 판정 기준과 어긋나 보인다).",
              "'그 주 프로야구 홈경기'가 있으면 경기 수와 '프로야구 홈경기 몫 추정'을 함께 말할 것. 벚꽃·단풍 '지난 기록'이 있으면 견줘 말할 것.",
              "판정을 말할 때 '판정 기준'을 함께 밝힐 것(예: -2.0배는 2배를 '넘지' 않아 '조금 적음').",
              "주차를 말할 때 '기간(월~일)' 날짜를 함께 밝힐 것.",
              "'그 주 축제'가 있으면 그 주에 든 행사 일수·주말 일수를 함께 말할 것(행사가 이틀뿐인 주는 상승이 작게 나온다).",
              "원인을 짚으려면 '함께 볼 것'의 도구로 날씨·일별 방문자를 확인하고 판단 재료로만 제시할 것.",
              "연휴 주면 '연휴 주였다'는 사실만 말하고 증가·감소의 원인을 연휴로 단정하지 말 것. "
              "'이 지역 지난 설·추석 주'와 견줘 예년보다 컸는지 작았는지를 말할 것.",
              "해마다 원값을 직접 견주지 말고 평소 대비끼리 견줄 것.",
              "여러 해에 걸쳐 외부 유입이 늘었다·줄었다고 판단하지 말 것. 해마다 비교는 같은 주의 평소 대비 판정끼리만 하고, 장기 추세는 KOSIS 이사·한국관광 데이터랩을 안내할 것.",
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
                "여러 해에 걸쳐 외부 유입이 늘었다·줄었다고 판단하지 말 것. 해마다 비교는 같은 주의 평소 대비 판정끼리만 하고, 장기 추세는 KOSIS 이사·한국관광 데이터랩을 안내할 것.",
                "유입−유출(순유입)은 방문·통근 이동의 차이일 뿐 인구가 늘거나 줄었다는 뜻이 아니다.",
                "'주의'·'지역 성격'이 있으면 함께 전할 것.",
                "이용자가 '왜'를 물어도 첫 문장은 '이 자료로는 원인을 확정할 수 없다'로 시작할 것. '가장 유력한 원인'처럼 원인에 순위를 매기지 말고, 같은 시기 일은 '같은 시기에 ~가 있었다'로만 말할 것.",
                "'시 단위 주의'가 있으면 구 관외 유입을 더해 '시 밖에서 온 유입'이라고 부르지 말 것."],
    "calendar": ["그 주 일정에 프로야구 홈경기가 있으면 경기 수·관중과 '프로야구 홈경기 몫 추정'을 함께 말할 것"
                 "(벚꽃·축제만 말하고 야구를 빼면 늘어난 이유를 한쪽으로 몰게 된다).",
                 "벚꽃·단풍 명소에 '지난 기록'이 있으면 그해 값을 지난 기록과 견줘 말할 것(예년보다 큰지 작은지).",
                 "벚꽃·단풍 날짜는 기상청 관측일이다. 웹 기사의 예보·예상 날짜가 있어도 관측일을 먼저 말하고, 웹 내용은 출처를 나눠 덧붙일 것.",
                 "'그 주 유입'이 '이동 자료에 없는 주'면 유입을 추정하거나 앞뒤 주로 미루어 결론 내지 말 것.",
                 "일정은 '같은 시기에 ~가 있었다'로만 말하고 유입 변화의 원인으로 단정하지 말 것."],
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
    ts = safe.call("관광지 방문객 몫", tourism.share, db, sido, sigungu)
    if ts:
        out["관광지 방문객"] = ts
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
                "답 끝에 아래 '답 끝에 적을 출처'를 줄마다 그대로 적을 것(그래프·표로 답할 때도, 짧은 답에도). "
                "문장 속에 '비공식 계산'이라고 설명했어도 이 줄은 따로 붙일 것. "
                "웹 검색 등 다른 곳에서 가져온 내용은 그 출처를 따로 밝힐 것.",
                "판정·비교·추정은 '빅데이터 Insight'의 계산이다. '나우캐스트 MCP', '나우캐스트 분석'처럼 나우캐스트를 붙여 "
                "이 서비스를 부르지 말 것(커넥터 이름이 nowcast-mcp여도). 나우캐스트는 국가데이터처 원자료 이름이다.",
                "'평소 출렁임'을 '변동 폭'·'변동성'으로 바꿔 말하지 말 것(변동 폭은 최고·최저 차이로 읽혀 배수가 부풀려 보인다). "
                "'통계적으로 유의하다·두드러진다'는 말도 쓰지 말 것(검정을 한 것이 아니다).",
                "이 서비스 자료에는 지역별 출발지·연령·성별·시간대·체류 시간이 없다. 그런 분석을 이 서비스로 할 수 있다고 "
                "말하거나 다음 단계로 제안하지 말 것(데이터랩 등 다른 자료가 필요하면 그 출처를 밝혀 안내할 것)."],
            "답 끝에 적을 출처": lines}
    r = {k: v for k, v in r.items() if k != "출처"}
    return {**head, **r, "서비스 도구": TOOL_NOTICE}


@mcp.tool(annotations=READ_ONLY)
def get_metadata() -> dict:
    """'Hello Insight'·'헬로 인사이트'·'안녕 인사이트'라고 하거나 이 서비스로 무엇을 할 수 있는지 물으면, 도구 목록이나 도구 이름을 보고 직접 요약하지 말고 반드시 이 도구를 먼저 불러 결과의 '할 수 있는 일'을 그대로 보여 줄 것(이름만 보고 요약하면 방문객 수 예측처럼 할 수 없는 일을 안내하게 된다).
    통신 모바일 인구이동량 통계의 정의와 이용 시 주의사항을 돌려준다.
    다른 도구를 쓰기 전에 반드시 먼저 한 번 호출해서 읽을 것.
    'Hello Insight 유입인구'처럼 이 서비스 이름을 붙여 불러도 이 도구다. 다른 '빅데이터 Insight' 서비스가 함께 연결돼 있으면 'Hello Insight'에는 그쪽 안내도 함께 보여 줄 것.
    결과에는 용어 정의, 값의 단위, 주의사항, 도구 사용 규칙, 판정 방식 변경 이력, 검토했지만 쓰지 않는 자료 등이 들어 있다.
    자료별 최신 시점은 get_data_status로."""
    return {
        "안내": NOTICE,
        "할 수 있는 일": {
            "제목": "빅데이터 Insight: 지역별 유입인구로 할 수 있는 일",
            "할 수 있는 일": [
                "평소와 다른지 보기 — \"○○군 지난주 외부 유입이 평소보다 많았어?\"",
                "전국에서 튄 곳 찾기 — \"지난주 외부 유입이 평소와 가장 달랐던 시군구는?\"",
                "주별 흐름 보기 — \"○○시 올해 주별 외부 유입 흐름 보여 줘\"",
                "그 기간 일정과 함께 보기 — \"지난 8월 ○○군에 무슨 행사가 있었고, 외부 유입은 어땠어?\"",
                "작년 같은 때와 비교 — \"올해 추석 주 외부 유입이 작년 추석 주보다 많았어?\"",
                "축제 참고 예측 — \"○○축제 기간에 외부 유입이 얼마나 늘까?\"",
                "생활인구 참고 추정 — \"○○군 지난달 생활인구 추정해 줘\"",
                "함께 보는 자료 — 날씨, 데이터랩 일별 방문자, 벚꽃·단풍 관측일, 자료 최신 시점",
            ],
            "할 수 없는 일": [
                "방문객 수(명) 계산·예측 — 값은 사람 수가 아닌 이동 건수",
                "늘거나 준 원인 단정 — 같은 시기에 있었던 일만 보여 줌",
                "지역별 출발지·연령·성별·시간대·체류 시간",
                "여러 해에 걸친 추세 판단",
            ],
            "의견": "틀린 값이나 추가했으면 하는 자료는 \"의견 보낼게\"라고 말하면 운영자에게 전달",
            "보여 줄 때": "이용자가 'Hello Insight'·'헬로 인사이트'·'안녕 인사이트'(또는 'Hello Insight 유입인구')라고 하거나 할 수 있는 일을 물으면 이 칸을 문구 그대로, 위 순서대로 보여 줄 것. 예시 질문의 ○○은 그대로 둘 것. 다른 '빅데이터 Insight' 서비스도 연결돼 있으면 서비스마다 나눠 보여 줄 것.",
        },
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
            "여러 해에 걸쳐 외부 유입이 늘었다·줄었다고 판단하지 말 것. 해마다 비교는 같은 주의 평소 대비 판정끼리만 하고, 장기 추세는 KOSIS 이사·한국관광 데이터랩을 안내할 것.",
            "'그때 무슨 일이 있었나·언제였나'는 get_region_calendar(지역·기간), 그해 벚꽃·단풍 날짜는 get_bloom_foliage로 볼 것. "
            "둘 다 그 주 유입 판정을 함께 주므로 '그래서 사람이 많이 왔나'까지 이어서 답할 것.",
            "공표 전 달의 생활인구는 estimate_life_pop으로만 추정할 것. 다른 통계표를 조합해 즉석에서 비율을 계산하지 말 것"
            "(검증된 방법과 범위가 도구에만 들어 있다).",
            "자료가 어디까지 있는지는 get_data_status로 확인할 것.",
            "모든 답 끝에 도구 결과의 '답 끝에 적을 출처'를 적을 것. 공표 통계(이동 건수·주민등록·생활인구)와 "
            "이 서비스의 비공식 계산(판정·추정·예측)을 섞어 부르지 말 것.",
            "이용자가 값이나 설명이 이상하다고 하거나, 결과에 원자료 오류 가능성 같은 주의가 나오면 "
            "'운영자에게 알릴 수 있다(report_issue)'고 한 번 안내할 것. 실제로 보내는 것은 이용자가 원할 때만.",
            "서울에서 '지금·오늘 그 장소가 붐비나'처럼 실시간·장소 단위를 물으면, 이 서비스는 시군구·주 단위라는 것을 밝히고 "
            "서울 열린데이터광장의 '서울 실시간 도시데이터'를 안내할 것. 그 자료는 지난 기록을 주지 않으므로 지난 날짜 질문에는 안내하지 말고, "
            "지난 날의 하루 단위는 get_datalab_daily(구 단위, 약 한 달 늦게 나옴)로 볼 것.",
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
            "관광지 방문객(한국문화관광연구원)": "주요 유료관광지 월별 방문객이 관외 유입의 약 4% 이상인 지역은 달마다 유입과 함께 "
                                         "움직였다(2023.01~2026.05, 15곳 중 대부분 상관 0.6 이상). 2% 미만 지역은 안 보였다. "
                                         "그런 지역에만 '지역 성격'에 몫을 붙이고, 일정 도구에는 달별 상위 관광지를 정보로 보여 준다. 약 5개월 늦게 나온다.",
            "대형 시설 개점·폐점": "보도로 확인한 9곳을 시험해 스타필드 수원(2024.01)만 드러났다(개점 주 4.6배, 뒤로 관외 유입 약 30% 계단). "
                            "효과가 확인된 큰 개점만 판정에 '개점 뒤 8주' 안내를 붙이고, 나머지는 일정에 시험 결과와 함께 보여 준다.",
        },
        "검토했지만 쓰지 않는 자료와 방법": {
            "안내": "아래는 이 서비스가 '주간 외부 유입 변화'를 보는 데 써 보려고 시험한 것들이다. 이 목적에는 맞지 않았다는 뜻이고, "
                    "다른 목적에는 쓸모가 있을 수 있다. 이용자가 이 자료들로 유입 변화를 설명하려 하면 시험 결과를 알려 줄 것.",
            "유가·환율": "휘발유 값이 전년보다 20% 넘게 오른 달(2026년 4~6월)에도 전국 관외 유입은 -0.3~+2.1%로 변화가 없었다. "
                          "엔·달러 환율과 강원·제주 유입의 관계도 약했다(상관 0.2 안팎).",
            "폭염": "최고기온 35도 이상인 날에도 방문은 줄지 않았다(휴가철과 겹친다). 큰비·폭설·추위에는 줄었다.",
            "지자체 집계 해수욕장 이용객 수": "여름 유입 증가와 거의 맞지 않았다(순위상관 0.36). 지자체마다 세는 방식이 다르다.",
            "데이터랩 '현지인' 방문자": "그 지역 주민·근무자의 지역 안 이동이라 외부 방문이 아니다. 생활인구의 체류인구와 따로 움직였다(상관 0.30). 방문은 '외지인'으로 본다.",
            "공항 도착 여객": "제주는 유입과 함께 움직였지만(상관 0.8), 육지 공항(양양·여수·청주)은 노선 변화와 주민 이동 때문에 지역 방문의 근거가 되지 못했다.",
            "KTX·SRT 역 이용객": "중소도시 역(정읍·나주·진주 등)은 유입과 함께 움직였지만 대도시·수도권 역은 맞지 않았다. 월 단위 자료라 주간 판정에 쓰지 않는다. "
                                  "또 일부 역은 역이 있는 시군구보다 실제 목적지와 더 함께 움직였다(동대구역은 동구보다 대구 중구, 김천구미역은 김천보다 구미). "
                                  "반대로 대전역·울산역은 그렇지 않아 일반 규칙은 아니다. 역 이용 변화를 말할 때는 역이 있는 곳과 주변 목적지를 함께 볼 것.",
            "큰 관광지 한 곳의 일별 입장객": "국립중앙박물관 관람객은 용산구 관외 유입의 약 2.6%라, 관람객이 평소의 2배인 주에도 구 전체 유입은 거의 그대로였다. "
                                              "하루 방문객이 그 지역 관외 유입의 5% 이상은 되어야 주간 값에 드러난다.",
            "스키장": "스키장이 있는 11개 시군구 가운데 겨울 유입이 뚜렷이 느는 곳은 무주·평창·태백·정선뿐이었다.",
            "사건·사고와 하루짜리 행사": "국내 주요 사건 약 100건을 대조했더니 시군구·주 단위에서 뚜렷이 보인 것은 20여 건이었다. "
                                          "하루짜리 큰 행사(예: 2025년 한국시리즈)도 주 평균에는 잘 드러나지 않았다.",
            "여러 해에 걸친 증감": "전국 유입에서 그 지역이 차지하는 비중의 해마다 변화가 다른 방문 자료와 같은 방향인 비율이 최근 1년 간격에서 51%뿐이었다. 장기 추세 판단에 쓰지 않는다.",
            "비슷한 축제로 예측 보정": "규모·예산·유형이 비슷한 축제를 섞어도 예측이 정확해지지 않았다. 예측은 그 축제 자신의 지난 회차로만 한다.",
            "이사(전입) 수": "관외 유입 증가는 이사와 거의 무관했다(화천 산천어축제 달의 총전입은 전달보다 줄었는데 관외 유입은 하루 약 2만 건).",
        },
        "생활인구 관련 외부 연구(인용용)": {
            "안내": "생활인구 정책·의견을 물으면 아래 연구를 근거로 인용할 수 있다. 인용할 때는 기관·보고서·저자·연도를 함께 밝히고, "
                    "연구 결과이지 이 서비스의 계산이나 의견이 아님을 분명히 할 것. 대부분 2024~2025년 자료 기준이므로 "
                    "최신 결과는 원문과 국가데이터처 공표를 확인하도록 안내할 것.",
            "방문객 수보다 재방문·체류시간": "생활인구 1인당 카드 소비에는 체류인구 수보다 재방문율과 평균 체류시간의 영향이 더 컸다"
                                          "(한국농촌경제연구원 R2025-03, 박형호 외, 2025. 2024~2025년 생활인구 패널 분석).",
            "지역 유형마다 다른 정책": "생활인구 특성으로 인구감소지역 89곳을 5유형(한국지방행정연구원 2025-06, 박진경 외, 2025), "
                                     "카드 소비로 4유형(한국지방행정연구원 2025-07, 김민영·박진경, 2025)으로 나누었고, "
                                     "전국 252개 시군구를 집중성·방향성·자족성으로 6유형으로 나누었다(국토연구원 기본 25-17, 안소현 외, 2025). "
                                     "모두 같은 정책을 일괄 적용하기보다 유형별 접근을 제안했다.",
            "이웃 경쟁보다 연계": "인구감소지역에서 다른 지역으로 가는 이동 중 가까운 4개 시군구로 가는 비율은 약 10%였다"
                                "(한국지방행정연구원 2023-13, 이원도 외, 2023. SKT 자료). 행정구역을 넘는 정주·근무·방문 연계권을 "
                                "정책 단위로 쓰자는 제안(국토연구원 기본 25-17, 안소현 외, 2025), 이웃 지역끼리 생활인구를 뺏는 경쟁은 "
                                "피하자는 제안(한국보건사회연구원 2025-24, 장인수 외, 2025)이 있다.",
            "격차 고착 우려": "주민등록인구가 많은 지역일수록 생활인구도 많은 경향이 이어져, 생활인구 격차가 굳어질 수 있다는 전망이 있다"
                            "(한국보건사회연구원 2025-24, 장인수 외, 2025).",
            "제도 과제": "생활인구는 인구감소지역 89곳만 공표되어 전국 분석에 쓰기 어렵고(국토연구원 기본 25-17, 안소현 외, 2025), "
                       "시군구보다 작은 읍면동·생활권 단위 통계가 필요하다는 제안이 있다(한국농촌경제연구원 R2025-03, 박형호 외, 2025). "
                       "한 달에 11일 이상 머문 체류인구가 주민등록인구의 평균 33%(지역별 14~85%)라는 분석도 있다"
                       "(국토연구원 국토정책 Brief 1012, 안소현 외, 2025).",
            "통계를 읽을 때 주의": "체류인구는 통신 3사 비가입자와 가명결합 실패로 빠진 부분을 보정한 값이라 실제와 차이가 날 수 있다"
                                 "(국회입법조사처 NARS 입법·정책 169호, 하혜영, 2025. 나우캐스트 생활인구 사용자가이드 인용). "
                                 "체류인구를 머릿수로 보느냐 체류일수를 곱해 보느냐에 따라 결과가 달라진다(국토연구원 수시 23-29, 안소현 외, 2023).",
        },
        "출처": SOURCE,
        "서비스 도구": TOOL_NOTICE,
    }


@mcp.tool(annotations=READ_ONLY)
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


@mcp.tool(annotations=READ_ONLY)
def get_movement(
    direction: Literal["유입", "유출"],
    category: Literal["전국", "성별", "연령", "입지유형", "시도", "시군구"],
    item: str,
    start_week: Week,
    end_week: Week,
    sido: Sido = "",
) -> dict:
    """한 대상의 주차별 인구이동량(일평균 건수)을 기간으로 조회한다. 들어온 양(유입)·나간 양(유출)만 있고 어디서 어디로 갔는지(지역 사이 이동)는 없다. 성별·연령·입지유형은 전국 기준만 있다.

    - category='전국'이면 item='계'.
    - category='성별'이면 item은 '남성' 또는 '여성'.
    - category='연령'이면 item은 '20대 미만','20대','30대','40대','50대','60대','70대 이상' 중 하나.
    - category='입지유형'이면 item은 '상업지역','관광지','대형아울렛','사무지역','레저스포츠','주거지등' 중 하나.
    - category='시도'이면 item은 시도 이름(예: '서울특별시').
    - category='시군구'이면 item은 시군구 이름이고 sido도 반드시 함께 넣을 것(예: item='중구', sido='서울특별시').
    - 몇 주~몇 달 흐름을 보는 데 쓴다. 여러 해에 걸친 증가·감소 판단에는 쓰지 말 것(해마다 원값은 바탕이 달라 견줄 수 없음). 해마다 비교는 compare_same_week, 한 주 판정은 analyze_region_week로.
    - start_week, end_week 형식: 'YYYY.MM.N주차' (예: '2026.10.5주차').
    - 하루 단위 방문자(사람 수)는 get_datalab_daily로(출처·단위가 달라 서로 섞지 말 것).
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
            out["주의"] = sus + [out["주의"]]
        prof = _profile(sido, item)
        if prof:
            out["지역 성격"] = prof
    if direction == "유출":
        out["순유입 주의"] = "유입−유출은 방문·통근 이동의 차이다. 인구가 늘거나 줄었다는 뜻으로 말하지 말 것(이사는 KOSIS 국내인구이동통계)."
    return _answer(out, "nowcast", SOURCE)


@mcp.tool(annotations=READ_ONLY)
def get_breakdown(
    direction: Literal["유입", "유출"],
    category: Literal["성별", "연령", "입지유형", "시도"],
    week: Week,
) -> dict:
    """한 주의 전국 이동량을 한 기준(성별·연령·입지유형·시도)으로 나눠 항목별 건수와 비중(share_pct)을 돌려준다. 성별·연령은 전국 값만 있고 지역별은 없다.
    - '이번 주 이동은 어느 연령·어느 시도가 많았나'처럼 한 주의 구성을 볼 때 쓴다.
    - 전국 기준만 있다. 한 시군구의 값·여러 주 흐름은 get_movement, 평소와 다른지는 analyze_region_week로.
    - 한 번에 한 기준만. 기준끼리 더하면 같은 이동이 두 번 세어진다.
    - 단위는 일평균 이동 건수(사람 수 아님). 유출은 2023년부터. 결과가 없으면 get_data_status로 기간을 확인할 것."""
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


@mcp.tool(annotations=READ_ONLY)
def get_weather(sigungu: Sigungu, sido: Sido, start_week: Week, end_week: Week) -> dict:
    """시군구의 주차별 날씨를 돌려준다. 나우캐스트 이동량과 같은 주차(월~일, 'YYYY.MM.N주차')로 묶여 있다.
    - 값: ta_avg 주 평균기온(°C), ta_max 주 최고기온, ta_min 주 최저기온, rn_sum 주 강수량 합계(mm),
      rain_days 1mm 이상 비 온 날 수, sd_max 주중 최심 적설(cm), days 관측된 날 수.
    - 날씨는 시군구가 아니라 관측소 값이다. 관측소가 없는 시군구는 인근 관측소 값을 빌려 쓰므로
      결과의 '관측소'와 '짝지은 방식'을 답에 함께 밝힐 것. 산간·해안처럼 관측소와 환경이 다른 곳은 오차가 있다.
    - 날씨와 이동량을 함께 볼 때, 같은 시기에 명절·연휴·행사가 겹쳤는지 먼저 확인할 것.
      비 온 주에 이동이 줄었다고 해서 비가 원인이라고 단정하지 말고 가설로 표현할 것.
    - 기온·강수 숫자가 필요할 때 쓴다. 그 기간의 큰 눈·비를 다른 일정과 함께 보려면 get_region_calendar로."""
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
    fo = safe.call("대형 시설 개점", facility.after_open, sido, sigungu, mon)
    if fo:
        r["그 주 큰 시설 개점 안내(원인 단정 금지)"] = fo
    pf = safe.call("공연", perf.in_range, db, sido, sigungu, mon, end)
    if pf:
        r["그 주 큰 공연장 공연(KOPIS, 원인 단정 금지)"] = pf
        r["공연 자료 안내"] = perf.NOTE


@mcp.tool(annotations=READ_ONLY)
def analyze_region_week(sido: Sido, sigungu: Sigungu, week: Week) -> dict:
    """한 시군구의 한 주 관외 유입이 '평소와 다른가, 얼마나 다른가'를 판정한다.

    - 평소 값: 직전 8주(연휴 주 제외) 중앙값. 비교군 대비: 같은 주 비슷한 지역 10곳의 변화를 뺀 값.
      평일 공휴일이 하루 든 주는 그 지역의 공휴일 반응을 뺀다.
    - 판정은 '평소 출렁임의 몇 배'로 말할 것. 결과의 '판정' 문구는 바꾸지 말고 그대로 전할 것.
      '신뢰구간'·'유의하다'는 말은 쓰지 말 것.
    - 원인(축제·날씨·행사)은 이 도구가 정하지 않는다. get_weather 등으로 판단 재료를 함께 보여 주되 단정하지 말 것.
    - 판정·비교군은 이 서비스(빅데이터 Insight: 지역별 유입인구)의 비공식 계산이다. 국가데이터처 값처럼 말하지 말 것.
    - 한 지역의 한 주를 평소와 비교할 때 쓴다. 전국에서 어디가 튀었는지는 find_unusual_regions, 여러 주 흐름은 get_movement,
      지난 해 같은 주와 비교는 compare_same_week, 그 기간에 있었던 일 목록은 get_region_calendar로.
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


def _wo_of_monday(mon):
    """월요일 날짜 → 주차 번호(week_dates의 반대). 목요일(2022년은 수요일)이 속한 달로 센다."""
    for k in (3, 2):
        d = mon + dt.timedelta(days=k)
        wo = d.year * 1000 + d.month * 10 + (d.day - 1) // 7 + 1
        if analysis.week_dates(wo)[:10] == mon.isoformat():
            return wo
    return None


def _label(wo):
    return f"{wo // 1000}.{(wo // 10) % 100:02d}.{wo % 10}주차"


def _same_weeks(wo, years_back):
    """해마다 견줄 주: 기본은 52주 전(월~일 같음), 설·추석 주는 그 해 설·추석 주끼리."""
    mon = dt.date.fromisoformat(analysis.week_dates(wo)[:10])
    hol = analysis.holidays(db)
    h = hol.get(wo)
    tag = None
    if h and h[1] > 0 and h[2]:
        tag = "추석" if "추석" in h[2] else ("설" if "설" in h[2] else None)
    out = []
    for k in range(1, years_back + 1):
        base = mon - dt.timedelta(days=364 * k)
        w, how = _wo_of_monday(base), "52주 전(월~일 같음)"
        if tag:
            best = None
            for c, (_, lunar, names) in hol.items():
                if lunar > 0 and names and tag in names:
                    cm = dt.date.fromisoformat(analysis.week_dates(c)[:10])
                    gap = abs((cm - base).days)
                    if gap <= 70 and (best is None or (-lunar, gap) < (-best[1], best[2])):
                        best = (c, lunar, gap)
            if best:
                w, how = best[0], f"그 해 {tag} 주"
        out.append((k, w, how))
    return tag, out


@mcp.tool(annotations=READ_ONLY)
def compare_same_week(sido: Sido, sigungu: Sigungu, week: Week,
                      years_back: Annotated[int, Field(description="몇 해 전까지 견줄지(1~3)", ge=1, le=3)] = 2) -> dict:
    """한 시군구의 한 주를 지난 해 같은 주와 '평소 대비 변화'끼리 견준다(원값끼리 견주지 않음).
    - '작년보다 사람 많이 왔어?', '올해 추석이 작년 추석보다?' 같은 해 비교 질문에 쓴다.
    - 같은 주는 52주 전(월~일이 같음). 설·추석 주는 그 해 설·추석 주끼리 맞춘다.
    - 한 주 판정은 analyze_region_week, 축제 회차 비교는 forecast_festival, 몇 주~몇 달 흐름은 get_movement로. 여러 해 추세(몇 년 새 늘었나)는 이 서비스로 판단하지 않는다.
    - 해마다 이동 건수 원값을 직접 견주지 말 것(통신사 가입자 등 바탕이 해마다 다름). 결과의 '평소 대비'와 판정끼리 말할 것.
    - 차이의 원인을 단정하지 말고, 해마다 '그 주에 있었던 일'은 판단 재료로만 말할 것."""
    try:
        wo = _order(week)
        analysis.week_dates(wo)
    except Exception:
        return {"error": "week 형식은 'YYYY.MM.N주차'(예: '2026.01.3주차')."}
    years_back = max(1, min(3, int(years_back)))
    keep = ("기간(월~일)", "판정", "평소 대비 변화(%)", "비교군 대비 변화(%)", "공휴일 보정(%)", "최종 변화(%)",
            "평소 출렁임의 몇 배", "관외 유입(일평균 건수)", "프로야구 홈경기 몫 추정(%)")

    sus_lines = []

    def one(w, how):
        r = analyze_region_week(sido, sigungu, _label(w))
        row = {"주차": _label(w), "견준 방식": how}
        if "error" in r:
            row["없음"] = r["error"]
            return row
        row.update({k: r[k] for k in keep if k in r})
        if analysis.is_suspect(sido, sigungu, w):
            row["원자료 확인 필요 구간"] = analysis.SUSPECT_FLAG
            sus_lines.extend(x for x in r.get("주의", []) if x.startswith("원자료 확인 필요 구간") and x not in sus_lines)
        ev = {k: v for k, v in r.items() if k.startswith("그 주 ") and v and "개점 안내" not in k}
        if ev:
            row["그 주에 있었던 일"] = ev
        ao = safe.call("개점 뒤 안내(해마다)", facility.after_open_short, sido, sigungu,
                       dt.date.fromisoformat(analysis.week_dates(w)[:10]))
        if ao:
            row["개점 뒤 안내"] = ao
        return row

    me = one(wo, "물은 주")
    if "없음" in me:
        return {"error": me["없음"]}
    tag, weeks = _same_weeks(wo, years_back)
    rows = [me] + [one(w, how) if w else {"주차": f"{k}년 전", "없음": "같은 주를 찾지 못함"} for k, w, how in weeks]
    def short(x):
        return (f"{x['주차']} {x['최종 변화(%)']:+.1f}% {x['판정']}" if "판정" in x and x.get("최종 변화(%)") is not None
                else f"{x['주차']} 자료 없음")
    out = {
        "지역": f"{sido} {sigungu}",
        "견준 주": rows,
        "요약": " / ".join(short(x) for x in rows),
        "견주는 법": "해마다 그 해의 평소(직전 8주) 대비 변화와 판정끼리 견준다. '관외 유입(일평균 건수)' 원값은 해마다 "
                 "통신사 가입자 등 바탕이 달라 직접 견주지 말 것.",
        "주의": ["판정은 해마다 그 해 평소 출렁임으로 따로 낸 것이다. 올해가 작년보다 '몇 % 더 왔다'로 바꿔 말하지 말 것.",
               "'건'은 사람 수가 아니다. '명'으로 바꿔 말하지 말 것."],
        "출처": SOURCE,
    }
    if any("개점 뒤 안내" in x for x in rows):
        out["주의"].insert(0, "'개점 뒤 안내'가 붙은 해는 판정이 부풀려졌을 수 있으니, 그 해와 견줘 '더 왔다·덜 왔다'고 말하지 말 것.")
    if tag:
        out["주의"].insert(0, f"{tag} 주라서 지난 해도 그 해 {tag} 주와 견줬다(날짜가 달라도).")
    if sus_lines:
        out["주의"] = sus_lines + ["원자료 확인 필요 구간인 해와 견줘 '더 왔다·덜 왔다'고 말하지 말 것."] + out["주의"]
    return _answer(out, "judge", SOURCE, calc=True)


def _hol_day_set():
    try:
        return {d for (d,) in db.execute("SELECT day FROM holidays")}
    except sqlite3.OperationalError:
        return set()


def _same_time(sido, sigungu, wo, h, hd):
    """그 주에 있었던 일을 짧은 줄로(기간 결과의 지역별 주 목록용). 축제(문체부 계획)·대형 행사·프로야구·
    대형 시설 개점·벚꽃/단풍·공휴일. 하나가 실패해도 그 항목만 빠진다."""
    mon = analysis.monday(wo)
    end = mon + dt.timedelta(days=6)
    out = []
    for f in safe.call("축제", festival.in_week, sido, sigungu, mon, hd, default=[]) or []:
        out.append(f"축제: {f['축제']}(문체부 계획 {f['계획 기간']})")
    for e in safe.call("대형 행사", bigevents.in_range, sido, sigungu, mon, end, default=[]) or []:
        out.append(f"대형 행사: {e['이름']}({e['시작']}~{e['끝']})")
    games, crowd = safe.call("프로야구", baseball.week, sido, sigungu, mon, default=([], 0))
    if games:
        out.append(f"프로야구 홈경기: {', '.join(games)}(관중 합계 {crowd:,}명)")
    for x in safe.call("벚꽃·단풍", season.in_week, sido, sigungu, mon, default=[]) or []:
        out.append(f"{x['종류']}: {x['명소']} {x['날짜']}({x['기준']})")
    ao = safe.call("개점 뒤 안내(기간)", facility.after_open_short, sido, sigungu, mon)
    if ao:
        out.append("대형 시설 개점: " + ao)
    if h and h[2]:
        out.append(f"공휴일: {h[2]}" + (" (연휴 주)" if analysis.is_long_holiday(h) else ""))
    return out


def _week_row(w):
    row = {"주차": analysis.label(w["wo"]), "기간(월~일)": analysis.week_dates(w["wo"]),
           "평소 대비(%)": analysis._pct(w["dev"]), "최종 변화(%)": analysis._pct(w["final"]),
           "평소 출렁임(%)": analysis._pct(w["s"]), "배수": w["ratio"], "판정": w["verdict"],
           "판정에 쓴 방식": w["method"]}
    if analysis.is_long_holiday(w["hol"]):
        row["연휴 주"] = w["hol"][2]
    return row


def _period_answer(a, b, top, sido, sigungu, asked):
    """기간 순위(지역 없음) 또는 한 지역의 기간 내 튄 주 목록(지역 있음). 전국순위_기간확장_작업.md 1·2·9번."""
    table = analysis.period_table(db)
    first, last = analysis.judged_range(table)
    if first is None:
        return {"error": "판정할 수 있는 자료가 없다. get_data_status로 자료 상태를 확인할 것."}
    a0, b0 = a or first, b or last
    if a0 > b0:
        return {"error": "start_week가 end_week보다 늦다."}
    lo, hi = max(a0, first), min(b0, last)
    if lo > hi:
        return {"error": f"물어본 기간({analysis.label(a0)} ~ {analysis.label(b0)})에는 판정한 주가 없다. "
                         f"이 서비스는 {analysis.label(first)}부터 {analysis.label(last)}까지 판정한다."}
    y, m = first // 1000, (first // 10) % 100
    head = {
        "판정한 기간": f"{analysis.label(lo)} ~ {analysis.label(hi)}",
        "물어본 기간": asked,
        "안내": (f"이 서비스는 {y}년 {m}월부터 판정한다(코로나 시기를 뺀 기준). "
               "물어본 기간이 그보다 앞서면 앞부분은 빠졌다고 이용자에게 먼저 말할 것."),
    }
    span = (analysis.monday(hi) - analysis.monday(lo)).days / 365.25
    repeat_word = f"해마다 같은 때 되풀이({int(span)}년 남짓한 자료 기준)" if span >= 1 else None
    rule = ("'해마다 같은 때 되풀이'는 서로 다른 두 해 이상에서 같은 때(앞뒤 2주 안)에 '많음' 이상(줄어든 쪽은 '적음' 이하)이 "
            "나왔다는 뜻이다. 원자료 확인 필요 구간인 주는 횟수에서 뺐다.")

    if sigungu:
        c = analysis.canon(sido, sigungu)
        r = table.get(c)
        if not r:
            return {"error": "지역을 찾지 못했거나 판정한 주가 없다. list_regions로 시도·시군구 이름을 확인할 것."}
        ws = [w for w in r["weeks"] if lo <= w["wo"] <= hi]
        hd, hol = _hol_day_set(), analysis.holidays(db)
        out = {**head, "지역": f"{r['sido']} {r['sigungu']}"}
        if ws and (ws[0]["wo"] > lo or ws[-1]["wo"] < hi):
            out["이 지역 판정한 기간"] = f"{analysis.label(ws[0]['wo'])} ~ {analysis.label(ws[-1]['wo'])}"
        rw = analysis.new_start(r["sido"], r["sigungu"])
        if rw and r["weeks"]:
            out["판정 시작"] = analysis.start_note(r["weeks"][0]["wo"], rw)
        for up, name, cnt in ((True, "평소보다 많았던 주('많음' 이상)", "'많음' 이상 횟수"),
                              (False, "평소보다 적었던 주('적음' 이하)", "'적음' 이하 횟수")):
            hit = [w for w in ws if (w["ratio"] > analysis.STRONG if up else w["ratio"] < -analysis.STRONG)]
            rows = []
            for w in hit:
                row = _week_row(w)
                ev = _same_time(r["sido"], r["sigungu"], w["wo"], hol.get(w["wo"]), hd)
                row["같은 시기에 있었던 일"] = ev or "이 서비스 자료에는 없음"
                if w["suspect"]:
                    row["원자료 확인 필요 구간"] = analysis.SUSPECT_FLAG
                rows.append(row)
            strong = analysis._strong(ws, up)
            out[name] = rows or "없음"
            out[cnt] = len(strong)
            if repeat_word and analysis.repeats([w["wo"] for w in strong]):
                out["되풀이" + ("('많음' 이상)" if up else "('적음' 이하)")] = repeat_word
        sus = safe.call("원자료 의심 구간", analysis.suspect, r["sido"], r["sigungu"], lo, hi)
        out["판정 기준"] = analysis.JUDGE_RULE
        out["AI에게"] = [
            "이용자가 물은 것만 답할 것(날짜를 물으면 날짜만, 그때 일을 물으면 그것만). 목록을 한꺼번에 다 펼치지 말 것.",
            "'같은 시기에 있었던 일'은 원인이 아니다. '같은 시기에 ~가 있었다'로만 말할 것.",
            "'이 서비스 자료에는 없음'인 주는 이 서비스가 모르는 일이 있었을 수 있다. 이용자가 원하면 웹 검색을 안내할 것.",
            "해마다 되풀이되는 지역에서 '올해가 작년보다 많았나'를 물으면, 이동 건수를 직접 견주지 말고 compare_same_week로 "
            "그 주를 불러 해마다 평소 대비 판정끼리 견줄 것.",
            rule,
        ]
        out["주의"] = (sus or []) + [
            "'건'은 사람 수가 아니다. '명'으로 바꿔 말하지 말 것.",
            "판정은 주마다 그때까지의 평소 값·평소 출렁임으로 따로 낸 것이다.",
        ]
        out["자료 기준"] = _as_of()
        return _answer(out, "rank", SOURCE, calc=True)

    up, down = analysis.rank_period(table, lo, hi)

    def rows(side):
        out = []
        for i, x in enumerate(side[:top], 1):
            r, w = x["r"], x["best"]
            row = {"순위": i, "지역": f"{r['sido']} {r['sigungu']}", **_week_row(w)}
            row["'많음' 이상 횟수" if side is up else "'적음' 이하 횟수"] = x["n"]
            rw = analysis.new_start(r["sido"], r["sigungu"])
            if rw and r["weeks"]:
                row["판정 시작"] = analysis.start_note(r["weeks"][0]["wo"], rw)
            if x["repeat"] and repeat_word:
                row["되풀이"] = repeat_word
            ao = safe.call("개점 뒤 안내(기간 순위)", facility.after_open_short, r["sido"], r["sigungu"],
                           analysis.monday(w["wo"]))
            if ao:
                row["개점 뒤 안내"] = ao
            out.append(row)
        return out

    return _answer({
        **head,
        "판정한 지역 수": sum(1 for r in table.values() if any(lo <= w["wo"] <= hi for w in r["weeks"])),
        "평소보다 늘어난 곳": rows(up), "평소보다 줄어든 곳": rows(down),
        "판정 기준": analysis.JUDGE_RULE,
        "AI에게": [
            "이 결과에는 지역마다 이 기간에 가장 크게 튄 주 하나만 있다('많음' 이상 횟수에는 그 주도 들어 있다).",
            "이용자가 그 지역이 언제 또 튀었는지(날짜)나 그때 있었던 일을 물으면, sido·sigungu를 넣고 같은 기간으로 "
            "이 도구를 다시 부를 것. 순위만 물었으면 순위만 답할 것.",
            rule,
        ],
        "주의": [
            "원자료 확인 필요 구간인 주는 순위에서 뺐다.",
            "연휴 주('연휴 주' 칸)는 지역마다 연휴 반응이 달라 판정이 흔들린다. 그 줄은 참고만 할 것.",
            "한 지역에서만 크게 튀고 이웃 지역이 반대로 움직였으면 원자료 처리 오류일 수도 있다. 이용자가 원하면 report_issue로 알릴 수 있다.",
        ],
        "자료 기준": _as_of(),
    }, "rank", SOURCE, calc=True)


Top = Annotated[int, Field(description="늘어난 곳·줄어든 곳 각각 몇 곳까지(1~30, 기본 15)", ge=1, le=30)]
OneWeek = Annotated[str, Field(description="한 주 순위의 주차 'YYYY.MM.N주차'(예: '2026.10.5주차'). 비우면 가장 최근 주차. "
                                           "start_week·end_week·sigungu와 함께 쓰지 않음")]
StartWeek = Annotated[str, Field(description="기간 결과의 시작 주 'YYYY.MM.N주차'(예: '2023.06.1주차'). 비우면 판정할 수 있는 "
                                             "첫 주(2023년 6월쯤). 그보다 앞을 넣어도 앞부분은 빠지고 결과 맨 위에 실제 기간이 나옴")]
EndWeek = Annotated[str, Field(description="기간 결과의 끝 주 'YYYY.MM.N주차'(예: '2026.10.5주차'). 비우면 가장 최근 주차")]


@mcp.tool(annotations=READ_ONLY)
def find_unusual_regions(week: OneWeek = "", top: Top = 15, start_week: StartWeek = "", end_week: EndWeek = "",
                         sido: Annotated[str, Field(description="기간 결과에서 한 지역만 볼 때 시도 이름(sigungu와 함께)")] = "",
                         sigungu: Annotated[str, Field(description="기간 결과에서 한 지역만 볼 때 시군구 이름. 넣으면 "
                                                                   "그 지역이 이 기간에 튄 주 전부와 같은 시기 일을 돌려줌")] = "") -> dict:
    """전국 시군구 중 관외 유입이 평소와 가장 크게 달랐던 곳을 '평소 출렁임의 몇 배' 순으로 돌려준다.
    '이번 주 어디가 튀었나', '2023년부터 사람이 가장 갑자기 몰린 지역' 같은 질문에 쓸 것. 직접 여러 지역·여러 주를 조회해 순위를 만들지 말 것.

    - 한 주 순위: week(비우면 가장 최근 주차). 형식 'YYYY.MM.N주차'.
    - 기간 순위: start_week·end_week(하나만 넣어도 됨, sigungu만 넣으면 판정한 전체 기간). 지역마다 가장 크게 튄 주 하나와
      '많음' 이상 횟수, 해마다 같은 때 되풀이 여부. 판정은 2023년 6월쯤부터만 된다(결과 맨 위 '판정한 기간').
    - 기간 순위에서 한 지역이 언제 튀었는지·그때 무슨 일이 있었는지는 sido·sigungu를 넣어 다시 부른다.
    - top은 늘어난 곳·줄어든 곳 각각 몇 곳(최대 30). 순위는 퍼센트가 아니라 평소 출렁임의 배수다. 원인은 정하지 않는다.
    - 연휴 주에는 전국이 함께 움직여 판정이 흔들린다(결과의 '주의').
    - 한 지역의 한 주는 analyze_region_week로."""
    try:
        a = _order(start_week) if start_week else None
        b = _order(end_week) if end_week else None
        for x in (a, b):
            if x:
                analysis.week_dates(x)
    except Exception:
        return {"error": "start_week·end_week 형식은 'YYYY.MM.N주차'(예: '2023.06.1주차')."}
    top = max(1, min(int(top or 15), 30))
    if bool(sido) != bool(sigungu):
        return {"error": "한 지역을 볼 때는 sido와 sigungu를 함께 넣을 것."}
    if a or b or sigungu:
        if week:
            return {"error": "week(한 주 순위)와 start_week·end_week·sigungu(기간 결과)는 함께 쓰지 말 것."}
        asked = f"{start_week or '처음'} ~ {end_week or '최신'}"
        return _period_answer(a, b, top, sido, sigungu, asked)
    try:
        if week:
            wo = _order(week)
        else:
            wo = db.execute("SELECT MAX(week_order) FROM moves WHERE direction='유입' AND category='시군구'").fetchone()[0]
    except Exception:
        return {"error": "week 형식은 'YYYY.MM.N주차'(예: '2026.10.5주차')."}
    rows = analysis.rank_week(db, wo)
    if not rows:
        return {"error": "그 주차 자료가 없거나 판정할 수 없다. get_data_status로 최신 주차를 확인할 것."}
    up = [dict(x) for x in rows if x["배수"] > 0][:top]
    down = [dict(x) for x in rows if x["배수"] < 0][:top]
    mon = dt.date.fromisoformat(analysis.week_dates(wo)[:10])
    sus_lines = []
    for x in up + down:
        sd, _, sg = x["지역"].partition(" ")
        ao = safe.call("개점 뒤 안내(순위)", facility.after_open_short, sd, sg, mon)
        if ao:
            x["개점 뒤 안내"] = ao
        if analysis.is_suspect(sd, sg, wo):
            x["원자료 확인 필요 구간"] = analysis.SUSPECT_FLAG
            sus_lines.extend(f"{x['지역']} — {t}" for t in analysis.suspect(sd, sg, wo))
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
        "주의": sus_lines + notes + [
            "평소 출렁임 2배를 넘는 지역은 평범한 주에도 전국에서 몇 곳씩 나온다(공휴일 없는 주에 위로 약 5%, 아래로 약 3%).",
            "한 지역에서만 크게 튀고 이웃 지역이 반대로 움직였으면 원자료 처리 오류일 수도 있다. 이용자가 원하면 report_issue로 알릴 수 있다.",
        ],
    }, "rank", SOURCE, calc=True)


@mcp.tool(annotations=READ_ONLY)
def estimate_life_pop(sido: Sido, sigungu: Sigungu, year_month: YearMonth) -> dict:
    """생활인구가 아직 공표되지 않은 달의 생활인구를 나우캐스트로 가늠한 '비공식 참고 추정치'(범위)를 돌려준다. 공표된 생활인구가 아니다.
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


@mcp.tool(annotations=READ_ONLY)
def forecast_festival(sido: Sido, sigungu: Sigungu, festival_name: str = "", start_date: str = "", end_date: str = "") -> dict:
    """지역 축제가 열리는 주의 관외 유입 이동 건수가 비교군 대비 얼마나 늘지(상승률 %) '비공식 참고 예측'을 범위로 돌려준다. 방문객 수(명) 예측이 아니다.

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
        return _answer(r, "festival", "축제 일정: 문화체육관광부 지역축제 개최 계획, 언론 보도 / "
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


@mcp.tool(annotations=READ_ONLY)
def get_datalab_daily(sido: Sido, sigungu: Sigungu, start_date: str, end_date: str,
                      kind: Literal["외지인", "현지인", "외국인"] = "외지인") -> dict:
    """한국관광 데이터랩 시군구별 '일별' 방문자 수(사람 수 추정, 단위 '명')와 그 지역의 요일·장날 리듬을 돌려준다.

    - 나우캐스트(주 단위, 이동 '건')와 단위가 다르다. 둘을 더하거나 섞지 말 것.
    - 방문 지표로는 외지인을 쓸 것. 현지인은 그 지역 상주자(거주·근무·통학)라 방문 지표가 아니다.
    - 외지인에는 근무자가 빠져 있어 출퇴근이 많은 지역(업무지구)은 이 자료로 가릴 수 없다.
    - 일 단위라 하루짜리 행사(야구 경기, 불꽃축제, 태풍)를 확인하는 데 쓸 것. 최소 단위는 시군구(읍면동 없음).
    - start_date·end_date: YYYY-MM-DD, 최대 400일. 자료는 약 한 달 늦게 나오고 최근 값은 소급 수정될 수 있다.
    - 주 단위 이동 흐름은 get_movement, 한 주가 평소와 다른지는 analyze_region_week로."""
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


@mcp.tool(annotations=SENDS)
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


@mcp.tool(annotations=READ_ONLY)
def get_region_calendar(sido: Sido, sigungu: Sigungu, start: str, end: str = "") -> dict:
    """한 시군구의 한 기간에 있었던 일(공휴일·축제·대형 행사·프로야구 홈경기 등)을
    주별로 모아, 주마다 그 주의 관외 유입 판정을 함께 돌려준다.
    - '이번 달 경주에 무슨 행사 있었어?', '10월 대구 포스트시즌 경기랑 관중은?'처럼 일정을 묻거나,
      일정과 유입을 함께 묻는 질문('그 행사 때 사람 많이 왔어?')에 쓴다.
    - 일정 없이 한 주가 평소와 다른지만 물으면 analyze_region_week, 벚꽃·단풍 날짜만 물으면 get_bloom_foliage,
      기온·강수 숫자는 get_weather로.
    - start·end는 'YYYY-MM-DD' 또는 'YYYY-MM'. start만 'YYYY-MM'이면 그 달 전체, 날짜 하나면 그 주. 한 번에 1년까지.
    - '그 주 유입'은 analyze_region_week와 같은 계산(판정 문구·배수를 그대로 전할 것). 자세한 판정은 그 도구로.
    - 일정은 '같은 시기에 있었던 일'로만 말하고 유입 변화의 원인으로 단정하지 말 것.
    - 달마다 그 지역 주요 유료관광지 방문객(사람 수, 약 5개월 늦게 나옴)도 함께 준다. 이동 건수와 섞어 계산하지 말 것.
    - '자료 기준' 뒤의 일은 없다. 그 뒤를 물으면 이 서비스에 어디까지 있는지 말하고 원출처를 안내할 것.
    - sido·sigungu는 list_regions로 확인한 이름."""
    r = regioncal.region_calendar(db, sido, sigungu, start, end)
    return _answer(r, "calendar", "일정: 한국천문연구원 특일 정보, 문화체육관광부 지역축제 개최 계획, 언론 보도, 한국야구위원회(KBO) 경기 기록, 한국문화관광연구원 관광자원통계, "
                   "기상청 계절관측·종관기상관측, 공연예술통합전산망(KOPIS), 정부·주최 측 발표 / " + SOURCE.removeprefix("출처: "),
                   calc=True)


@mcp.tool(annotations=READ_ONLY)
def get_bloom_foliage(year: int, kind: str) -> dict:
    """그해 전국 벚꽃(개화·만발) 또는 단풍(유명산 첫 단풍·절정, 단풍나무 표준목) 관측일(기상청 계절관측)을 돌려준다.
    유입 효과가 확인된 명소는 그 주 관외 유입 판정과 이 지역 지난 기록을 함께 준다.
    - '올해 내장산 단풍 절정 언제였어?', '작년 진해 벚꽃 만발은?'과 이어지는 '그때 사람 많이 왔어?'에 쓴다.
    - kind는 '벚꽃' 또는 '단풍'. 관측이 끝난 날짜만 있다(예보 없음). 없는 해는 기상청 날씨누리 '봄꽃·단풍'을 안내할 것.
    - 유입 변화를 벚꽃·단풍 때문이라고 단정하지 말 것.
    - 한 지역의 그 달 일정 전체는 get_region_calendar로."""
    r = regioncal.bloom_foliage(db, year, kind)
    return _answer(r, "calendar", "기상청 기상자료개방포털 계절관측 / " + SOURCE.removeprefix("출처: "), calc=True)


@mcp.tool(annotations=READ_ONLY)
def get_data_status() -> dict:
    """이 서비스가 가진 자료별 최신 시점과 마지막으로 받아 온 날을 돌려준다.
    '언제 자료까지 있나', '최신 주차가 뭐냐'는 질문이나, 다른 도구 결과가 비어 있을 때 기간을 확인하는 데 쓴다.
    - latest: 자료가 담고 있는 가장 최근 시점(주차·날짜·월·반기·연).
    - fetched_ok: 이 서비스가 원천에서 마지막으로 받아 오는 데 성공한 시각(한국 시간).
    - last_failed=1이면 가장 최근 받기 시도가 실패한 상태다. 답할 때 '최신이 아닐 수 있음'을 밝힐 것.
    - 원자료는 소급 수정될 수 있으므로, 수치를 인용할 때 자료 시점과 받아 온 날을 함께 밝힐 것.
    - 자료의 정의와 해석 주의는 get_metadata로."""
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


# ---------- 업데이트 확인(2026-11-17) ----------
# 이용자의 커넥터는 연결(업데이트 반영) 때 받은 도구 설명을 그대로 쓰고, 결과는 늘 최신 서버에서 온다.
# 도구 설명·입력값으로 만든 짧은 코드를 설명 첫 줄과 결과의 '서비스 도구'에 함께 넣어,
# 둘이 다르면(또는 설명에 줄이 없으면) AI가 이용자에게 업데이트 반영을 한 번 알리게 한다.
# 비교는 AI만 하고 이용자에게는 알림 문장만 간다. 설명이 바뀌면 코드도 저절로 바뀐다.
def _stamp_update_code():
    import hashlib
    tools = mcp._tool_manager._tools
    # 입력값은 이름·종류·설명·고를 수 있는 값만 쓴다(pydantic 버전에 따라 바뀌는 모양은 빼서, 코드를 안 고쳤는데 알림이 가지 않게)
    def _params(schema):
        props = (schema or {}).get("properties", {})
        keep = ("type", "description", "enum", "const", "default")
        req = sorted((schema or {}).get("required", []))
        return [req, {k: {kk: v.get(kk) for kk in keep if kk in v} for k, v in sorted(props.items())}]
    src = json.dumps([[n, t.description, _params(t.parameters)] for n, t in sorted(tools.items())],
                     ensure_ascii=False, sort_keys=True, default=str)
    code = hashlib.sha1(src.encode()).hexdigest()[:6]
    for t in tools.values():
        t.description = f"[업데이트 확인 {code}] " + (t.description or "")
    return code


try:
    UPDATE_CODE = _stamp_update_code()
except Exception:
    UPDATE_CODE = None   # 실패하면 옛 안내 그대로(코드 없이 비교하라고 하면 늘 '업데이트 필요'가 나감)
if UPDATE_CODE:
    _old = dict(TOOL_NOTICE)
    TOOL_NOTICE.clear()
    TOOL_NOTICE.update({
        "버전": _old["버전"],
        f"도구 목록({len(TOOLS)}개)": TOOLS,
        "도구가 바뀐 날": TOOLS_CHANGED,
        "업데이트 확인": UPDATE_CODE,
        "안내": ("지금 쓰는 도구 설명 첫 줄의 [업데이트 확인] 값이 위 값과 다르거나 그 줄이 없으면, "
               "대화에서 한 번만 아래 문장을 이용자에게 전할 것. 같으면 아무 말도 하지 말 것. "
               "값이나 비교 과정은 이용자에게 말하지 말 것."),
        "이용자에게 전할 문장(쓰고 있는 AI에 맞는 것 하나만)": {
            "Claude": "빅데이터 Insight에 업데이트가 있습니다. 설정 → 커넥터에서 연결 해제 → 다시 연결해 주세요.",
            "ChatGPT": "빅데이터 Insight에 업데이트가 있습니다. 설정 → 플러그인 → 빅데이터 Insight → 도구 새로고침을 해 주세요.",
            "그 밖의 AI": "빅데이터 Insight에 업데이트가 있습니다. 설정 메뉴에 들어가셔서 다시 연결해 주세요.",
        },
    })


if __name__ == "__main__":
    mcp.run()  # 로컬 시험은 stdio. 인터넷 주소로 띄울 때는 server_http.py
