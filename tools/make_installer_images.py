"""Генератор графики установщика (градиент + название). Запуск: python tools/make_installer_images.py"""
from PIL import Image, ImageDraw, ImageFont
import os

os.makedirs("installer", exist_ok=True)


def bg(w, h):
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    top = (16, 38, 76)
    bot = (8, 16, 34)
    for y in range(h):
        k = y / h
        d.line([(0, y), (w, y)],
               fill=tuple(int(top[i] + (bot[i] - top[i]) * k) for i in range(3)))
    for x in range(20, w, 44):
        for y in range(20, h, 44):
            d.text((x, y), "+", fill=(40, 90, 130), font=ImageFont.load_default())
    return img, d


try:
    FBIG = ImageFont.truetype("C:\\Windows\\Fonts\\arial.ttf", 44)
    FSMALL = ImageFont.truetype("C:\\Windows\\Fonts\\arial.ttf", 20)
    FICO = ImageFont.truetype("C:\\Windows\\Fonts\\arial.ttf", 84)
except Exception:
    FBIG = FSMALL = FICO = ImageFont.load_default()

img, d = bg(328, 628)
d.text((28, 250), "DicomBridge", fill=(255, 255, 255), font=FBIG)
d.text((30, 310), "DICOM -> PACS", fill=(140, 200, 235), font=FSMALL)
d.text((30, 340), "gateway", fill=(140, 200, 235), font=FSMALL)
img.save("installer\\wizard.png")

img2, d2 = bg(110, 116)
d2.text((8, 40), "DB", fill=(255, 255, 255), font=FBIG)
img2.save("installer\\header.png")

ico = Image.new("RGBA", (256, 256), (16, 38, 76, 255))
di = ImageDraw.Draw(ico)
di.rounded_rectangle([24, 24, 232, 232], radius=48, fill=(30, 110, 170))
di.text((78, 92), "DB", fill=(255, 255, 255), font=FICO)
ico.save("installer\\app.ico", sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
print("IMAGES_OK")
