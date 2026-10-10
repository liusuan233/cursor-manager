"""Windows 光标格式解析：.ani（动画光标）/ .cur（光标）/ .ico（图标）。

Windows 的三种文件其实是同一套容器套娃：

    .ani  = RIFF/ACON 容器，里面塞了 nFrames 个完整的 .cur/.ico 文件
    .cur  = ICONDIR 容器，type=2，每个条目带热点坐标
    .ico  = ICONDIR 容器，type=1，没有热点

Windows 光标主题包（微软官方那种）就是一个文件夹里放 17 个 .ani/.cur，
外加一个 .inf 说明哪个文件对应哪个系统角色。

本模块只负责"读"，不含角色映射 —— 那是 importer.py 的事。
"""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass, field
from pathlib import Path

try:
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None


class CursorParseError(Exception):
    """Windows 光标文件解析失败。"""


# ---------------------------------------------------------------------------
# ICONDIR / ICONDIRENTRY
# ---------------------------------------------------------------------------

ICONDIR_SIZE = 6
ICONDIRENTRY_SIZE = 16
ICON_TYPE_ICO = 1
ICON_TYPE_CUR = 2


@dataclass(slots=True)
class IconEntry:
    """ICONDIR 里的一个条目。

    注意 bpp 字段的语义随容器类型而变：
      · ICO(type=1)：wBitCount 就是**位深**
      · CUR(type=2)：wPlanes 和 wBitCount 被**复用成热点坐标**
        （wPlanes=热点X，wBitCount=热点Y）—— 所以此时 bpp 不是位深。
    真正的位深得从 DIB 头里读（见 decode_dib）。
    """

    width: int
    height: int
    bpp: int
    size: int
    offset: int
    hotspot_x: int = 0
    hotspot_y: int = 0
    is_cursor: bool = False

    @property
    def pixels(self) -> int:
        return self.width * self.height


