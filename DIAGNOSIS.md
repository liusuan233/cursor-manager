# niri (Wayland) 鼠标光标主题 —— 诊断与修复记录

**环境**：Arch Linux · niri 26.04 · Wayland · DMS (DankMaterialShell) 桌面外壳
**主题**：KINOMOTO SAKURA（Windows 光标包，`.ani` 格式）
**结论**：主题文件本身没问题，**已有一套转换好的 Xcursor 光标**；坏的是**目录布局 + 主题名对不上**，导致所有 Wayland 客户端静默回落到 Adwaita。

---

## 一、根因

Wayland 下**没有全局光标设置**。每个程序各查各的，而且都遵守同一条硬规则：

```
<主题目录>/cursors/<光标名>
```

`cursors/` 这一层**不能省**。

修复前你机器上的三个"主题"：

| 路径 | 布局 | 谁在引用 | 结果 |
|---|---|---|---|
| `~/.icons/KINOMOTO SAKURA/cursors/` | ✅ 合法（20 实体 + 124 别名，12 档尺寸，8~20 帧动画） | **没有任何配置引用** | 白躺着 |
| `~/.icons/Kinomoto‑Sakura/` | ❌ 平铺，**没有 `cursors/`**，且只有 22 个非标准名 | niri 配置 + `$XCURSOR_THEME` 都指向它 | Wayland 客户端找不到 → 回落 Adwaita |
| `/usr/share/icons/Kinomoto‑Sakura/` | ❌ 同样平铺 | 无 | 同上（root 所有） |

**关键点**：第二个和第三个目录名用的是 **U+2011（非断字连字符 `‑`）**，不是 ASCII 连字符 `-`。
看起来一模一样，但只要有一处写成 ASCII `-` 就永远匹配不上。这是最隐蔽的一环。

**为什么"桌面上的光标好像又是对的"**：`libXcursor` 兼容平铺老布局（实测确认），所以 niri 自己画的光标可能加载成功；而 `libwayland-cursor`（GTK4 / Firefox / Electron 在 Wayland 下走它）**只认 `cursors/` 子目录**，于是应用内部是 Adwaita —— 这就是"光标主题一半生效一半不生效"的来源。

**DMS 的干扰**：`~/.config/niri/dms/cursor.kdl` 由 DMS 生成（文件头写着 `DO NOT EDIT / CHANGES WILL BE OVERWRITTEN`），里面的 `xcursor-theme` 之前是 `Adwaita`。手改 `config.kdl` 能被 DMS 覆盖。

---

## 二、已做的修复

### 1. 主题归位（`install-kinomoto-cursor.sh`）

```
~/.local/share/icons/Kinomoto-Sakura/
├── cursors/          ← 144 个条目（20 实体 Xcursor 文件 + 124 别名符号链接）
├── index.theme       ← Inherits=Adwaita（缺失光标名兜底）
└── cursor.theme
```

- 用 **纯 ASCII 名 `Kinomoto-Sakura`**，永久避开 U+2011 陷阱
- 落在 **XDG 规范位置** `~/.local/share/icons`（`libXcursor`、`libwayland-cursor`、DMS 三方扫描顺序里的第一位）
- 清掉了 `cursors/` 里的杂物 `thumbnail.png`、`wayland-cursor`
- 两个坏掉的旧目录移到 `~/.icons/.disabled-20261010/`（**没删**，随时可还原）

### 2. 五处配置全部统一指向 `Kinomoto-Sakura`（`set-cursor.sh`）

| 位置 | 写入内容 | 管谁 |
|---|---|---|
| `~/.config/niri/config.kdl` | `cursor { xcursor-theme "Kinomoto-Sakura"; xcursor-size 24 }` | niri 自己的光标 |
| `~/.config/niri/config.kdl` | `environment { XCURSOR_THEME ... XCURSOR_SIZE ... }` | niri 启动的所有子进程 |
| `~/.config/niri/dms/cursor.kdl` | 同上 | DMS 托管的那份 |
| `~/.config/gtk-3.0/settings.ini`<br>`~/.config/gtk-4.0/settings.ini` | `gtk-cursor-theme-name` / `-size` | GTK3 / GTK4 应用 |
| `~/.Xresources` | `Xcursor.theme` / `Xcursor.size` | XWayland 应用 |
| `gsettings org.gnome.desktop.interface` | `cursor-theme` / `cursor-size` | 走 GSettings 的程序 |

