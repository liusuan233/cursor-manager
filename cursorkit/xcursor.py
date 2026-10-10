"""Xcursor 二进制格式的读取与写入。

格式速查（全部小端）::

    Header   magic 'Xcur' | header_size(16) | version(0x00010000) | ntoc
    TOC      每项 12 字节: type | subtype | position
    Image    36 字节头: header_size(36) type subtype version
                        width height xhot yhot delay
             之后 width*height 个 u32 像素

要点：
  * type 0xFFFD0002 = 图片；subtype = 标称尺寸（nominal size），
    libXcursor 用它挑选最接近请求尺寸的那一组图。
  * 同一标称尺寸下可以有多张图 —— 那就是动画帧，按 TOC 顺序播放，delay 单位毫秒。
  * 像素是 **预乘 alpha** 的 ARGB（内存字节序 B,G,R,A）。本模块对外统一转换成
    直通 alpha 的 RGBA，因为 GDK/Gtk 的 R8G8B8A8 要的就是直通 alpha。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

try:  # numpy 只是加速，没有也能跑
    import numpy as _np
except Exception:  # pragma: no cover
    _np = None

MAGIC = b"Xcur"
VERSION = 0x00010000
IMAGE_TYPE = 0xFFFD0002
COMMENT_TYPE = 0xFFFE0001
HEADER_SIZE = 16
IMAGE_HEADER_SIZE = 36


class XcursorError(Exception):
    """不是合法的 Xcursor 文件。"""


@dataclass(slots=True)
class Frame:
    """一帧解码后的图像，rgba 为直通 alpha 的 R,G,B,A 字节串。"""

    width: int
    height: int
    xhot: int
    yhot: int
    delay: int
    rgba: bytes
    nominal: int

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height

    def scaled(self, factor: int) -> "Frame":
        """最近邻整数倍放大 —— 光标是像素画，模糊插值会毁掉锐利边缘。"""
        if factor <= 1:
            return self
        if _np is not None:
            a = _np.frombuffer(self.rgba, dtype=_np.uint8).reshape(self.height, self.width, 4)
            a = _np.repeat(_np.repeat(a, factor, axis=0), factor, axis=1)
            rgba = a.tobytes()
        else:
            rows = []
            stride = self.width * 4
            for y in range(self.height):
                row = self.rgba[y * stride : (y + 1) * stride]
                rows.append(row * factor)
            big = b"".join(rows)
            rgba = b"".join(big[i * stride : (i + 1) * stride] for i in range(self.height) for _ in range(factor))
        return Frame(
            width=self.width * factor,
            height=self.height * factor,
            xhot=self.xhot * factor,
            yhot=self.yhot * factor,
            delay=self.delay,
            rgba=rgba,
            nominal=self.nominal,
        )


def _unpremultiply(raw: bytes, width: int, height: int) -> bytes:
    """文件里的 ARGB 是预乘的，转成直通 alpha 的 RGBA。"""
    n = width * height
    if _np is not None:
        a = _np.frombuffer(raw, dtype=_np.uint8, count=n * 4).reshape(n, 4)
        alpha = a[:, 3].astype(_np.float32)
        scale = _np.where(alpha > 0, 255.0 / _np.maximum(alpha, 1.0), 0.0)
        out = _np.empty((n, 4), dtype=_np.uint8)
        for ch in range(3):  # 文件里是 B,G,R -> 输出 R,G,B
            out[:, ch] = _np.clip(a[:, 2 - ch].astype(_np.float32) * scale, 0, 255)
        out[:, 3] = a[:, 3]
        return out.tobytes()

    out = bytearray(n * 4)
    for i in range(n):
        b, g, r, al = raw[i * 4 : i * 4 + 4]
        if al == 0:
            continue
        if al == 255:
            out[i * 4 : i * 4 + 4] = bytes((r, g, b, 255))
        else:
            out[i * 4 : i * 4 + 4] = bytes(
                (min(255, r * 255 // al), min(255, g * 255 // al), min(255, b * 255 // al), al)
            )
    return bytes(out)


def _premultiply(rgba: bytes, width: int, height: int) -> bytes:
    """直通 alpha RGBA -> 文件里要的预乘 ARGB。"""
    n = width * height
    if _np is not None:
        a = _np.frombuffer(rgba, dtype=_np.uint8, count=n * 4).reshape(n, 4)
        alpha = a[:, 3].astype(_np.float32)
        scale = alpha / 255.0
        out = _np.empty((n, 4), dtype=_np.uint8)
        for ch in range(3):          # 输出 B,G,R <- 输入 R,G,B 反着取
            out[:, ch] = _np.clip(a[:, 2 - ch].astype(_np.float32) * scale, 0, 255)
        out[:, 3] = a[:, 3]
        return out.tobytes()

    out = bytearray(n * 4)
    for i in range(n):
        r, g, b, al = rgba[i * 4 : i * 4 + 4]
        out[i * 4 + 0] = b * al // 255
        out[i * 4 + 1] = g * al // 255
        out[i * 4 + 2] = r * al // 255
        out[i * 4 + 3] = al
    return bytes(out)


class CursorFile:
    """一个 Xcursor 文件的全部内容，按标称尺寸分组。"""

    __slots__ = ("path", "images")

    def __init__(self, path: str | Path, images: dict[int, list[Frame]]):
        self.path = Path(path)
        self.images = images

    # ---- 查询 ----
    @property
    def sizes(self) -> list[int]:
        return sorted(self.images)

    @property
    def max_size(self) -> int:
        return max(self.images) if self.images else 0

    @property
    def frame_count(self) -> int:
        """按最大标称尺寸算的动画帧数。"""
        if not self.images:
            return 0
        return len(self.images[self.max_size])

    @property
    def animated(self) -> bool:
        return self.frame_count > 1

    def nearest_size(self, want: int) -> int:
        if not self.images:
            raise XcursorError(f"{self.path} 里没有任何图像")
        return min(self.images, key=lambda s: (abs(s - want), s))

    def frames(self, want: int) -> list[Frame]:
        """取最接近 want 的那组标称尺寸的全部帧。"""
        return self.images[self.nearest_size(want)]

    def poster(self, want: int = 64) -> Frame:
        return self.frames(want)[0]

    def total_duration(self, want: int) -> int:
        return sum(f.delay or 0 for f in self.frames(want)) or len(self.frames(want)) * 50

    # ---- 读取 ----
    @classmethod
    def load(cls, path: str | Path, want: int | None = None) -> "CursorFile":
        """读取 Xcursor 文件。

        want 不为 None 时，只解码"最接近 want 的那一档标称尺寸"。
        主题里常带 256x256 的大图，全解码纯属浪费 —— 预览只要一档就够。
        """
        path = Path(path)
        try:
            data = path.read_bytes()
        except OSError as e:
            raise XcursorError(f"无法读取 {path}: {e}") from e

        if len(data) < HEADER_SIZE or data[:4] != MAGIC:
            raise XcursorError(f"{path} 不是 Xcursor 文件（magic={data[:4]!r}）")

        _magic, _hsize, _version, ntoc = struct.unpack_from("<4sIII", data, 0)
        if HEADER_SIZE + ntoc * 12 > len(data):
            raise XcursorError(f"{path} 的 TOC 越界，文件可能损坏")

        # 第一遍：只登记 (标称尺寸, 偏移)，不解码像素
        entries: list[tuple[int, int]] = []
        for i in range(ntoc):
            ctype, subtype, pos = struct.unpack_from("<III", data, HEADER_SIZE + i * 12)
            if ctype != IMAGE_TYPE or pos + IMAGE_HEADER_SIZE > len(data):
                continue
            inominal = struct.unpack_from("<I", data, pos + 8)[0] or subtype
            entries.append((inominal, pos))
        if not entries:
            raise XcursorError(f"{path} 里没有解析出任何图像")

        if want is not None:
            best = min({n for n, _ in entries}, key=lambda s: (abs(s - want), s))
            entries = [e for e in entries if e[0] == best]

        # 第二遍：只解码选中档位
        images: dict[int, list[Frame]] = {}
        for inominal, pos in entries:
            (_ihs, _itype, _isub, _iver, w, h, xh, yh, delay) = struct.unpack_from(
                "<IIIIIIIII", data, pos
            )
            need = w * h * 4
            if w <= 0 or h <= 0 or pos + IMAGE_HEADER_SIZE + need > len(data):
                continue
            raw = data[pos + IMAGE_HEADER_SIZE : pos + IMAGE_HEADER_SIZE + need]
            images.setdefault(inominal, []).append(
                Frame(w, h, xh, yh, max(delay, 0), _unpremultiply(raw, w, h), inominal)
            )

        if not images:
            raise XcursorError(f"{path} 里没有解析出任何图像")
        return cls(path, images)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<CursorFile {self.path.name} sizes={self.sizes} frames={self.frame_count}>"


def write_xcursor(
    path: str | Path,
    groups: dict[int, Sequence[tuple[int, int, int, int, int, bytes]]],
) -> None:
    """写出一个 Xcursor 文件。

    groups: {标称尺寸: [(width, height, xhot, yhot, delay, rgba), ...]}
    """
    path = Path(path)
    toc: list[tuple[int, int, int]] = []
    chunks: list[bytes] = []
    offset = HEADER_SIZE  # TOC 紧跟在头部之后

    ntoc = sum(len(v) for v in groups.values())
    offset += ntoc * 12

    for nominal in sorted(groups):
        for (w, h, xh, yh, delay, rgba) in groups[nominal]:
            if len(rgba) != w * h * 4:
                raise ValueError(f"像素数据长度不符: {len(rgba)} != {w*h*4}")
            body = _premultiply(rgba, w, h)
            chunk = struct.pack(
                "<IIIIIIIII", IMAGE_HEADER_SIZE, IMAGE_TYPE, nominal, 1, w, h, xh, yh, delay
            ) + body
            toc.append((IMAGE_TYPE, nominal, offset))
            chunks.append(chunk)
            offset += len(chunk)

    out = bytearray()
    out += struct.pack("<4sIII", MAGIC, HEADER_SIZE, VERSION, ntoc)
    for ctype, subtype, pos in toc:
        out += struct.pack("<III", ctype, subtype, pos)
    for c in chunks:
        out += c

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))


def probe_sizes(path: str | Path) -> list[int]:
    """轻量探测：只返回标称尺寸列表，不解码像素。"""
    return sorted(probe_size_map(path))


def probe_size_map(path: str | Path) -> dict[int, tuple[int, int]]:
    """只读 TOC 和图片头，返回 {标称尺寸: (实际宽, 实际高)} —— 不解码像素。

    为什么需要它：**标称尺寸不等于实际像素宽度**。有些主题（尤其是用非标准
    工具生成的）会在标称 96 的档位里塞 128x128 的图。要按显示尺寸精确选档，
    就必须看真实的 width/height，而不是标称值。
    """
    try:
        data = Path(path).read_bytes()
    except OSError:
        return {}
    if len(data) < HEADER_SIZE or data[:4] != MAGIC:
        return {}
    _m, _h, _v, ntoc = struct.unpack_from("<4sIII", data, 0)
    out: dict[int, tuple[int, int]] = {}
    for i in range(ntoc):
        ctype, subtype, pos = struct.unpack_from("<III", data, HEADER_SIZE + i * 12)
        if ctype != IMAGE_TYPE or pos + IMAGE_HEADER_SIZE > len(data):
            continue
        # 图片头偏移: 0=header_size 4=type 8=subtype(标称) 12=version 16=width 20=height
        inominal, _iver, w, h = struct.unpack_from("<IIII", data, pos + 8)
        key = inominal or subtype
        # 同档多帧取最大的那张，保证不会低估
        prev = out.get(key)
        if prev is None or w * h > prev[0] * prev[1]:
            out[key] = (w, h)
    return out
