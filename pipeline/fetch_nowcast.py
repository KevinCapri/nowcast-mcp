"""나우캐스트 포털에서 인구이동 원자료(유입·유출 엑셀)의 최신 파일을 찾아 받고 DB를 다시 만든다.

- 목록: https://data.kostat.go.kr/nowcast/bigDataDwnOrgDtAjax.do 에 POST
  (searchDvsn=1, pageIndex=0, searchWrd='', searchBaseYear='') → 게시물마다 제목·수정일·첨부파일 이름(atchFileNm)
  2026-11-14 확인: 게시물 '통신모바일 인구이동(유입) 관내외 시군구 자료'(342), '…(유출)…'(350)
- 다운로드: https://data.kostat.go.kr/nowcast/cmm/fileDownload.do 에 POST(atchFileNm, orgnlFileNm)
- 지난번에 받은 파일 이름을 data/raw/latest.txt에 적어 두고, 같으면 다시 받지 않는다(--force로 무시)
- 받은 파일이 1MB보다 작으면(오류 페이지) 멈춘다

쓰는 법:
  python pipeline/fetch_nowcast.py                 # 최신 파일 자동 확인 → 바뀌었으면 받고 DB 다시 만들기
  python pipeline/fetch_nowcast.py --force         # 같아도 다시
  python pipeline/fetch_nowcast.py "<유입 atchFileNm>" "<유출 atchFileNm>"   # 이름을 직접 지정
  --no-build 를 붙이면 받기만 함
"""
import os
import subprocess
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = Path("/home/data/raw") if os.environ.get("WEBSITE_SITE_NAME") else ROOT / "data" / "raw"
BASE = "https://data.kostat.go.kr/nowcast"
PAGE = BASE + "/bigDataDwnOrgDt.do?menuId=6&subMenuId=1&searchDvsn=1"
LIST = BASE + "/bigDataDwnOrgDtAjax.do"
DOWN = BASE + "/cmm/fileDownload.do"
MIN_BYTES = 1_000_000   # 정상 파일은 수백만 바이트(유입 약 500만, 유출 약 320만)


def latest(s):
    """목록에서 유입·유출 게시물의 엑셀 첨부 이름을 찾는다."""
    r = s.post(LIST, data={"searchDvsn": 1, "pageIndex": 0, "searchWrd": "", "searchBaseYear": ""}, timeout=60)
    r.raise_for_status()
    found = {}
    for d in r.json().get("data", []):
        title = d.get("boardDwnldTitle", "")
        files = [f["atchFileNm"] for f in d.get("atchFileList") or [] if f["atchFileNm"].endswith(".xlsx")]
        for tag in ("유입", "유출"):
            if f"({tag})" in title and "시군구" in title and files:
                found[tag] = (files[0], d.get("boardDwnldUpdateDt", ""))
    if set(found) != {"유입", "유출"}:
        sys.exit(f"목록에서 유입·유출 파일을 못 찾음(찾은 것: {found}). 포털 게시판 구조가 바뀌었는지 확인할 것")
    return found


def download(s, atch, save_as):
    orig = atch.split("_", 1)[1] if "_" in atch else atch
    r = s.post(DOWN, data={"atchFileNm": atch, "orgnlFileNm": orig}, timeout=180)
    r.raise_for_status()
    if len(r.content) < MIN_BYTES:
        sys.exit(f"{save_as}: {len(r.content)}바이트 — 파일 대신 오류 페이지가 온 것 같음")
    save_as.write_bytes(r.content)
    print(save_as, len(r.content), "바이트")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    RAW.mkdir(parents=True, exist_ok=True)
    stamp = RAW / "latest.txt"
    s = requests.Session()
    s.get(PAGE, timeout=60)                       # 세션 쿠키 받기(목록 요청이 세션을 요구할 수 있음)
    if len(args) == 2:
        names = {"유입": args[0], "유출": args[1]}
    elif not args:
        found = latest(s)
        names = {k: v[0] for k, v in found.items()}
        print("최신:", {k: f"{v[0]} (수정 {v[1]})" for k, v in found.items()})
    else:
        sys.exit(__doc__)
    key = names["유입"] + "\n" + names["유출"]
    if stamp.exists() and stamp.read_text() == key and "--force" not in sys.argv:
        print("지난번과 같은 파일 → 건너뜀")
        return
    inflow, outflow = RAW / "inflow.xlsx", RAW / "outflow.xlsx"
    download(s, names["유입"], inflow)
    download(s, names["유출"], outflow)
    if "--no-build" not in sys.argv:
        subprocess.run([sys.executable, str(ROOT / "pipeline" / "build_db.py"), str(inflow), str(outflow)], check=True)
    stamp.write_text(key)


if __name__ == "__main__":
    main()
