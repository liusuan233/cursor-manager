"""命令行界面 —— 不导入 GTK，所以无图形环境下也能用。

    cursor-manager --list
    cursor-manager --import <路径> [选项]
    cursor-manager apply <主题名> [尺寸]
    cursor-manager remove <主题名> [选项]
    cursor-manager --help

`dispatch()` 返回 None 表示"不是命令行子命令"，交给 GUI 处理。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .apply import apply_theme, read_state
from .importer import DEFAULT_SIZES, import_pack
from .themes import delete_theme, deletion_blockers, scan_themes, theme_size
from .update import check as check_update

USAGE = f"""cursor-manager {__version__} —— Linux 光标主题管理器（niri / Wayland）

用法:
  cursor-manager                          打开图形界面
  cursor-manager --list                   列出已安装的光标主题
  cursor-manager --import <路径> [选项]    导入 Windows 光标包
  cursor-manager apply <主题名> [尺寸]     切换光标主题（命令行）
  cursor-manager remove <主题名> [选项]    删除光标主题
  cursor-manager --check-update           检查 GitHub 上有没有新版本

导入选项:
  --name NAME       指定主题名（默认取包内 .inf 的 SCHEME_NAME）
  --sizes 24,48,96  要生成的尺寸档（默认 {",".join(map(str, DEFAULT_SIZES))}，总是包含原始分辨率）
  --overwrite       覆盖同名主题
  --apply           导入后立即应用到系统

apply 选项:
  --dry-run         只报告会改哪些文件，不真的写

remove 选项:
  --purge           永久删除（默认是移入回收站，可恢复）
  --dry-run         只显示会删什么、有多大，不动文件
  --yes             跳过确认（非交互环境下必须显式给出）

  <路径> 可以是：文件夹 / .zip / .tar.gz / .tar.xz / .tar.bz2 / 单个 .ani/.cur/.ico

  只有用户目录下的主题可以删除；系统主题（/usr/share/icons）和当前正在生效的主题会被拒绝。
