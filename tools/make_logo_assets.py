"""Дериваты логотипа из исходника img/*.png.
Запуск: python tools/make_logo_assets.py
  assets/logo.png      512px для окна/трея/About/README
  assets/app.ico       многоразмерная иконка для exe
  installer/app.ico    иконка установщика (копия)
  installer/wizard.png мастер установки 328x628 (лого на градиенте)
  installer/header.png шапка мастера 110x116
"""
from PIL import Image, ImageDraw, ImageFont
import glob
import os

SRC = sorted(glob.glob("img/*.png"))[0]
print("SRC:", SRC, os.path.getsize(SRC) // 1024 // 1024, "MB")
os.makedirs("assets", exist_ok=True)

src = Image.open(SRC).convert("RGBA")

logo = src.resize((512, 512), Image.LANCZOS)
logo.save("assets/logo.png")

ico = src.copy()
ico.save("assets/app.ico", sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
ico.save("installer/app.ico", sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])


def bg(w, h):
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    top = (16, 38, 76)
    bot = (8, 16, 34)
    for y in range(h):
        k = y / h
        d.line([(0, y), (w, y)],
               fill=tuple(int(top[i] + (bot[i] - top[i]) * k) for i in range(3)))
    return img, d


def fitted(base_size, box, pad=14):
    """Вписать квадратный логотип в box на градиенте + подпись."""
    w, h = base_size
    img, d = bg(w, h)
    bw, bh = box
    pic = src.resize((bw - pad * 2, bh - pad * 2), Image.LANCZOS)
    img.paste(pic, ((w - pic.width) // 2, 20), pic)
    try:
        f = ImageFont.truetype("C:\\Windows\\Fonts\\arial.ttf", 30 if w > 200 else 13)
    except Exception:
        f = ImageFont.load_default()
    d.text((20, 20 + bh), "DicomBridge", fill=(255, 255, 255), font=f)
    return img


fitted((328, 628), (328, 300)).save("installer/wizard.png")
fitted((110, 116), (110, 62)).save("installer/header.png")
print("ASSETS_OK")
