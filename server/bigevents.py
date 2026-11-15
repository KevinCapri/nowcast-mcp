"""대형 행사 목록(server/events.csv): 전국체전·국제행사 등 축제·공연 목록 밖의 큰 일.

- 날짜는 정부·주최 측 발표나 보도로 확인한 것만 넣는다(파일의 source 칸).
- 프로야구 포스트시즌 등은 확인한 일정을 받으면 같은 파일에 kind를 달리해 더한다.
- 원인으로 단정하지 않는다. '같은 시기에 있었던 일'로만.
"""
import csv
import datetime as dt
from pathlib import Path

import safe
from perf import _sidos

SOURCE = "대형 행사(전국체전·국제행사 등, 정부·주최 측 발표로 확인한 일정)"
_ROWS = None


def _rows():
    global _ROWS
    if _ROWS is None:
        try:
            with open(Path(__file__).with_name("events.csv"), encoding="utf-8") as f:
                _ROWS = []
                for r in csv.DictReader(f):
                    try:
                        if dt.date.fromisoformat(r["s"]) > dt.date.fromisoformat(r["e"]):
                            raise ValueError("시작이 끝보다 늦음")
                        _ROWS.append(r)
                    except Exception as e:
                        safe.bad_row("events.csv", r, e)
        except FileNotFoundError:
            _ROWS = []
    return _ROWS


def in_range(sido, sigungu, a, z):
    """기간 [a, z](date)와 겹치는 대형 행사. [{이름, 종류, 시작, 끝}]"""
    sd = _sidos(sido)
    return [{"이름": r["name"], "종류": r["kind"], "시작": r["s"], "끝": r["e"]}
            for r in _rows() if r["sigungu"] == sigungu and r["sido"] in sd
            and r["s"] <= z.isoformat() and r["e"] >= a.isoformat()]
