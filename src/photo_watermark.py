# -*- coding: utf-8 -*-
"""
照片批量水印工具 v3.0
------------------------------------------------------------
流程：浏览（选文件夹或选照片文件）→ 自动读取 EXIF 生成列表
      → 核对 / 预览 / 移除不要的 → 批量写入

支持格式：jpg / jpeg / png / tif / tiff / webp
（RAW 如 .ARW/.CR2 不能直接处理，请先用相机或软件导出 JPEG）

输出：默认写入「原文件夹\水印输出」，绝不覆盖原图。
"""
import os
import sys
import shutil
import threading
import queue
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from fractions import Fraction
from datetime import datetime

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser, simpledialog

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageTk

SUPPORTED_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}
OUT_SUBDIR = "水印输出"  # 扫描时跳过，避免重复处理上一轮的输出

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhl.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    r"C:\Windows\Fonts\arial.ttf",
]

# 字体下拉：显示名 → 字体文件（系统字体写绝对路径，随包的液晶字体写相对路径）
FONT_FILES = [
    ("微软雅黑", r"C:\Windows\Fonts\msyh.ttc"),
    ("微软雅黑 粗", r"C:\Windows\Fonts\msyhbd.ttc"),
    ("微软雅黑 细", r"C:\Windows\Fonts\msyhl.ttc"),
    ("等线", r"C:\Windows\Fonts\Deng.ttf"),
    ("等线 粗", r"C:\Windows\Fonts\Dengb.ttf"),
    ("宋体", r"C:\Windows\Fonts\simsun.ttc"),
    ("黑体", r"C:\Windows\Fonts\simhei.ttf"),
    ("楷体", r"C:\Windows\Fonts\simkai.ttf"),
    ("仿宋", r"C:\Windows\Fonts\simfang.ttf"),
    ("Consolas", r"C:\Windows\Fonts\consola.ttf"),
    ("Bahnschrift", r"C:\Windows\Fonts\bahnschrift.ttf"),
    ("Calibri", r"C:\Windows\Fonts\calibri.ttf"),
    ("Arial", r"C:\Windows\Fonts\arial.ttf"),
    ("Times New Roman", r"C:\Windows\Fonts\times.ttf"),
    ("Segoe UI", r"C:\Windows\Fonts\segoeui.ttf"),
    ("Impact", r"C:\Windows\Fonts\impact.ttf"),
    ("液晶 · 7 段（相机日期）", "字体/DSEG7Classic-Bold.ttf"),
    ("液晶 · 14 段（字母齐全）", "字体/DSEG14Classic-Bold.ttf"),
]

# 常用字体颜色
COLOR_CHOICES = [
    ("白", "#FFFFFF"), ("奶白", "#F6EFDC"), ("浅灰", "#D8DEE9"),
    ("黑", "#111111"), ("深灰", "#4B5563"), ("金", "#D9B36C"),
    ("橙", "#F0862B"), ("红", "#D9534F"), ("玫红", "#E0559B"),
    ("黄", "#F2C230"), ("绿", "#4CAF50"), ("青", "#2FB6C8"),
    ("蓝", "#2F6BFF"), ("紫", "#9B6BE8"), ("棕", "#8B5E34"),
]


# 资源查找：本地用中文目录名，GitHub 仓库里用 ASCII 路径，两边都能跑
ICON_PNG_CANDIDATES = ("图标-64.png", "icon-64.png", "assets/icon-64.png")
ICON_ICO_CANDIDATES = ("图标.ico", "icon.ico", "assets/icon.ico")
_ASSET_ALIASES = {
    "字体/DSEG7Classic-Bold.ttf": ("字体/DSEG7Classic-Bold.ttf", "fonts/DSEG7Classic-Bold.ttf",
                                   "assets/fonts/DSEG7Classic-Bold.ttf"),
    "字体/DSEG14Classic-Bold.ttf": ("字体/DSEG14Classic-Bold.ttf", "fonts/DSEG14Classic-Bold.ttf",
                                    "assets/fonts/DSEG14Classic-Bold.ttf"),
}


def resolve_asset(rel):
    for cand in _ASSET_ALIASES.get(rel, (rel,)):
        p = resource_path(cand)
        if os.path.exists(p):
            return p
    return resource_path(rel)


def font_path(key):
    for label, p in FONT_FILES:
        if label == key:
            return p if os.path.isabs(p) else resolve_asset(p)
    return None


def font_labels():
    out = []
    for label, p in FONT_FILES:
        full = p if os.path.isabs(p) else resolve_asset(p)
        if os.path.exists(full):
            out.append(label)
    return out


def hex_to_rgb(value):
    v = (value or "#FFFFFF").lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    try:
        return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
    except Exception:
        return (255, 255, 255)

# ---------------------------------------------------------------- 配色（扁平）
BG = "#EEF1F6"
CARD = "#FFFFFF"
BORDER = "#E3E7EF"
FG = "#1E2532"
MUTED = "#7A8394"
ACCENT = "#2F6BFF"
ACCENT_D = "#1B4FD8"
ACCENT_SOFT = "#E7EEFF"
CHIP_BG = "#F3F5FA"

UI_FONT = "Microsoft YaHei UI"

TAG_DATETIME_ORIGINAL = 36867
TAG_DATETIME_DIGITIZED = 36868
TAG_DATETIME = 306
TAG_MAKE = 271
TAG_MODEL = 272
TAG_FNUMBER = 33437
TAG_EXPOSURE = 33434
TAG_ISO = 34855
TAG_FOCAL = 37386
TAG_LENS = 42036
TAG_ORIENTATION = 274
TAG_GPS = 34853


