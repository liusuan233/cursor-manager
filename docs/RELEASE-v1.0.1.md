# v1.0.1

新增**删除主题**功能，并修掉两个 v1.0.0 里的问题。

## 新增：删除主题

只能浏览、切换、导入确实不算"管理"，这个版本补上了删除。

因为这个操作不可逆，加了**四道安全闸门**：

| 闸门 | 行为 |
|---|---|
| 只删用户目录 | `/usr/share/icons` 里的系统主题会被拒绝，提示改用发行版包管理器 |
| 不删正在使用的 | 删掉当前主题会让所有程序**静默回落到 Adwaita**（正是本项目要防的坑），会拒绝并让你先切换 |
| 必须"长得像主题" | 既没有 `cursors/` 也没有 `index.theme` 就拒绝，防止传错路径 |
| 默认进回收站 | 按 freedesktop 规范写 `.trashinfo`，可从文件管理器恢复；`--purge` 才是真删 |

```bash
cursor-manager remove <主题名>              # 移入回收站
cursor-manager remove <主题名> --purge      # 永久删除
cursor-manager remove <主题名> --dry-run    # 只看会删什么、多大
```

图形界面里头部多了删除按钮：**删不动的时候按钮直接禁用**，悬停会说明原因
（"位于系统目录…" / "它正是当前生效的主题…"），不会让你点了才被拒。

## 修复

**在非 niri 的机器上凭空创建 `~/.config/niri/`。**
v1.0.0 的 `apply` 无条件创建 `~/.config/niri/dms/` 再写文件。
GNOME / KDE / X11 用户跑一次 `apply`，家目录里就多出一个 `niri/` 目录。
现在会先检查 `~/.config/niri/` 是否存在，不存在则跳过并在报告里标注。

**自检硬编码主题名。**
`selftest_gui.py` 原来断言"自动选中的是 Kinomoto-Sakura"，
用户换主题后自检会莫名其妙失败（代码一行没改）。现在改为从实际状态读出当前生效的主题。

## 文档

README 补上了**平台支持矩阵** —— 功能 × 平台、`apply` 到底写哪些配置、
哪些桌面只部分支持（KDE 不写 `kcminputrc`、sway/Hyprland 需自行设 `XCURSOR_THEME` 等）。

`docs/NOTES.md` 记录了几个测试时踩的坑，其中一个值得单独提醒：

> **`HOME=假目录` 隔离不了 gsettings。** dconf 由会话总线上的服务写，
> 客户端只是通过 D-Bus 发变更，真正落盘的是那个已用真实环境启动的服务。
> 所以拿假 HOME 测 `apply` 仍然会改到你真实的 `org.gnome.desktop.interface cursor-theme`，
> 而且**不会报错**。测试 `apply` 一律加 `--dry-run`。

## 从 v1.0.0 升级

Arch 用户直接用新包覆盖：

```bash
sudo pacman -U cursor-manager-1.0.1-1-any.pkg.tar.zst
```

其他安装方式重新执行一次安装命令即可，配置无需改动。

## 下载哪个

| 文件 | 用途 |
|---|---|
| `cursor-manager-1.0.1-1-any.pkg.tar.zst` | **Arch Linux**：`sudo pacman -U <文件>` |
| `cursor-manager-1.0.1.tar.gz` | 通用源码包，解包即用，也能被 `pip install` |
| `cursor_manager-1.0.1-py3-none-any.whl` | Python wheel，配合 `pipx install` |
| `SHA256SUMS` | 上面三个文件的校验和 |

## 安装

**Arch Linux**

```bash
sudo pacman -U cursor-manager-1.0.1-1-any.pkg.tar.zst
```

**其他发行版（源码包，解包即用）**

```bash
tar -xzf cursor-manager-1.0.1.tar.gz
cd cursor-manager-1.0.1
./cursor-manager            # 直接跑
./install.sh                # 或装到 ~/.local（含桌面入口和图标）
```

**pipx**

```bash
pipx install cursor_manager-1.0.1-py3-none-any.whl
```

PyGObject 无法用 pip 可靠安装，需要走发行版包管理器
（它只影响图形界面，`--list` / `--import` / `apply` / `remove` 都不需要它）。

## 校验下载

```bash
sha256sum -c SHA256SUMS
```

## 自检

```bash
make test        # 导入器 38 项 + 删除 24 项 + GUI 39 项
```

三套自检都不需要人看图，退出码非 0 即失败。
删除功能的 24 项全部在伪造的 `HOME` 里跑，包括"闸门拒绝时确实没动手"和
回收站 `.trashinfo` 的 `Path` 能正确还原。

## 已知限制

- **Arch 包是按当前 Python 版本编译的**（3.14）。Arch 换 Python 大版本后需等重新构建；
  源码包不受影响
- `apply` 覆盖 niri / DMS / GTK / XWayland / gsettings；KDE 的 `kcminputrc` 未处理
- 导入功能对非 32bpp 的老式 `.cur` 依赖 Pillow
