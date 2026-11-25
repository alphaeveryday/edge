"""디자인 정본 화면과 앱 화면을 같은 높이로 나란히 놓는다. 사람이 "정본과 같은가"를 한눈에 판단하게 한다.

사용: python3 side_by_side.py <정본 스크린샷> <앱 스크린샷> <결과.png>
정본 스크린샷은 design/app-v188-v3.dc.html 을 브라우저로 연 화면이다. 회색 배경을 잘라 휴대폰 틀만 남긴다.
"""
import sys

from PIL import Image, ImageChops, ImageDraw


def phone(im):
    im = im.convert("RGB")
    bg = Image.new("RGB", im.size, im.getpixel((5, 5)))
    box = ImageChops.difference(im, bg).convert("L").point(lambda v: 255 if v > 12 else 0).getbbox()
    return im.crop(box) if box else im


def main(design, app, out):
    d, a = phone(Image.open(design)), Image.open(app).convert("RGB")
    h = 1200
    d = d.resize((round(d.width * h / d.height), h)); a = a.resize((round(a.width * h / a.height), h))
    pair = Image.new("RGB", (d.width + a.width + 60, h + 50), "white")
    pair.paste(d, (20, 40)); pair.paste(a, (d.width + 40, 40))
    draw = ImageDraw.Draw(pair)
    draw.text((20, 12), "design", fill="black"); draw.text((d.width + 40, 12), "app", fill="black")
    pair.save(out)
    print("SIDE_BY_SIDE", out)


if __name__ == "__main__":
    main(*sys.argv[1:4])