def resource_path(name):
    """兼容源码运行与 PyInstaller 单文件运行"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


# ---------------------------------------------------------------- 基础工具

def apply_window_icon(win):
    # 用小尺寸 PNG 做窗口图标：比 iconbitmap 读 .ico 快很多
    for name in ICON_PNG_CANDIDATES:
        png = resource_path(name)
        if os.path.exists(png):
            try:
                img = tk.PhotoImage(file=png)
                win.iconphoto(True, img)
                win._icon_img = img  # 防止被回收
                return
            except Exception:
                continue
    for name in ICON_ICO_CANDIDATES:
        ico = resource_path(name)
        if os.path.exists(ico):
            try:
                win.iconbitmap(ico)
                return
            except Exception:
                continue


def load_font(size, key=None):
    """按选择的字体加载；找不到就退回系统默认"""
    p = font_path(key)
    if p and os.path.exists(p):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            pass
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _to_float(v):
    if v is None:
        return None
    try:
        if isinstance(v, tuple) and len(v) == 2:
            return float(v[0]) / float(v[1]) if v[1] else None
        if isinstance(v, Fraction):
            return float(v)
        return float(v)
    except Exception:
        return None


def fmt_shutter(value):
    f = _to_float(value)
    if not f or f <= 0:
        return None
    if f >= 1:
        return ("%.1f" % f).rstrip("0").rstrip(".") + "s"
    return "1/%ds" % int(round(1.0 / f))


def fmt_aperture(value):
    f = _to_float(value)
    if not f:
        return None
    return "f/%s" % ("%.1f" % f).rstrip("0").rstrip(".")


def fmt_iso(value):
    if not value:
        return None
    try:
        return "ISO%d" % int(value)
    except Exception:
        return None


def fmt_focal(value):
    f = _to_float(value)
    if not f:
        return None
    return "%smm" % ("%.0f" % f if abs(f - round(f)) < 0.05 else "%.1f" % f)


def gps_to_decimal(gps_ifd):
    if not gps_ifd:
        return None
    try:
        def dms(seq):
            vals = [float(Fraction(str(x))) if not isinstance(x, Fraction) else float(x) for x in seq]
            while len(vals) < 3:
                vals.append(0.0)
            return vals[0] + vals[1] / 60.0 + vals[2] / 3600.0

        lat = dms(gps_ifd.get(2))
        lon = dms(gps_ifd.get(4))
        lat_ref = (gps_ifd.get(1) or "N")
        lon_ref = (gps_ifd.get(3) or "E")
        if isinstance(lat_ref, bytes):
            lat_ref = lat_ref.decode("ascii", "ignore")
        if isinstance(lon_ref, bytes):
            lon_ref = lon_ref.decode("ascii", "ignore")
        if str(lat_ref).upper().startswith("S"):
            lat = -lat
        if str(lon_ref).upper().startswith("W"):
            lon = -lon
        if abs(lat) > 90 or abs(lon) > 180:
            return None
        return (lat, lon)
    except Exception:
        return None


@dataclass
class PhotoMeta:
    path: str
    name: str
    size_kb: int = 0
    width: int = 0
    height: int = 0
    dt: str = ""
    clock: str = ""
    aperture: str = ""
    shutter: str = ""
    iso: str = ""
    focal: str = ""
    camera: str = ""
    lens: str = ""
    gps: tuple = None
    error: str = ""
    wm_override: str = ""   # 这一张手动改过的水印文字（空=按设置自动生成）

    @property
    def exposure(self):
        return " · ".join([p for p in (self.aperture, self.shutter, self.iso) if p])

    @property
    def datetime_text(self):
        if self.dt and self.clock:
            return "%s %s" % (self.dt, self.clock)
        return self.dt or self.clock or ""


def read_meta(path):
    meta = PhotoMeta(path=path, name=os.path.basename(path))
    try:
        meta.size_kb = int(os.path.getsize(path) / 1024)
    except Exception:
        pass

    try:
        with Image.open(path) as im:
            meta.width, meta.height = im.size
            exif = im.getexif()
            if not exif:
                return meta

            def tag_value(t):
                """主 IFD 读不到时，再去 Exif 子 IFD(34665) / Interop(40965) 找。"""
                v = exif.get(t)
                if v is not None and v != "":
                    return v
                for sub_id in (34665, 40965):
                    try:
                        sub_ifd = exif.get_ifd(sub_id)
                    except Exception:
                        sub_ifd = None
                    if sub_ifd:
                        v2 = sub_ifd.get(t)
                        if v2 is not None and v2 != "":
                            return v2
                return None

            raw_dt = (tag_value(TAG_DATETIME_ORIGINAL)
                      or tag_value(TAG_DATETIME_DIGITIZED)
                      or tag_value(TAG_DATETIME))
            if raw_dt:
                raw_dt = str(raw_dt).strip()
                for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d"):
                    try:
                        d = datetime.strptime(raw_dt, fmt)
                        meta.dt = d.strftime("%Y-%m-%d")
                        meta.clock = d.strftime("%H:%M")
                        break
                    except ValueError:
                        continue

            meta.aperture = fmt_aperture(tag_value(TAG_FNUMBER)) or ""
            meta.shutter = fmt_shutter(tag_value(TAG_EXPOSURE)) or ""
            meta.iso = fmt_iso(tag_value(TAG_ISO)) or ""
            meta.focal = fmt_focal(tag_value(TAG_FOCAL)) or ""

            make = str(tag_value(TAG_MAKE) or "").strip()
            model = str(tag_value(TAG_MODEL) or "").strip()
            if model and make and model.upper().startswith(make.upper()):
                model = model[len(make):].strip()
            meta.camera = ("%s %s" % (make, model)).strip() if make or model else ""
            meta.lens = str(tag_value(TAG_LENS) or "").strip()

            try:
                gps_ifd = exif.get_ifd(TAG_GPS)
            except Exception:
                gps_ifd = None
            meta.gps = gps_to_decimal(gps_ifd)
    except Exception as exc:
        meta.error = str(exc)
    return meta


# ---------------------------------------------------------------- 文本与水印

def exposure_parts(meta, opt):
    """按勾选情况返回曝光要素（光圈/快门/ISO 各自独立）"""
    out = []
    if opt["include_aperture"] and meta.aperture:
        out.append(meta.aperture)
    if opt["include_shutter"] and meta.shutter:
        out.append(meta.shutter)
    if opt["include_iso"] and meta.iso:
        out.append(meta.iso)
    return out


def build_lines(meta, opt):
    """按选项拼出水印的每一行；手动改过的优先"""
    if meta.wm_override.strip():
        return [meta.wm_override.strip()]
    pieces = []
    date_part = ""
    if opt["include_date"]:
        date_part = meta.dt
        if opt["include_time"] and meta.clock:
            date_part = (date_part + " " + meta.clock).strip()
    if date_part:
        pieces.append(date_part)

    exp = exposure_parts(meta, opt)
    if exp:
        pieces.append(" · ".join(exp))
    if opt["include_focal"] and meta.focal:
        pieces.append(meta.focal)

    loc = ""
    if opt["include_location"]:
        if opt["location_alias"].strip():
            loc = opt["location_alias"].strip()
        elif meta.gps:
            loc = "%.5f, %.5f" % (meta.gps[0], meta.gps[1])
    if loc:
        pieces.append(loc)

    if opt["include_camera"] and meta.camera:
        pieces.append(meta.camera)
    if opt["include_custom"] and opt["custom_text"].strip():
        pieces.append(opt["custom_text"].strip())

    if not pieces:
        return []

    if opt["layout"] == "multi":
        lines = []
        if date_part:
            lines.append(date_part)
        second = list(exp)
        if opt["include_focal"] and meta.focal:
            second.append(meta.focal)
        if opt["include_camera"] and meta.camera:
            second.append(meta.camera)
        if second:
            lines.append(" · ".join(second))
        if loc:
            lines.append(loc)
        if opt["include_custom"] and opt["custom_text"].strip():
            lines.append(opt["custom_text"].strip())
        return [l for l in lines if l]
    return ["  ".join(pieces)]


# ---------------------------------------------------------------- 水印排版与合成

def layout_watermark(lines, opt, W, H):
    # 计算字体、行高、整块尺寸和左上角坐标；超宽时自动缩字号
    font_size = max(10, int(H * opt["font_scale"]))
    probe = ImageDraw.Draw(Image.new("RGB", (4, 4)))
    max_w = int(W * 0.94)

    def measure(fs):
        f = load_font(fs, opt.get("font"))
        gap = int(fs * 0.35)
        szs = []
        for line in lines:
            b = probe.textbbox((0, 0), line, font=f)
            szs.append((b[2] - b[0], b[3] - b[1]))
        bw = max(s[0] for s in szs)
        bh = sum(s[1] for s in szs) + gap * (len(lines) - 1)
        return f, gap, szs, bw, bh

    font, gap, sizes, bw, bh = measure(font_size)
    guard = 0
    while bw > max_w and font_size > 10 and guard < 12:
        font_size = max(10, int(font_size * max_w / bw * 0.98))
        font, gap, sizes, bw, bh = measure(font_size)
        guard += 1

    margin = max(8, int(H * 0.03))
    pos = opt["position"]
    if pos == "bl":
        x, y = margin, H - margin - bh
    elif pos == "br":
        x, y = W - margin - bw, H - margin - bh
    elif pos == "tl":
        x, y = margin, margin
    elif pos == "tr":
        x, y = W - margin - bw, margin
    elif pos == "tc":
        x, y = (W - bw) // 2, margin
    else:
        x = int(W * opt["custom_x"] / 100.0) - bw // 2
        y = int(H * opt["custom_y"] / 100.0) - bh // 2
        x = max(margin // 2, min(x, W - bw - margin // 2))
        y = max(margin // 2, min(y, H - bh - margin // 2))
    return font, gap, sizes, bw, bh, x, y, font_size


def draw_watermark(img, lines, opt):
    # 只合成文字所在的小块，避免整图转 RGBA（24MP 照片能省一半时间）
    W, H = img.size
    font, gap, sizes, bw, bh, x, y, font_size = layout_watermark(lines, opt, W, H)
    stroke_w = max(1, int(font_size * 0.06)) if opt["outline"] else 0
    alpha = int(opt["opacity"] * 255)
    rgb = tuple(opt.get("color") or (255, 255, 255))
    fill = rgb + (alpha,)
    if opt["outline"]:
        lum = (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255.0
        stroke_fill = (0, 0, 0, alpha) if lum > 0.55 else (255, 255, 255, alpha)
    else:
        stroke_fill = None

    pad = stroke_w * 2 + 6
    bx0 = max(0, x - pad)
    by0 = max(0, y - pad)
    bx1 = min(W, x + bw + pad)
    by1 = min(H, y + bh + pad)
    if bx1 <= bx0 or by1 <= by0:
        return img
    overlay = Image.new("RGBA", (bx1 - bx0, by1 - by0), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    cy = y - by0
    for line, (_, lh) in zip(lines, sizes):
        draw.text((x - bx0, cy), line, font=font, fill=fill,
                  stroke_width=stroke_w, stroke_fill=stroke_fill)
        cy += lh + gap
    region = img.crop((bx0, by0, bx1, by1)).convert("RGBA")
    region.alpha_composite(overlay)
    img.paste(region.convert("RGB"), (bx0, by0))
    return img


def render(meta, opt, preview_max=None):
    # 兼容接口：整图渲染一张（批量请走 process_one）
    lines = build_lines(meta, opt)
    with Image.open(meta.path) as im:
        img = ImageOps.exif_transpose(im).convert("RGB")
    if lines:
        draw_watermark(img, lines, opt)
    if preview_max:
        img.thumbnail(preview_max, Image.LANCZOS)
    return img


def _draft_scale(im, max_dim):
    # 让 JPEG 解码器直接按 1/2、1/4 出图，省掉全分辨率解码
    try:
        w, h = im.size
    except Exception:
        return
    if max(w, h) <= max_dim:
        return
    scale = 1
    while max(w, h) / (scale * 2) >= max_dim:
        scale *= 2
    try:
        im.draft("RGB", (max(1, int(w / scale)), max(1, int(h / scale))))
    except Exception:
        pass


def prepare_preview(meta, opt, max_dim):
    # 预览用：JPEG 按 1/2 或 1/4 直接解码，再按小图算水印
    with Image.open(meta.path) as im:
        size = im.size
        orient = 1
        try:
            orient = im.getexif().get(TAG_ORIENTATION, 1) or 1
        except Exception:
            pass
        _draft_scale(im, max_dim)
        img = ImageOps.exif_transpose(im).convert("RGB")
    if max(img.size) > max_dim * 1.6:
        img.thumbnail((max_dim, max_dim), Image.BOX)
    if orient in (5, 6, 7, 8):
        size = (size[1], size[0])
    lines = build_lines(meta, opt)
    if lines:
        draw_watermark(img, lines, opt)
    return img, size


def prepare_hires(meta, opt):
    # 只有缩放超过工作分辨率时才调用：原图 + 水印
    with Image.open(meta.path) as im:
        img = ImageOps.exif_transpose(im).convert("RGB")
    lines = build_lines(meta, opt)
    if lines:
        draw_watermark(img, lines, opt)
    return img


def process_one(meta, opt, out_path):
    # 一次解码完成：读 EXIF、转正、画水印、存盘（不再二次打开原图）
    lines = build_lines(meta, opt)
    exif_bytes = None
    with Image.open(meta.path) as im:
        if opt.get("keep_exif"):
            try:
                ex = im.getexif()
                ex.pop(TAG_ORIENTATION, None)
                if opt.get("strip_gps"):
                    ex.pop(TAG_GPS, None)
                exif_bytes = ex.tobytes()
            except Exception:
                exif_bytes = None
        img = ImageOps.exif_transpose(im).convert("RGB")
    if lines:
        draw_watermark(img, lines, opt)
    kwargs = {"quality": int(opt["quality"]), "subsampling": 0}
    if exif_bytes:
        kwargs["exif"] = exif_bytes
    img.save(out_path, "JPEG", **kwargs)


# ---------------------------------------------------------------- 输出路径

_RESERVE_LOCK = threading.Lock()
_RESERVED = set()
_MADE_DIRS = set()


def unique_path(path):
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    i = 2
    while os.path.exists("%s_%d%s" % (stem, i, ext)):
        i += 1
    return "%s_%d%s" % (stem, i, ext)


def ensure_dir(path):
    with _RESERVE_LOCK:
        if path in _MADE_DIRS:
            return
        os.makedirs(path, exist_ok=True)
        _MADE_DIRS.add(path)


def reserve_path(path):
    # 多线程同时写也不会撞名
    with _RESERVE_LOCK:
        stem, ext = os.path.splitext(path)
        cand = path
        i = 2
        while os.path.exists(cand) or cand in _RESERVED:
            cand = "%s_%d%s" % (stem, i, ext)
            i += 1
        _RESERVED.add(cand)
        return cand


def apply_job(meta, opt, out_root, base_folder):
    # 单张处理（在线程池里跑），返回输出路径
    lines = build_lines(meta, opt)
    if not lines:
        target_dir = out_root or os.path.join(os.path.dirname(meta.path), OUT_SUBDIR)
        ensure_dir(target_dir)
        dst = reserve_path(os.path.join(target_dir, meta.name))
        shutil.copy2(meta.path, dst)
        return dst
    if out_root:
        rel = os.path.relpath(os.path.dirname(meta.path), base_folder)
        target_dir = os.path.join(out_root, rel) if rel not in (".", "") else out_root
    else:
        target_dir = os.path.join(os.path.dirname(meta.path), OUT_SUBDIR)
    ensure_dir(target_dir)
    stem = os.path.splitext(meta.name)[0]
    dst = reserve_path(os.path.join(target_dir, stem + ".jpg"))
    process_one(meta, opt, dst)
    return dst


# ---------------------------------------------------------------- 扁平样式

def setup_styles(root):
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except tk.TclError:
        pass

    st.configure(".", background=CARD, foreground=FG, font=(UI_FONT, 9), borderwidth=0)

    st.configure("Card.TFrame", background=CARD)
    st.configure("Card.TLabel", background=CARD, foreground=FG, font=(UI_FONT, 9))
    st.configure("H1.TLabel", background=BG, foreground=FG, font=(UI_FONT, 14, "bold"))
    st.configure("Sub.TLabel", background=BG, foreground=MUTED, font=(UI_FONT, 9))
    st.configure("Section.TLabel", background=CARD, foreground=MUTED, font=(UI_FONT, 8, "bold"))
    st.configure("Hint.TLabel", background=CARD, foreground=MUTED, font=(UI_FONT, 8))
    st.configure("Value.TLabel", background=CARD, foreground=FG, font=(UI_FONT, 9, "bold"))
    st.configure("Status.TLabel", background=BG, foreground=MUTED, font=(UI_FONT, 9))

    st.configure("Accent.TButton", background=ACCENT, foreground="#FFFFFF",
                 padding=(20, 9), font=(UI_FONT, 9, "bold"), relief="flat", borderwidth=0)
    st.map("Accent.TButton",
           background=[("disabled", "#A9C0F8"), ("pressed", ACCENT_D), ("active", ACCENT_D)],
           foreground=[("disabled", "#FFFFFF")])

    st.configure("Ghost.TButton", background=CARD, foreground=FG, padding=(14, 7),
                 relief="flat", borderwidth=1, bordercolor=BORDER)
    st.map("Ghost.TButton", background=[("active", "#F2F5FB"), ("pressed", "#E8EDF7")],
           bordercolor=[("active", "#CBD5E4")])

    st.configure("Flat.TButton", background=BG, foreground=FG, padding=(12, 6),
                 relief="flat", borderwidth=0)
    st.map("Flat.TButton", background=[("active", "#E2E8F2")])

    # 选项用「胶囊」方块：去掉勾选框，选中 = 淡蓝底 + 蓝字
    st.configure("Chip.TCheckbutton", background=CHIP_BG, foreground=FG,
                 padding=(12, 7), font=(UI_FONT, 9), relief="flat", borderwidth=0)
    st.map("Chip.TCheckbutton",
           background=[("selected", ACCENT_SOFT), ("active", "#E9EDF6")],
           foreground=[("selected", ACCENT_D)])
    try:
        st.layout("Chip.TCheckbutton", [
            ("Checkbutton.padding", {"sticky": "nswe", "children": [
                ("Checkbutton.label", {"sticky": "nswe"})]})])
    except tk.TclError:
        pass

    st.configure("TEntry", fieldbackground=CARD, foreground=FG, bordercolor=BORDER,
                 lightcolor=CARD, darkcolor=CARD, insertcolor=FG, padding=(8, 6), relief="flat")
    st.map("TEntry", bordercolor=[("focus", ACCENT)])

    st.configure("TCombobox", fieldbackground=CARD, background=CARD, foreground=FG,
                 bordercolor=BORDER, lightcolor=CARD, darkcolor=CARD,
                 padding=(8, 5), arrowsize=14, relief="flat")
    st.map("TCombobox", fieldbackground=[("readonly", CARD)],
           bordercolor=[("focus", ACCENT)],
           selectbackground=[("readonly", CARD)],
           selectforeground=[("readonly", FG)])

    st.configure("TSpinbox", fieldbackground=CARD, foreground=FG, bordercolor=BORDER,
                 lightcolor=CARD, darkcolor=CARD, arrowcolor=MUTED,
                 padding=(8, 5), arrowsize=12, relief="flat")
    st.map("TSpinbox", bordercolor=[("focus", ACCENT)])

    st.configure("TRadiobutton", background=CARD, foreground=FG, font=(UI_FONT, 9))
    st.map("TRadiobutton", background=[("active", CARD)], foreground=[("selected", ACCENT_D)])

    st.configure("Horizontal.TScale", background=CARD, troughcolor="#E6EBF3",
                 borderwidth=0, sliderlength=18, relief="flat")
    st.map("Horizontal.TScale", background=[("active", ACCENT)])

    st.configure("Preview.TButton", background="#E4EEFF", foreground=ACCENT_D,
                 padding=(16, 8), font=(UI_FONT, 9, "bold"), relief="flat", borderwidth=0)
    st.map("Preview.TButton", background=[("active", "#D3E2FE"), ("pressed", "#C4D7FC")])
    st.configure("Danger.TButton", background="#FBDCDC", foreground="#B93B3B",
                 padding=(16, 8), font=(UI_FONT, 9, "bold"), relief="flat", borderwidth=0)
    st.map("Danger.TButton", background=[("active", "#F7C7C7"), ("pressed", "#F2B8B8")])
    st.configure("BarChip.TCheckbutton", background=CHIP_BG, foreground=FG,
                 padding=(9, 6), font=(UI_FONT, 9), relief="flat", borderwidth=0)
    st.map("BarChip.TCheckbutton",
           background=[("selected", ACCENT_SOFT), ("active", "#E9EDF6")],
           foreground=[("selected", ACCENT_D)])
    try:
        st.layout("BarChip.TCheckbutton", [
            ("Checkbutton.padding", {"sticky": "nswe", "children": [
                ("Checkbutton.label", {"sticky": "nswe"})]})])
    except tk.TclError:
        pass
    st.configure("Icon.TButton", background="#1B1F27", foreground="#E8EDF7",
                 padding=(9, 7), relief="flat", borderwidth=0,
                 lightcolor="#1B1F27", darkcolor="#1B1F27", bordercolor="#1B1F27")
    st.map("Icon.TButton",
           background=[("active", "#2B3242"), ("pressed", "#343C4E")],
           lightcolor=[("active", "#2B3242"), ("pressed", "#343C4E")],
           darkcolor=[("active", "#2B3242"), ("pressed", "#343C4E")],
           bordercolor=[("active", "#2B3242"), ("pressed", "#343C4E")])
    st.configure("Tiny.TButton", background=CARD, foreground=MUTED, padding=(8, 5),
                 relief="flat", borderwidth=1, bordercolor=BORDER)
    st.map("Tiny.TButton", background=[("active", "#F2F5FB")])

    st.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=FG,
                 rowheight=30, borderwidth=0, font=(UI_FONT, 9))
    st.configure("Treeview.Heading", background="#F7F9FC", foreground=MUTED,
                 relief="flat", borderwidth=0, padding=(8, 8), font=(UI_FONT, 9, "bold"))
    st.map("Treeview", background=[("selected", ACCENT_SOFT)], foreground=[("selected", FG)])
    st.map("Treeview.Heading", background=[("active", "#EFF3FA")])

    st.configure("Vertical.TScrollbar", background="#DCE2EC", troughcolor=CARD,
                 bordercolor=CARD, arrowcolor=MUTED, arrowsize=12, relief="flat",
                 width=12)
    st.map("Vertical.TScrollbar", background=[("active", "#C8D1E0")])

    st.configure("Accent.Horizontal.TProgressbar", troughcolor="#E9EDF4", background=ACCENT,
                 bordercolor="#E9EDF4", lightcolor=ACCENT, darkcolor=ACCENT,
                 borderwidth=0, thickness=8)

    root.option_add("*TCombobox*Listbox.background", CARD)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT_SOFT)
    root.option_add("*TCombobox*Listbox.selectForeground", FG)
    root.option_add("*TCombobox*Listbox.font", (UI_FONT, 9))


def card(parent, **kw):
    """白底卡片 + 1px 浅边"""
    return tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1,
                    bd=0, **kw)


class Tooltip:
    """鼠标悬停 delay 毫秒后显示提示"""

    def __init__(self, widget, text, delay=2000):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.tip = None
        self._job = None
        widget.bind("<Enter>", self._enter, add="+")
        widget.bind("<Leave>", self._leave, add="+")
        widget.bind("<ButtonPress>", self._leave, add="+")

    def _enter(self, _event=None):
        self._leave()
        self._job = self.widget.after(self.delay, self._show)

    def _show(self):
        self._job = None
        if self.tip is not None or not self.text:
            return
        x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2
        y = self.widget.winfo_rooty() - 30
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.attributes("-topmost", True)
        self.tip.wm_geometry("+%d+%d" % (x, y))
        tk.Label(self.tip, text=self.text, bg="#2A3040", fg="#E9EEF8",
                 font=(UI_FONT, 8), padx=9, pady=4, bd=0).pack()

    def _leave(self, _event=None):
        if self._job is not None:
            try:
                self.widget.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None


WHITE = (255, 255, 255, 242)


def _mk_icon(draw_fn, size=22, scale=3):
    """把绘制回调渲染成 20px 图标（3 倍超采样再缩小，边缘干净）"""
    S = size * scale
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(img), S)
    img = img.resize((size, size), Image.LANCZOS)
    return ImageTk.PhotoImage(img)


def icon_chevron(d, S, direction):
    w = max(2, int(S * 0.10))
    cx, cy = S / 2.0, S / 2.0
    dx = S * 0.17 * (1 if direction > 0 else -1)
    d.line([(cx - dx, cy - S * 0.22), (cx + dx, cy), (cx - dx, cy + S * 0.22)],
           fill=WHITE, width=w, joint="curve")


def icon_magnifier(d, S, plus):
    w = max(2, int(S * 0.085))
    r = S * 0.27
    cx, cy = S * 0.42, S * 0.42
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=WHITE, width=w)
    d.line([(cx + r * 0.70, cy + r * 0.70), (S * 0.86, S * 0.86)], fill=WHITE, width=w)
    k = r * 0.46
    d.line([(cx - k, cy), (cx + k, cy)], fill=WHITE, width=w)
    if plus:
        d.line([(cx, cy - k), (cx, cy + k)], fill=WHITE, width=w)


def icon_fit(d, S):
    w = max(2, int(S * 0.09))
    m = S * 0.19
    L = S * 0.24
    for sx, sy in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
        x = S / 2.0 + sx * (S / 2.0 - m)
        y = S / 2.0 + sy * (S / 2.0 - m)
        d.line([(x, y), (x - sx * L, y)], fill=WHITE, width=w)
        d.line([(x, y), (x, y - sy * L)], fill=WHITE, width=w)


def icon_actual(d, S):
    w = max(2, int(S * 0.075))
    m = S * 0.15
    d.rounded_rectangle([m, m, S - m, S - m], radius=S * 0.11, outline=WHITE, width=w)
    try:
        f = ImageFont.truetype("arialbd.ttf", int(S * 0.40))
    except Exception:
        try:
            f = ImageFont.truetype("arial.ttf", int(S * 0.40))
        except Exception:
            f = ImageFont.load_default()
    txt = "1:1"
    bb = d.textbbox((0, 0), txt, font=f)
    d.text(((S - (bb[2] - bb[0])) / 2.0 - bb[0], (S - (bb[3] - bb[1])) / 2.0 - bb[1]),
           txt, font=f, fill=WHITE)


def icon_close(d, S):
    w = max(2, int(S * 0.10))
    m = S * 0.25
    d.line([(m, m), (S - m, S - m)], fill=WHITE, width=w)
    d.line([(S - m, m), (m, S - m)], fill=WHITE, width=w)


# ---------------------------------------------------------------- 预览窗口

class PreviewWindow(tk.Toplevel):
    # 预览窗：自己做缩放/平移；先出工作分辨率，放大到看不清再后台载入原图
    MIN_SCALE = 0.02
    MAX_SCALE = 8.0
    WORK_MAX = 1500

    def __init__(self, app, index):
        super().__init__(app)
        self.app = app
        self.idx = index
        self.scale = None
        self.cx = self.cy = 0.0
        self.work = None
        self.hi = None
        self.img_w = self.img_h = 0
        self._photo = None
        self._img_item = None
        self._resize_job = None
        self._zoom_job = None
        self._pending_zoom = None
        self._drag = None
        self._gen = 0
        self._hi_loading = False
        self._icons = {}
        self._q = queue.Queue()
        self._qjob = self.after(30, self._drain_q)

        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w = min(int(sw * 0.74), 1200)
        h = min(int(sh * 0.78), 880)
        self.geometry("%dx%d+%d+%d" % (w, h, (sw - w) // 2, (sh - h) // 2))
        self.minsize(640, 460)
        self.configure(bg="#12151B")
        apply_window_icon(self)

        bar = tk.Frame(self, bg="#1B1F27")
        bar.pack(side="bottom", fill="x")
        self.lbl = tk.Label(bar, text="", bg="#1B1F27", fg="#C8D0DE", font=(UI_FONT, 9))
        self.lbl.pack(side="left", padx=14, pady=8)
        tk.Label(bar, text="滚轮平移 · Ctrl+滚轮缩放 · 拖动移动 · ←/→ 换图 · Esc 关闭",
                 bg="#1B1F27", fg="#6C7787", font=(UI_FONT, 8)).pack(side="left", padx=(4, 0))

        def mk(icon, cmd, tip):
            b = ttk.Button(bar, style="Icon.TButton", command=cmd, takefocus=0)
            b.configure(image=icon)
            b.image = icon
            Tooltip(b, tip, delay=2000)
            b.pack(side="right", padx=2, pady=5)
            return b

        self._icons["close"] = _mk_icon(icon_close)
        self._icons["zin"] = _mk_icon(lambda d, S: icon_magnifier(d, S, True))
        self._icons["zout"] = _mk_icon(lambda d, S: icon_magnifier(d, S, False))
        self._icons["actual"] = _mk_icon(icon_actual)
        self._icons["fit"] = _mk_icon(icon_fit)
        self._icons["next"] = _mk_icon(lambda d, S: icon_chevron(d, S, 1))
        self._icons["prev"] = _mk_icon(lambda d, S: icon_chevron(d, S, -1))

        mk(self._icons["close"], self.destroy, "关闭")
        mk(self._icons["zin"], lambda: self.zoom_by(1.25), "放大")
        mk(self._icons["zout"], lambda: self.zoom_by(1 / 1.25), "缩小")
        mk(self._icons["actual"], lambda: self.set_zoom(1.0), "原始大小 1:1")
        mk(self._icons["fit"], lambda: self.set_zoom(None), "适应窗口")
        mk(self._icons["next"], lambda: self.step(1), "下一张")
        mk(self._icons["prev"], lambda: self.step(-1), "上一张")

        self.canvas = tk.Canvas(self, bg="#12151B", highlightthickness=0, bd=0)
        self.canvas.pack(side="top", fill="both", expand=True)
        self.canvas.bind("<Configure>", self._on_resize)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Left>", lambda e: self.step(-1))
        self.bind("<Right>", lambda e: self.step(1))
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Control-plus>", lambda e: self.zoom_by(1.25))
        self.bind("<Control-equal>", lambda e: self.zoom_by(1.25))
        self.bind("<Control-minus>", lambda e: self.zoom_by(1 / 1.25))
        self.bind("<Key-0>", lambda e: self.set_zoom(None))
        self.bind("<Key-1>", lambda e: self.set_zoom(1.0))
        self.focus_set()
        self.after(20, self.load)

    # ---------- 载入
    def _canvas_size(self):
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        if cw < 10 or ch < 10:
            cw, ch = 900, 620
        return cw, ch

    def load(self):
        n = len(self.app.photos)
        if n == 0:
            self.destroy()
            return
        meta = self.app.photos[self.idx]
        opt = self.app.collect_opt()
        self.title("预览 · %s" % meta.name)
        self._gen += 1
        gen = self._gen
        self.work = None
        self.hi = None
        self._hi_loading = False
        self.scale = None
        self._photo = None
        self._img_item = None
        self.canvas.delete("img")
        self._img_item = None
        self._photo = None
        self.lbl.configure(text="载入中…")

        def job():
            try:
                img, size = prepare_preview(meta, opt, self.WORK_MAX)
                self._q.put(("work", gen, img, size))
            except Exception as exc:
                self._q.put(("err", gen, str(exc), None))

        threading.Thread(target=job, daemon=True).start()

    def _on_work(self, gen, img, size):
        if gen != self._gen or not self.winfo_exists():
            return
        self.work = img
        self.img_w, self.img_h = size
        self.cx = self.img_w / 2.0
        self.cy = self.img_h / 2.0
        self.paint()

    def _work_factor(self):
        if not self.img_w or self.work is None:
            return 1.0
        return self.work.width / float(self.img_w)

    def _fit(self):
        cw, ch = self._canvas_size()
        if not self.img_w:
            return 1.0
        return min(cw / float(self.img_w), ch / float(self.img_h), 1.0)

    def _scale(self):
        return self.scale if self.scale else self._fit()

    def _clamp(self, scale):
        cw, ch = self._canvas_size()
        vw, vh = cw / scale, ch / scale
        if vw >= self.img_w:
            self.cx = self.img_w / 2.0
        else:
            self.cx = min(max(self.cx, vw / 2.0), self.img_w - vw / 2.0)
        if vh >= self.img_h:
            self.cy = self.img_h / 2.0
        else:
            self.cy = min(max(self.cy, vh / 2.0), self.img_h - vh / 2.0)

    # ---------- 绘制
    def paint(self):
        if self.work is None:
            return
        cw, ch = self._canvas_size()
        scale = self._scale()
        self._clamp(scale)
        vw, vh = cw / scale, ch / scale
        x0 = self.cx - vw / 2.0
        y0 = self.cy - vh / 2.0
        wf = self._work_factor()
        if self.hi is None and not self._hi_loading and scale > wf * 1.5:
            self._load_hires()
        if self.hi is not None and scale > wf * 1.15:
            src, k = self.hi, 1.0
        else:
            src, k = self.work, wf
        sx0 = max(0, int(x0 * k))
        sy0 = max(0, int(y0 * k))
        sx1 = min(src.width, int(x0 * k + vw * k) + 1)
        sy1 = min(src.height, int(y0 * k + vh * k) + 1)
        if sx1 - sx0 < 1 or sy1 - sy0 < 1:
            return
        tw, th = sx1 - sx0, sy1 - sy0
        ow = max(1, int(round(tw / k * scale)))
        oh = max(1, int(round(th / k * scale)))
        crop = src.crop((sx0, sy0, sx1, sy1))
        if (ow, oh) != (tw, th):
            crop = crop.resize((ow, oh), Image.BILINEAR if scale > k else Image.BOX)
        photo = self._photo
        if photo is None or photo.width() != ow or photo.height() != oh:
            # 尺寸变了才重建 PhotoImage；否则复用同一张，省掉每帧的整图上传
            photo = ImageTk.PhotoImage(crop)
            self._photo = photo
        else:
            photo.paste(crop)
        if vw >= self.img_w:
            ox = (cw - ow) / 2.0
        else:
            ox = -(sx0 / k) * scale
        if vh >= self.img_h:
            oy = (ch - oh) / 2.0
        else:
            oy = -(sy0 / k) * scale
        if self._img_item is None:
            self._img_item = self.canvas.create_image(ox, oy, anchor="nw",
                                                      image=photo, tags="img")
        else:
            self.canvas.coords(self._img_item, ox, oy)
            self.canvas.itemconfig(self._img_item, image=photo)
        pct = int(round(scale * 100))
        tail = " · 高清中…" if self._hi_loading else ""
        self.lbl.configure(text="%d / %d · %s · %d%%%s" % (
            self.idx + 1, len(self.app.photos), self.app.photos[self.idx].name, pct, tail))

    def _load_hires(self):
        self._hi_loading = True
        meta = self.app.photos[self.idx]
        opt = self.app.collect_opt()
        gen = self._gen

        def job():
            try:
                img = prepare_hires(meta, opt)
            except Exception:
                img = None
            self._q.put(("hires", gen, img, None))

        threading.Thread(target=job, daemon=True).start()

    def _on_hires(self, gen, img):
        if gen != self._gen or not self.winfo_exists():
            return
        self._hi_loading = False
        self.hi = img
        if img is not None:
            self.paint()

    def _drain_q(self):
        # 工作线程只往队列里塞结果，UI 线程统一取（避免跨线程调用 Tk）
        if not self.winfo_exists():
            return
        try:
            while True:
                kind, gen, payload, size = self._q.get_nowait()
                if gen != self._gen:
                    continue
                if kind == "work":
                    self._on_work(gen, payload, size)
                elif kind == "hires":
                    self._on_hires(gen, payload)
                elif kind == "err":
                    messagebox.showerror("预览失败", str(payload), parent=self)
        except queue.Empty:
            pass
        except Exception:
            pass
        self._qjob = self.after(30, self._drain_q)
        self._qjob_done = True

    # ---------- 缩放
    def set_zoom(self, zoom):
        self.scale = zoom
        if zoom is None:
            self.cx = self.img_w / 2.0
            self.cy = self.img_h / 2.0
        self.paint()

    def zoom_by(self, factor, anchor=None):
        if self.work is None:
            return
        cur = self._scale()
        new = max(self.MIN_SCALE, min(self.MAX_SCALE, cur * factor))
        if abs(new - cur) < 1e-6:
            return
        if anchor is not None:
            cw, ch = self._canvas_size()
            ax, ay = anchor
            px = self.cx + (ax - cw / 2.0) / cur
            py = self.cy + (ay - ch / 2.0) / cur
            self.cx = px - (ax - cw / 2.0) / new
            self.cy = py - (ay - ch / 2.0) / new
        self.scale = new
        self.paint()

    def step(self, delta):
        n = len(self.app.photos)
        if n == 0:
            return
        self.idx = (self.idx + delta) % n
        self.load()

    # ---------- 事件
    def _on_resize(self, event):
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(80, self.paint)

    def _on_wheel(self, event):
        if event.state & 0x0004:
            new = max(self.MIN_SCALE, min(self.MAX_SCALE,
                                          self._scale() * (1.15 ** (event.delta / 120.0))))
            self._pending_zoom = (new, (event.x, event.y))
            if self._zoom_job:
                self.after_cancel(self._zoom_job)
            self._zoom_job = self.after(12, self._flush_zoom)
            return
        self.cy -= (event.delta / 120.0) * 90.0 / self._scale()
        self.paint()

    def _flush_zoom(self):
        self._zoom_job = None
        if not self._pending_zoom or self.work is None:
            return
        new, anchor = self._pending_zoom
        self._pending_zoom = None
        cur = self._scale()
        if abs(new - cur) < 1e-6:
            return
        cw, ch = self._canvas_size()
        ax, ay = anchor
        px = self.cx + (ax - cw / 2.0) / cur
        py = self.cy + (ay - ch / 2.0) / cur
        self.cx = px - (ax - cw / 2.0) / new
        self.cy = py - (ay - ch / 2.0) / new
        self.scale = new
        self.paint()

    def _on_press(self, event):
        self._drag = (event.x, event.y)

    def _on_drag(self, event):
        if not self._drag:
            return
        s = self._scale()
        self.cx -= (event.x - self._drag[0]) / s
        self.cy -= (event.y - self._drag[1]) / s
        self._drag = (event.x, event.y)
        self.paint()

    def _on_release(self, event):
        self._drag = None


# ---------------------------------------------------------------- 主界面

COLS = [
    ("name", "文件名", 0.17),
    ("datetime", "拍摄时间", 0.12),
    ("exposure", "曝光", 0.14),
    ("focal", "焦距", 0.05),
    ("camera", "相机", 0.12),
    ("gps", "GPS", 0.06),
    ("watermark", "将添加的水印", 0.34),
    ("status", "状态", 0.04),
]

COLS_KEYS = [c[0] for c in COLS]
WATERMARK_COL = COLS_KEYS.index("watermark")

POSITIONS = [
    ("左下角", "bl"), ("右下角", "br"), ("左上角", "tl"),
    ("右上角", "tr"), ("顶部居中", "tc"), ("自定义坐标", "custom"),
]


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("照片批量水印工具 v3.0")
        self.geometry("1440x900")
        self.minsize(1180, 760)
        self.configure(bg=BG)
        apply_window_icon(self)

        setup_styles(self)

        self.folder = tk.StringVar()
        self.recursive = tk.BooleanVar(value=True)
        self.source_files = None
        self.photos = []
        self.msg_q = queue.Queue()
        self.busy = False
        self._fit_job = None
        self._refresh_job = None
        self._rows = []
        self._col_need = None

        self.var_date = tk.BooleanVar(value=True)
        self.var_time = tk.BooleanVar(value=True)
        self.var_aperture = tk.BooleanVar(value=False)
        self.var_shutter = tk.BooleanVar(value=False)
        self.var_iso = tk.BooleanVar(value=False)
        self.var_focal = tk.BooleanVar(value=False)
        self.var_location = tk.BooleanVar(value=False)
        self.var_camera = tk.BooleanVar(value=False)
        self.var_custom = tk.BooleanVar(value=False)
        self.var_custom_text = tk.StringVar()
        self.var_location_alias = tk.StringVar()
        self.var_layout = tk.StringVar(value="单行")
        self.var_position = tk.StringVar(value="左下角")
        self.var_cx = tk.IntVar(value=50)
        self.var_cy = tk.IntVar(value=92)
        self.var_font = tk.DoubleVar(value=1.5)
        self.var_opacity = tk.DoubleVar(value=85)
        self.var_outline = tk.BooleanVar(value=True)
        self.var_fontname = tk.StringVar(value="微软雅黑")
        self.var_color = tk.StringVar(value="白")
        self._color_hex = "#FFFFFF"
        self.var_quality = tk.IntVar(value=100)
        self.var_keep_exif = tk.BooleanVar(value=True)
        self.var_strip_gps = tk.BooleanVar(value=False)
        self.var_outdir_mode = tk.StringVar(value="sub")
        self.var_outdir = tk.StringVar()

        self._build_ui()
        self.after(120, self._drain_queue)
        self._watch_options()

    # ---------------- UI
    def _build_ui(self):
        root = tk.Frame(self, bg=BG)
        root.pack(fill="both", expand=True, padx=22, pady=(16, 12))
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        # ---------------- 顶部：浏览 / 包含子文件夹 / 地址（处理时变进度条）/ 开始加水印
        bar = card(root)
        bar.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        inner = tk.Frame(bar, bg=CARD)
        inner.pack(fill="x", padx=12, pady=10)

        ttk.Button(inner, text="浏览…", style="Accent.TButton",
                   command=self.open_browse_menu).pack(side="left")
        ttk.Checkbutton(inner, text="包含子文件夹", variable=self.recursive,
                        style="BarChip.TCheckbutton",
                        command=self.rescan).pack(side="left", padx=(8, 0))

        self.btn_apply = ttk.Button(inner, text="开始批量添加水印", style="Accent.TButton",
                                    command=self.start_apply)
        self.btn_apply.pack(side="right", padx=(10, 0))

        path_wrap = tk.Frame(inner, bg=CARD)
        path_wrap.pack(side="left", fill="x", expand=True, padx=12)
        self.path_entry = tk.Entry(path_wrap, textvariable=self.folder, bg="#F7FAFD", fg=FG,
                                   relief="flat", font=(UI_FONT, 9), insertbackground=FG,
                                   highlightthickness=1, highlightbackground="#D5DDEA",
                                   highlightcolor=ACCENT)
        self.path_entry.pack(fill="x", ipady=6, ipadx=8)
        self.prog_canvas = tk.Canvas(path_wrap, bg="#EDF2FA", height=32,
                                     highlightthickness=0, bd=0)
        self._total = 1

        # ---------------- 主体：左列表 + 右设置
        body = tk.Frame(root, bg=BG)
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        # 列表卡片
        list_card = card(body)
        list_card.grid(row=0, column=0, sticky="nsew")
        lh = tk.Frame(list_card, bg=CARD)
        lh.pack(fill="x", padx=14, pady=(12, 6))
        tk.Label(lh, text="照片列表", bg=CARD, fg=FG,
                 font=(UI_FONT, 10, "bold")).pack(side="left")
        self.count_lbl = tk.Label(lh, text="还没有照片", bg=CARD, fg=MUTED, font=(UI_FONT, 9))
        self.count_lbl.pack(side="left", padx=10)
        tk.Label(lh, text="双击「将添加的水印」可单张修改", bg=CARD, fg=MUTED,
                 font=(UI_FONT, 8)).pack(side="right")

        tree_wrap = tk.Frame(list_card, bg=CARD)
        tree_wrap.pack(fill="both", expand=True, padx=6, pady=(0, 8))
        self.tree = ttk.Treeview(tree_wrap, columns=[c[0] for c in COLS],
                                 show="headings", height=15, style="Treeview")
        for key, head_txt, ratio in COLS:
            self.tree.heading(key, text=head_txt)
            self.tree.column(key, width=int(900 * ratio), minwidth=40, anchor="w", stretch=False)
        self.tree.tag_configure("odd", background=CARD)
        self.tree.tag_configure("even", background="#FAFBFD")
        self.tree.tag_configure("err", foreground="#C0392B")
        vsb = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self._on_tree_double)
        self.tree.bind("<Configure>", self._on_tree_configure)
        self.tree.bind("<Delete>", lambda e: self.remove_selected())

        # 设置卡片：固定宽度，一屏放下（不滚动）
        set_card = card(body)
        set_card.grid(row=0, column=1, sticky="ns", padx=(12, 0))
        set_card.configure(width=342)
        set_card.grid_propagate(False)
        sh = tk.Frame(set_card, bg=CARD)
        sh.pack(fill="x", padx=16, pady=(12, 2))
        tk.Label(sh, text="水印设置", bg=CARD, fg="#111827",
                 font=(UI_FONT, 10, "bold")).pack(side="left")

        P = tk.Frame(set_card, bg=CARD)
        P.pack(fill="both", expand=True, padx=16, pady=(0, 12))

        def section(text, pad_top=14):
            tk.Label(P, text=text, bg=CARD, fg="#111827",
                     font=(UI_FONT, 10, "bold")).pack(anchor="w", pady=(pad_top, 6))

        def caption(text, pad_top=8):
            tk.Label(P, text=text, bg=CARD, fg=MUTED,
                     font=(UI_FONT, 8)).pack(anchor="w", pady=(pad_top, 2))

        # ---- 排版与位置
        section("排版与位置", 2)
        self._g1 = tk.Frame(P, bg=CARD)
        self._g1.pack(fill="x")
        self._g1.columnconfigure(0, weight=1, uniform="c")
        self._g1.columnconfigure(1, weight=1, uniform="c")
        col_a = tk.Frame(self._g1, bg=CARD)
        col_a.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        tk.Label(col_a, text="排版", bg=CARD, fg=MUTED, font=(UI_FONT, 8)).pack(anchor="w")
        ttk.Combobox(col_a, textvariable=self.var_layout, state="readonly",
                     values=["单行", "多行"]).pack(fill="x", pady=(2, 0))
        col_b = tk.Frame(self._g1, bg=CARD)
        col_b.grid(row=0, column=1, sticky="ew", padx=(5, 0))
        tk.Label(col_b, text="位置", bg=CARD, fg=MUTED, font=(UI_FONT, 8)).pack(anchor="w")
        ttk.Combobox(col_b, textvariable=self.var_position, state="readonly",
                     values=[p[0] for p in POSITIONS]).pack(fill="x", pady=(2, 0))

        self.xy_row = tk.Frame(P, bg=CARD)
        tk.Label(self.xy_row, text="坐标 X", bg=CARD, fg=MUTED,
                 font=(UI_FONT, 8)).pack(side="left")
        ttk.Spinbox(self.xy_row, from_=0, to=100, textvariable=self.var_cx,
                    width=4).pack(side="left", padx=(4, 10))
        tk.Label(self.xy_row, text="Y", bg=CARD, fg=MUTED,
                 font=(UI_FONT, 8)).pack(side="left")
        ttk.Spinbox(self.xy_row, from_=0, to=100, textvariable=self.var_cy,
                    width=4).pack(side="left", padx=4)

        # ---- 外观
        section("外观")
        r = tk.Frame(P, bg=CARD)
        r.pack(fill="x")
        tk.Label(r, text="字号（高度 %）", bg=CARD, fg=MUTED,
                 font=(UI_FONT, 8)).pack(side="left")
        ttk.Spinbox(r, from_=0.5, to=12.0, increment=0.1, width=5,
                    textvariable=self.var_font).pack(side="left", padx=(4, 0))
        ttk.Checkbutton(r, text="文字描边", variable=self.var_outline,
                        style="Chip.TCheckbutton").pack(side="right")

        caption("字体")
        ttk.Combobox(P, textvariable=self.var_fontname, state="readonly",
                     values=font_labels()).pack(fill="x")

        caption("字体颜色")
        crow = tk.Frame(P, bg=CARD)
        crow.pack(fill="x")
        self.swatch = tk.Label(crow, bg=self._color_hex, width=2, bd=0,
                               highlightthickness=1, highlightbackground="#C9D1E0")
        self.swatch.pack(side="left", ipady=5)
        self.color_box = ttk.Combobox(crow, textvariable=self.var_color, state="readonly",
                                      values=[c[0] for c in COLOR_CHOICES] + ["自定义…"])
        self.color_box.pack(side="left", fill="x", expand=True, padx=7)
        self.color_box.bind("<<ComboboxSelected>>", self._on_color_pick)
        ttk.Button(crow, text="…", style="Tiny.TButton",
                   command=self.pick_color).pack(side="left")

        r4 = tk.Frame(P, bg=CARD)
        r4.pack(fill="x", pady=(10, 0))
        tk.Label(r4, text="不透明度", bg=CARD, fg=MUTED, font=(UI_FONT, 8)).pack(side="left")
        self.opacity_lbl = tk.Label(r4, text="85%", bg=CARD, fg=FG,
                                    font=(UI_FONT, 9, "bold"))
        self.opacity_lbl.pack(side="right")
        ttk.Scale(P, from_=15, to=100, variable=self.var_opacity,
                  orient="horizontal").pack(fill="x")

        # ---- 输出
        section("输出")
        ttk.Radiobutton(P, text="原文件夹下新建「水印输出」", value="sub",
                        variable=self.var_outdir_mode).pack(anchor="w")
        ttk.Radiobutton(P, text="输出到指定文件夹…", value="custom",
                        variable=self.var_outdir_mode,
                        command=self.pick_outdir).pack(anchor="w")
        self.outdir_entry = ttk.Entry(P, textvariable=self.var_outdir)

        self.quality_row = tk.Frame(P, bg=CARD)
        self.quality_row.pack(fill="x", pady=(8, 0))
        tk.Label(self.quality_row, text="JPEG 质量", bg=CARD, fg=MUTED,
                 font=(UI_FONT, 8)).pack(side="left")
        ttk.Spinbox(self.quality_row, from_=70, to=100, textvariable=self.var_quality,
                    width=4).pack(side="left", padx=(4, 0))
        exif_chips = tk.Frame(self.quality_row, bg=CARD)
        exif_chips.pack(side="right")
        ttk.Checkbutton(exif_chips, text="保留 EXIF", variable=self.var_keep_exif,
                        style="Chip.TCheckbutton").pack(side="left")
        ttk.Checkbutton(exif_chips, text="移除 GPS", variable=self.var_strip_gps,
                        style="Chip.TCheckbutton").pack(side="left", padx=(6, 0))

        # ---------------- 底部：操作 + 水印字段 + 状态（单行）
        ctl = card(root)
        ctl.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        ci = tk.Frame(ctl, bg=CARD)
        ci.pack(fill="x", padx=12, pady=9)

        ttk.Button(ci, text="预览选中", style="Preview.TButton",
                   command=self.preview_selected).pack(side="left")
        ttk.Button(ci, text="移除选中", style="Danger.TButton",
                   command=self.remove_selected).pack(side="left", padx=(8, 14))

        chips = tk.Frame(ci, bg=CARD)
        chips.pack(side="left")

        def chip(text, var, cmd=None):
            ttk.Checkbutton(chips, text=text, variable=var, style="BarChip.TCheckbutton",
                            command=cmd or self.schedule_refresh).pack(side="left", padx=(0, 5))

        chip("拍摄日期", self.var_date)
        chip("具体时间", self.var_time)
        chip("光圈", self.var_aperture)
        chip("快门", self.var_shutter)
        chip("ISO", self.var_iso)
        chip("焦距", self.var_focal)
        chip("拍摄地点", self.var_location, self._ask_location)
        chip("相机型号", self.var_camera)
        chip("自定义文本", self.var_custom, self._ask_custom)

        self.status = tk.Label(ci, text="就绪", bg=CARD, fg=MUTED, font=(UI_FONT, 9))
        self.status.pack(side="right")

        self._toggle_xy()
        self._toggle_outdir()

    # ---------------- 颜色
    def _on_color_pick(self, event=None):
        label = self.var_color.get()
        if label == "自定义…":
            self.pick_color()
            return
        for name, hexv in COLOR_CHOICES:
            if name == label:
                self._color_hex = hexv
                self.swatch.configure(bg=hexv)
                break

    def pick_color(self):
        rgb, hexv = colorchooser.askcolor(color=self._color_hex, parent=self,
                                         title="选择字体颜色")
        if hexv:
            self._color_hex = hexv.upper()
            self.swatch.configure(bg=self._color_hex)
            self.var_color.set("自定义")

    # ---------------- 选项联动（改设置后列表里的水印文字即时更新）
    # ---------------- 自定义文本 / 地点文字的弹窗输入
    def _ask_custom(self):
        self._ask_option(self.var_custom, self.var_custom_text, "自定义文本",
                         "这一批照片要加的自定义文字（例：家庭相册 2026）")

    def _ask_location(self):
        self._ask_option(self.var_location, self.var_location_alias, "地点显示文字",
                         "这批照片要显示的地点（留空则用照片的 GPS 坐标）")

    def _ask_option(self, flag, textvar, title, prompt):
        if not flag.get():
            self.schedule_refresh()
            return
        val = simpledialog.askstring(title, prompt + "：", initialvalue=textvar.get().strip(),
                                     parent=self)
        if val is None:
            flag.set(False)
        else:
            textvar.set(val.strip())
        self.schedule_refresh()

    # ---------------- 条件显示
    def _toggle_xy(self):
        if self.var_position.get() == "自定义坐标":
            if not self.xy_row.winfo_manager():
                self.xy_row.pack(fill="x", pady=(6, 0), after=self._g1)
        else:
            self.xy_row.pack_forget()

    def _toggle_outdir(self):
        if self.var_outdir_mode.get() == "custom":
            if not self.outdir_entry.winfo_manager():
                self.outdir_entry.pack(fill="x", pady=(6, 0), before=self.quality_row)
        else:
            self.outdir_entry.pack_forget()

    # ---------------- 地址栏 → 进度条
    def _show_progress(self, on, total=None):
        if total is not None:
            self._total = max(1, total)
        if on:
            self.path_entry.pack_forget()
            self.prog_canvas.pack(fill="x", ipady=0)
            self._draw_progress(0, self._total, "准备中…")
        else:
            self.prog_canvas.pack_forget()
            self.path_entry.pack(fill="x", ipady=6, ipadx=8)

    def _draw_progress(self, value, total, text=None):
        c = self.prog_canvas
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 10:
            w, h = 620, 32
        c.create_rectangle(0, 0, w, h, fill="#EDF2FA", outline="")
        total = max(1, int(total))
        fw = int(w * min(1.0, value / float(total)))
        if fw > 0:
            c.create_rectangle(0, 0, fw, h, fill=ACCENT, outline="")
        if text is None:
            text = "正在添加水印  %d / %d" % (value, total)
        c.create_text(w // 2, h // 2, text=text, font=(UI_FONT, 9, "bold"),
                      fill="#FFFFFF" if fw > w * 0.45 else "#1F2937")


    def _watch_options(self):
        for var in (self.var_date, self.var_time, self.var_aperture, self.var_shutter,
                    self.var_iso, self.var_focal, self.var_location, self.var_camera,
                    self.var_custom, self.var_custom_text, self.var_location_alias,
                    self.var_layout, self.var_opacity, self.var_position):
            var.trace_add("write", lambda *a: self.schedule_refresh())
        self.var_opacity.trace_add(
            "write", lambda *a: self.opacity_lbl.configure(text="%d%%" % self.var_opacity.get()))
        self.var_position.trace_add("write", lambda *a: self._toggle_xy())
        self.var_outdir_mode.trace_add("write", lambda *a: self._toggle_outdir())


    def schedule_refresh(self):
        if self._refresh_job:
            self.after_cancel(self._refresh_job)
        self._refresh_job = self.after(150, self.refresh_rows)

    def refresh_rows(self):
        self._refresh_job = None
        if not self.photos or not getattr(self, "_rows", None):
            return
        opt = self.collect_opt()
        for i, m in enumerate(self.photos):
            iid = str(i)
            if not self.tree.exists(iid) or i >= len(self._rows):
                continue
            vals = list(self._rows[i])
            vals[6] = "" if m.error else "  ".join(build_lines(m, opt))
            vals[7] = "✕" if m.error else ("✎" if m.wm_override.strip() else "✓")
            self._rows[i] = tuple(vals)
            self.tree.set(iid, "watermark", vals[6])
            self.tree.set(iid, "status", vals[7])
        self._col_need = self._compute_need()
        self.fit_columns()

    # ---------------- 列表列宽自适应
    def _on_tree_configure(self, event):
        if self._fit_job:
            self.after_cancel(self._fit_job)
        self._fit_job = self.after(60, self.fit_columns)

    def fit_columns(self):
        self._fit_job = None
        total = self.tree.winfo_width()
        if total < 120:
            return
        usable = total - 6
        need = getattr(self, "_col_need", None)
        if not need:
            need = {k: int(900 * r) for k, _, r in COLS}
        if sum(need.values()) <= usable:
            for key, _, _ in COLS:
                self.tree.column(key, width=need[key], minwidth=40, stretch=False)
            # 富余宽度全给「将添加的水印」列
            self.tree.column("watermark", width=need["watermark"] + usable - sum(need.values()),
                             minwidth=40, stretch=False)
        else:
            # 空间不够时：信息列按比例缩，「水印」列保留足够宽度别被压扁
            info_keys = [k for k, _, _ in COLS if k != "watermark"]
            wm_w = max(220, min(need["watermark"], int(usable * 0.34)))
            info_avail = max(200, usable - wm_w)
            info_total = sum(need[k] for k in info_keys) or 1
            if info_total <= info_avail:
                scale = 1.0
            else:
                scale = info_avail / float(info_total)
            for k in info_keys:
                self.tree.column(k, width=max(40, int(need[k] * scale)),
                                 minwidth=40, stretch=False)
            used = sum(max(40, int(need[k] * scale)) for k in info_keys)
            self.tree.column("watermark", width=max(40, usable - used),
                             minwidth=40, stretch=False)

    # ---------------- 浏览
    def open_browse_menu(self):
        menu = tk.Menu(self, tearoff=0, bg=CARD, fg=FG, activebackground=ACCENT_SOFT,
                       activeforeground=FG, relief="flat", bd=0,
                       font=(UI_FONT, 9))
        menu.add_command(label="选择文件夹…", command=self.choose_folder)
        menu.add_command(label="选择照片文件…", command=self.choose_files)
        try:
            menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())
        finally:
            menu.grab_release()

    def choose_folder(self):
        d = filedialog.askdirectory(title="选择照片所在文件夹")
        if not d:
            return
        self.source_files = None
        self.folder.set(d)
        self.start_scan()

    def choose_files(self):
        types = [("图片文件", "*.jpg *.jpeg *.png *.tif *.tiff *.webp"), ("所有文件", "*.*")]
        files = filedialog.askopenfilenames(title="选择照片文件", filetypes=types)
        if not files:
            return
        files = [f for f in files if os.path.splitext(f)[1].lower() in SUPPORTED_EXT]
        if not files:
            messagebox.showinfo("提示", "选中的文件里没有支持的图片格式。")
            return
        self.source_files = files
        self.folder.set("已选 %d 张照片（%s…）" % (len(files), os.path.dirname(files[0])))
        self.start_scan()

    def rescan(self):
        if self.source_files:
            self.start_scan()
        elif os.path.isdir(self.folder.get().strip()):
            self.start_scan()

    def pick_outdir(self):
        d = filedialog.askdirectory(title="选择输出文件夹")
        if d:
            self.var_outdir.set(d)

    def collect_opt(self):
        pos = dict(POSITIONS).get(self.var_position.get(), "bl")
        return {
            "include_date": self.var_date.get(),
            "include_time": self.var_time.get(),
            "include_aperture": self.var_aperture.get(),
            "include_shutter": self.var_shutter.get(),
            "include_iso": self.var_iso.get(),
            "include_focal": self.var_focal.get(),
            "include_location": self.var_location.get(),
            "include_camera": self.var_camera.get(),
            "include_custom": self.var_custom.get(),
            "custom_text": self.var_custom_text.get(),
            "location_alias": self.var_location_alias.get(),
            "layout": "multi" if self.var_layout.get() == "多行" else "single",
            "position": pos,
            "custom_x": self.var_cx.get(),
            "custom_y": self.var_cy.get(),
            "font_scale": self.var_font.get() / 100.0,
            "opacity": self.var_opacity.get() / 100.0,
            "outline": self.var_outline.get(),
            "font": self.var_fontname.get(),
            "color": hex_to_rgb(self._color_hex),
            "quality": self.var_quality.get(),
            "keep_exif": self.var_keep_exif.get(),
            "strip_gps": self.var_strip_gps.get(),
        }

    # ---------------- 读取
    def start_scan(self):
        if self.busy:
            return
        folder = self.folder.get().strip()
        files = self.source_files
        if not files and (not folder or not os.path.isdir(folder)):
            messagebox.showwarning("提示", "请先通过「浏览…」选择照片文件夹或照片文件。")
            return
        recur = self.recursive.get()

        self.busy = True
        self._show_progress(True, 100)
        self._draw_progress(0, 100, "正在读取照片信息…")
        self.status.configure(text="正在读取照片信息…")

        def work():
            try:
                if files:
                    image_files = list(files)
                else:
                    image_files = []
                    if recur:
                        for root_dir, dirs, names in os.walk(folder):
                            dirs[:] = [d for d in dirs if d != OUT_SUBDIR]
                            for n in names:
                                if os.path.splitext(n)[1].lower() in SUPPORTED_EXT:
                                    image_files.append(os.path.join(root_dir, n))
                    else:
                        for n in os.listdir(folder):
                            p = os.path.join(folder, n)
                            if os.path.isfile(p) and os.path.splitext(n)[1].lower() in SUPPORTED_EXT:
                                image_files.append(p)
                image_files.sort()
                total = max(1, len(image_files))
                metas = [None] * len(image_files)
                workers = max(4, min(12, (os.cpu_count() or 4) * 2))
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futs = {pool.submit(read_meta, f): i for i, f in enumerate(image_files)}
                    for n, fut in enumerate(as_completed(futs), 1):
                        i = futs[fut]
                        try:
                            metas[i] = fut.result()
                        except Exception as exc:
                            metas[i] = PhotoMeta(path=image_files[i],
                                                 name=os.path.basename(image_files[i]),
                                                 error=str(exc))
                        if n % 8 == 0 or n == len(image_files):
                            self.msg_q.put(("progress", int(n * 100 / total)))
                self.msg_q.put(("scanned", [m for m in metas if m is not None]))
            except Exception as exc:
                self.msg_q.put(("error", "读取失败：%s" % exc))

        threading.Thread(target=work, daemon=True).start()

    # ---------------- 列表操作
    def _fill_tree(self):
        self.tree.delete(*self.tree.get_children())
        opt = self.collect_opt()
        self._rows = []
        for i, m in enumerate(self.photos):
            vals = self._row_values(m, opt)
            self._rows.append(vals)
            tags = ["even" if i % 2 else "odd"]
            if m.error:
                tags.append("err")
            self.tree.insert("", "end", iid=str(i), values=vals, tags=tuple(tags))
        self._col_need = self._compute_need()
        self.fit_columns()
        self.update_count()

    def _compute_need(self):
        """按真实文字宽度算每列需要多宽（用 Treeview 的实际字体量，避免低估）"""
        import tkinter.font as tkfont
        st = ttk.Style(self)
        try:
            f = tkfont.Font(root=self, font=st.lookup("Treeview", "font") or (UI_FONT, 9))
        except Exception:
            f = tkfont.Font(root=self, family=UI_FONT, size=9)
        try:
            fb = tkfont.Font(root=self,
                             font=st.lookup("Treeview.Heading", "font") or (UI_FONT, 9, "bold"))
        except Exception:
            fb = tkfont.Font(root=self, family=UI_FONT, size=9, weight="bold")

        caps = {"name": 260, "datetime": 162, "exposure": 212, "focal": 72,
                "camera": 162, "gps": 98, "watermark": 470, "status": 56}
        need = {}
        for idx, (key, head_txt, _) in enumerate(COLS):
            w = fb.measure(head_txt) + 30
            for row in getattr(self, "_rows", []):
                try:
                    w = max(w, f.measure(str(row[idx])) + 30)
                except Exception:
                    pass
            need[key] = min(w, caps[key])
        return need

    def update_count(self):
        n = len(self.photos)
        sel = len(self.tree.selection())
        if n == 0:
            self.count_lbl.configure(text="还没有照片")
        else:
            self.count_lbl.configure(text="共 %d 张%s" % (n, ("　已选 %d 张" % sel) if sel else ""))

    def _row_values(self, m, opt):
        gps = "%.4f,%.4f" % m.gps if m.gps else ""
        wm = "" if m.error else "  ".join(build_lines(m, opt))
        status = "✕" if m.error else ("✎" if m.wm_override.strip() else "✓")
        return (m.name, m.datetime_text, m.exposure, m.focal, m.camera, gps, wm, status)

    def _on_tree_double(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return
        if self.tree.identify_column(event.x) == "#%d" % (WATERMARK_COL + 1):
            self.edit_watermark(int(row))
        else:
            self.preview_at(int(row))

    def edit_watermark(self, idx):
        """单张微调水印文字；留空保存 = 恢复自动生成"""
        if not (0 <= idx < len(self.photos)):
            return
        meta = self.photos[idx]
        win = tk.Toplevel(self)
        win.title("单独修改水印 · %s" % meta.name)
        win.configure(bg=CARD)
        win.transient(self)
        win.resizable(False, False)
        try:
            apply_window_icon(win)
        except Exception:
            pass

        body = tk.Frame(win, bg=CARD, padx=18, pady=16)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="这一张的水印文字（留空保存＝恢复自动生成）", bg=CARD, fg=MUTED,
                 font=(UI_FONT, 8)).pack(anchor="w")
        var = tk.StringVar(value=meta.wm_override or "  ".join(build_lines(meta, self.collect_opt())))
        ent = ttk.Entry(body, textvariable=var, width=52, font=(UI_FONT, 10))
        ent.pack(fill="x", pady=(8, 14))
        ent.focus_set()
        ent.icursor("end")
        ent.select_range(0, "end")

        btns = tk.Frame(body, bg=CARD)
        btns.pack(fill="x")

        def commit(text):
            meta.wm_override = text.strip()
            self._refresh_row(idx)
            n_txt = "已单独修改 %s" % meta.name if meta.wm_override else "%s 已恢复自动" % meta.name
            self.status.configure(text=n_txt)
            win.destroy()

        def on_save(event=None):
            commit(var.get())

        def on_reset():
            commit("")

        ttk.Button(btns, text="保存", style="Accent.TButton",
                   command=on_save).pack(side="right")
        ttk.Button(btns, text="取消", style="Ghost.TButton",
                   command=win.destroy).pack(side="right", padx=(0, 8))
        ttk.Button(btns, text="恢复自动", style="Ghost.TButton",
                   command=on_reset).pack(side="left")
        ent.bind("<Return>", on_save)
        win.bind("<Escape>", lambda e: win.destroy())
        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - win.winfo_width()) // 2
        y = self.winfo_rooty() + 140
        win.geometry("+%d+%d" % (max(0, x), max(0, y)))

    def _refresh_row(self, idx):
        """只刷新一行（改水印后用）"""
        if not (0 <= idx < len(self.photos)):
            return
        iid = str(idx)
        if self.tree.exists(iid):
            self.tree.item(iid, values=self._row_values(self.photos[idx], self.collect_opt()))

    def preview_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("提示", "先在列表里选一张照片。")
            return
        self.preview_at(int(sel[0]))

    def preview_at(self, idx):
        if 0 <= idx < len(self.photos):
            PreviewWindow(self, idx)

    def remove_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        idxs = sorted((int(i) for i in sel), reverse=True)
        for idx in idxs:
            if 0 <= idx < len(self.photos):
                del self.photos[idx]
        self._fill_tree()
        self.status.configure(text="已移除 %d 张，列表剩余 %d 张" % (len(idxs), len(self.photos)))

    # ---------------- 批量写入
    def start_apply(self):
        if self.busy:
            return
        if not self.photos:
            messagebox.showinfo("提示", "列表还是空的，请先用「浏览…」选择照片。")
            return
        todo = list(self.photos)
        opt = self.collect_opt()
        no_text = [m.name for m in todo if not build_lines(m, opt)]
        if no_text:
            if not messagebox.askyesno("确认", "有 %d 张照片算不出水印内容（缺少 EXIF 时间等），\n这些会原样复制、不加水印。继续？" % len(no_text)):
                return
        if not messagebox.askyesno("确认", "将处理 %d 张照片，按当前设置写入水印。\n开始？" % len(todo)):
            return

        if self.var_outdir_mode.get() == "custom" and self.var_outdir.get().strip():
            out_root = self.var_outdir.get().strip()
        else:
            out_root = None
        base_folder = self.folder.get().strip()
        if not os.path.isdir(base_folder):
            base_folder = os.path.dirname(todo[0].path) if todo else os.getcwd()

        try:
            workers = max(2, min(6, (os.cpu_count() or 4) - 1))
        except Exception:
            workers = 4

        self.busy = True
        self._show_progress(True, len(todo))
        self.btn_apply.state(["disabled"])
        self.status.configure(text="正在添加水印…（%d 线程并行）" % workers)

        def work():
            done = 0
            fail = 0
            try:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futs = {pool.submit(apply_job, m, opt, out_root, base_folder): m
                            for m in todo}
                    for fut in as_completed(futs):
                        meta = futs[fut]
                        try:
                            fut.result()
                            done += 1
                        except Exception as exc:
                            fail += 1
                            self.msg_q.put(("log", "失败：%s（%s）" % (meta.name, exc)))
                        self.msg_q.put(("progress", done + fail))
            except Exception as exc:
                self.msg_q.put(("error", "写入失败：%s" % exc))
            finally:
                self.msg_q.put(("done", done))

        threading.Thread(target=work, daemon=True).start()

    # ---------------- 队列
    def _drain_queue(self):
        try:
            while True:
                kind, payload = self.msg_q.get_nowait()
                if kind == "progress":
                    self._draw_progress(payload, self._total)
                elif kind == "log":
                    self.status.configure(text=payload)
                elif kind == "error":
                    self.status.configure(text=payload)
                    self.btn_apply.state(["!disabled"])
                    self._show_progress(False)
                    self.busy = False
                    messagebox.showerror("出错了", payload)
                elif kind == "scanned":
                    self.photos = payload
                    self._fill_tree()
                    ok = sum(1 for m in self.photos if not m.error and m.dt)
                    self.status.configure(text="共 %d 张，其中 %d 张读到拍摄时间" % (len(self.photos), ok))
                    self._show_progress(False)
                    self.busy = False
                elif kind == "done":
                    self._draw_progress(self._total, self._total,
                                        "完成：已处理 %d 张" % payload)
                    self.status.configure(text="完成：已处理 %d 张" % payload)
                    self.btn_apply.state(["!disabled"])
                    self.after(1500, lambda: self._show_progress(False))
                    self.busy = False
                    messagebox.showinfo("完成", "已处理 %d 张照片。\n输出目录：%s" % (
                        payload, self._outdir_hint()))
        except queue.Empty:
            pass
        self.after(120, self._drain_queue)

    def _outdir_hint(self):
        if self.var_outdir_mode.get() == "custom" and self.var_outdir.get().strip():
            return self.var_outdir.get().strip()
        base = self.folder.get().strip()
        if not os.path.isdir(base) and self.photos:
            base = os.path.dirname(self.photos[0].path)
        return os.path.join(base, OUT_SUBDIR)


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
