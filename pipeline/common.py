"""적재 스크립트 공통: DB 위치, 인증키, 나우캐스트 주차 계산."""
import datetime as dt
import os
import sqlite3
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = Path("/home/data/nowcast.db") if os.environ.get("WEBSITE_SITE_NAME") else ROOT / "data" / "nowcast.db"
DB = Path(os.environ.get("NOWCAST_DB", DEFAULT_DB))


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    # 서버가 긴 읽기(연도별 흐름 검증 등)를 하는 동안 쓰기가 막혀도 바로 실패하지 않게 기다린다(기본 5초 → 120초)
    return sqlite3.connect(DB, timeout=120)


def key(name):
    """인증키는 환경변수로만 받는다(코드·저장소에 쓰지 않음)."""
    v = os.environ.get(name)
    if not v:
        sys.exit(f"환경변수 {name}가 없음")
    return v


def week_label(d):
    """나우캐스트 주차: 한 주는 월~일, 그 주의 목요일이 속한 달로 셈(2022년만 수요일 기준).
    돌려주는 값: ('YYYY.MM.N주차', week_order, 그 주 월요일)"""
    mon = d - dt.timedelta(days=d.weekday())
    wed = mon + dt.timedelta(days=2)
    k = wed if wed.year == 2022 else mon + dt.timedelta(days=3)
    n = (k.day - 1) // 7 + 1
    return f"{k.year}.{k.month:02d}.{n}주차", k.year * 1000 + k.month * 10 + n, mon


SECRET_PARAMS = {"service", "serviceKey", "ServiceKey", "apiKey", "authKey", "key", "KEY"}


def masked(params):
    """오류 메시지·로그·메일에 인증키가 찍히지 않게 가린 사본."""
    return {k: ("***" if k in SECRET_PARAMS else v) for k, v in (params or {}).items()}


def get(url, params, tries=4, timeout=120, wait=20, retry_codes=(), headers=None):
    """외부 API 호출. 응답이 없거나 서버 오류(5xx)면 잠시 쉬었다가 다시 시도한다(공공데이터포털이 가끔 멈춤).
    retry_codes: 4xx 중에서도 다시 시도할 코드(예: KOPIS는 호출이 몰리면 잠깐 400을 돌려줌).
    오류 메시지에는 인증키를 가린 값만 남긴다."""
    for i in range(1, tries + 1):
        try:
            r = requests.get(url, params=params, timeout=timeout, headers=headers)
            if r.status_code < 400:
                return r
            err = f"HTTP {r.status_code}"
            if r.status_code < 500 and r.status_code not in retry_codes:
                raise RuntimeError(f"{err}: {url} {masked(params)}")
        except (requests.Timeout, requests.ConnectionError) as e:
            err = type(e).__name__
        if i == tries:
            raise RuntimeError(f"{tries}번 시도 모두 실패({err}): {url} {masked(params)}")
        print(f"  다시 시도 {i}/{tries - 1} ({err}), {wait * i}초 뒤", flush=True)
        time.sleep(wait * i)
