#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# QQ群：807916734
"""
====================================================================
 饭团影院 爬虫插件 (www.fantuan.vip)   —— v1.1
 适用于：TVBox / 影视仓 / FongMi (Python Spider 规范)
====================================================================
 v1.1 修复:
   * 分类页首页 URL 兼容 MacCMS 规范(省略页码)
   * 分类页解析增加 module-card-item 兜底
   * _parse_cards 放宽 <a> 属性顺序约束
   * _get_html 被盾拦时先预热首页建立 PHPSESSID
====================================================================
"""

from __future__ import print_function

import re
import json
import time
import random
import os

try:
    _SELF_DIR = os.path.dirname(os.path.abspath(__file__))
except Exception:
    _SELF_DIR = os.getcwd()

try:
    import requests
except ImportError:
    requests = None

try:
    from urllib.parse import quote
except ImportError:
    from urllib import quote  # py2

try:
    from html import unescape as _unescape
except ImportError:
    _unescape = None

try:
    from base.spider import Spider as BaseSpider
except ImportError:
    BaseSpider = object

# 验证码 CNN 前向的加速依赖：有 numpy 时卷积/全连接走矢量化，
# 单次识别从 ~0.8s 降到 ~5ms（约 100 倍）；没有则回退纯 Python，行为不变。
try:
    import numpy as _np
except Exception:
    _np = None


# ====================================================================
#  播放解密算法（逆向自 /ftplayer/muiplayer.php 内联混淆脚本）
# ====================================================================
_DEC_KEY = "098f6bcd4621d373cade4e832627b4f6"


def _b64d(s):
    import base64
    s = str(s)
    for cand in (s,
                 s.replace("-", "+").replace("_", "/"),
                 re.sub(r"[^A-Za-z0-9+/=]", "", s)):
        try:
            return base64.b64decode(cand + "=" * ((-len(cand)) % 4))
        except Exception:
            continue
    return b""


def _custom_str_decode(s):
    import base64
    raw = _b64d(s)
    if not raw:
        return ""
    key = _DEC_KEY
    txt = bytes(b ^ ord(key[i % len(key)]) for i, b in enumerate(raw))
    try:
        return base64.b64encode(txt).decode("ascii")
    except Exception:
        return ""


def _de_string(mapping, s):
    if not mapping:
        return s
    out = []
    for ch in str(s):
        if ("a" <= ch <= "z") or ("A" <= ch <= "Z"):
            out.append(mapping[mapping.index(ch)] if ch in mapping else ch)
        else:
            out.append(ch)
    return "".join(out)


def _decode_play(enc, mode=1):
    if not enc:
        return ""
    if str(mode) == "2":
        return _decode_urlmode2(enc)
    try:
        s1 = _custom_str_decode(enc)
        if not s1:
            return ""
        inner = _b64d(s1).decode("utf-8", "ignore")
        text = _b64d(inner).decode("utf-8", "ignore")
        if "/" not in text:
            return ""
        parts = text.split("/")
        if len(parts) < 3:
            return ""
        mp_plain = json.loads(_b64d(parts[0]).decode("utf-8", "ignore"))
        mp_cipher = json.loads(_b64d(parts[1]).decode("utf-8", "ignore"))
        cipher = _b64d("/".join(parts[2:])).decode("utf-8", "ignore")
        buf = []
        for c in cipher:
            if ("a" <= c <= "z") or ("A" <= c <= "Z"):
                buf.append(mp_plain[mp_cipher.index(c)] if c in mp_cipher else c)
            else:
                buf.append(c)
        plain = "".join(buf).replace("\\/", "/").strip()
        if plain.startswith("http") or "://" in plain:
            return plain
    except Exception:
        pass
    return ""


_DEC2_KEY = "PXhw7UT1B0a9kQDKZsjIASmOezxYG4CHo5Jyfg2b8FLpEvRr3WtVnlqMidu6cN"


def _decode_urlmode2(enc):
    if not enc:
        return ""
    try:
        s = _b64d(enc).decode("latin1")
    except Exception:
        return ""
    key = _DEC2_KEY
    out = []
    i = 1
    n = len(s)
    while i < n:
        c = s[i]
        idx = key.find(c)
        if idx == -1:
            out.append(c)
        else:
            out.append(key[(idx + 0x3B) % 0x3E])
        i += 3
    res = "".join(out).replace("\\/", "/").strip()
    if res.startswith("http") or "://" in res:
        return res
    return ""


# ====================================================================
#  纯 Python 验证码识别（无 ddddocr 时的兜底方案）
# ====================================================================
def _png_gray(png_bytes):
    """解析PNG并返回灰度图 (二维列表，0=黑 255=白)。
    支持灰度、RGB、调色板(P)模式，支持位深 1/2/4/8。
    """
    if png_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    try:
        import zlib
        import struct
    except ImportError:
        return None
    i, idat, plte = 8, b"", None
    width = height = ctype = bit_depth = None
    while i < len(png_bytes):
        ln = struct.unpack(">I", png_bytes[i:i + 4])[0]
        name = png_bytes[i + 4:i + 8]
        data = png_bytes[i + 8:i + 8 + ln]
        if name == b"IHDR":
            width, height = struct.unpack(">II", data[:8])
            bit_depth = data[8]
            ctype = data[9]
        elif name == b"PLTE":
            plte = data
        elif name == b"IDAT":
            idat += data
        elif name == b"IEND":
            break
        i += 8 + ln + 4
    if width is None or ctype is None or bit_depth is None:
        return None
    try:
        raw = zlib.decompress(idat)
    except Exception:
        return None
    # 每像素字节数（仅适用于 8/16 位深；低位深需要特殊处理）
    chans = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ctype, 1)
    bpp = chans  # bytes per pixel (8-bit)
    # 行字节数 (向上取整)
    bits_per_row = width * chans * bit_depth
    stride = (bits_per_row + 7) // 8 + 1  # +1 for filter byte
    # 解压图像数据（按行去滤镜）
    # 对于位深 < 8 的，先按原始字节去滤镜，后面再拆像素
    raw_pixels = []  # 每一行的原始字节数据（去滤镜后）
    prev_row = bytearray(stride - 1)
    for y in range(height):
        base = y * stride
        if base + stride > len(raw):
            break
        fil = raw[base]
        row = bytearray(raw[base + 1: base + stride])
        # 去滤镜（按字节）
        for x in range(len(row)):
            left = row[x - bpp] if x >= bpp else 0
            up = prev_row[x]
            ul = prev_row[x - bpp] if x >= bpp else 0
            if fil == 0:
                pass
            elif fil == 1:
                row[x] = (row[x] + left) & 0xFF
            elif fil == 2:
                row[x] = (row[x] + up) & 0xFF
            elif fil == 3:
                row[x] = (row[x] + ((left + up) >> 1)) & 0xFF
            elif fil == 4:
                p = left + up - ul
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - ul)
                pr = left if (pa <= pb and pa <= pc) else (up if pb <= pc else ul)
                row[x] = (row[x] + pr) & 0xFF
        prev_row = row[:]
        raw_pixels.append(bytes(row))
    # 把每一行展开成 width 个像素值 (0-255)
    pixels = []
    for y in range(len(raw_pixels)):
        row_data = raw_pixels[y]
        row_pixels = []
        if bit_depth == 8:
            # 每像素 1 字节
            samples = list(row_data)
        elif bit_depth == 4:
            # 每字节 2 个像素 (高4位 + 低4位)
            samples = []
            for byte_val in row_data:
                samples.append((byte_val >> 4) & 0x0F)
                samples.append(byte_val & 0x0F)
            samples = samples[:width]
        elif bit_depth == 2:
            samples = []
            for byte_val in row_data:
                for shift in (6, 4, 2, 0):
                    samples.append((byte_val >> shift) & 0x03)
            samples = samples[:width]
        elif bit_depth == 1:
            samples = []
            for byte_val in row_data:
                for shift in (7, 6, 5, 4, 3, 2, 1, 0):
                    samples.append((byte_val >> shift) & 0x01)
            samples = samples[:width]
        else:
            # 16位深等，简化处理
            samples = list(row_data)[::2][:width]
        # 根据颜色类型转灰度
        if ctype == 0:  # 灰度
            max_val = (1 << bit_depth) - 1
            gray_row = [int(s * 255 / max_val) for s in samples]
            pixels.append(gray_row)
        elif ctype == 3:  # 调色板
            gray_row = []
            for idx in samples:
                if plte and idx * 3 + 2 < len(plte):
                    r = plte[idx * 3]
                    g = plte[idx * 3 + 1]
                    b = plte[idx * 3 + 2]
                    gray_row.append(int(r * 0.299 + g * 0.587 + b * 0.114))
                else:
                    gray_row.append(255)
            pixels.append(gray_row)
        elif ctype in (2, 6):  # RGB / RGBA
            gray_row = []
            for x in range(width):
                off = x * chans
                if off + 2 < len(samples):
                    r, g, b = samples[off], samples[off + 1], samples[off + 2]
                    gray_row.append(int(r * 0.299 + g * 0.587 + b * 0.114))
                else:
                    gray_row.append(255)
            pixels.append(gray_row)
        else:
            pixels.append(samples[:width])
    return pixels



# ====================================================================
#  纯 Python CNN 验证码识别 (基于 juhaha_v4 模型)
#  当 ddddocr 不可用时作为兜底方案，单字准确率 ~95%
# ====================================================================

import zlib as _zlib_cnn
import base64 as _base64_cnn
import struct as _struct_cnn

CAPTCHA_CHARS = "2345678ABCDEFGHIJKLMNPQRSTUYabcdefghijklmnpqrstuvwxyz"
CAPTCHA_BG = (243, 251, 254)

_CAP_W_B64 = None
_CAP_MODEL = None
_CAP_MODEL_ERR = None

