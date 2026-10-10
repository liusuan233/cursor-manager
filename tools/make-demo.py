#!/usr/bin/env python3
"""自动生成 cursor-manager 的演示视频。

不需要录屏、不需要剪辑软件。画面全部来自**真实数据**：

  · 终端场景跑的是真命令，渲染的是真输出（含 ANSI 颜色）
  · 动画光标是从你已安装的主题里真解码出来的（Xcursor → 逐帧 RGBA）
  · 导入场景用 XDG_DATA_HOME 指向临时目录，**不碰你的真实配置**

帧用 PIL 渲染，直接通过管道喂给 ffmpeg 编码，不落中间文件。

用法:
    python3 tools/make-demo.py                        # 生成 demo/cursor-manager-demo.mp4
    python3 tools/make-demo.py -o /tmp/x.mp4          # 指定输出
    python3 tools/make-demo.py --fps 60               # 更高帧率
    python3 tools/make-demo.py --still preview        # 只导出某个场景的一帧 PNG（调试用）
    python3 tools/make-demo.py --list                 # 列出场景与时长
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from cursorkit import CursorFile, scan_themes  # noqa: E402

# ---------------------------------------------------------------------------
# 基本配置
# ---------------------------------------------------------------------------

W, H = 1920, 1080
FPS = 30
FADE_IN = 0.35              # 每个场景从黑淡入的时长（秒）

MONO = "/usr/share/fonts/TTF/JetBrains-Maple-Mono-NF-XX-XX/JetBrainsMapleMono-Regular.ttf"
MONO_BOLD = "/usr/share/fonts/TTF/JetBrains-Maple-Mono-NF-XX-XX/JetBrainsMapleMono-Bold.ttf"

# 配色（Tokyo Night 一系，暗色、对录屏友好）
C = {
    "bg": (22, 23, 33),
    "panel": (33, 37, 54),
    "panel_edge": (56, 62, 86),
    "bar": (43, 48, 68),
    "fg": (192, 202, 245),
    "dim": (86, 95, 137),
    "green": (158, 206, 106),
    "red": (247, 118, 142),
    "yellow": (224, 175, 104),
    "blue": (122, 162, 247),
    "cyan": (125, 207, 255),
    "magenta": (187, 154, 247),
    "accent": (122, 162, 247),
}

# ANSI SGR 前景色 → 调色板
ANSI_FG = {
    30: (68, 71, 90), 31: C["red"], 32: C["green"], 33: C["yellow"],
    34: C["blue"], 35: C["magenta"], 36: C["cyan"], 37: C["fg"],
    90: C["dim"], 91: C["red"], 92: C["green"], 93: C["yellow"],
    94: C["blue"], 95: C["magenta"], 96: C["cyan"], 97: (255, 255, 255),
}

ANSI_RE = re.compile(r"\x1b\[([0-9;]*)m")


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = MONO_BOLD if bold else MONO
    if not Path(path).is_file():                    # 退路
        path = "/usr/share/fonts/TTF/DejaVuSansMono.ttf"
    return ImageFont.truetype(path, size)


# ---------------------------------------------------------------------------
# ANSI → 着色文本片段
# ---------------------------------------------------------------------------

def parse_ansi(text: str, base: tuple[int, int, int] | None = None) -> list[list[tuple[str, tuple[int, int, int]]]]:
    """把带 ANSI 颜色的文本切成「每行一串 (文字, 颜色) 片段」。"""
    base = base or C["fg"]
    rows: list[list[tuple[str, tuple[int, int, int]]]] = [[]]
    color = base
    pos = 0
    for m in ANSI_RE.finditer(text):
        chunk = text[pos:m.start()]
        if chunk:
            rows[-1].append((chunk, color))
        codes = [int(x) for x in m.group(1).split(";") if x.strip()] or [0]
        for code in codes:
            if code == 0:
                color = base
            elif code in ANSI_FG:
                color = ANSI_FG[code]
        pos = m.end()
    rest = text[pos:]
    if rest:
        rows[-1].append((rest, color))
    # 处理换行
    out: list[list[tuple[str, tuple[int, int, int]]]] = [[]]
    for seg, col in rows[0]:
        parts = seg.split("\n")
        for i, p in enumerate(parts):
            if i:
                out.append([])
            if p:
                out[-1].append((p, col))
    return out


def run_capture(cmd: list[str], env: dict | None = None) -> str:
    """跑真实命令，拿到带 ANSI 的输出。命令里的仓库路径统一显示成相对形式。"""
    e = dict(os.environ)
    if env:
        e.update(env)
    p = subprocess.run(cmd, capture_output=True, text=True, env=e, cwd=str(ROOT))
    text = (p.stdout or "") + (p.stderr or "")
    return text


def pretty_cmd(cmd: list[str]) -> str:
    """把命令显示得好看点：去掉仓库绝对路径。"""
    out = []
    for c in cmd:
        c = str(c).replace(str(ROOT) + "/", "").replace(str(Path.home()) + "/", "~/")
        out.append(c)
    return " ".join(out)


# ---------------------------------------------------------------------------
# 终端面板
# ---------------------------------------------------------------------------

@dataclass
class Terminal:
    """一个仿终端窗口。文本逐行"打"出来，已完成的整行会被缓存成图像。

    面板高度**按内容自适应**，不撑满屏幕 —— 否则几行输出会把窗口拉得很大、
    下半截全空，很难看。
    """

    cmd: str
    lines: list[list[tuple[str, tuple[int, int, int]]]]
    speed: float = 42.0                 # 每秒打多少字符
    margin: int = 130
    font_size: int = 32
    line_h: int = 46
    title: str = "liusuan@arch — cursor-manager"
    top: int = 210                      # 面板顶端（给标题留位置）
    _cache: dict = field(default_factory=dict)
    _chrome: Image.Image | None = None

    # 标题栏 52 + 命令行(84起，字高约34) + 分隔线 + 留白
    CHROME_H = 140

    def __post_init__(self) -> None:
        self._fit_font()

    def _fit_font(self) -> None:
        """自动把字号收到「最长一行放得下」为止 —— 保证任何输出都不会被裁掉。

        同时也尽量用大字号：1080p 的演示视频里，27px 的终端字偏小。
        """
        avail = (W - self.margin * 2) - 68
        probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        size = self.font_size
        while size > 14:
            f = font(size)
            widest = max((sum(probe.textlength(t, font=f) for t, _ in ln)
                          for ln in self.lines), default=0)
            if widest <= avail:
                break
            size -= 1
        self.font_size = size
        self.line_h = int(size * 1.45)

    @property
    def max_lines(self) -> int:
        """面板最多能放下几行（超出就截断，不许画到面板外）。"""
        avail = (H - 130 - self.top) - self.CHROME_H - 26
        return max(1, avail // self.line_h)

    @property
    def shown_lines(self) -> int:
        return min(len(self.lines), self.max_lines)

    @property
    def panel(self) -> tuple[int, int, int, int]:
        h = self.CHROME_H + self.shown_lines * self.line_h + 26
        h = min(h, H - 130 - self.top)
        return self.margin, self.top, W - self.margin, self.top + h

    @property
    def text_x(self) -> int:
        return self.margin + 34

    def line_y(self, i: int) -> int:
        return self.top + self.CHROME_H + i * self.line_h

    def _build_chrome(self) -> Image.Image:
        """面板 + 标题栏 + 命令行，这些不变，只画一次。"""
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        x0, y0, x1, y1 = self.panel
        d.rounded_rectangle([x0, y0, x1, y1], radius=16, fill=C["panel"],
                            outline=C["panel_edge"], width=2)
        # 标题栏
        d.rounded_rectangle([x0, y0, x1, y0 + 52], radius=16, fill=C["bar"])
        d.rectangle([x0, y0 + 36, x1, y0 + 52], fill=C["bar"])
        for i, col in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
            cx = x0 + 30 + i * 26
            d.ellipse([cx, y0 + 19, cx + 14, y0 + 33], fill=col)
        d.text((x1 - 24, y0 + 16), self.title, font=font(20), fill=C["dim"], anchor="ra")
        # 命令行
        py = y0 + 84
        d.text((self.text_x, py), "$ ", font=font(self.font_size, True), fill=C["green"])
        pw = d.textlength("$ ", font=font(self.font_size, True))
        d.text((self.text_x + pw, py), self.cmd, font=font(self.font_size), fill=C["fg"])
        d.line([self.text_x, py + self.line_h + 6, x1 - 34, py + self.line_h + 6],
               fill=C["panel_edge"], width=1)
        return img

    def render(self, t: float, cursor_on: bool = True) -> Image.Image:
        if self._chrome is None:
            self._chrome = self._build_chrome()

        img = self._chrome.copy()
        d = ImageDraw.Draw(img)
        tx = self.text_x

        budget = max(0.0, t * self.speed)          # 已经打出来的字符数
        consumed = 0.0
        typing_row = None
        for i, line in enumerate(self.lines[: self.shown_lines]):
            if consumed >= budget:
                break
            y = self.line_y(i)
            total = sum(len(s) for s, _ in line)
            if i not in self._cache and consumed + total <= budget:
                li = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                self._paint_line(ImageDraw.Draw(li), line, tx, y)
                self._cache[i] = li
            if i in self._cache:
                img.alpha_composite(self._cache[i])
            else:
                remain = budget - consumed
                partial = []
                for seg, col in line:
                    if remain <= 0:
                        break
                    partial.append((seg[: int(remain)], col))
                    remain -= len(seg)
                self._paint_line(d, partial, tx, y)
                typing_row = (y, partial)
            consumed += total

        # 打字光标
        if cursor_on and typing_row is not None and int(t * 3) % 2 == 0:
            y, partial = typing_row
            wpx = sum(d.textlength(s, font=font(self.font_size)) for s, _ in partial)
            d.rectangle([tx + wpx + 3, y + 5, tx + wpx + 13, y + self.font_size + 9],
                        fill=C["accent"])
        # 全部打完后留一个静止光标
        elif cursor_on and typing_row is None and self.lines:
            y = self.line_y(self.shown_lines - 1) + self.line_h
            if y < self.panel[3] - 20:
                d.rectangle([tx, y, tx + 11, y + 6], fill=C["dim"])
        return img

    def _paint_line(self, d: ImageDraw.ImageDraw, line, x: int, y: int) -> None:
        f = font(self.font_size)
        cx = x
        for seg, col in line:
            d.text((cx, y), seg, font=f, fill=col)
            cx += d.textlength(seg, font=f)

    @property
    def total_chars(self) -> int:
        return sum(sum(len(s) for s, _ in ln) for ln in self.lines)


# ---------------------------------------------------------------------------
# 素材采集
# ---------------------------------------------------------------------------

def pick_animated_theme():
    """挑一个光标带多帧动画的主题（没有就用任意可用的）。"""
    themes = [t for t in scan_themes() if t.valid and t.cursors]
    for t in themes:
        for c in t.cursors:
            try:
                if CursorFile.load(c.path, want=96).animated:
                    return t
            except Exception:
                continue
    return themes[0] if themes else None


def pick_broken_theme():
    for t in scan_themes():
        if not t.valid:
            return t
    return None


def load_animation(cursor, nominal: int = 96):
    """把一个光标解码成 (帧图, 累计时间轴, 总时长)。"""
    cf = CursorFile.load(cursor.path, want=nominal)
    frames = cf.frames(nominal)
    imgs, bounds, acc = [], [], 0.0
    for f in frames:
        imgs.append(Image.frombytes("RGBA", (f.width, f.height), f.rgba))
        acc += (f.delay or 50) / 1000.0
        bounds.append(acc)
    return imgs, bounds, max(acc, 0.001)


def frame_at(anim, t: float) -> Image.Image:
    imgs, bounds, total = anim
    x = t % total
    for img, b in zip(imgs, bounds):
        if x < b:
            return img
    return imgs[-1]


# ---------------------------------------------------------------------------
# 辅助绘制
# ---------------------------------------------------------------------------

def paste_center(base: Image.Image, im: Image.Image, cx: int, cy: int) -> None:
    base.alpha_composite(im, (int(cx - im.width / 2), int(cy - im.height / 2)))


def caption(d: ImageDraw.ImageDraw, text: str, y: int = H - 118, size: int = 30,
            color=None) -> None:
    d.text((W // 2, y), text, font=font(size), fill=color or C["dim"], anchor="mm")


def scene_header(d: ImageDraw.ImageDraw, text: str, sub: str = "") -> None:
    d.text((110, 74), text, font=font(44, True), fill=C["fg"])
    if sub:
        d.text((110, 136), sub, font=font(24), fill=C["dim"])


TERM_DONE_AT = 0.80        # 打字在场景时长的这个比例处打完，剩下的时间留给观众看


def make_terminal(cmd: str, out: str, duration: float, **kw) -> Terminal:
    """建终端并按内容自动定打字速度。

    之前是写死 42 字符/秒 —— 结果 450 字符的 `--list` 输出在 9 秒的场景里
    **永远打不完**，视频里会看到打字打一半就切走了。现在的速度保证在
    场景时长的 TERM_DONE_AT 处打完。
    """
    lines = parse_ansi(out)
    t = Terminal(cmd, lines, **kw)
    chars = max(1, t.total_chars)
    t.speed = chars / max(0.5, duration * TERM_DONE_AT)
    return t


# ---------------------------------------------------------------------------
# 场景
# ---------------------------------------------------------------------------

@dataclass
class Scene:
    name: str
    duration: float
    render: object                      # Callable[[float], Image.Image]
    desc: str = ""


def build_scenes(args) -> list[Scene]:
    scenes: list[Scene] = []

    # ---- 1. 标题 ----
    from cursorkit import __version__
    def s_title(t: float) -> Image.Image:
        img = Image.new("RGBA", (W, H), C["bg"] + (255,))
        d = ImageDraw.Draw(img)
        # 背景网格，避免大块纯色太单调
        for x in range(0, W, 60):
            d.line([x, 0, x, H], fill=(255, 255, 255, 6), width=1)
        for y in range(0, H, 60):
            d.line([0, y, W, y], fill=(255, 255, 255, 6), width=1)
        d.text((W // 2, H // 2 - 108), "cursor-manager", font=font(96, True),
               fill=C["fg"], anchor="mm")
        d.text((W // 2, H // 2 + 6), "niri / Wayland 下的鼠标光标主题管理器",
               font=font(38), fill=C["accent"], anchor="mm")
        d.text((W // 2, H // 2 + 74), "浏览 · 动画预览 · 一键切换 · 导入 Windows 光标包",
               font=font(30), fill=C["dim"], anchor="mm")
        d.text((W // 2, H // 2 + 150), f"v{__version__}", font=font(26), fill=C["dim"],
               anchor="mm")
        return img

    scenes.append(Scene("title", 3.6, s_title, "标题卡"))

    # ---- 2. 痛点：静默失败 ----
    out = run_capture(["./cursor-manager", "--list"])
    broken = pick_broken_theme()
    term = make_terminal(pretty_cmd(["./cursor-manager", "--list"]), out, 9.0)
    # 找出带 ✘ 的那一行，做高亮
    bad_idx = next((i for i, ln in enumerate(term.lines)
                    if any("✘" in s for s, _ in ln)), None)

    def s_problem(t: float) -> Image.Image:
        img = Image.new("RGBA", (W, H), C["bg"] + (255,))
        d = ImageDraw.Draw(img)
        scene_header(d, "问题：改了配置却没反应",
                     "缺一层 cursors/ 目录 → 程序静默忽略 → 回落到系统默认，不报任何错")
        img.alpha_composite(term.render(t - 0.6))
        if bad_idx is not None and t > 2.2 and int(t * 2) % 2 == 0:
            y0 = term.line_y(bad_idx) - 7
            d.rounded_rectangle([term.panel[0] + 18, y0, term.panel[2] - 18, y0 + term.line_h + 3],
                                radius=8, outline=C["red"], width=2)
        caption(d, "这个工具把它变成看得见的红叉", H - 62)
        return img

    scenes.append(Scene("problem", 9.0, s_problem, "终端：把静默失败标出来"))

    # ---- 3. 动画光标预览 ----
    theme = pick_animated_theme()
    anims = []
    if theme is not None:
        picked = [c for c in theme.cursors
                  if len(anims) < 8 and _has_multi(c)]
        for c in picked:
            anims.append((c.name, load_animation(c, 96)))

    def s_preview(t: float) -> Image.Image:
        img = Image.new("RGBA", (W, H), C["bg"] + (255,))
        d = ImageDraw.Draw(img)
        scene_header(d, "动画预览：直接解码 Xcursor 逐帧播放",
                     "这种格式普通图片查看器打不开 —— 它不是 PNG，是自己的一套容器格式")
        if theme is not None:
            d.text((110, 196), f"主题：{theme.name}   ·   {len(theme.cursors)} 个光标 / "
                               f"{theme.alias_count} 个别名",
                   font=font(26), fill=C["green"])
        cols, cw, chh = 4, 330, 250
        gx = (W - cols * cw) // 2
        for i, (name, anim) in enumerate(anims):
            col, row = i % cols, i // cols
            cx = gx + col * cw + cw // 2
            cy = 330 + row * chh
            paste_center(img, frame_at(anim, t), cx, cy)
            d.text((cx, cy + 92), name, font=font(24), fill=C["dim"], anchor="mm")
        caption(d, "8 帧 ~ 20 帧的动画，按各帧自己的延时播放", H - 58)
        return img

    scenes.append(Scene("preview", 11.0, s_preview, "动画光标网格"))

    # ---- 4. 导入 Windows 包（真实输出，写进临时目录）----
    imp_lines = None
    zip_path = _find_demo_zip()
    tmpdir = None
    if zip_path is not None:
        tmpdir = tempfile.mkdtemp(prefix="demo-xdg-")
        out = run_capture(["./cursor-manager", "--import", str(zip_path),
                           "--name", "DemoTheme"],
                          env={"XDG_DATA_HOME": tmpdir})
        imp_lines = parse_ansi(out)
        shutil.rmtree(tmpdir, ignore_errors=True)

    if imp_lines:
        imp_term = make_terminal(
            pretty_cmd(["./cursor-manager", "--import", zip_path.name or str(zip_path),
                        "--name", "DemoTheme"]),
            out, 11.5, title="导入 Windows 光标包")

        def s_import(t: float) -> Image.Image:
            img = Image.new("RGBA", (W, H), C["bg"] + (255,))
            d = ImageDraw.Draw(img)
            scene_header(d, "导入 Windows 光标包",
                         "优先读包内 .inf 的官方角色映射；原始分辨率降采样出多档尺寸")
            img.alpha_composite(imp_term.render(t - 0.5))
            return img

        scenes.append(Scene("import", 11.5, s_import, "终端：真实导入输出"))

    # ---- 5. 一键应用 ----
    target = theme.name if theme else "Adwaita"
    out = run_capture(["./cursor-manager", "apply", target, "24", "--dry-run"])
    apply_term = make_terminal(
        pretty_cmd(["./cursor-manager", "apply", target, "24", "--dry-run"]),
        out, 8.5, title="一键写全 6 个配置点")

    def s_apply(t: float) -> Image.Image:
        img = Image.new("RGBA", (W, H), C["bg"] + (255,))
        d = ImageDraw.Draw(img)
        scene_header(d, "一键应用：六个配置点全写一遍",
                     "niri · DMS · GTK3 · GTK4 · XWayland .Xresources · GSettings")
        img.alpha_composite(apply_term.render(t - 0.5))
        caption(d, "--dry-run 只报告不落盘", H - 58)
        return img

    scenes.append(Scene("apply", 8.5, s_apply, "终端：六项报告"))

    # ---- 6. 删除闸门 ----
    out = run_capture(["./cursor-manager", "remove", "Adwaita"])
    del_term = make_terminal(pretty_cmd(["./cursor-manager", "remove", "Adwaita"]),
                             out, 8.0, title="删除：四道安全闸门")

    def s_delete(t: float) -> Image.Image:
        img = Image.new("RGBA", (W, H), C["bg"] + (255,))
        d = ImageDraw.Draw(img)
        scene_header(d, "删除是唯一不可逆的操作",
                     "系统主题 / 正在使用的主题会被拒绝；默认移入回收站，可恢复")
        img.alpha_composite(del_term.render(t - 0.5))
        return img

    scenes.append(Scene("delete", 8.0, s_delete, "终端：闸门拒绝"))

    # ---- 7. 结尾 ----
    url = "https://github.com/liusuan233/cursor-manager"
    def s_outro(t: float) -> Image.Image:
        img = Image.new("RGBA", (W, H), C["bg"] + (255,))
        d = ImageDraw.Draw(img)
        for x in range(0, W, 60):
            d.line([x, 0, x, H], fill=(255, 255, 255, 6), width=1)
        for y in range(0, H, 60):
            d.line([0, y, W, y], fill=(255, 255, 255, 6), width=1)
        d.text((W // 2, H // 2 - 150), "cursor-manager", font=font(64, True),
               fill=C["fg"], anchor="mm")
        d.text((W // 2, H // 2 - 58), url, font=font(34), fill=C["accent"], anchor="mm")
        d.text((W // 2, H // 2 + 34), "sudo pacman -S --needed base-devel   # 或直接下 Release 包",
               font=font(28), fill=C["dim"], anchor="mm")
        d.text((W // 2, H // 2 + 86), "MIT License · GTK4 + Python · 命令行不需要图形环境",
               font=font(26), fill=C["dim"], anchor="mm")
        return img

    scenes.append(Scene("outro", 4.4, s_outro, "结尾卡"))
    return scenes


def _has_multi(cursor) -> bool:
    try:
        return CursorFile.load(cursor.path, want=96).animated
    except Exception:
        return False


def _find_demo_zip():
    """找一个真的 Windows 光标包来演示导入。"""
    cands = sorted(Path.home().joinpath("Downloads").glob("*.zip")) if (
        Path.home() / "Downloads").is_dir() else []
    for p in cands:
        name = p.name.lower()
        if any(k in name for k in ("鼠标", "cursor", "ani")):
            return p
    return None


# ---------------------------------------------------------------------------
# 合成与编码
# ---------------------------------------------------------------------------

def render_frame(scene: Scene, t: float) -> Image.Image:
    img = scene.render(t).convert("RGB")
    if t < FADE_IN:                                  # 每个场景从黑淡入
        k = t / FADE_IN
        img = Image.blend(Image.new("RGB", (W, H), (0, 0, 0)), img, k)
    return img


def encode(scenes: list[Scene], out: Path, fps: int = FPS) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    total = sum(s.duration for s in scenes)
    total_frames = int(total * fps)
    print(f"  总时长 {total:.1f}s，{total_frames} 帧 @ {fps}fps → {out}")

    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps),
           "-i", "-",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    frame_i = 0
    try:
        for s in scenes:
            n = int(s.duration * fps)
            for i in range(n):
                t = i / fps
                proc.stdin.write(render_frame(s, t).tobytes())
                frame_i += 1
                if frame_i % 60 == 0:
                    pct = frame_i * 100 // total_frames
                    print(f"\r    编码 {pct:>3}%  ({frame_i}/{total_frames})",
                          end="", file=sys.stderr, flush=True)
    finally:
        proc.stdin.close()
        rc = proc.wait()
    print("\r" + " " * 44 + "\r", end="", file=sys.stderr)
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 cursor-manager 演示视频")
    ap.add_argument("-o", "--output", default=str(ROOT / "demo" / "cursor-manager-demo.mp4"))
    ap.add_argument("--fps", type=int, default=FPS)
    ap.add_argument("--list", action="store_true", help="列出场景")
    ap.add_argument("--still", metavar="SCENE", help="只导出该场景的一帧 PNG")
    ap.add_argument("--still-at", type=float, default=0.92,
                    help="导出帧的时刻占场景时长的比例（默认 0.92，接近结尾）")
    args = ap.parse_args()

    print("渲染 cursor-manager 演示视频")
    print("  采集真实素材…")
    scenes = build_scenes(args)

    if args.list:
        for s in scenes:
            print(f"  {s.name:<10} {s.duration:>5.1f}s   {s.desc}")
        print(f"  {'合计':<10} {sum(s.duration for s in scenes):>5.1f}s")
        return 0

    if args.still:
        s = next((x for x in scenes if x.name == args.still), None)
        if s is None:
            print(f"  没有叫 {args.still!r} 的场景", file=sys.stderr)
            return 1
        p = Path(args.still + ".png")
        render_frame(s, s.duration * args.still_at).save(p)
        print(f"  已导出 {p}")
        return 0

    if shutil.which("ffmpeg") is None:
        print("  找不到 ffmpeg，无法编码。装一下：sudo pacman -S ffmpeg", file=sys.stderr)
        return 1

    rc = encode(scenes, Path(args.output), args.fps)
    if rc != 0:
        print(f"  ffmpeg 退出码 {rc}", file=sys.stderr)
        return rc
    size = Path(args.output).stat().st_size
    print(f"  ✓ 完成：{args.output}  ({size / 1024 / 1024:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
