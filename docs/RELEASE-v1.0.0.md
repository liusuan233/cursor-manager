# v1.0.0

**Linux 鼠标光标主题管理器** —— 在 niri / Wayland 上浏览、动画预览、一键切换光标主题，
还能直接导入 Windows 的 `.ani` 光标包。

## 亮点

- **一次写全 6 个配置点** —— niri、DMS、GTK3、GTK4、`.Xresources`、gsettings。
  Wayland 下没有全局光标设置，漏掉任何一处，那一类程序就会继续用 Adwaita
- **把"静默失败"标红** —— 主题目录缺一层 `cursors/` 时，Wayland 客户端会悄悄忽略它
  并回落到 Adwaita，**不报任何错**。这个工具把布局校验做成界面上可见的 ✔/✘
- **动画预览** —— 自己解码 Xcursor 二进制格式逐帧播放，普通图片查看器打不开这种文件
- **导入 Windows 光标包** —— 优先读包内 `.inf` 的 `[Strings]` 段拿到微软官方角色映射，
  比按文件名猜可靠得多；自动从 256×256 原图降采样出多档尺寸
- **删除主题** —— 默认移入回收站（可从文件管理器恢复），`--purge` 才真删。
  系统主题、当前正在使用的主题会被拒绝 —— 删掉正在用的那个会让所有程序静默回落到
  Adwaita，正是这个项目要防的坑
- **CLI 不依赖 GTK** —— `--list` / `--import` / `apply` / `remove` 在无图形环境的服务器上也能用

## 下载哪个

| 文件 | 用途 |
|---|---|
| `cursor-manager-1.0.0-1-any.pkg.tar.zst` | **Arch Linux** 用户装这个：`sudo pacman -U <文件>` |
| `cursor-manager-1.0.0.tar.gz` | 通用源码包。解包即用（`./cursor-manager`），也能 `pip install` 它 |
| `cursor_manager-1.0.0-py3-none-any.whl` | Python wheel，配合 `pipx install` 使用 |
| `SHA256SUMS` | 上面三个文件的校验和 |

## 安装

**Arch Linux**

```bash
sudo pacman -U cursor-manager-1.0.0-1-any.pkg.tar.zst
```

依赖会自动从仓库装（`python-gobject`、`gtk4`、`python-numpy`、`python-pillow`）。

**其他发行版（源码包，解包即用）**

```bash
tar -xzf cursor-manager-1.0.0.tar.gz
cd cursor-manager-1.0.0
./cursor-manager            # 直接跑
./install.sh                # 或者装到 ~/.local（含桌面入口和图标）
```

**pipx**

```bash
pipx install cursor_manager-1.0.0-py3-none-any.whl
```

PyGObject 无法用 pip 可靠安装，需要走发行版包管理器：

```bash
sudo pacman -S python-gobject gtk4                    # Arch
sudo apt install python3-gi gir1.2-gtk-4.0            # Debian / Ubuntu
sudo dnf install python3-gobject gtk4                 # Fedora
```

## 用法

```bash
cursor-manager                              # 图形界面
cursor-manager --list [-v]                  # 列出已安装主题
cursor-manager apply <主题名> [尺寸]         # 切换主题
cursor-manager apply <主题名> --dry-run     # 只报告会改哪些文件
cursor-manager --import <路径> [选项]        # 导入 Windows 光标包
cursor-manager remove <主题名>              # 删除主题（移入回收站）
cursor-manager remove <主题名> --purge      # 永久删除
cursor-manager --help
```

切换后 niri 保存配置时会自动重载，但**已经开着的程序需要重启**才能换过来。

## 校验下载

```bash
sha256sum -c SHA256SUMS
```

## 环境要求

- Linux + Wayland（niri、DMS 为最佳适配）
- Python 3.10+
- PyGObject + GTK4（仅图形界面需要，CLI 不需要）
- numpy、Pillow（可选；没有会自动退回纯 Python 实现，只是慢一些）

## 已知限制

- **Arch 包是按当前 Python 版本编译的**（3.14）。Arch 滚动更新换 Python 大版本后，
  需要等包重新构建；源码包不受影响
- `apply` 目前只覆盖 niri / DMS / GTK / XWayland / gsettings 这几条链路；
  KDE 的 `kcminputrc` 没有处理（KDE 用户可以用 CLI 的 `--dry-run` 看会改什么）
- 导入功能对非 32bpp 的老式 `.cur` 依赖 Pillow；没装 Pillow 时只支持 32bpp

## 自检

这个版本带三套机器可验证的自检，不需要人看图，退出码非 0 即失败：

```bash
make test        # 导入器 38 项 + 删除 24 项 + GUI 39 项
```

`selftest_import.py` 会自己合成光标包，没有外部素材也能完整跑；
`selftest_delete.py` 在伪造的 HOME 里验证删除闸门与回收站规范。
另有 `verify_cursor.c`，用 libXcursor 官方 API 验证"主题名能否真的被加载"。

---

**首次发布。**
