"""대형 행사 목록(server/events.csv): 전국체전·국제행사 등 축제·공연 목록 밖의 큰 일.

- 날짜는 정부·주최 측 발표나 보도로 확인한 것만 넣는다(파일의 source 칸).
- 프로야구 포스트시즌 등은 확인한 일정을 받으면 같은 파일에 kind를 달리해 더한다.
- 원인으로 단정하지 않는다. '같은 시기에 있었던 일'로만.
"""
import csv
from pathlib import Path

from perf import _sidos

SOURCE = "대형 행사(전국체전·국제행사 등, 정부·주최 측 발표로 확인한 일정)"
_ROWS = None


def _rows():
    global _ROWS
    if _ROWS is None:
        from manual_data import rows      # 형식이 틀린 줄은 건너뜀
        _ROWS = rows("events.csv")
    return _ROWS


def in_range(sido, sigungu, a, z):
    """기간 [a, z](date)와 겹치는 대형 행사. [{이름, 종류, 시작, 끝}]"""
    sd = _sidos(sido)
    return [{"이름": r["name"], "종류": r["kind"], "시작": r["s"], "끝": r["e"]}
            for r in _rows() if r["sigungu"] == sigungu and r["sido"] in sd
            and r["s"] <= z.isoformat() and r["e"] >= a.isoformat()]
