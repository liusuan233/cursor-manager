#!/usr/bin/env python3
"""删除功能自检：闸门、回收站规范、永久删除、干跑。

全程在**伪造的 HOME** 里跑，绝不碰真实系统。注意：`HOME` 能隔离文件路径，
但隔离不了 gsettings（dconf 由会话总线上的服务写）—— 所以这里只测文件操作，
不碰 apply。

覆盖：
  1. 四道闸门：系统目录 / 当前生效 / 不像主题 / 正常可删
  2. 回收站：目录真的被移走、.trashinfo 格式合规、Path 可还原
  3. 重名冲突自动加后缀
  4. --purge 永久删除
  5. --dry-run 不动任何文件
  6. 路径边界：同前缀的兄弟目录不会被误判为"在允许目录内"
退出码 0 = 全部通过。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []
CHECKS = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    mark = "\033[32m✓\033[0m" if cond else "\033[31m✘\033[0m"
    print(f"  {mark} {label}" + (f"  — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(label)


def section(t: str) -> None:
    print(f"\n=== {t} ===")


def setup_fake_home(tmp: Path) -> tuple[Path, Path]:
    """把 HOME/XDG_DATA_HOME 指向临时目录，返回 (home, icons_dir)。"""
    home = tmp / "home"
    (home / ".local/share").mkdir(parents=True, exist_ok=True)
    os.environ["HOME"] = str(home)
    os.environ["XDG_DATA_HOME"] = str(home / ".local/share")
    return home, home / ".local/share/icons"


def make_theme(icons: Path, name: str, cursors: int = 3) -> Path:
    d = icons / name
    (d / "cursors").mkdir(parents=True, exist_ok=True)
    (d / "index.theme").write_text(f"[Icon Theme]\nName={name}\n", encoding="utf-8")
    for i in range(cursors):
        (d / "cursors" / f"cursor{i}").write_bytes(b"x" * 512)
    return d


def main() -> int:
    print("删除功能自检\n" + "=" * 56)

    with tempfile.TemporaryDirectory(prefix="cursorkit-del-") as td:
        home, icons = setup_fake_home(Path(td))
        icons.mkdir(parents=True, exist_ok=True)

        # 环境变量必须在导入前设好（apply.py 在 import 时算路径）
        from cursorkit.themes import (  # noqa: E402
            Theme, delete_theme, deletion_blockers, theme_size, user_theme_roots,
        )

        # ---------------------------------------------------------------- 1
        section("1. 四道闸门")
        p = make_theme(icons, "UserTheme")
        mine = Theme(name="UserTheme", path=p, root=icons, valid=True)
        check("用户目录 + 没在用 → 可删", not deletion_blockers(mine, active=""))

        check("当前生效 → 拒绝",
              any("当前生效" in r for r in deletion_blockers(mine, active="UserTheme")),
              "；".join(deletion_blockers(mine, active="UserTheme")))

        sys_theme = Theme(name="Adwaita", path=Path("/usr/share/icons/Adwaita"),
                          root=Path("/usr/share/icons"), valid=True)
        check("系统目录 → 拒绝",
              any("系统目录" in r for r in deletion_blockers(sys_theme)),
              "；".join(deletion_blockers(sys_theme)))

        notheme = Theme(name="Random", path=icons / "Empty", root=icons, valid=False)
        (icons / "Empty").mkdir(exist_ok=True)
        check("不像主题的目录 → 拒绝",
              any("不像光标主题" in r for r in deletion_blockers(notheme)),
              "；".join(deletion_blockers(notheme)))

        # 路径边界：/…/icons-evil 不能被当成 /…/icons 里面
        evil = Theme(name="Evil", path=icons.parent / "icons-evil", root=icons.parent, valid=True)
        (icons.parent / "icons-evil").mkdir(exist_ok=True)
        check("同前缀的兄弟目录不被误判（icons-evil ⊄ icons）",
              any("系统目录" in r for r in deletion_blockers(evil)),
              "；".join(deletion_blockers(evil)) or "竟然放行了")

        check("user_theme_roots 就是那两个用户目录",
              [str(r) for r in user_theme_roots()] ==
              [str(icons), str(Path(os.environ["HOME"]) / ".icons")],
              str([str(r) for r in user_theme_roots()]))

        # ---------------------------------------------------------------- 2
        section("2. 回收站")
        size, files = theme_size(p)
        # 3 个光标文件（各 512 字节）+ index.theme（它自己也有大小）
        check("大小统计合理", size >= 512 * 3 and files == 4,
              f"{files} 个文件，{size} 字节（3×512 + index.theme）")

        rep = delete_theme(mine)
        check("删除成功", rep.ok and rep.method == "trash", rep.detail)
        check("原目录已移走", not p.exists())
        check("落在 Trash/files/ 下", rep.moved_to is not None and rep.moved_to.is_dir(),
              str(rep.moved_to))

        ti = home / ".local/share/Trash/info" / f"{rep.moved_to.name}.trashinfo"
        check(".trashinfo 已生成", ti.is_file())
        text = ti.read_text(encoding="utf-8") if ti.is_file() else ""
        check("trashinfo 有 [Trash Info] 段", text.startswith("[Trash Info]"))
        check("trashinfo 含 DeletionDate", "DeletionDate=" in text)
        recorded = next((l[5:] for l in text.splitlines() if l.startswith("Path=")), "")
        check("Path 解码后等于原路径", unquote(recorded) == str(p),
              f"记录 {unquote(recorded)}")
        check("回收站里文件还在（可恢复）", (rep.moved_to / "cursors" / "cursor0").is_file())

        # ---------------------------------------------------------------- 3
        section("3. 重名冲突")
        p2 = make_theme(icons, "UserTheme")
        rep2 = delete_theme(Theme(name="UserTheme", path=p2, root=icons, valid=True))
        check("第二次删除自动加后缀", rep2.ok and rep2.moved_to.name != rep.moved_to.name,
              f"{rep.moved_to.name} vs {rep2.moved_to.name}")

        # ---------------------------------------------------------------- 4
        section("4. 永久删除")
        p3 = make_theme(icons, "PurgeMe")
        rep3 = delete_theme(Theme(name="PurgeMe", path=p3, root=icons, valid=True), purge=True)
        check("purge 成功", rep3.ok and rep3.method == "purge", rep3.detail)
        check("原目录已消失", not p3.exists())
        check("没有进回收站",
              not any(f.name == "PurgeMe" for f in (home / ".local/share/Trash/files").iterdir()))

        # ---------------------------------------------------------------- 5
        section("5. 干跑")
        p4 = make_theme(icons, "DryRun")
        before = sorted(f.name for f in icons.iterdir())
        rep4 = delete_theme(Theme(name="DryRun", path=p4, root=icons, valid=True), dry_run=True)
        check("干跑报告 ok", rep4.ok and rep4.method == "dry-run", rep4.detail)
        check("原目录原封不动", p4.is_dir())
        check("没有产生回收站条目",
              sorted(f.name for f in icons.iterdir()) == before)

        # ---------------------------------------------------------------- 6
        section("6. 闸门拒绝时确实不动手")
        p5 = make_theme(icons, "Active1")
        rep5 = delete_theme(Theme(name="Active1", path=p5, root=icons, valid=True),
                            active="Active1")
        check("当前生效的主题被拒绝", not rep5.ok, rep5.detail)
        check("文件原封不动", p5.is_dir())

    print("\n" + "=" * 56)
    if FAILURES:
        print(f"\033[31m{len(FAILURES)}/{CHECKS} 项失败:\033[0m")
        for f in FAILURES:
            print("   -", f)
        return 1
    print(f"\033[32m全部 {CHECKS} 项检查通过\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
