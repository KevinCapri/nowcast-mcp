"""손으로 넣는 자료 파일 검사. 자료를 고친 뒤 올리기 전에 돌린다(GitHub 배포 단계에서도 자동으로 돈다).

  python pipeline/check_manual_data.py

틀린 줄이 있으면 줄 번호와 이유를 보여 주고 종료 코드 1(배포가 멈춤).
KBO 파일은 해마다 정규시즌 경기 수(720)도 확인한다.
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import manual_data as M  # noqa: E402


def main():
    fail = False
    for name in M.CHECKS:
        try:
            good, bad = M.check(name)
        except FileNotFoundError:
            print(f"{name}: 파일 없음")
            fail = True
            continue
        print(f"{name}: 좋은 줄 {len(good)}, 틀린 줄 {len(bad)}")
        for i, line, why in bad[:20]:
            print(f"  {i}번째 줄: {why} | {line}")
        fail |= bool(bad)
        if name == "kbo_games.csv":
            n = Counter(r["날짜"][:4] for r in good if r["구분"] == "정규시즌")
            for y, c in sorted(n.items()):
                if c != 720:
                    print(f"  주의: {y}년 정규시즌 {c}경기(720경기가 아님. 시즌 중이면 괜찮음)")
        if name not in M.manifest():
            print(f"  manual_data.json에 {name} 설명(출처·자료 기준·받은 날·다음 갱신)이 없음")
            fail = True
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