所有被改的文件都留了 `.bak.<时间戳>` 备份。

### 3. 验证

- `niri validate` → **config is valid**
- niri 日志 → 16:56:57 自动重载配置成功
- `libXcursor` 官方 API 实测 → **15/15 个标准光标名加载成功**，请求 48px 得到 48px
- `verify_cursor` / `xcursor_tool.py` 随附，可随时复验

---

## 三、以后怎么切换光标主题

```bash
cd ~/code/linuxcursettingtool

./set-cursor.sh --list              # 列出所有主题，标注哪些布局合法（✔/✘）
./set-cursor.sh <主题名> [尺寸]      # 一键切换（默认 24）
./set-cursor.sh Adwaita 24          # 例如换回 Adwaita
```

**推荐方式（DMS 用户）**：DMS 设置 → **Cursors** 里直接选。
DMS 会自己写 `dms/cursor.kdl`，且它扫描的路径包含 `~/.local/share/icons`
（`dms` 二进制内确认：`dataDirs.map(d => d + "/icons").concat([~/.icons, ~/.local/share/icons])`），
所以 `Kinomoto-Sakura` 现在应该会正常出现在列表里。

### 生效范围

| | 何时生效 |
|---|---|
| niri 桌面 / 窗口边框光标 | 保存 `config.kdl` 后自动重载（已确认重载成功） |
| 之后新启动的程序 | 立即 |
| 已经开着的程序（Firefox / 终端 / GUI） | 需重启该程序 |
| `$XCURSOR_THEME` 对新进程完全生效 | **最稳是注销重登一次 niri 会话** |

强制刷新 XWayland 的光标设置：

```bash
xrdb -merge ~/.Xresources
```

---

## 四、附带工具

| 文件 | 用途 |
|---|---|
| `set-cursor.sh` | 统一切换光标主题（niri + DMS + GTK + XWayland + gsettings） |
| `install-kinomoto-cursor.sh` | 把光标包整理成合法主题布局（可改造成通用转换器） |
| `xcursor_tool.py` | `list` / `info` / `render` —— 检查 Xcursor 文件的帧数、尺寸、热点，导出 PNG |
| `verify_cursor.c` → `verify_cursor` | 用 libXcursor 官方 API 验证"主题名能否真的被加载" |
| `preview/` | 各光标导出图 |

编译验证器：

```bash
gcc -O2 -o verify_cursor verify_cursor.c $(pkg-config --cflags --libs xcursor)
./verify_cursor Kinomoto-Sakura
```

---

## 五、回滚

```bash
# 配置回滚
cp ~/.config/niri/config.kdl.bak.<时间戳> ~/.config/niri/config.kdl
cp ~/.Xresources.bak.<时间戳> ~/.Xresources

# 主题回滚（旧目录还在）
mv ~/.icons/.disabled-20261010/* ~/.icons/
rm -rf ~/.local/share/icons/Kinomoto-Sakura
```

可选清理（需要 sudo，那个目录布局同样不合法且已无人引用）：

```bash
sudo rm -rf '/usr/share/icons/Kinomoto‑Sakura'
```

---

## 六、这套光标的技术参数（实测）

- **12 档标称尺寸**：24 / 28 / 32 / 40 / 48 / 56 / 64 / 72 / 80 / 88 / 96 / 256
- **动画帧数**：`left_ptr` 8 帧、`xterm` 12 帧、`hand2` 20 帧（每帧 150ms）
- **20 个实体文件**：`left_ptr` `hand2` `wait` `left_ptr_watch` `xterm` `cross` `move` `circle`
  `question_arrow` `pencil` `vertical-text` `zoom-in` `zoom-out` `center_ptr` `right_ptr`
  `dotbox` `left_side` `right_side` `top_side` `bottom_side` + 4 个 corner
- **124 个别名**：覆盖 X11 传统名（`sb_h_double_arrow` `bd_double_arrow` …）、
  CSS 名（`ew-resize` `ns-resize` `col-resize` `not-allowed` …）和一堆哈希名
  （`00000000000000020006000e7e9ffc3f` = `left_ptr_watch` 等，Chromium/Firefox 老式查找用）

热点已逐个抽检：`left_ptr` (0,0)、`hand2` (3,0)、`xterm` (2,4)、`cross` (5,5)、`move` (12,13)，均在实体像素上。
（`wait` / `not-allowed` / `progress` 热点落在 (0,0) 透明处 —— 属正常，这类光标热点无语义。）