_W_LINES = [
"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",
"6GWAN6EHd4geQCwrAHH00eK4tbENZW+EL9iM34LWe+DaVdR8GaKrjie8IqVRVhLIlTyhlxubJcCgvMNoNkPYgildltz+R15N",
"WE8oYrC9g7u1iUQflaCc9AkXLcyle5nhYTHHsfIstqYGhEYvUwHgQnaJyBHg3kHXSnKq1q0YI0L59LppdVZyiYbglU4H++Vx",
"nvnDGjHYSaQCfDhNcdR60a4BW9VjNOso+F52EL4XNwqBaATquB7SdE0RgKTELioiC1/eSaiXgD7TK514GHrnzZORGhtpI+kw",
"b0k9DJDGoht3WhulxWX0Wbbe0ml+sUO1X0/bm6TGoi7Ytx6v6Hv31TLd9g+4aTzze/4UDBtLwspxanyT27vW7lReEVubr8Ha",
"UmifisoGx8jbpdlHbgUGerX6HR7L0TWJevGTo2HSx+azMxtbjr6Wxl91gTFzO1j7HdkDGJBvQvQxy5voeexxm35Tv1AAzbsI",
"Spv27p3GqMbUwEa+YxkxHoseDoR2QiUZSKwobsh8+gwNN5ZqncOu0NVYXBc/C71y3pegNbo6B7MJO6CZWnE9Mq/BWgq0TBzQ",
"BaFWIx0WXoCVd4TXjxdB4QsX9a30hJpzfvJ9dX0Wa3FkoHLo/qlRwK9T68/U2jhiucfbXCUfv2gQ58dgz9Cpz7mfdEeA+rXi",
"yiX1HHUapTy7cPgoB5FN0wdQjDDu5W1p5Boajo63C6RyBqKmo0xox80pwyI1bA2bc+PmhbeFP4uPx0pEjVL+9cDFC49obQF+",
"eGV6SFIsefLULL1bC6HLW54A3Y/YSpIEWa8WzB33MjTv7jcMkNAxLP/L8UXf853/+WvvrZ+99xdpb6/9pcSw9/7FG98D/tWE",
"F/1/DZoCtq/05jvvBtdzd/Fn0983dm41f3D5e7+uTeN//z/49ja9GT/4b/4h+N+d3/jm1R9+3x6lFX9yGwBM7YcWl4U/kWbu",
"s798+K23fgT/i8P/eRrcX81u+ut2/J/7lX/8D370137+p//tX/2lX/yVX/r1L37xV3/hh774C7/6j774D37m7/3CP/6FX/4P",
"f+of/v2f/cWf/Nn/+Kd++Wd+/pd++cf/01/9t37uiz/9o1/8R3/vF3/ul3/5h/5KDtfPHv/G7/zLvf/pv969/+SbX3/yXx7+",
"9snz9/7zJ9/59lc+fOeb3/rdb/7O7375Kw/euXfvGyff+NJvfvS7v/OBdvgnj3/rSx/90z/+73/7S795+Gfvfenit3/vo288",
"/dpv/JW/MP4vtv+rW39x+NL/dfj/uzS5v25F+IN3v/RUfHf0X6vj5DeeJv/88H+4+P0H2uVvPnn3O/dc+X8ZDr55ujr2/uCd",
"93/zK/+jOvjDb9z8p5/8r1/5J4P0/tnoq08/+iD/2l8uAvA/3fs7f1EE6S8X4V9Km/vrVoOf+r6f+Pztf+ftn/zMv3Hr177w",
"xn/yN37xb/34nf/oc7/yI59768d+4If+3R//zI+9/uuf/aGf/Nf/7i+8/ZPf+/m/9Wuf+vd+4nv+7t/+/L/5az/8/T/193/+",
"Z269/f+QRvfa/4/T6MIvvvHF/+wUAL77+PM0utf/ahrd6b9Iozv9V3D9b795c+PPE+n+36fR7Sf+5m5QbeYTnedW0NDdfBZd",
"+sMstC9nnjn0kqu+wdoQVIDfK5bupMQ+URc2ylApzSvkMgcW9s7oCI0UFxHASkFvUGAHcVgz5mCTYnyuORE3M9ftJsaWqG/2",
"6KC8p7ajqQVFQFrkM31OwaiK46D3XCfRAI08n604W6nL5SqYmC4oc/CK7Rh5DBFsYFVQirBoDvPUxieWY+ObN3h1l+J3MlKJ",
"kOA4XDwOcpaBuWTlYju45OtGqb7Xbma39Jf7t0wKvwSNRQAxF07VNuueJ4ajfKHTJABtHxBsRQTdS0BtP4t9cIbCPhUvGzBd",
"vABfwJxH0YebMAIskJorfFC8ws0rr5EqaqSCG2xp1r3F5sSwYChPaakIJZzKOaPXmkCf3tqhTGdRyWwfPBqKvh4RAVgDNYI7",
"Q0GVANrfvdMOlp7ayL0CO/pE4Eu4XQIhyo7AHI72aQkK2VzsDp3oCAMJLZFITBgTNWIDq4SDtqVQCG5kYJoIFFBDHDTGSBus",
"mGnbcRxPM/vGcoN3qDe15w32/RPxfSP5+tPlbj3swZPXgEWKAC8KGcVcQKPwwlBUOsERl8o2lsHwFHgpBYMBc8/cHuvU6bTR",
"gAXdA9GyR7ginUvLqfkGLN9pSBtVdc7BYoYX4wgpVqgII7WSoj14CnsQ5q0llyW9AohBknYE3GkwOdq0UCzO/dNuFDAcKRU1",
"bJ4gfO7GQs51FU0USyJfqfY3So5kuYNFeHWZXLqdSpmBwV2dWdssF4lZrmE1+iJoVTD91nLIH6eb8v/muy6VN0WkTMVw2s/i",
"0QR1JqKqrJRB6VbeuQaVgmYAgK2EZ2y4qnaR2U0ykIbV6wC2rb3+PYFPAS7siTQQzYDTPjse15IndBR11x3JgOctcdBVBJh/",
"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",
"sMEVrOHmTu9nHTTutt7/HhiJAf/UysAHkoBaHW+90WkgBnzmwplP/9anHj/z6Jf/BxAQ/t9h4J93v9H++H2PvPf9vwoCXwd+",
"f+Y3Pvv4Zx6buVSa+YPfWp5ZLM187kuPffWxTz36yS899huffWP84U998fHPjscf/81Pffmz4/ezD9y3OLdY+p9K//8bpa72",
"elPYPW/LdhH0ZXC/sKTWx4ru+Ln64OCS+nD8NihRKxGDKWhXM5f371tS3U//0hL8knP22p39GSe3Dm6cyxmS+Ye3n0JTjjYZ",
"kWKrR93LkcNY45kKf5CeZn8WnKefGen/Ag47BufTF8787md/6/O/+dV/q/D8wR8+9jtfffTLX/767339i+PnL//OY7/9ld/5",
"2pf+4OtfeezLX/zq1377s7//ta//5pe+/PnH/+cvPfrlx37vq1//ra/9zld+77GvfekrX/qDzz32xa98bTz8xd//3S88+tXH",
"v/rl3//KVx977MuPf+HRr3/+S7/76Bf+4H987As/C0//XOF//69j59P/tmPnP//RX7/yZ09/60f/+INnvvvvn/2rl//kqe//",
"h7//y298/08uX77y98/s7Dz1F0/84xN/9/yT/+75//Tsk3/9Z99B/+hPn/urZ77zzW+98tqf3PzT1//jX33zj//m//zbv/jm",
"s3/5g6e+/a0/+fE/fPOlJ777d0985zt//kd/9e/++ofP/yw46NXYzL+AE/0ncB772qOP/tajn//k73z2U4/+WwXpsPWtK+jL",
"+9vx3oD8xp3V9olVx7wf7b3QhV+5veZurKh/tf79PWn0vcvXwLXN+PbWf2qiztY/RELZcrS1p3+i9Aanlzd3zcs7V4dzd+ln",
"IFOWbuxLg+MbkCdVr6zgPwvS+/G3PvEvIIk/C9LXP/XYv1WMfvWjH/rgr3zo0x94/4c+9POf+NQnHvnVTzzy+MO/9snPf+oz",
"H/21j3/sg4+85+Mf+cUP/vIjv/DRD3/kvR/52Bd++Q/f/0vv+9D7PvG5D37wlx//yKc+/IGP/PqHP/a+L3/y19//uY9/7L0f",
"+fi7P/bRj3zs/Z/+yMc+9/Gff8+HP/ErH/hZjE4v3Kt+Y7z1Rs/8E0aPfu13PvnpT331M7/52cc/OZ7tZ377s7/xL1h9458/",
"943/Hlj9K4F6A6s//NnJEHPk0v8yDwBvdGE8mc995vEz5/4bUn1j3xvtX17/VVNYXj6/WHrgvn/FTCi4UJ/wfCG4F5raGx7F",
"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",
"P01Wx2DjFO2m4sg/3871PQWbPyFYsYju3ov6u49IyT0UMHB93O6Z6E6Buc8G16ga+Nc7WD/4Lz58/jc7fu//re743ebvf5N+",
"sb/5925c/72+Nf9G+O3g1u1/8ObuL7/8nY3fpxSp+W9T3xP//b+/k9/7pe+Mmr/Jy//O7V85Hexc+4/+YfKrb/3S8te3Xtp+",
"baPzqxz/xraM7e1Tx3d/9/d+dfM/aH5L3P3Wd+9s3n9++x/Sb/3uf4ULL//u71Dfqyt/fb5+2/zF7G92/L70t7zjlxnAlSs8",
"jjaXODV2/UFOiHSCQpIUwTviVqHYN6605UpWrraMTAnYCVjEknND7d9U/QcpF10JeVbqAkqR/bQWQvjDDRxu1VebvTzz4I/5",
"omi03pJ5Kz3y/ZQjCVx8xxPCteYAuLjMPY+9IDw4qTpxpJBVCOTSrZMKvn/AUP6azc6OT2XJLIaoT4lMjTC0lT2oP/728vGW",
"JPeoFaoGZsML+1lI0yxi22wjBejOqw1Uhp9yeBTHs0YRnlep/VctoHkXiDEeqZKQSQ4Wae4F3Vyf4oh+7Wr8CTQEGr/6cCNa",
"QQXlav9C/tTR2VNsnBPUXK7j69EymHe213g3/RAX3S6mLyY1nCb5gbItW+1CQ8UijCMnebhXTWjbkR9yCSl9fgrZ+TncolQg",
"S5r0evsBJqJISv6A1uqTbdlUq5kwyV/4YKYB38gimQ4hw29MYN4dbFEVMhlgwJxv2ihsHvBZzPj6lkbtOuRi+A1WqPyEWgHv",
"5/PlM38z3o8XfagBx91GONqNLvX1unvnFcaBp5+R8Gf/ksUuQf1FizemLkc+aiMF+mCwfo89pVeMTooCFN1KsyYzD5PHde1N",
"Jynwy9uV7V8IN8iufm3V3Z9Ji8BD+lf59GeDE3x9x6tWM8IsGbn3LN5Kgp+eIGweoWGWxmCKQrAX5ylaoPHXvJdmCZ57SQrA",
"KYKCQYwXRRQnMQIFGQzAEZQjWBhnOfT1bVAO+rGPu0iaelkUZkX57+IcCbGsRMajxXqeZeZGom2CS7gNfl4o0xEQy+BLHtR2",
"grsAvvFR7GNBwTykKUxfVvgvmzc01ZDgSh07zBe9RL6YUI/3g3DEG1Xg8Of8TARoZhEUT7Sty23cTVvZ3iokAYNeB9JGt+9E",
"Kz7iZHcLQkDObIkgwfvgXY4i/hgm4co7sLLY1yh+ZK9TSfAC/vSavrcYQsbmjM16OrX9PFheYUaeqSLyObgOJzjR8hzUDxow",
"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",
"jOVkHnbgU+lc5TtKMVD6pSjPMhZNTKo+AYWqGxNSI4Prd2zeqkSmx3tlx0XqSp5NI+FW3VeUSpGKD6fC/UkSL5402qnQWmrX",
"UsNKWmba3hfvreXVnRi9qaiEdXLZhckZdE0BfOKLEoSAfft+YS506/2fcK1f7jCbvb57+ZucQXn07PPKVqVyvzamrxB9SPGy",
"f3gixDeO0bkPSIWcfVZp3D5RITuolMYfXgrspFwpk5Wo9tmprmXZlwx6MD7y5gz40YEcO14TjC5lPJWEXjjRwhZtqkYlm2C0",
"MHUZ87EP1f5XV6V2fO6y9yrXfLiBEGO1k63D6rT9SczPQu2cSahUWt1xtpv5WvhMV3Fh6sSQU8QsLe2aqpwCsemlL+7+jVLH",
"P/3j2ed/s9Txzb/dpY4MTOQfCf8d9c9v1fSffv7fvvSC06n4vf1/4/1X/70Of+63vxtdfm/39a+9+P4Pp9/XNOGTb3zynZ/5",
"N57j/l//3X9C/81Sx7f+lpc6AsVBekNvJKVa0s0dBuAnVWplYQJI08VuJ040TWHL0M6PrN87PfodTajqmLlNYQVVF+5O4t55",
"SBJNyooWd56uOoMUxUgi66tgdnm5j4aNbnepeKTOZtTDcJpknbRZsdXVYZQ6CuoOSb+jSOeZLhcV0eiTBLselmDM7cSDr/MV",
"d1VoP0eqth06JW0U5J1WaWk2DWfz6mueyyQ5kLaarHP5Vm93kW6Z3dBmygNUlZvJPjp1sleKqV4fbksUTqh2VoWozvnqnakJ",
"8zgzmJkBiMIINsiund8JiElaqpUH7Nzk1QMRAobzNrf0bUx2PRyY3YPzi7LQxRBdi6f2wQ3t43ZYY4919sdpLE8q4ZPTVBQn",
"RaVAWwnYEbysDOXzdB8XcTj3FZPF5skOtnan1VV7WphzJNu6U+M7k0ZOdhcNa3XPkqZAW3t6+GSIH4xA/Q1OPGVz8OFiFnTf",
"mDcSMznEimUXQ+DPGne2nghC9yevBqFpYHsXXFqUtdqFpu9GmH8cVx/zG58q8RZWsnyiTPzyCw8B9OkE4ZlOLDoE4whNb7Ek",
"Nse/z/Dh0LGuJpiHB3tZ646SMGnWwtpZdiozV6fY2Gq5dSDa2qnAJoxz2sI0+kqHfQPZD/Lzk8KIWjaImfCu8cCcuWvd1U/k",
"2WZ4SpVuy83ZCZzQK7Gi++5hKFlk5PQ6GW60hkb1bEfY2Qt+1hVK/HoJH1D1yP3wtG+/lAidLX4D4cpuu6nb3U/syTeKRye2",
"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",
"+8+xllF7l5NqhHS54Q9Aw3/hLWeNnMnqUo3k+ekk1LkW9tRWTlT4i6rAW6RsI4URxS5Svr+QQ6pmdtnYElHTh+PeP4ZKQxWN",
"vOQFYqSz68TAgiOaIpD+sQwxnmsdPYFSYQo0Xdf31Yn/dfd0pMTkz+8CCXxpyK5VLPfWvjXSwhJ+PAlclhWPd5iYaJzILQlD",
"NrPRFqzW4eGNRiH5NrskO3DrhlGjyPSAHVsbs5em4jz8y+T++lLUP81njcBW8Paq9bS37/nKRbGCridOz/D28ZEjVNG+dAfE",
"hwdeGJlaU+S/I6yrkS3l49tlbIGuSwEwgOKfCI9fuP+2WZTncendaikbOPIEKbOq1Hgi35AvmU27+EYojBBBqJRARdKg/Lk6",
"TC2S66Wt9lyTRrWfcDfG1538tMe09PlqQk0dYRCMEtdV8fzL4C/In1prMVnyVKpA+gRrbWb0QxOk2J2GJ/7lUEd0E9wZcjEd",
"4jbYJtNr2VO0Xagbx9ES/M4hrk29qY1TUxfnZ94SgFStDv6ivR1upKPk0ZskSEgr7g0Kykx5pi48sgUV/Ov3/NFv/ea/8zdL",
"Hd/6W13qQJ5+3rb48/jLTVT96R+9yL+Kv9rU/+xP/6fL3/uDc/nZRw8I69uFIN0/QrBbSO88e/fxo/fZv37Z/+FvzH7wN0sd",
"3/5bXupAinRQUU+E6NC7085UOXzNSKxaAFagHIa4F4nQexFFSjD6sgV9KId8RJt4BIR3SALJlEcr2B+c5A8tbmgPyKrFVmbF",
"L7YN/oKWeu2bNWZFJTFtxQvvZ9tOQyqIlyJVtic/+tTLEdDUH25t8WmcIIRPDX+JKWf/vD3wd1FMkwBnllGsN4WFSpPwmJ2+",
"naEDW6eaLNY0B0a4UCkAVD89YqwdGe6mHHhKfC/TVzJ87r/HIijWnJi3LDYd7jfbWubXFuOf1IbiCayKnXg/EIzhiZSnqiEC",
"ArKOG6fj7zvXhh7HeIU+CL6o1uhx558YJjrokQw01EqHIKgZ+Xrn0xmW927TdzzOlwj2POOxfKMWSlBvSkXK6dCsdKmoyZ60",
"f72T8I5cS1/d57I3enjqmPm1zfCY31ftL/FR5hOKEU5DHLSzgnf9kKd0uWPp5q5RUHoMt+UNQqMj3BXYUzevtEzXGEp7owr0",
"PN296wgqilg62iQt2gcSeZ3+Ql/cf31A0srfedQytv2KtggWOhOkdfWLt4vHRlVVlXxiRF/J7BSTAxR4nmPS0PrSrIm4ua5l",
"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",
]
_CAP_W_LINES = _W_LINES
del _W_LINES


