"""기준 스크린샷과 지금 스크린샷을 화면 이름별로 비교한다.

사용: python3 shotdiff.py <기준 디렉터리> <지금 디렉터리> <결과 디렉터리> [--allow 화면이름 ...]
- 이름이 같은 PNG 끼리 비교한다(Maestro takeScreenshot 이름). 상태 표시줄(위 120px)과 내비게이션 막대(아래 100px)는 뺀다.
- 화면마다 바뀐 픽셀 비율을 적고, 바뀐 곳을 빨갛게 칠한 diff-<이름>.png 와 나란히 놓은 pair-<이름>.png 를 남긴다.
- --allow 에 없는 화면이 THRESHOLD(0.1%) 넘게 바뀌면 종료 코드 1. 고치려던 화면만 --allow 에 넣는다.
- 기준에만 있거나 지금에만 있는 화면도 실패다(화면이 사라졌거나 기준이 없다).
"""
import argparse
import pathlib
import sys

from PIL import Image, ImageChops, ImageDraw

TOP, BOTTOM, THRESHOLD = 120, 100, 0.001


def shots(d):
    return {p.stem: p for p in pathlib.Path(d).rglob("*.png") if "/screenshots/" not in str(p)}


def crop(im):
    return im.convert("RGB").crop((0, TOP, im.width, im.height - BOTTOM))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base"); ap.add_argument("cur"); ap.add_argument("out")
    ap.add_argument("--allow", nargs="*", default=[])
    a = ap.parse_args()
    out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
    base, cur = shots(a.base), shots(a.cur)
    fail = False
    for name in sorted(base.keys() | cur.keys()):
        if name not in base or name not in cur:
            print(f"FAIL {name}: {'기준 없음' if name not in base else '지금 스크린샷 없음'}"); fail = True; continue
        b, c = crop(Image.open(base[name])), crop(Image.open(cur[name]))
        if b.size != c.size:
            print(f"FAIL {name}: 크기 다름 {b.size} vs {c.size}"); fail = True; continue
        diff = ImageChops.difference(b, c).convert("L").point(lambda v: 255 if v > 16 else 0)
        ratio = sum(1 for v in diff.getdata() if v) / (b.width * b.height)
        allowed = name in a.allow
        bad = ratio > THRESHOLD and not allowed
        fail |= bad
        print(f"{'FAIL' if bad else 'OK  '} {name}: {ratio:.3%} 바뀜{' (고치려던 화면)' if allowed else ''}")
        if ratio > 0:
            red = Image.new("RGB", c.size, (255, 0, 0))
            Image.composite(red, c, diff).save(out / f"diff-{name}.png")
            pair = Image.new("RGB", (b.width * 2 + 20, b.height), "white")
            pair.paste(b, (0, 0)); pair.paste(c, (b.width + 20, 0))
            ImageDraw.Draw(pair).text((10, 10), "before", fill="red"); ImageDraw.Draw(pair).text((b.width + 30, 10), "after", fill="red")
            pair.save(out / f"pair-{name}.png")
    print("SHOTDIFF", "FAIL" if fail else "OK")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