"""


# ---------------------------------------------------------------------------

def cmd_list(verbose: bool = False) -> int:
    themes = scan_themes(with_sizes=verbose)
    if not themes:
        print("没有找到任何光标主题。")
        return 1
    valid = sum(1 for t in themes if t.valid)
    for t in themes:
        mark = "\033[32m✔\033[0m" if t.valid else "\033[31m✘\033[0m"
        if t.valid:
            extra = f"{len(t.cursors):>3} 光标 / {t.alias_count:>3} 别名"
            if verbose and t.cursors:
                sizes: set[int] = set()
                for c in t.cursors:
                    sizes.update(c.sizes)
                if sizes:
                    extra += f" / 尺寸 {min(sizes)}–{max(sizes)}"
        else:
            extra = t.reason
        print(f"  {mark} {t.name:<26} {extra}")
        if verbose:
            print(f"      {t.path}")

    active = read_state().get("niri") or read_state().get("gtk3") or "?"
    print(f"\n  共 {len(themes)} 个主题（{valid} 个可用），当前生效: {active}")
    bad = [t for t in themes if not t.valid]
    if bad:
        print(f"  \033[33m✘ 标记的主题布局不合法，Wayland 客户端会忽略它们并回落到 Adwaita。\033[0m")
        print(f"    需要把光标文件放进该目录的 cursors/ 子目录里。")
    return 0


def cmd_import(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="cursor-manager --import", add_help=False)
    p.add_argument("path")
    p.add_argument("--name")
    p.add_argument("--sizes", default=",".join(map(str, DEFAULT_SIZES)))
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--apply", action="store_true")
    try:
        args = p.parse_args(argv)
    except SystemExit:
        print(USAGE)
        return 2

    try:
        sizes = tuple(sorted({int(s) for s in args.sizes.split(",") if s.strip()}))
    except ValueError:
        print("\033[31m--sizes 只能是逗号分隔的数字，例如 --sizes 24,32,48\033[0m")
        return 2
    if not sizes:
        sizes = DEFAULT_SIZES

    path = Path(args.path).expanduser()
    tty = sys.stdout.isatty()
    print(f"导入 {path}")
    print(f"  目标尺寸档: {', '.join(map(str, sizes))}")

    def progress(i: int, n: int, name: str) -> None:
        if not tty:
            return
        filled = i * 20 // max(n, 1)
        bar = "█" * filled + "░" * (20 - filled)
        print(f"\r  [{bar}] {i}/{n}  {name:<22}", end="", flush=True)

    try:
        res = import_pack(path, sizes=sizes, name=args.name,
                          overwrite=args.overwrite, progress=progress)
    except Exception as e:  # noqa: BLE001 —— CLI 顶层，直接报给用户
        print(f"\n\033[31m导入失败: {type(e).__name__}: {e}\033[0m")
        return 1

    if tty:
        print()
    info = res.inspection
    print(f"\n\033[32m✔ 导入完成\033[0m   {info.name}")
    print(f"  原名      : {info.raw_name}"
          + ("（主题名已清洗成 ASCII 安全）" if info.raw_name != info.name else ""))
    print(f"  角色映射  : {'包内 .inf（官方数据）' if info.used_inf else '按文件名推断'}")
    print(f"  识别角色  : {len(info.roles)} 个 / {info.total_frames} 帧 / "
          f"{info.animated_count} 个动画")
    if info.native_sizes:
        print("  原始分辨率: " + "、".join(f"{w}×{h}" for w, h in info.native_sizes))
    print(f"  转出      : {res.files_written} 个光标文件 + {res.aliases_created} 个别名"
          f"（{res.bytes_written / 1024 / 1024:.1f} MB）")
    print(f"  安装位置  : {res.dest}")
    for w in info.warnings:
        print(f"  \033[33m⚠ {w}\033[0m")
    if info.unmapped:
        print(f"  \033[33m⚠ 未识别 {len(info.unmapped)} 个文件: "
              + ", ".join(f.name for f in info.unmapped[:6]) + "\033[0m")
    for e in res.errors:
        print(f"  \033[31m✘ {e}\033[0m")

    if args.apply:
        return cmd_apply([info.name], quiet=False)
    print("\n下一步 —— 应用它:")
    print(f"  cursor-manager apply {info.name} 24")
    return 0 if res.ok else 1


def cmd_apply(argv: list[str], quiet: bool = True) -> int:
    p = argparse.ArgumentParser(prog="cursor-manager apply", add_help=False)
    p.add_argument("theme")
    p.add_argument("size", nargs="?", type=int, default=24)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true",
                   help="即使主题布局不合法也照样写配置")
    try:
        args = p.parse_args(argv)
    except SystemExit:
        print(USAGE)
        return 2

    themes = {t.name: t for t in scan_themes()}
    t = themes.get(args.theme)
    if t is None:
        print(f"\033[31m找不到主题 '{args.theme}'\033[0m")
        near = [n for n in themes if args.theme.lower() in n.lower()]
        if near:
            print("  你是不是想要: " + ", ".join(near[:5]))
        print("  用 cursor-manager --list 看全部可用主题。")
        return 1
    if not t.valid and not args.force:
        print(f"\033[31m主题 '{t.name}' 布局不合法，装上去也不会生效:\033[0m\n  {t.reason}")
        print("  确认要用就加 --force。")
        return 1

    if not 8 <= args.size <= 512:
        print(f"\033[31m尺寸 {args.size} 不合理（应在 8–512 之间）\033[0m")
        return 1

    verb = "试运行" if args.dry_run else "应用"
    print(f"{verb}: {t.name} / {args.size}px")
    res = apply_theme(t.name, args.size, dry_run=args.dry_run)
    for r in res.reports:
        print(f"  {r}")
    if not res.ok:
        print(f"\n\033[31m{len(res.failures)} 处失败\033[0m")
        return 1

    if not args.dry_run:
        print("\n生效方式:")
        print("  · niri 保存配置后会自动重载 —— 桌面光标立即变化")
        print("  · 新启动的程序立即生效；已开着的程序需要重启")
        print("  · 想让环境变量对所有新进程一致，最稳是注销重登一次会话")
    return 0


# ---------------------------------------------------------------------------

def cmd_check_update(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="cursor-manager --check-update", add_help=False)
    p.add_argument("--quiet", "-q", action="store_true")
    p.add_argument("--notes", action="store_true", help="同时打印新版本的发布说明")
    try:
        args = p.parse_args(argv)
    except SystemExit:
        print(USAGE)
        return 2

    if not args.quiet:
        print("正在检查 GitHub 上的最新版本…")
    info = check_update()

    if info.error:
        print(f"\033[31m{info.summary}\033[0m")
        print("  （检查更新失败不影响任何本地功能）")
        return 1

    if info.has_update:
        print(f"\033[33m⬆ {info.summary}\033[0m")
        if info.published:
            print(f"  发布于: {info.published}")
        print(f"  发布页: {info.url}")
        print()
        print("  怎么升级（本工具不会自动更新）:")
        print("    Arch Linux : sudo pacman -U <新的 .pkg.tar.zst>")
        print("    源码 / 其他: 重新下载源码包，解包后 ./install.sh")
        print("    pipx       : pipx install --force <新的 .whl>")
        if args.notes and info.notes:
            print()
            print("  ── 发布说明（节选）" + "─" * 30)
            for line in info.notes.splitlines()[:40]:
                print(f"  {line}")
        return 0

    print(f"\033[32m✔ {info.summary}\033[0m")
    if info.url and not args.quiet:
        print(f"  最新发布页: {info.url}")
    return 0


# ---------------------------------------------------------------------------

def _human(n: int) -> str:
    v = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if v < 1024 or unit == "GB":
            return f"{v:.0f} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1024
    return f"{n} B"


def cmd_remove(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="cursor-manager remove", add_help=False)
    p.add_argument("theme")
    p.add_argument("--purge", action="store_true",
                   help="永久删除（默认移入回收站，可恢复）")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--yes", "-y", action="store_true")
    try:
        args = p.parse_args(argv)
    except SystemExit:
        print(USAGE)
        return 2

    themes = {t.name: t for t in scan_themes()}
    t = themes.get(args.theme)
    if t is None:
        print(f"\033[31m找不到主题 '{args.theme}'\033[0m")
        near = [n for n in themes if args.theme.lower() in n.lower()]
        if near:
            print("  你是不是想要: " + ", ".join(near[:5]))
        print("  用 cursor-manager --list 看全部可用主题。")
        return 1

    active = read_state().get("niri") or read_state().get("gtk3") or ""
    blockers = deletion_blockers(t, active=active)
    if blockers:
        print(f"\033[31m不能删除 '{t.name}':\033[0m")
        for b in blockers:
            print(f"  · {b}")
        return 1

    size, files = theme_size(t.path)
    action = "永久删除" if args.purge else "移入回收站"
    print(f"{action}: {t.name}")
    print(f"  路径: {t.path}")
    print(f"  内容: {files} 个文件，{_human(size)}")
    if args.purge:
        print("  \033[33m⚠ --purge 是永久删除，无法从回收站恢复\033[0m")

    if args.dry_run:
        print("\n（--dry-run，没有改动任何文件）")
        return 0

    if not args.yes:
        if not sys.stdin.isatty():
            print("\n\033[31m非交互环境，需要显式加 --yes 才会真的删除\033[0m")
            return 1
        try:
            ans = input(f"确认{action}？输入 yes 继续: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消")
            return 1
        if ans not in ("yes", "y"):
            print("已取消")
            return 1

    rep = delete_theme(t, active=active, purge=args.purge)
    if not rep.ok:
        print(f"\033[31m删除失败: {rep.detail}\033[0m")
        return 1
    print(f"\n\033[32m✔ {rep.detail}\033[0m")
    if not args.purge and rep.moved_to:
        print(f"  位置: {rep.moved_to}")
        print("  要恢复：从文件管理器的回收站还原即可")
    return 0


# ---------------------------------------------------------------------------

def dispatch(argv: list[str]) -> int | None:
    """处理命令行子命令。返回 None 表示"交给 GUI"。"""
    args = argv[1:]
    if not args:
        return None

    head = args[0]
    if head in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    if head in ("-V", "--version", "version"):
        print(f"cursor-manager {__version__}")
        return 0
    if head == "--list":
        return cmd_list(verbose="-v" in args or "--verbose" in args)
    if head == "--import":
        return cmd_import(args[1:])
    if head in ("--check-update", "check-update", "update"):
        return cmd_check_update(args[1:])
    if head == "apply":
        return cmd_apply(args[1:])
    if head in ("remove", "rm", "delete", "uninstall-theme"):
        return cmd_remove(args[1:])
    if head.startswith("-"):
        print(f"\033[31m未知选项: {head}\033[0m\n")
        print(USAGE)
        return 2
    # 既不是选项也不是子命令 —— 当作主题名，等价于 apply
    return cmd_apply(args)
