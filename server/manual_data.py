"""손으로 넣는 자료 파일의 검사와 안전한 읽기.

- 서버는 rows()로 읽는다: 형식이 틀린 줄은 건너뛰고(로그에 남김) 나머지로 동작한다. 한 줄 오타로 도구가 멈추지 않게.
- 올리기 전 검사: python pipeline/check_manual_data.py (GitHub 배포 단계에서도 돌아서, 틀린 줄이 있으면 배포가 멈춘다)
- 파일마다 출처·자료 기준·받은 날·다음 갱신 예상 시점은 manual_data.json에 적는다(get_data_status에 나감).
"""
import csv
import datetime as dt
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "manual_data.json"


def _date(v, need=True):
    if not v:
        if need:
            raise ValueError("날짜가 비어 있음")
        return
    dt.date.fromisoformat(v)


def _int(v, need=True):
    if v in ("", None):
        if need:
            raise ValueError("숫자가 비어 있음")
        return
    int(float(v))


def _need(r, *cols):
    for c in cols:
        if not (r.get(c) or "").strip():
            raise ValueError(f"'{c}' 칸이 비어 있음")


def _kbo(r):
    _date(r["날짜"]); _need(r, "구분", "홈", "방문", "구장"); _int(r["관중수"])


def _season(r):
    _need(r, "구분", "지점", "단계"); _int(r["연도"]); _date(r["날짜"])
    if r["날짜"][:4] != r["연도"]:
        raise ValueError("날짜의 해와 '연도'가 다름")


def _event(r):
    _need(r, "sido", "sigungu", "name"); _date(r["s"]); _date(r["e"])
    if r["s"] > r["e"]:
        raise ValueError("시작이 끝보다 늦음")


def _fest(r):
    _need(r, "sido", "sigungu", "name"); _int(r["y"])
    _date(r.get("s"), False); _date(r.get("e"), False); _date(r.get("main_mon"), False)
    if r.get("s") and r.get("e") and r["s"] > r["e"]:
        raise ValueError("시작이 끝보다 늦음")


CHECKS = {
    "kbo_games.csv": (_kbo, ["날짜", "요일", "구분", "홈", "방문", "구장", "시군구", "관중수"]),
    "kma_season.csv": (_season, ["구분", "지점", "산", "연도", "단계", "날짜"]),
    "events.csv": (_event, ["sido", "sigungu", "name", "s", "e", "kind", "source"]),
    "festivals.csv": (_fest, ["sido", "sigungu", "fid", "y", "name", "s", "e", "main_mon"]),
}


def check(name):
    """(좋은 줄 목록, 오류 목록[(줄 번호, 내용, 이유)])"""
    fn, cols = CHECKS[name]
    good, bad = [], []
    with open(HERE / name, encoding="utf-8-sig") as f:
        rd = csv.DictReader(f)
        miss = [c for c in cols if c not in (rd.fieldnames or [])]
        if miss:
            return [], [(1, ",".join(rd.fieldnames or []), f"없는 칸: {', '.join(miss)}")]
        for i, r in enumerate(rd, start=2):
            try:
                fn(r)
                good.append(r)
            except Exception as e:
                bad.append((i, ",".join((v or "") for v in r.values())[:120], str(e)))
    return good, bad


def rows(name):
    """서버용: 좋은 줄만. 틀린 줄은 건너뛰고 로그에 남긴다. 파일이 없으면 빈 목록."""
    try:
        good, bad = check(name)
    except FileNotFoundError:
        return []
    for i, line, why in bad[:5]:
        print(f"[manual_data] {name} {i}번째 줄 건너뜀({why}): {line}", flush=True)
    if len(bad) > 5:
        print(f"[manual_data] {name} 건너뛴 줄 {len(bad)}개", flush=True)
    return good


def manifest():
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    except Exception:
        return {}