def _cap_weights_b64():
    global _CAP_W_B64
    if _CAP_W_B64 is None:
        _CAP_W_B64 = "".join(_CAP_W_LINES)
    return _CAP_W_B64

def _cap_npz_load(raw):
    out = {}
    if raw[:4] != b"PK\x03\x04":
        raise ValueError("not a zip")
    i, n = 0, len(raw)
    while i < n - 4 and raw[i:i + 4] == b"PK\x03\x04":
        (_v, _f, method, _mt, _md, _crc, csize, _us,
         nlen, elen) = _struct_cnn.unpack("<HHHHHIIIHH", raw[i + 4:i + 30])
        name = raw[i + 30:i + 30 + nlen].decode("utf-8", "replace")
        off = i + 30 + nlen + elen
        blob = raw[off:off + csize]
        if method == 8:
            blob = _zlib_cnn.decompress(blob, -15)
        elif method != 0:
            raise ValueError("bad method %d" % method)
        out[name] = blob
        i = off + csize
    return out


def _cap_npy_read(raw):
    if raw[:6] != b"\x93NUMPY":
        raise ValueError("not npy")
    if raw[6] == 1:
        hlen = _struct_cnn.unpack("<H", raw[8:10])[0]
        hstart = 10
    else:
        hlen = _struct_cnn.unpack("<I", raw[8:12])[0]
        hstart = 12
    header = raw[hstart:hstart + hlen].decode("latin1")
    ms = re.search(r"'shape'\s*:\s*\(([^)]*)\)", header)
    inner = ms.group(1).strip() if ms else ""
    shape = [int(t) for t in inner.split(",") if t.strip()] if inner else []
    md = re.search(r"'descr'\s*:\s*'([^']*)'", header)
    descr = md.group(1) if md else "<f4"
    n = 1
    for s in shape:
        n *= s
    off = hstart + hlen
    if "f4" in descr:
        return list(_struct_cnn.unpack("<%df" % n, raw[off:off + 4 * n]))
    if "f8" in descr:
        return list(_struct_cnn.unpack("<%dd" % n, raw[off:off + 8 * n]))
    if descr.endswith("i1"):
        return list(_struct_cnn.unpack("<%db" % n, raw[off:off + n]))
    if descr.endswith("i4"):
        return list(_struct_cnn.unpack("<%di" % n, raw[off:off + 4 * n]))
    if descr.startswith("<U") or descr.startswith("|S"):
        w = int(descr[2:]) if descr.startswith("<U") else 0
        if w:
            out = []
            for i in range(n):
                seg = raw[off + i * 4 * w:off + (i + 1) * 4 * w]
                out.append(seg.decode("utf-32-le", "ignore").rstrip("\x00"))
            return out
        return []
    return []