def parse_icondir(data: bytes) -> tuple[int, list[IconEntry]]:
    """解析 ICONDIR，返回 (type, entries)。"""
    if len(data) < ICONDIR_SIZE:
        raise CursorParseError("文件太小，不是 ICONDIR")
    reserved, itype, count = struct.unpack_from("<HHH", data, 0)
    if reserved != 0 or itype not in (ICON_TYPE_ICO, ICON_TYPE_CUR):
        raise CursorParseError(f"不是 .ico/.cur（reserved={reserved} type={itype}）")
    if count == 0 or ICONDIR_SIZE + count * ICONDIRENTRY_SIZE > len(data):
        raise CursorParseError(f"ICONDIR 条目数异常: {count}")

    entries: list[IconEntry] = []
    for i in range(count):
        off = ICONDIR_SIZE + i * ICONDIRENTRY_SIZE
        w, h, colors, _res, planes, bitcount, size, offset = struct.unpack_from("<BBBBHHII", data, off)
        # 宽高为 0 表示 256
        w = w or 256
        h = h or 256
        if itype == ICON_TYPE_CUR:
            # CUR 里这两个字段被复用成热点坐标
            entries.append(IconEntry(w, h, bitcount, size, offset, planes, bitcount, True))
        else:
            entries.append(IconEntry(w, h, bitcount, size, offset, w // 2, h // 2, False))
    return itype, entries


def extract_single_entry(data: bytes, entry: IconEntry, itype: int) -> bytes:
    """把多条目容器重建成"只有这一条"的最小容器。

    这样就能让 Pillow 精确解码指定条目，而不是它自己挑"最大"的那个。
    """
    image = data[entry.offset : entry.offset + entry.size]
    if len(image) != entry.size:
        raise CursorParseError("条目数据越界，文件可能损坏")

    def dim(v: int) -> int:
        return 0 if v >= 256 else v

    out = bytearray()
    out += struct.pack("<HHH", 0, itype, 1)
    if itype == ICON_TYPE_CUR:
        out += struct.pack(
            "<BBBBHHII",
            dim(entry.width), dim(entry.height), 0, 0,
            entry.hotspot_x & 0xFFFF, entry.hotspot_y & 0xFFFF,
            entry.size, ICONDIR_SIZE + ICONDIRENTRY_SIZE,
        )
    else:
        out += struct.pack(
            "<BBBBHHII",
            dim(entry.width), dim(entry.height), 0, 0, 1, entry.bpp,
            entry.size, ICONDIR_SIZE + ICONDIRENTRY_SIZE,
        )
    out += image
    return bytes(out)


def decode_dib(image: bytes) -> tuple[int, int, int, bytes] | None:
    """解码 CUR/ICO 里的 DIB，返回 (width, height, bpp, rgba)；不支持时返回 None。

    自己写而不用 Pillow 的原因：**Pillow 对 8bpp 调色板图像（mode=P）不会应用
    AND 掩码**，会把整个光标变成不透明的黑底方块。而 8bpp / 24bpp 的调色板光标
    在老式 Windows 光标包里非常普遍 —— 用户导入「初音未来」主题时踩到的就是这个。

    只处理无压缩（BI_RGB）和 BI_BITFIELDS；RLE 之类交给调用方退回 Pillow。
    """
    if len(image) < 40:
        return None

    header_size, w, h2, _planes, bpp, compression = struct.unpack_from("<IiiHHI", image, 0)
    if header_size < 40 or compression not in (0, 3):
        return None
    if bpp not in (1, 4, 8, 24, 32):
        return None
    if not (0 < w <= 1024) or h2 == 0:
        return None

    top_down = h2 < 0
    h = abs(h2) // 2                     # DIB 高度 = XOR 位图 + AND 掩码，两倍
    if not (0 < h <= 1024):
        return None

    # 调色板紧跟在头之后，每项 4 字节（B, G, R, 保留）
    palette: list[tuple[int, int, int]] = []
    if bpp <= 8:
        n_colors = 1 << bpp
        clr_used = struct.unpack_from("<I", image, 32)[0]
        if clr_used:
            n_colors = min(clr_used, 1 << bpp)
        pal_off = header_size
        if pal_off + n_colors * 4 > len(image):
            return None
        for i in range(n_colors):
            b, g, r, _ = image[pal_off + i * 4 : pal_off + i * 4 + 4]
            palette.append((r, g, b))
        xor_off = pal_off + n_colors * 4
    else:
        xor_off = header_size

    xor_stride = ((w * bpp + 31) // 32) * 4
    mask_stride = ((w + 31) // 32) * 4
    xor_len = xor_stride * h
    if xor_off + xor_len > len(image):
        return None
    xor = image[xor_off : xor_off + xor_len]
    mask = image[xor_off + xor_len : xor_off + xor_len + mask_stride * h]
    has_mask = len(mask) >= mask_stride * h

    # 32bpp 且有真实 alpha 通道时，以 alpha 为准（Windows 行为，AND 掩码忽略）。
    # 但有些包把 32bpp 的 alpha 全填 0、只用 AND 掩码 —— 这时把掩码当作透明来源。
    use_mask = has_mask
    if bpp == 32 and has_mask:
        alphas = xor[3::4]
        if any(alphas):                  # 有真实 alpha
            use_mask = False

    out = bytearray(w * h * 4)
    for y in range(h):
        src_y = y if top_down else (h - 1 - y)
        row = xor[src_y * xor_stride : (src_y + 1) * xor_stride]
        mrow = mask[src_y * mask_stride : (src_y + 1) * mask_stride] if use_mask else b""
        for x in range(w):
            if bpp == 32:
                si = x * 4
                # DIB 里是 B,G,R,A —— 第一个字节是蓝不是红
                b, g, r, a = row[si], row[si + 1], row[si + 2], row[si + 3]
            elif bpp == 24:
                si = x * 3
                b, g, r = row[si], row[si + 1], row[si + 2]
                a = 255
            else:
                if bpp == 8:
                    idx = row[x]
                elif bpp == 4:
                    idx = (row[x // 2] >> (4 if x % 2 == 0 else 0)) & 0x0F
                else:                    # 1bpp
                    idx = (row[x // 8] >> (7 - x % 8)) & 1
                r, g, b = palette[idx] if idx < len(palette) else (0, 0, 0)
                a = 255

            if use_mask and (mrow[x // 8] >> (7 - x % 8)) & 1:
                a = 0

            di = (y * w + x) * 4
            out[di : di + 4] = bytes((r, g, b, a))

    return w, h, bpp, bytes(out)


@dataclass(slots=True)
class DecodedImage:
    width: int
    height: int
    hotspot_x: int
    hotspot_y: int
    rgba: bytes
    bpp: int = 32


def decode_cur_image(data: bytes, pick: str = "best") -> DecodedImage:
    """解码 .cur/.ico 的一个条目。

    解码顺序：**先用自己的 decode_dib**（正确处理 8/24bpp 的 AND 掩码），
    只有遇到它处理不了的编码（RLE 压缩等）才退回 Pillow。
    反过来的话，调色板光标会变成不透明的黑底方块 —— Pillow 对 mode=P 不套掩码。
    """
    itype, entries = parse_icondir(data)
    if not entries:
        raise CursorParseError("没有任何图标条目")

    entry = max(entries, key=lambda e: e.pixels)
    raw_image = data[entry.offset : entry.offset + entry.size]

    got = decode_dib(raw_image)
    if got is not None:
        w, h, bpp, rgba = got
        return DecodedImage(w, h, entry.hotspot_x, entry.hotspot_y, rgba, bpp)

    # 后备：Pillow（能处理 RLE 等冷门编码，但对调色板图像不套 AND 掩码）
    if Image is not None:
        try:
            single = extract_single_entry(data, entry, itype)
            with Image.open(io.BytesIO(single)) as im:
                im = im.convert("RGBA")
                return DecodedImage(im.size[0], im.size[1], entry.hotspot_x,
                                    entry.hotspot_y, im.tobytes(), 32)
        except Exception:
            pass

    raise CursorParseError(
        f"无法解码 {entry.width}x{entry.height} 的图标（不支持的位深或压缩方式）"
        + ("；装个 Pillow 可以覆盖更多格式: pip install pillow" if Image is None else "")
    )


# ---------------------------------------------------------------------------
# ANI (RIFF / ACON)
# ---------------------------------------------------------------------------

AF_ICON = 0x01          # 帧是 ICO 而不是 CUR（没有热点，用中心点）
AF_SEQUENCE = 0x02      # 用 seq 块里的帧顺序

ANIH_SIZE = 36
JIFFY_MS = 1000.0 / 60.0


@dataclass
class AniFile:
    """一个 .ani 动画光标。"""

    frames: list[bytes] = field(default_factory=list)     # 每个是完整的 .cur/.ico 数据
    rates: list[int] = field(default_factory=list)        # 每帧延时（jiffies）
    sequence: list[int] = field(default_factory=list)     # 播放顺序（帧下标）
    steps: int = 0
    width: int = 0
    height: int = 0
    bit_count: int = 0
    planes: int = 0
    disp_rate: int = 0
    flags: int = 0

    @property
    def is_icon_frames(self) -> bool:
        return bool(self.flags & AF_ICON)

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    @property
    def animated(self) -> bool:
        return len(self.play_order) > 1

    @property
    def play_order(self) -> list[int]:
        """实际播放顺序。"""
        if self.sequence and (self.flags & AF_SEQUENCE):
            return [i for i in self.sequence if 0 <= i < len(self.frames)] or list(range(len(self.frames)))
        return list(range(len(self.frames)))

    def delay_ms(self, step: int) -> int:
        """第 step 步的延时（毫秒）。"""
        jiffies = 0
        if self.rates and step < len(self.rates):
            jiffies = self.rates[step]
        elif self.disp_rate:
            jiffies = self.disp_rate
        if jiffies <= 0:
            jiffies = 6                                    # Windows 默认 6 jiffies ≈ 100ms
        return max(10, int(round(jiffies * JIFFY_MS)))

    def decode_frames(self) -> list[DecodedImage]:
        """按播放顺序解码所有帧。

        注意：**不要**因为 bfAttributes 里有 AF_ICON 就覆盖热点。
        实测（KINOMOTO SAKURA 那套 17 个 .ani）AF_ICON 全部置位，但内嵌容器
        都是真正的 CUR(type 2)，带各自的热点 —— 覆盖成中心点会让指针点偏。
        热点该由 parse_icondir 按容器类型决定：CUR 用容器里的值，ICO 才用中心。
        """
        return [decode_cur_image(self.frames[idx]) for idx in self.play_order]


def parse_ani(data: bytes) -> AniFile:
    """解析 RIFF/ACON 动画光标。"""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"ACON":
        raise CursorParseError("不是 RIFF/ACON 动画光标")

    ani = AniFile()
    pos = 12
    n_frames_declared = None

    while pos + 8 <= len(data):
        cid = data[pos : pos + 4]
        csize = struct.unpack_from("<I", data, pos + 4)[0]
        body = pos + 8
        end = body + csize
        if end > len(data):
            break

        if cid == b"anih" and csize >= ANIH_SIZE:
            (_cbsize, n_frames, n_steps, w, h, bits, planes, disp, flags) = struct.unpack_from(
                "<IIIIIIIII", data, body
            )
            n_frames_declared = n_frames
            ani.steps = n_steps
            ani.width, ani.height = w, h
            ani.bit_count, ani.planes = bits, planes
            ani.disp_rate, ani.flags = disp, flags
        elif cid == b"rate":
            ani.rates = list(struct.unpack_from(f"<{csize // 4}I", data, body))
        elif cid == b"seq ":
            ani.sequence = list(struct.unpack_from(f"<{csize // 4}I", data, body))
        elif cid == b"LIST" and data[body : body + 4] == b"fram":
            sub = body + 4
            while sub + 8 <= end:
                sid = data[sub : sub + 4]
                ssize = struct.unpack_from("<I", data, sub + 4)[0]
                sbody = sub + 8
                if sbody + ssize > end:
                    break
                if sid == b"icon":
                    ani.frames.append(data[sbody : sbody + ssize])
                sub = sbody + ssize + (ssize & 1)          # RIFF 块按偶数字节对齐

        pos = end + (csize & 1)

    if not ani.frames:
        raise CursorParseError("ACON 里没有找到任何帧")
    if n_frames_declared and n_frames_declared != len(ani.frames):
        # 以实际解析到的为准，但记下来
        ani.steps = ani.steps or n_frames_declared
    ani.steps = ani.steps or len(ani.frames)
    return ani


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------

WINDOWS_SUFFIXES = {".ani", ".cur", ".ico"}


def is_windows_cursor(path: str | Path) -> bool:
    return Path(path).suffix.lower() in WINDOWS_SUFFIXES


def load_windows_cursor(path: str | Path) -> tuple[list[DecodedImage], list[int]]:
    """读一个 Windows 光标文件，返回 (帧列表, 每帧延时ms)。

    .ani 走 ACON 解析，.cur/.ico 当作单帧处理。
    """
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as e:
        raise CursorParseError(f"无法读取 {path}: {e}") from e

    if data[:4] == b"RIFF" and data[8:12] == b"ACON":
        ani = parse_ani(data)
        frames = ani.decode_frames()
        delays = [ani.delay_ms(i) for i in range(len(frames))]
        return frames, delays

    img = decode_cur_image(data)
    return [img], [0]
