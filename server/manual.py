"""손으로 넣는 자료의 출처·기준일·받은 날·다음 갱신(manual_sources.json). get_data_status와 갱신 알림이 쓴다."""
import csv
import datetime as dt
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def status(today=None):
    today = today or dt.date.today()
    out = []
    for s in json.loads((HERE / "manual_sources.json").read_text(encoding="utf-8")):
        latest = None
        try:
            with open(HERE / s["파일"], encoding="utf-8") as f:
                ds = [r.get(s["날짜칸"]) for r in csv.DictReader(f)]
            latest = max(d for d in ds if d and len(d) == 10)
        except Exception:
            pass
        out.append({"자료": s["자료"], "출처": s["출처"], "자료 기준(가장 늦은 날짜)": latest, "받아서 넣은 날": s["받은 날"],
                    "다음 갱신 예상": s["다음 갱신"], "갱신 때": s["갱신 때"],
                    "갱신 시점 지남": dt.date.fromisoformat(s["다음 갱신"]) < today})
    return out