class _CapNet(object):

    def __init__(self, raw, hh=40, ww=128):
        self.hh, self.ww = hh, ww
        entries = _cap_npz_load(raw)
        W = {}
        for name, npy in entries.items():
            if name.endswith(".npy"):
                W[name[:-4]] = _cap_npy_read(npy)
        self._dequant(W)
        self.W = W
        self.n1 = len(W["c1.weight"]) // 25
        self.n2 = len(W["c2.weight"]) // (25 * self.n1)
        self.n3 = len(W["c3.weight"]) // (9 * self.n2)
        self.n4 = len(W["c4.weight"]) // (9 * self.n3)
        self.nslot = len([k for k in W if re.match(r"heads\.\d+\.weight$", k)])
        self.ncls = len(W["heads.0.bias"])
        self.fc_in = len(W["fcs.0.weight"]) // len(W["fcs.0.bias"])
        self.fc_out = len(W["fcs.0.bias"])
        self._fuse_bn()
        # numpy 加速视图（无 numpy 时 _np=None，forward 自动回退纯 Python）
        self._np_w = self._build_np() if _np is not None else None

    def _build_np(self):
        """把融合 BN 后的权重整理成 numpy 数组，供 forward_np 矢量化计算。"""
        W = self.W
        k = {}
        k["c1w"] = _np.asarray(W["c1.weight"], dtype=_np.float32).reshape(self.n1, 1, 5, 5)
        k["c1b"] = _np.asarray(W["c1.bias"], dtype=_np.float32)
        k["c2w"] = _np.asarray(W["c2.weight"], dtype=_np.float32).reshape(self.n2, self.n1, 5, 5)
        k["c2b"] = _np.asarray(W["c2.bias"], dtype=_np.float32)
        k["c3w"] = _np.asarray(W["c3.weight"], dtype=_np.float32).reshape(self.n3, self.n2, 3, 3)
        k["c3b"] = _np.asarray(W["c3.bias"], dtype=_np.float32)
        k["c4w"] = _np.asarray(W["c4.weight"], dtype=_np.float32).reshape(self.n4, self.n3, 3, 3)
        k["c4b"] = _np.asarray(W["c4.bias"], dtype=_np.float32)
        k["fcw"] = [_np.asarray(W["fcs.%d.weight" % s], dtype=_np.float32)
                    .reshape(self.fc_out, self.fc_in) for s in range(self.nslot)]
        k["fcb"] = [_np.asarray(W["fcs.%d.bias" % s], dtype=_np.float32)
                   for s in range(self.nslot)]
        k["hw"] = [_np.asarray(W["heads.%d.weight" % s], dtype=_np.float32)
                   .reshape(self.ncls, self.fc_out) for s in range(self.nslot)]
        k["hb"] = [_np.asarray(W["heads.%d.bias" % s], dtype=_np.float32)
                   for s in range(self.nslot)]
        return k

    @staticmethod
    def _conv_np(x, w, b, p, relu):
        """矢量化卷积：im2col 风格的滑动窗口 + einsum 求和。"""
        if p:
            x = _np.pad(x, ((0, 0), (p, p), (p, p)), mode="constant")
        k = w.shape[2]
        xw = _np.lib.stride_tricks.sliding_window_view(x, (k, k), axis=(1, 2))
        out = _np.einsum("ocij,crwij->orw", w, xw) + b[:, None, None]
        if relu:
            _np.maximum(out, 0, out=out)
        return out

    @staticmethod
    def _pool_np(x):
        cin, h, w = x.shape
        if h % 2:
            x = x[:, :-1, :]
            h -= 1
        if w % 2:
            x = x[:, :, :-1]
            w -= 1
        return x.reshape(cin, h // 2, 2, w // 2, 2).max(axis=(2, 4))

    def forward_np(self, img):
        W = self._np_w
        x = _np.asarray(img, dtype=_np.float32).reshape(1, self.hh, self.ww)
        x = self._conv_np(x, W["c1w"], W["c1b"], 2, True)
        x = self._pool_np(x)
        x = self._conv_np(x, W["c2w"], W["c2b"], 2, True)
        x = self._pool_np(x)
        x = self._conv_np(x, W["c3w"], W["c3b"], 1, True)
        x = self._pool_np(x)
        x = self._conv_np(x, W["c4w"], W["c4b"], 1, True)
        n4, h, w = x.shape
        seg_w = w // self.nslot
        outs = []
        for s in range(self.nslot):
            region = x[:, :, s * seg_w:(s + 1) * seg_w]
            vec = region.mean(axis=(1, 2))
            hid = _np.maximum(0.0, W["fcw"][s].dot(vec) + W["fcb"][s])
            outs.append(W["hw"][s].dot(hid) + W["hb"][s])
        return outs

    def forward(self, img):
        """优先 numpy 矢量化前向；任何异常都回退纯 Python，保证识别不中断。"""
        if self._np_w is not None:
            try:
                return self.forward_np(img)
            except Exception:
                pass
        return self._forward_py(img)

    @staticmethod
    def _dequant(W):
        sk = W.pop("__scale_keys__", None)
        sv = W.pop("__scales__", None)
        if not sk or not sv:
            return
        for name, s in zip(sk, sv):
            a = W.get(name)
            if a is not None:
                W[name] = [v * s for v in a]

    def _fuse_bn(self):
        """BN 融进上一层卷积: w' = w*gamma/sqrt(var+eps), b' = beta+(b-mean)*scale"""
        W = self.W
        for ctag, btag in (("c1", "b1"), ("c2", "b2"), ("c3", "b3"), ("c4", "b4")):
            if (btag + ".weight") not in W:
                continue
            w = W[ctag + ".weight"]
            b = W[ctag + ".bias"]
            g = W[btag + ".weight"]
            bt = W[btag + ".bias"]
            rm = W[btag + ".running_mean"]
            rv = W[btag + ".running_var"]
            co = len(g)
            per = len(w) // co
            for oc in range(co):
                sc = g[oc] / ((rv[oc] + 1e-5) ** 0.5)
                base = oc * per
                for j in range(per):
                    w[base + j] *= sc
                b[oc] = bt[oc] + (b[oc] - rm[oc]) * sc

    @staticmethod
    def _pad(x, cin, hin, win, p):
        if p == 0:
            return x, hin, win
        h2, w2 = hin + 2 * p, win + 2 * p
        buf = [0.0] * (cin * h2 * w2)
        for c in range(cin):
            sb = c * hin * win
            db = c * h2 * w2 + p * w2 + p
            for h in range(hin):
                buf[db:db + win] = x[sb + h * win:sb + (h + 1) * win]
                db += w2
        return buf, h2, w2

    @staticmethod
    def _conv(x, cin, hin, win, co, k, w, b, relu=True):
        hout, wout = hin - k + 1, win - k + 1
        out = [0.0] * (co * hout * wout)
        ks = k * k
        rng = range(wout)
        for oc in range(co):
            wch = w[oc * cin * ks:(oc + 1) * cin * ks]
            obase = oc * hout * wout
            bias = b[oc]
            for oh in range(hout):
                orow = obase + oh * wout
                terms = []
                for ic in range(cin):
                    xbase = ic * hin * win
                    wcb = ic * ks
                    for kh in range(k):
                        xr = xbase + (oh + kh) * win
                        wr = wcb + kh * k
                        for kw in range(k):
                            v = wch[wr + kw]
                            if v != 0.0:
                                terms.append((v, x[xr + kw:xr + kw + wout]))
                if not terms:
                    out[orow:orow + wout] = [bias if bias > 0.0 else 0.0] * wout \
                        if relu else [bias] * wout
                elif relu:
                    out[orow:orow + wout] = [
                        (t if t > 0.0 else 0.0) for t in
                        [sum(v * r[i] for v, r in terms) + bias for i in rng]]
                else:
                    out[orow:orow + wout] = [
                        sum(v * r[i] for v, r in terms) + bias for i in rng]
        return out, hout, wout

    @staticmethod
    def _pool2(x, cin, hin, win):
        hout, wout = hin // 2, win // 2
        out = [0.0] * (cin * hout * wout)
        for c in range(cin):
            ib = c * hin * win
            ob = c * hout * wout
            for oh in range(hout):
                r0 = ib + (oh * 2) * win
                r1 = r0 + win
                orow = ob + oh * wout
                for ow in range(wout):
                    j = ow * 2
                    a = x[r0 + j]
                    t = x[r0 + j + 1]
                    if t > a:
                        a = t
                    t = x[r1 + j]
                    if t > a:
                        a = t
                    t = x[r1 + j + 1]
                    if t > a:
                        a = t
                    out[orow + ow] = a
        return out, hout, wout

    @staticmethod
    def _linear(x, n, m, w, b, relu):
        """全连接。用 zip+sum 把逐元素乘法交给 C 层执行。"""
        out = [0.0] * m
        if relu:
            for j in range(m):
                wb = j * n
                s = b[j] + sum(a * c for a, c in zip(x, w[wb:wb + n]))
                out[j] = s if s > 0.0 else 0.0
        else:
            for j in range(m):
                wb = j * n
                out[j] = b[j] + sum(a * c for a, c in zip(x, w[wb:wb + n]))
        return out

    def _forward_py(self, img):
        W = self.W
        x, c, h, w = img, 1, self.hh, self.ww
        x, h, w = self._pad(x, c, h, w, 2)
        x, h, w = self._conv(x, c, h, w, self.n1, 5, W["c1.weight"], W["c1.bias"])
        x, h, w = self._pool2(x, self.n1, h, w)
        x, h, w = self._pad(x, self.n1, h, w, 2)
        x, h, w = self._conv(x, self.n1, h, w, self.n2, 5,
                             W["c2.weight"], W["c2.bias"])
        x, h, w = self._pool2(x, self.n2, h, w)
        x, h, w = self._pad(x, self.n2, h, w, 1)
        x, h, w = self._conv(x, self.n2, h, w, self.n3, 3,
                             W["c3.weight"], W["c3.bias"])
        x, h, w = self._pool2(x, self.n3, h, w)
        x, h, w = self._pad(x, self.n3, h, w, 1)
        x, h, w = self._conv(x, self.n3, h, w, self.n4, 3,
                             W["c4.weight"], W["c4.bias"])
        seg_w = w // self.nslot
        outs = []
        for s in range(self.nslot):
            c0 = s * seg_w
            vec = [0.0] * self.n4
            inv = 1.0 / float(h * seg_w)
            for ch in range(self.n4):
                base = ch * h * w
                acc = 0.0
                for r in range(h):
                    rb = base + r * w + c0
                    acc += sum(x[rb:rb + seg_w])
                vec[ch] = acc * inv
            hid = self._linear(vec, self.fc_in, self.fc_out,
                               W["fcs.%d.weight" % s], W["fcs.%d.bias" % s], True)
            outs.append(self._linear(hid, self.fc_out, self.ncls,
                                     W["heads.%d.weight" % s],
                                     W["heads.%d.bias" % s], False))
        return outs

    def decode(self, img, chars):
        return "".join(chars[max(range(self.ncls), key=lambda i, o=o: o[i])]
                       for o in self.forward(img))

def _cap_model():
    global _CAP_MODEL, _CAP_MODEL_ERR
    if _CAP_MODEL is None and _CAP_MODEL_ERR is None:
        try:
            raw = _zlib_cnn.decompress(_base64_cnn.b64decode(_cap_weights_b64()))
            _CAP_MODEL = _CapNet(raw)
        except Exception as e:
            _CAP_MODEL_ERR = "%s: %s" % (type(e).__name__, str(e)[:120])
    return _CAP_MODEL

def _cap_png_decode(buf):
    if buf[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not png")
    i = 8
    idat = []
    pal = None
    W = H = bd = ct = None
    inter = 0
    while i < len(buf):
        ln = int.from_bytes(buf[i:i + 4], "big")
        typ = buf[i + 4:i + 8]
        data = buf[i + 8:i + 8 + ln]
        if typ == b"IHDR":
            W = int.from_bytes(data[0:4], "big")
            H = int.from_bytes(data[4:8], "big")
            bd = data[8]
            ct = data[9]
            inter = data[12]
        elif typ == b"PLTE":
            pal = data
        elif typ == b"IDAT":
            idat.append(data)
        elif typ == b"IEND":
            break
        i += 12 + ln
    if inter:
        raise ValueError("interlaced png not supported")
    raw = _zlib_cnn.decompress(b"".join(idat))
    bpp = max(1, bd // 8)
    stride = (W * bd + 7) // 8
    prev = bytearray(stride)
    pix = bytearray(W * H)
    p = 0
    for r in range(H):
        ft = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        if ft == 1:
            for x in range(bpp, stride):
                line[x] = (line[x] + line[x - bpp]) & 255
        elif ft == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 255
        elif ft == 3:
            for x in range(stride):
                a = line[x - bpp] if x >= bpp else 0
                line[x] = (line[x] + ((a + prev[x]) >> 1)) & 255
        elif ft == 4:
            for x in range(stride):
                a = line[x - bpp] if x >= bpp else 0
                b = prev[x]
                c = prev[x - bpp] if x >= bpp else 0
                pp = a + b - c
                pa = abs(pp - a)
                pb = abs(pp - b)
                pc = abs(pp - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pr) & 255
        base = r * W
        if bd == 8:
            pix[base:base + W] = line[:W]
        elif bd == 4:
            for x in range(W):
                byte = line[x >> 1]
                pix[base + x] = (byte >> 4) if (x & 1) == 0 else (byte & 15)
        elif bd == 2:
            for x in range(W):
                byte = line[x >> 2]
                pix[base + x] = (byte >> (6 - 2 * (x & 3))) & 3
        elif bd == 1:
            for x in range(W):
                byte = line[x >> 3]
                pix[base + x] = (byte >> (7 - (x & 7))) & 1
        else:
            raise ValueError("bad bitdepth %s" % bd)
        prev = line
    return W, H, bd, ct, pal, pix


def _cap_png_feature(buf):
    """PNG -> 前景强度 list(0~1), 长度 W*H。用与背景色的曼哈顿距离。"""
    W, H, bd, ct, pal, pix = _cap_png_decode(buf)
    if ct != 3 or pal is None:
        raise ValueError("expect palette png")
    br, bgc, bb = CAPTCHA_BG
    g = [0.0] * (W * H)
    for idx in range(W * H):
        ci = pix[idx] * 3
        v = (abs(pal[ci] - br) + abs(pal[ci + 1] - bgc)
             + abs(pal[ci + 2] - bb)) * 0.004
        g[idx] = 1.0 if v > 1.0 else v
    return g

def _cap_recognize(img_bytes):
    """纯Python CNN识别验证码，返回4位字符串。失败返回空。"""
    net = _cap_model()
    if net is None:
        return ""
    try:
        feat = _cap_png_feature(img_bytes)
        return net.decode(feat, CAPTCHA_CHARS)
    except Exception:
        return ""

class _CaptchaOCR(object):
    def __init__(self):
        self._ddd = None
        self._tried = False

    def _lazy(self):
        if self._tried:
            return self._ddd
        self._tried = True
        try:
            import ddddocr
            self._ddd = ddddocr.DdddOcr(show_ad=False)
            return self._ddd
        except Exception:
            pass
        # ddddocr 缺失：限时安装一次(单镜像、≤12s)，失败即放弃，
        # 由 recognize() 回退到内置纯 Python CNN(无需联网、已 numpy 加速)。
        # >>> FIX(fast-boot): 原来依次尝试 4 个镜像、单镜像 timeout=180s，
        #          网络差时会阻塞多达十几分钟，直接导致首屏超时打不开。
        import subprocess, sys as _sys
        try:
            cmd = [_sys.executable, "-m", "pip", "install", "-q",
                   "ddddocr", "--no-warn-script-location",
                   "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"]
            subprocess.run(cmd, timeout=12,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            import ddddocr
            self._ddd = ddddocr.DdddOcr(show_ad=False)
        except Exception:
            self._ddd = None
        return self._ddd

    def recognize(self, img_bytes):
        # >>> FIX: 恢复 ddddocr 优先(通用 OCR、准确率高)，内置纯 Python CNN
        #          仅作兜底。上一版改成 CNN 优先后，搜索验证码被内置 CNN 误识，
        #          过搜索盾反复失败 → 搜索返回空。ddddocr 装着的环境即时且准确；
        #          缺失时由 _lazy 限时安装(≤12s)兜底，失败再回退内置 CNN。
        d = self._lazy()
        if d is not None:
            try:
                r = d.classification(img_bytes)
                r = re.sub(r"[^0-9A-Za-z]", "", r or "")
                if len(r) == 4:
                    return r
            except Exception:
                pass
        try:
            return self._template(img_bytes)
        except Exception:
            return ""

    def _template(self, img_bytes):
        """纯 Python 验证码识别兜底（准确率较低，优先使用 ddddocr）。"""
        return _cap_recognize(img_bytes)
# ====================================================================
#  主 Spider
# ====================================================================
class Spider(BaseSpider):
    """饭团影院 www.fantuan.vip"""

    HOST = "http://www.fantuan.vip"
    FPLAYER = HOST + "/ftplayer"

    UA = ("Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36")

    HEADERS = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Connection": "keep-alive",
    }

    CLASSES = [
        {"type_id": "1", "type_name": "电影"},
        {"type_id": "2", "type_name": "剧集"},
        {"type_id": "3", "type_name": "综艺"},
        {"type_id": "4", "type_name": "动漫"},
        {"type_id": "new", "type_name": "最新更新"},
        {"type_id": "hot", "type_name": "热播榜"},
        # ---- 电影子分类 ----
        {"type_id": "1_动作", "type_name": "电影·动作"},
        {"type_id": "1_喜剧", "type_name": "电影·喜剧"},
        {"type_id": "1_爱情", "type_name": "电影·爱情"},
        {"type_id": "1_科幻", "type_name": "电影·科幻"},
        {"type_id": "1_恐怖", "type_name": "电影·恐怖"},
        {"type_id": "1_剧情", "type_name": "电影·剧情"},
        {"type_id": "1_犯罪", "type_name": "电影·犯罪"},
        {"type_id": "1_战争", "type_name": "电影·战争"},
        {"type_id": "1_动画", "type_name": "电影·动画"},
        {"type_id": "1_悬疑", "type_name": "电影·悬疑"},
        {"type_id": "1_奇幻", "type_name": "电影·奇幻"},
        {"type_id": "1_武侠", "type_name": "电影·武侠"},
        {"type_id": "1_冒险", "type_name": "电影·冒险"},
        {"type_id": "1_惊悚", "type_name": "电影·惊悚"},
        {"type_id": "1_古装", "type_name": "电影·古装"},
        {"type_id": "1_历史", "type_name": "电影·历史"},
        # ---- 剧楚子分类 ----
        {"type_id": "2_古装", "type_name": "剧集·古装"},
        {"type_id": "2_喜剧", "type_name": "剧集·喜剧"},
        {"type_id": "2_动作", "type_name": "剧集·动作"},
        {"type_id": "2_犯罪", "type_name": "剧集·犯罪"},
        {"type_id": "2_剧情", "type_name": "剧集·剧情"},
        {"type_id": "2_奇幻", "type_name": "剧集·奇幻"},
        {"type_id": "2_战争", "type_name": "剧集·战争"},
        {"type_id": "2_家庭", "type_name": "剧集·家庭"},
        {"type_id": "2_历史", "type_name": "剧集·历史"},
        # ---- 综艺子分类 ----
        {"type_id": "3_音乐", "type_name": "综艺·音乐"},
        # ---- 动漫子分类 ----
        {"type_id": "4_科幻", "type_name": "动漫·科幻"},
        {"type_id": "4_冒险", "type_name": "动漫·冒险"},
        {"type_id": "4_动作", "type_name": "动漫·动作"},
        {"type_id": "4_运动", "type_name": "动漫·运动"},
        {"type_id": "4_战争", "type_name": "动漫·战争"},
    ]

    # >>> FIX: 12 次重试在服务端风控下纯属浪费——连续取图会被返回空 body，
    #          重试越多越被拦。降到 6 次，配合下面的总耗时预算。
    CAPTCHA_RETRY = 4
    SEARCH_GAP = 3.2
    CACHE_TTL = 120
    # 单次 _get_html 的总耗时预算（秒）。超时即放弃，不再无限重试，
    # 避免客户端分类页/搜索页卡死或超时。
    FETCH_BUDGET = 12.0

    def __init__(self):
        try:
            super(Spider, self).__init__()
        except Exception:
            pass
        self.session = requests.Session() if requests else None
        if self.session is not None:
            self.session.headers.update(self.HEADERS)
        self._ocr = _CaptchaOCR()
        self._logged = False
        self._show_ok = False
        self._search_ok = False
        self._last_search = 0.0
        self._play_cache = {}
        self._plist = None
        # >>> OPT: 列表结果缓存(TTL)。首页/分类/搜索的 HTML 在数分钟内变化不大，
        #          命中缓存可让来回切分类、反复搜索“秒开”，避免重复请求+过盾。
        # >>> OPT: 会话与列表缓存落盘。默影视等客户端可能每次请求新建 spider
        #          实例(或不回传 homeContent 的 extend)，导致内存里的登录态/缓存
        #          全部丢失、每次都重新过盾 -> 表现为“还是慢”。落盘后即使重建实例
        #          也能从文件读回 cookie 与结果，跳过过盾、命中缓存。
        self._rcache = {}
        self._session_file = os.path.join(_SELF_DIR, ".ftys_session.json")
        self._cache_file = os.path.join(_SELF_DIR, ".ftys_cache.json")

    # ---------------- 结果缓存 ----------------
    def _rcache_get(self, key):
        v = self._rcache.get(key)
        if v is None:
            return None
        ts, val = v
        if time.time() > ts:
            self._rcache.pop(key, None)
            return None
        return val

    def _rcache_put(self, key, val, ttl=None):
        if ttl is None:
            ttl = self.CACHE_TTL
        self._rcache[key] = (time.time() + ttl, val)
        # >>> OPT: 同步落盘, 跨实例/重启复用(见 __init__ 落盘说明)
        try:
            data = {}
            for kk, vv in self._rcache.items():
                data[json.dumps(list(kk), ensure_ascii=False)] = [vv[0], vv[1]]
            with open(self._cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception:
            pass

    # ---------------- 会话/缓存落盘 ----------------
    def _load_session(self):
        """从本地文件读回 cookie，复用登录态(跨实例/重启)。

        >>> OPT: 默影视等客户端可能每次请求新建 spider 实例，或根本不回传
                  homeContent 的 extend。把 cookie 罐落盘后，新建实例也能
                  直接读回登录态与盾 cookie，跳过过盾，首屏/搜索即时返回。
        """
        try:
            if not os.path.exists(self._session_file):
                return False
            with open(self._session_file, "r", encoding="utf-8") as f:
                cookies = json.load(f)
            if not cookies:
                return False
            for c in cookies:
                try:
                    self.session.cookies.set(
                        c.get("name"), c.get("value"),
                        domain=c.get("domain"), path=c.get("path") or "/",
                        secure=bool(c.get("secure")),
                        expires=c.get("expires"))
                except Exception:
                    pass
            sid = self.session.cookies.get("PHPSESSID")
            if sid and "deleted" not in str(sid):
                self._logged = True
                return True
        except Exception:
            pass
        return False

    def _save_session(self):
        """把当前 cookie 罐落盘，供下次实例/重启复用。"""
        try:
            cookies = []
            for c in self.session.cookies:
                cookies.append({"name": c.name, "value": c.value,
                                "domain": c.domain, "path": c.path,
                                "secure": c.secure, "expires": c.expires})
            with open(self._session_file, "w", encoding="utf-8") as f:
                json.dump(cookies, f, ensure_ascii=False)
        except Exception:
            pass

    def _rcache_load(self):
        """从本地文件读回列表缓存(跨实例/重启)。"""
        try:
            if not os.path.exists(self._cache_file):
                return
            with open(self._cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            now = time.time()
            for k, v in data.items():
                try:
                    ts, val = v[0], v[1]
                    if ts > now:
                        self._rcache[tuple(json.loads(k))] = (ts, val)
                except Exception:
                    continue
        except Exception:
            pass

    # ---------------- 基础请求 ----------------
    def getName(self):
        return "饭团影院"

    def getDependence(self):
        return ["requests", "ddddocr"]

    def init(self, extend=""):
        # >>> OPT: 先读回落盘的列表缓存(跨实例/重启复用)，即便客户端每次新建
        #          实例也能命中“上次已经抓过”的列表，免去重复请求+过盾。
        self._rcache_load()
        # 从 extend 恢复缓存的 session（PHPSESSID + captcha_login_sign）
        # 复用 session 后，全站盾/分类盾/搜索盾都不需要重新过
        has_cache = False
        try:
            if extend and ("PHPSESSID" in extend or "phpsessid" in extend):
                import re
                domain = self.HOST.replace("http://", "").replace("https://", "").split("/")[0]
                sid_m = re.search(r"PHPSESSID=([^;&\s]+)", extend, re.I)
                sign_m = re.search(r"captcha_login_sign=([^;&\s]+)", extend, re.I)
                if sid_m:
                    sid = sid_m.group(1)
                    if len(sid) > 10 and "deleted" not in sid:
                        self.session.cookies.set("PHPSESSID", sid, domain=domain)
                if sign_m:
                    sign = sign_m.group(1)
                    if "deleted" not in sign and len(sign) > 10:
                        self.session.cookies.set("captcha_login_sign", sign, domain=domain)
                if sid_m and sign_m:
                    self._logged = True
                    # >>> OPT(fast-boot): 恢复缓存会话后【不在 init 内做网络
                    #          验证/预过盾】。会话是否有效、盾是否仍需要过，
                    #          统统延迟到首次真实请求(_get_html 内部惰性过盾)
                    #          再裁决，避免首屏因多趟过盾/安装而超时打不开。
                    self._show_ok = False
                    self._search_ok = True
                    # >>> OPT: extend 之外, 优先用更完整的本地会话文件(含盾 cookie)
                    self._load_session()
                    return
                # 仅有 sid 或 sign 之一：视为不可信，清空重来
                self._logged = False
                self._show_ok = False
                self._search_ok = False
                self.session.cookies.clear()
        except Exception:
            pass

        # >>> OPT(fast-boot): init 全程零网络、零阻塞安装。
        #   · ddddocr 缺失时的安装改由 _CaptchaOCR._lazy 限时尝试(≤12s)，
        #     失败即回退内置纯 Python CNN(已 numpy 加速、无需联网安装)。
        #   · 登录盾/分类盾/搜索盾全部延迟到首次真实请求(_get_html 内部
        #     惰性过盾)才触发——首屏只在用户真正访问对应页面时才过“那一道
        #     盾”，不再在加载阶段把三道盾连过一遍导致超时打不开。
        #   · 无 extend 时从本地会话文件恢复(若有)，避免每次新建实例都重新过盾。
        self._load_session()
        return

    def isVideoFormat(self, url):
        return bool(re.search(r"\.(m3u8|mp4|flv|mkv|ts)(\?|$)", str(url), re.I))

    def _is_direct(self, url):
        u = str(url or "")
        if not u.startswith("http"):
            return False
        low = u.lower()
        path_part = low.split("?")[0]
        # 路径中包含视频后缀
        if ".m3u8" in path_part or path_part.endswith(".mp4"):
            return True
        # query 参数的 filename 中包含视频后缀（如 S3/OSS 签名链接）
        if "?response-content-disposition" in low or "filename%3d" in low or "filename=" in low:
            if ".m3u8" in low or ".mp4" in low:
                return True
        # 兜底：交给 isVideoFormat 判断
        return self.isVideoFormat(u)

    def manualVideoCheck(self):
        return False

    def localProxy(self, param):
        return [404, "text/plain", "Not Found"]

    def liveContent(self, url):
        return {"list": []}

    def action(self, action):
        return {}

    def destroy(self):
        try:
            self.session.close()
        except Exception:
            pass

    @staticmethod
    def _unesc(s):
        if not s:
            return s
        if _unescape is not None:
            try:
                return _unescape(s)
            except Exception:
                pass
        return (s.replace("&nbsp;", " ").replace("&amp;", "&")
                 .replace("&quot;", '"').replace("&#39;", "'"))

    def _get(self, path, timeout=15, headers=None, allow_redirects=True):
        url = path if str(path).startswith("http") else self.HOST + str(path)
        h = dict(self.HEADERS)
        if headers:
            h.update(headers)
        if self.session is None:
            return None
        try:
            return self.session.get(url, headers=h, timeout=timeout,
                                    allow_redirects=allow_redirects)
        except Exception:
            # >>> FIX: 异常重试必须带超时！原逻辑 `session.get(url, headers=h)`
            #          无 timeout，一旦连接/读取挂起会无限阻塞，直接表现为
            #          “加载/搜索转圈打不开”甚至被客户端判超时。
            try:
                return self.session.get(url, headers=h, timeout=timeout)
            except Exception:
                return None

    def _post(self, path, data, timeout=15, headers=None):
        url = path if str(path).startswith("http") else self.HOST + str(path)
        h = dict(self.HEADERS)
        h["X-Requested-With"] = "XMLHttpRequest"
        if headers:
            h.update(headers)
        if self.session is None:
            return None
        try:
            return self.session.post(url, data=data, headers=h, timeout=timeout)
        except Exception:
            return None

    # ---------------- 验证码过盾（三重盾） ----------------
    @staticmethod
    def _is_blocked(html):
        if not html:
            return True
        return ("mac_verify" in html) or ("系统安全验证" in html) \
            or ("captchaVerify" in html and "verify_submit" in html)

    @staticmethod
    def _is_throttled(html):
        return bool(html) and ("搜索时间间隔" in html or "请不要频繁操作" in html)

    def _fetch_captcha(self, url, referer=None):
        if self.session is None:
            return ""
        try:
            r = self.session.get(url, headers={
                "User-Agent": self.UA,
                "Referer": referer or (self.HOST + "/"),
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            }, timeout=10)
            if r is None or r.status_code != 200 or len(r.content) < 80:
                return ""
            if r.content[:1] == b"<":
                return ""
            code = self._ocr.recognize(r.content)
            return re.sub(r"[^0-9A-Za-z]", "", code or "")
        except Exception:
            return ""

    def _login(self, reset_session=False):
        if self.session is None:
            return False
        if self._logged and not reset_session:
            return True

        # 需要重置会话时，清空所有状态重新建立
        if reset_session:
            try:
                self.session.cookies.clear()
                self._logged = False
                self._show_ok = False
                self._search_ok = False
                # 预热首页，建立新的 PHPSESSID
                self._get("/", timeout=10)
            except Exception:
                pass

        consec_fail = 0
        for attempt in range(self.CAPTCHA_RETRY):
            code = self._fetch_captcha(
                self.HOST + "/captcha.php?type=code&r=%f" % random.random())
            if len(code) != 4:
                consec_fail += 1
                # 连续获取失败，可能是网络问题，稍等后重试
                time.sleep(0.12 + min(consec_fail * 0.08, 0.35))
                continue
            try:
                vr = self.session.post(
                    self.HOST + "/captcha.php",
                    data={"type": "verify", "check": code},
                    headers={
                        "User-Agent": self.UA,
                        "Referer": self.HOST + "/",
                        "X-Requested-With": "XMLHttpRequest",
                        "Content-Type": "application/x-www-form-urlencoded",
                    }, timeout=10)
                if vr is not None and vr.json().get("code") == 1:
                    self._logged = True
                    # >>> OPT: 登录盾过了即落盘 cookie(跨实例/重启复用)
                    self._save_session()
                    return True
                consec_fail += 1
            except Exception:
                consec_fail += 1
                time.sleep(0.15 + min(consec_fail * 0.1, 0.45))
                continue

            # 指数退避：验证码识别失败时逐渐增加等待时间
            # 避免请求过快被风控
            delay = 0.08 + (0.05 * min(attempt, 4))
            time.sleep(delay)

        # 所有重试都失败，返回 False 由上层决定是否重置会话
        return False

    def _shield_valid(self, kind):
        """校验已过盾凭证是否真的还有效（发一次真实请求看是否仍被拦）。

        >>> FIX: 原来只要 _show_ok/_search_ok 被置 True 就永远返回 True，
                 但服务端会主动作废凭证（连续取图后返回空 body）。
                 结果是脚本"自以为过了盾"，页面却仍返回验证页，
                 白白空转多轮重试，最终把分类页拖到 20 秒+。
                 改为：标记为已过盾后，仍用一次真实页面请求来裁决。
        """
        try:
            probe = "/vodshow/1-----------.html" if kind == "show" else "/"
            r = self._get(probe, timeout=8)
            return not self._is_blocked(r.text if r is not None else "")
        except Exception:
            return False

    def _pass_shield(self, kind):
        if self.session is None:
            return False
        flag = "_show_ok" if kind == "show" else "_search_ok"
        if getattr(self, flag):
            # >>> OPT: 本函数只在 _get_html 判定“页面已拦截”时调用，
            #          既然已被拦，直接重新过盾即可；原先用 _shield_valid 再发一次
            #          8s 超时探针 GET 纯属浪费(多数情况下探针同样被拦返回 False)。
            pass

        # 确保已登录，未登录则先尝试登录
        if not self._logged:
            if not self._login():
                return False

        consec_fail = 0
        for attempt in range(self.CAPTCHA_RETRY):
            code = self._fetch_captcha(
                self.HOST + "/index.php/verify/index.html?r=%f" % random.random())
            if len(code) != 4:
                consec_fail += 1
                time.sleep(0.12 + min(consec_fail * 0.08, 0.35))
                continue
            try:
                vr = self.session.post(
                    self.HOST + "/index.php/ajax/verify_check?type=%s&verify=%s"
                    % (kind, code),
                    headers={
                        "User-Agent": self.UA,
                        "Referer": self.HOST + "/",
                        "X-Requested-With": "XMLHttpRequest",
                    }, timeout=10)
                if vr is not None and vr.json().get("code") == 1:
                    setattr(self, flag, True)
                    # >>> OPT: 分类/搜索盾过了即落盘 cookie(跨实例/重启复用)
                    self._save_session()
                    return True
                consec_fail += 1
            except Exception:
                consec_fail += 1
                time.sleep(0.15 + min(consec_fail * 0.1, 0.45))
                continue

            # 指数退避
            delay = 0.08 + (0.05 * min(attempt, 4))
            time.sleep(delay)

        # 过盾失败，尝试重新登录后再过一次（会话可能失效了）
        if self._login(reset_session=True):
            for _ in range(5):
                code = self._fetch_captcha(
                    self.HOST + "/index.php/verify/index.html?r=%f" % random.random())
                if len(code) != 4:
                    continue
                try:
                    vr = self.session.post(
                        self.HOST + "/index.php/ajax/verify_check?type=%s&verify=%s"
                        % (kind, code),
                        headers={
                            "User-Agent": self.UA,
                            "Referer": self.HOST + "/",
                            "X-Requested-With": "XMLHttpRequest",
                        }, timeout=10)
                    if vr is not None and vr.json().get("code") == 1:
                        setattr(self, flag, True)
                        return True
                except Exception:
                    time.sleep(0.5)
                    continue
                time.sleep(0.3)
        return False

    def _get_html(self, path, timeout=15, retry=3, shield=None, throttle=False):
        """带三重盾兜底的 GET

        shield: None | 'show' | 'search' —— 该路径额外需要的盾类型
        throttle: 是否遵守搜索限流间隔
        """
        if self.session is None:
            return ""
        html = ""
        session_reset = False
        deadline = time.time() + self.FETCH_BUDGET
        # >>> OPT: 搜索限流只在“本次调用开始前”评估一次，严禁放进下面的重试循环。
        #          否则一次搜索被拦、重试 3 次会被迫等待 3×SEARCH_GAP≈10s，
        #          叠加 FETCH_BUDGET 直接表现为“搜索卡死/超时打不开”。
        if throttle or shield == "search":
            gap = self.SEARCH_GAP - (time.time() - self._last_search)
            if gap > 0:
                time.sleep(gap)
            self._last_search = time.time()
        for attempt in range(retry + 1):
            # >>> FIX: 总耗时熔断。过盾失败时不再无限重试，
            #          超过预算就放弃，避免客户端的分类页/搜索卡到 20 秒+。
            if time.time() > deadline:
                break

            r = self._get(path, timeout=timeout)
            html = r.text if r is not None else ""

            # 命中限流 -> 等待后重试
            if self._is_throttled(html):
                time.sleep(self.SEARCH_GAP + 0.5)
                self._last_search = time.time()
                continue

            if not self._is_blocked(html):
                # >>> OPT: 页面正常返回即把(可能被服务端刷新的) cookie 落盘,
                #          跨实例/重启复用, 免去下次重新过盾。
                self._save_session()
                return html

            # >>> FIX: 首次被拦时先预热一次首页，确保 PHPSESSID 建立；
            #          否则 verify_check 写入的会是一个全新的空会话。
            # >>> OPT: 若会话里已有 PHPSESSID 则跳过；目标本就是首页时也无需
            #          额外再 GET 一次(首页自身就是 _get(path))，省一趟往返。
            if (attempt == 0 and path != "/"
                    and not (self.session
                             and self.session.cookies.get("PHPSESSID"))):
                try:
                    self._get("/", timeout=timeout)
                except Exception:
                    pass

            # 最后一次尝试仍失败：彻底重置会话，做最后一搏
            is_last = (attempt == retry)
            if is_last and not session_reset:
                # 重置会话后重新过所有盾
                if self._login(reset_session=True):
                    if shield in ("show", "search"):
                        self._pass_shield(shield)
                    session_reset = True
                    # 重置后再试一次请求
                    r2 = self._get(path, timeout=timeout)
                    html2 = r2.text if r2 is not None else ""
                    if not self._is_blocked(html2):
                        # >>> OPT: 重置后正常返回也落盘(服务端可能刷新了 cookie)
                        self._save_session()
                        return html2
                    html = html2
                continue

            # 被盾拦：先过全站盾，再过该路径专属盾
            if not self._login():
                continue
            if shield in ("show", "search"):
                # >>> FIX: 同一路径连续过盾失败，说明凭证被服务端作废
                #          （连续取图会返回空 body）。此时继续循环毫无意义，
                #          直接跳出，把时间还给调用方。
                if self._pass_shield(shield):
                    guard = getattr(self, "_shield_fail_" + str(shield), 0)
                    r3 = self._get(path, timeout=timeout)
                    html3 = r3.text if r3 is not None else ""
                    if not self._is_blocked(html3):
                        setattr(self, "_shield_fail_" + str(shield), 0)
                        return html3
                    html = html3
                    guard += 1
                    setattr(self, "_shield_fail_" + str(shield), guard)
                    if guard >= 2 or time.time() > deadline:
                        break
                else:
                    break
                continue
            # 无专属盾却仍被拦，可能实际需要 show 盾（容错）
            if attempt >= 1 and "/vodshow/" in str(path):
                if not self._pass_shield("show"):
                    break
        return html

    # ---------------- 线路表 ----------------
    def player_list(self):
        if self._plist:
            return self._plist
        self._plist = {}
        try:
            html = self._get_html("/static/js/playerconfig.js", timeout=10)
            i = html.find("player_list=")
            if i >= 0:
                j = html.find("{", i)
                depth, instr, esc = 0, False, False
                for k in range(j, len(html)):
                    c = html[k]
                    if instr:
                        if esc:
                            esc = False
                        elif c == "\\":
                            esc = True
                        elif c == '"':
                            instr = False
                    else:
                        if c == '"':
                            instr = True
                        elif c == "{":
                            depth += 1
                        elif c == "}":
                            depth -= 1
                            if depth == 0:
                                self._plist = json.loads(html[j:k + 1])
                                break
        except Exception:
            pass
        return self._plist or {}

    def _src_name(self, frm):
        d = self.player_list().get(frm) or {}
        return d.get("show") or str(frm or "")

    # ---------------- 列表解析 ----------------
    @staticmethod
    def _clean_pic(pic):
        if not pic:
            return ""
        pic = pic.strip()
        if pic.startswith("//"):
            pic = "https:" + pic
        return pic

    @staticmethod
    def _parse_cards(html):
        """解析 module-poster-item 卡片（首页/分类/搜索通用）

        >>> FIX: 放宽 <a> 属性顺序约束；名称做三级回退；
                 图片额外支持 data-src。
        """
        items, seen = [], set()
        for m in re.finditer(
                r'<a[^>]*href="/video/(\d+)\.html"[^>]*>([\s\S]{0,900}?)</a>', html):
            vid, block = m.group(1), m.group(2)
            if vid in seen:
                continue
            seen.add(vid)

            # 名称：卡片标题 > module-card-item-title > <a title> > <img alt>
            nm = (re.search(r'module-poster-item-title[^>]*>([^<]*)', block)
                  or re.search(r'module-card-item-title[\s\S]{0,120}?<a[^>]*>'
                               r'(?:<strong>)?([^<]+)', block))
            name = Spider._unesc(nm.group(1)).strip() if nm else ""
            if not name:
                tm = re.search(r'title="([^"]*)"', m.group(0))
                if tm:
                    name = Spider._unesc(tm.group(1)).strip()
            if not name:
                am = re.search(r'<img[^>]*alt="([^"]+)"', block)
                if am:
                    name = Spider._unesc(am.group(1)).strip()
            if not name:
                continue

            rm = re.search(r'module-item-note[^>]*>([^<]*)', block)
            remark = Spider._unesc(rm.group(1)).strip() if rm else ""

            pm = (re.search(r'data-original="([^"]+)"', block)
                  or re.search(r'<img[^>]*data-src="([^"]+)"', block)
                  or re.search(r'<img[^>]*src="(http[^"]+)"', block))
            pic = Spider._clean_pic(pm.group(1)) if pm else ""

            items.append({"vod_id": vid, "vod_name": name,
                          "vod_pic": pic, "vod_remarks": remark})
        return items

    @staticmethod
    def _parse_search_cards(html):
        """搜索页使用 module-card-item 结构，单独解析"""
        items, seen = [], set()
        for m in re.finditer(
                r'<div class="module-card-item[^"]*">([\s\S]{0,1600}?)'
                r'<div class="module-card-item-footer"', html):
            block = m.group(1)
            vm = re.search(r'href="/video/(\d+)\.html"', block)
            if not vm:
                continue
            vid = vm.group(1)
            if vid in seen:
                continue
            seen.add(vid)
            nm = re.search(r'module-card-item-title[\s\S]{0,120}?<a[^>]*>'
                           r'(?:<strong>)?([^<]+)', block)
            name = Spider._unesc(nm.group(1)).strip() if nm else ""
            pm = re.search(r'data-original="([^"]+)"', block)
            pic = Spider._clean_pic(pm.group(1)) if pm else ""
            rm = re.search(r'module-item-note[^>]*>([^<]*)', block)
            remark = Spider._unesc(rm.group(1)).strip() if rm else ""
            if name:
                items.append({"vod_id": vid, "vod_name": name,
                              "vod_pic": pic, "vod_remarks": remark})
        return items

    # ---------------- 首页 ----------------
    def homeContent(self, filter=False):
        result = {"class": self.CLASSES, "filters": {}}
        # 把 session 通过 extend 回传给调用方缓存（复用后所有盾都不用重新过）
        try:
            sid = self.session.cookies.get("PHPSESSID") if self.session else ""
            sign = self.session.cookies.get("captcha_login_sign") if self.session else ""
            parts = []
            if sid and "deleted" not in sid:
                parts.append("PHPSESSID=" + sid)
            if sign and "deleted" not in sign:
                parts.append("captcha_login_sign=" + sign)
            if parts:
                result["extend"] = "&".join(parts)
        except Exception:
            pass
        return result

    def homeVideoContent(self):
        # >>> OPT: 首页推荐缓存, 来回切回首页“秒开”
        ck = ("home",)
        hit = self._rcache_get(ck)
        if hit is not None:
            return hit
        try:
            html = self._get_html("/")
            items = self._parse_cards(html)
            if not items:
                items = self._parse_search_cards(html)
            if items:
                res = {"list": items[:60]}
                self._rcache_put(ck, res)
                return res
        except Exception:
            pass
        return {"list": []}

    # ---------------- 分类 ----------------
    def categoryContent(self, tid, pg, filter, extend):
        page = int(pg) if str(pg).isdigit() else 1
        tid = str(tid)

        if tid == "new":
            path, shield = "/label/new.html", None
        elif tid == "hot":
            path, shield = "/vodshow/1--hits---------.html", "show"
        else:
            import urllib.parse
            # 支持子分类格式: cid_type (如 1_动作, 2_古装)
            if "_" in tid:
                parts = tid.split("_", 1)
                cid = parts[0]
                type_name = parts[1]
                type_enc = urllib.parse.quote(type_name)
                if page <= 1:
                    # type在第4位: cid---type-------- (3dash + type + 8dash = 11dash)
                    path = "/vodshow/%s---%s--------.html" % (cid, type_enc)
                else:
                    # type在第4位, page在第9位: cid---type-----page---
                    # (3dash + type + 5dash + page + 3dash = 11dash)
                    path = "/vodshow/%s---%s-----%s---.html" % (cid, type_enc, page)
            else:
                cid = tid
                if page <= 1:
                    path = "/vodshow/%s-----------.html" % cid
                else:
                    path = "/vodshow/%s--------%d---.html" % (cid, page)
            shield = "show"

        # >>> OPT: 分类结果缓存(键含完整 path, 不同页码/子分类互不串)。
        #          翻页来回、切回原分类都直接命中, 省去请求+过盾。
        ck = ("cat", path)
        hit = self._rcache_get(ck)
        if hit is not None:
            return hit

        try:
            html = self._get_html(path, shield=shield)
            items = self._parse_cards(html)
            if not items:
                items = self._parse_search_cards(html)

            if items:
                res = {"list": items, "page": page,
                       "pagecount": page + 1, "limit": len(items),
                       "total": 9999}
                self._rcache_put(ck, res)
                return res
        except Exception:
            pass
        return {"list": [], "page": page, "pagecount": page, "limit": 30, "total": 0}

    # ---------------- 详情 ----------------
    def detailContent(self, ids):
        try:
            vid = str(ids[0]).split(",")[0].strip() if isinstance(ids, list) \
                else str(ids).split(",")[0]
        except Exception:
            return {"list": []}
        html = self._get_html("/video/%s.html" % vid)
        if self._is_blocked(html):
            return {"list": []}
        return {"list": [self._build_vod(vid, html)]}

    def _build_vod(self, vid, html):
        mt = re.search(r"<title>([^<]*)</title>", html)
        name = ""
        if mt:
            name = re.sub(r"[-_—|].*$", "", Spider._unesc(mt.group(1))).strip()
        hm = re.search(r"<h1[^>]*>([\s\S]{0,200}?)</h1>", html)
        if hm:
            h1txt = re.sub(r"<[^>]+>", "", hm.group(1)).strip()
            if h1txt:
                name = Spider._unesc(h1txt)
        hm2 = re.search(r"module-info-heading[\s\S]{0,400}?</h1>[\s\S]{0,300}?<a[^>]*>([^<]+)</a>", html)
        if hm2 and not name:
            name = Spider._unesc(hm2.group(1)).strip() or name

        pic = ""
        pm = re.search(r'module-item-pic[\s\S]{0,200}?data-original="([^"]+)"', html)
        if pm:
            pic = Spider._clean_pic(pm.group(1))

        def _field(label):
            m = re.search(r'<span class="module-info-item-title">%s[：:]*</span>'
                          r'([\s\S]{0,400}?)</div>' % re.escape(label), html)
            if not m:
                return ""
            txt = re.sub(r"<[^>]+>", "", m.group(1))
            return Spider._unesc(txt).replace("/", ",").strip(", ").strip()

        director = _field("导演")
        actor = _field("主演")
        cm = re.search(r"module-info-introduction-content[^>]*>([\s\S]*?)</div>", html)
        content = re.sub(r"<[^>]+>", "", cm.group(1)).strip() if cm else ""

        tags = [Spider._unesc(t) for t in
                re.findall(r'module-info-tag-link"><a[^>]*>([^<]+)</a>', html)]
        year = ""
        area = ""
        for t in tags:
            if re.match(r"^(19|20)\d{2}$", t):
                year = t
            elif t and not year:
                area = t

        # >>> FIX: 线路名按“顺序”取（源站 tab 与播放块严格一一对应），
        #          同时记录每个名称出现的次数，用于给同名线路加序号。
        tab_names = [Spider._unesc(t).strip() for t in
                     re.findall(r'data-dropdown-value="([^"]+)"', html)]
        groups = []
        for b in re.findall(r'<div class="module-play-list-content[^"]*">([\s\S]*?)</div>',
                            html):
            eps = re.findall(r'href="/player/(\d+)-(\d+)-(\d+)\.html"[^>]*>\s*'
                             r'<span>([^<]*)</span>', b)
            if not eps:
                continue
            sid = eps[0][1]
            items, seen = [], set()
            for _v, _s, nid, nm in eps:
                if nid in seen:
                    continue
                seen.add(nid)
                items.append((nid, Spider._unesc(nm).strip() or ("第%s集" % nid)))
            if not items:
                continue
            try:
                items.sort(key=lambda x: int(x[0]))
            except Exception:
                pass
            groups.append((sid, items))

        # 同名线路统计：源站存在“名字相同但集数不同”的两条真实线路，
        # 若直接原样输出，播放器里会显示成两条一模一样的线路名，容易误判为重复。
        name_count = {}
        for i in range(len(groups)):
            raw = tab_names[i] if i < len(tab_names) else ("线路%d" % (i + 1))
            name_count[raw] = name_count.get(raw, 0) + 1

        # 同名线路按集数从多到少排序，集数多的保留原名，其余追加序号
        order = sorted(range(len(groups)),
                       key=lambda i: (-len(groups[i][1]), i))
        suffix = {}
        seen_name = {}
        for i in order:
            raw = tab_names[i] if i < len(tab_names) else ("线路%d" % (i + 1))
            k = seen_name.get(raw, 0)
            seen_name[raw] = k + 1
            if name_count.get(raw, 1) > 1 and k > 0:
                suffix[i] = "%s%d" % (raw, k + 1)
            else:
                suffix[i] = raw

        sources = []
        for idx, (sid, items) in enumerate(groups):
            nm = suffix.get(idx) or (tab_names[idx] if idx < len(tab_names)
                                     else ("线路%d" % (idx + 1)))
            play_url = "#".join("%s$%s-%s-%s" % (n, vid, sid, nid) for nid, n in items)
            sources.append((nm, play_url))

        return {
            "vod_id": vid, "vod_name": name, "vod_pic": pic,
            "type_name": ",".join(tags[:3]),
            "vod_year": year, "vod_area": area, "vod_remarks": "",
            "vod_actor": actor, "vod_director": director, "vod_content": content,
            "vod_play_from": "$$$".join(s[0] for s in sources),
            "vod_play_url": "$$$".join(s[1] for s in sources),
        }

    # ---------------- 搜索 ----------------
    def searchContent(self, key, quick, pg="1"):
        page = int(pg) if str(pg).isdigit() else 1
        if page > 1:
            path = "/vodsearch/%s-----------%d---.html" % (quote(str(key)), page)
        else:
            path = "/vodsearch/%s-------------.html" % quote(str(key))
        # >>> OPT: 搜索结果缓存(键含关键词+页码)。同一查询翻页/重复搜“秒开”。
        ck = ("search", path)
        hit = self._rcache_get(ck)
        if hit is not None:
            return hit
        try:
            html = self._get_html(path, shield="search", throttle=True)
            items = self._parse_cards(html)
            if not items:
                items = self._parse_search_cards(html)
            res = {"list": items, "page": page, "pagecount": page + 1,
                   "limit": len(items) or 30, "total": len(items)}
            if items:
                self._rcache_put(ck, res)
            return res
        except Exception:
            return {"list": []}

    # ---------------- 播放 ----------------
    def _extract_player(self, html):
        i = html.find("player_aaaa=")
        if i < 0:
            return None
        j = html.find("{", i)
        if j < 0:
            return None
        depth, instr, esc = 0, False, False
        for k in range(j, len(html)):
            c = html[k]
            if instr:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    instr = False
            else:
                if c == '"':
                    instr = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(html[j:k + 1])
                        except Exception:
                            return None
        return None

    def _resolve(self, play_id):
        key = str(play_id)
        old = self._play_cache.get(key)
        if old and (time.time() - old[0]) < self.CACHE_TTL:
            return old[1], old[2]

        page_url = (key if key.startswith("http")
                    else "%s/player/%s.html" % (self.HOST, key))

        html = self._get_html(page_url)
        if self._is_blocked(html):
            return "", ""
        obj = self._extract_player(html)
        if not obj:
            return "", ""
        enc = str(obj.get("url", "") or "")
        if not enc:
            return "", ""
        enc_flag = str(obj.get("encrypt", "0"))
        if enc_flag == "1":
            try:
                from urllib.parse import unquote as _uq
                enc = _uq(enc)
            except Exception:
                pass
        elif enc_flag == "2":
            try:
                import base64
                enc = base64.b64decode(enc + "==").decode("utf-8", "ignore")
            except Exception:
                pass

        r = self._post("%s/api.php" % self.FPLAYER, {"vid": enc},
                       headers={"Referer": "%s/muiplayer.php?vid=%s" % (self.FPLAYER, enc),
                                "Content-Type":
                                    "application/x-www-form-urlencoded; charset=UTF-8"})
        direct = ""
        data = {}
        try:
            j = r.json() if r is not None else {}
            data = j.get("data") or {}
            api_url = str(data.get("url", "") or "")
            mode = str(data.get("urlmode", "") or "1")
            if api_url:
                if api_url.startswith("http") or self._is_direct(api_url):
                    direct = api_url
                else:
                    direct = _decode_play(api_url, int(mode) if mode.isdigit() else 1)
        except Exception:
            pass

        if not direct:
            try:
                cand = (data or {}).get("url", "")
                if isinstance(cand, str) and cand.startswith("http"):
                    direct = cand
            except Exception:
                pass

        # 无效域名黑名单（这些域名的链接无法播放）
        _BAD_DOMAINS = ("fangcloud.com", "download.fantuan")
        if direct and any(bd in direct for bd in _BAD_DOMAINS):
            direct = ""

        self._play_cache[key] = (time.time(), direct, enc)
        if not direct:
            self._play_cache.pop(key, None)
        return direct, enc

    def playerContent(self, flag, id, vipFlags):
        header = {"User-Agent": self.UA, "Referer": self.HOST + "/"}
        direct, enc = self._resolve(id)
        if direct and self._is_direct(direct):
            return {"parse": 0, "url": direct, "header": header}
        self._play_cache.pop(str(id), None)
        direct2, enc2 = self._resolve(id)
        if direct2 and self._is_direct(direct2):
            return {"parse": 0, "url": direct2, "header": header}
        enc = enc2 or enc
        if enc:
            return {"parse": 1,
                    "url": "%s/muiplayer.php?vid=%s" % (self.FPLAYER, enc),
                    "header": header}
        fb = id if str(id).startswith("http") else \
            "%s/player/%s.html" % (self.HOST, id)
        return {"parse": 1, "url": fb, "header": header}


# ==================== 本地自检 ====================
if __name__ == "__main__":
    sp = Spider()
    print("=" * 66)
    print("饭团影院 自检 www.fantuan.vip")
    print("=" * 66)
    print("[全站盾] %s" % sp._login())
    print("[分类盾 show] %s" % sp._pass_shield("show"))
    print("[搜索盾 search] %s" % sp._pass_shield("search"))
    if sp.session is not None:
        print("[Cookie] %s" % list(sp.session.cookies.get_dict().keys()))
    pl = sp.player_list()
    print("[线路] %d 条" % len(pl))
    hc = sp.homeContent()
    print("[分类] %d 个" % len(hc["class"]))
    hv = sp.homeVideoContent()
    print("[首页] %d 条 | 首条: %s" % (len(hv["list"]),
                                      hv["list"][0]["vod_name"] if hv["list"] else "-"))
    print("-" * 66)
    for tid, tn in [("1", "电影"), ("2", "剧集"), ("3", "综艺"), ("4", "动漫")]:
        cat = sp.categoryContent(tid, "1", False, {})
        print("[分类-%s] %d 条 | 首条: %s" % (
            tn, len(cat["list"]),
            cat["list"][0]["vod_name"] if cat["list"] else "-"))
    for tid in ("1", "2"):
        cat = sp.categoryContent(tid, "2", False, {})
        print("[分类-%s-第2页] %d 条 | 首条: %s" % (
            tid, len(cat["list"]),
            cat["list"][0]["vod_name"] if cat["list"] else "-"))
    print("-" * 66)
    for tid in ("1", "2", "4"):
        cat = sp.categoryContent(tid, "1", False, {})
        if not cat["list"]:
            continue
        v = cat["list"][0]
        d = sp.detailContent([v["vod_id"]])
        if not d["list"]:
            print("[详情] %s 失败" % v["vod_name"])
            continue
        dv = d["list"][0]
        print("[详情] %s | %s" % (dv["vod_name"][:22], dv["vod_play_from"]))
        pfs = dv["vod_play_from"].split("$$$")
        pus = dv["vod_play_url"].split("$$$")
        if pfs and pus and "$" in pus[0].split("#")[0]:
            ep_id = pus[0].split("#")[0].split("$", 1)[1]
            pc = sp.playerContent(pfs[0], ep_id, [])
            tag = "直链" if pc.get("parse") == 0 else "解析"
            print("   ▶ %-12s %s -> %s" % (pfs[0], tag, str(pc.get("url"))[:70]))
    print("-" * 66)
    s = sp.searchContent("变形金刚", False, "1")
    print("[搜索] %d 条 | 首条: %s" % (
        len(s.get("list", [])),
        s["list"][0]["vod_name"] if s.get("list") else "-"))
    if s.get("list"):
        v = s["list"][0]
        d = sp.detailContent([v["vod_id"]])
        print("[搜索->详情] %s | %s" % (
            d["list"][0]["vod_name"][:22] if d["list"] else "失败",
            d["list"][0]["vod_play_from"] if d["list"] else "-"))
    print("=" * 66)
    print("完成")