#!/usr/bin/env python3
"""把照片轉成永恆曆 (ULANI P7320E-B) 的 frame 檔。

用法:
  python3 ulani_convert.py 照片.jpg 輸出前綴 [--fit cover|contain] [--no-dither]
  python3 ulani_convert.py --selftest

輸出:
  <前綴>.bin      192000 bytes，800x480，每顆像素 4 bit，高 nibble 在前
  <前綴>_preview.png  把 .bin 解回來的預覽（看到的就是面板會顯示的）
  <前綴>.json     長度與兩個 CRC，給傳送程式用
"""
import argparse
import binascii
import json
import sys
import zlib

import numpy as np
from PIL import Image, ImageOps

W, H = 800, 480
FRAME_BYTES = W * H // 2  # 192000

# index: (名稱, 預覽/抖動用的 RGB)。RGB 是估計值，真實面板顏色會略有差異。
PALETTE = [
    ("black",  (0, 0, 0)),
    ("white",  (255, 255, 255)),
    ("green",  (0, 128, 64)),
    ("blue",   (0, 64, 192)),
    ("red",    (200, 0, 0)),
    ("yellow", (255, 220, 0)),
    ("orange", (255, 125, 0)),
]


def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def crc16_xmodem(data: bytes) -> int:
    return binascii.crc_hqx(data, 0x0000)


def fit_image(img: Image.Image, mode: str) -> Image.Image:
    img = ImageOps.exif_transpose(img).convert("RGB")
    if img.height > img.width:  # 直式照片轉成橫式
        img = img.rotate(90, expand=True)
    if mode == "cover":
        return ImageOps.fit(img, (W, H), method=Image.LANCZOS)
    img = ImageOps.contain(img, (W, H), method=Image.LANCZOS)
    canvas = Image.new("RGB", (W, H), (255, 255, 255))
    canvas.paste(img, ((W - img.width) // 2, (H - img.height) // 2))
    return canvas


def quantize(img: Image.Image, dither: bool) -> np.ndarray:
    pal = Image.new("P", (1, 1))
    flat = []
    for _, rgb in PALETTE:
        flat.extend(rgb)
    flat.extend([0, 0, 0] * (256 - len(PALETTE)))
    pal.putpalette(flat)
    d = Image.FLOYDSTEINBERG if dither else Image.NONE
    q = img.quantize(palette=pal, dither=d)
    idx = np.asarray(q, dtype=np.uint8)
    assert idx.shape == (H, W) and int(idx.max()) < len(PALETTE)
    return idx


def pack(idx: np.ndarray) -> bytes:
    flat = idx.reshape(-1)
    packed = (flat[0::2] << 4) | flat[1::2]
    return packed.astype(np.uint8).tobytes()


def unpack(data: bytes) -> np.ndarray:
    a = np.frombuffer(data, dtype=np.uint8)
    out = np.empty(a.size * 2, dtype=np.uint8)
    out[0::2] = a >> 4
    out[1::2] = a & 0x0F
    return out.reshape(H, W)


def preview(idx: np.ndarray) -> Image.Image:
    lut = np.array([rgb for _, rgb in PALETTE], dtype=np.uint8)
    return Image.fromarray(lut[idx], "RGB")


def validate(data: bytes) -> None:
    assert len(data) == FRAME_BYTES, f"長度 {len(data)} != {FRAME_BYTES}"
    a = np.frombuffer(data, dtype=np.uint8)
    assert int((a >> 4).max()) <= 6 and int((a & 15).max()) <= 6, "出現超過 6 的色碼"


def convert(src, prefix, fit="cover", dither=True):
    img = fit_image(Image.open(src) if isinstance(src, str) else src, fit)
    data = pack(quantize(img, dither))
    validate(data)
    # 解回來再比對一次：打包/解包要完全可逆
    back = unpack(data)
    assert pack(back) == data
    preview(back).save(f"{prefix}_preview.png")
    with open(f"{prefix}.bin", "wb") as f:
        f.write(data)
    meta = {"bin": f"{prefix}.bin", "length": len(data),
            "crc32": f"{crc32(data):08X}", "crc16": f"{crc16_xmodem(data):04X}"}
    with open(f"{prefix}.json", "w") as f:
        json.dump(meta, f, indent=2)
    return meta


def selftest():
    # CRC 標準檢驗值
    assert crc32(b"123456789") == 0xCBF43926, hex(crc32(b"123456789"))
    assert crc16_xmodem(b"123456789") == 0x31C3, hex(crc16_xmodem(b"123456789"))
    print("CRC32 / CRC16-XMODEM 標準檢驗值 OK")
    # 高 nibble 在前
    t = np.zeros((H, W), dtype=np.uint8); t[0, 0] = 5; t[0, 1] = 2
    assert pack(t)[0] == 0x52
    print("打包順序（第一顆像素在高 nibble）OK")
    # 合成測試圖：漸層 + 7 色色塊
    g = np.zeros((H, W, 3), dtype=np.uint8)
    g[..., 0] = np.linspace(0, 255, W, dtype=np.uint8)[None, :]
    g[..., 1] = np.linspace(0, 255, H, dtype=np.uint8)[:, None]
    g[..., 2] = 128
    im = Image.fromarray(g, "RGB")
    for i, (_, rgb) in enumerate(PALETTE):
        im.paste(rgb, (20 + i * 100, 20, 100 + i * 100, 100))
    meta = convert(im, "selftest", fit="cover")
    print("轉換 OK:", meta)
    # 非 800x480 輸入（直式）也要得到 192000 bytes
    meta2 = convert(Image.new("RGB", (300, 900), (200, 30, 30)), "selftest_portrait")
    assert meta2["length"] == FRAME_BYTES
    print("直式輸入 OK")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("src", nargs="?")
    ap.add_argument("prefix", nargs="?")
    ap.add_argument("--fit", choices=["cover", "contain"], default="cover")
    ap.add_argument("--no-dither", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest(); sys.exit(0)
    if not a.src or not a.prefix:
        ap.error("需要 照片 與 輸出前綴")
    print(json.dumps(convert(a.src, a.prefix, a.fit, not a.no_dither), indent=2))
