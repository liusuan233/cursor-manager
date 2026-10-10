# 技术笔记与踩坑记录

主 [README](../README.md) 之外的东西都放这。

---

## Xcursor 格式要点

* 小端；头部 `Xcur` + header_size + version + ntoc
* TOC 每项 `type | subtype | position`；`type=0xFFFD0002` 是图片，`subtype` 是**标称尺寸**
* 图片头 36 字节：`header_size | type | subtype | version | width | height | xhot | yhot | delay`
* 同一标称尺寸下多张图 = 动画帧，按 TOC 顺序播放
* 像素是**预乘 alpha 的 ARGB**，且**文件里字节序是 B,G,R,A**（小端 u32 的 `0xAARRGGBB`）

GDK 的 `R8G8B8A8` 要直通 alpha，所以读的时候反预乘、写的时候预乘。

## Windows 光标格式要点

```
.ani = RIFF/ACON 容器，里面塞了 nFrames 个完整的 .cur/.ico
.cur = ICONDIR 容器，type=2，每个条目带热点坐标
.ico = ICONDIR 容器，type=1，没有热点
```

`anih` 块 36 字节给帧数/步数/尺寸/标志；`rate` 块给每帧延时（单位 jiffies，1/60 秒）；
`seq ` 块给播放顺序；`LIST`/`fram` 里是 nFrames 个 `icon` 子块，每个就是一份完整的 .cur。

`ICONDIRENTRY` 16 字节。注意 **type=2（CUR）时 `wPlanes` 和 `wBitCount` 两个字段被复用成
热点坐标**，type=1（ICO）时才是本来的含义。

---

## 踩过的坑

开发过程中被自检和交叉验证抓到的真 bug，按发现顺序。

### 1. 红蓝通道写反（`_premultiply` 的 numpy 分支）

Xcursor 文件存的是 B,G,R,A，而这个函数的输入是 R,G,B,A。纯 Python 回退分支是对的，
numpy 分支少了通道对调。

这个 bug 从第一版就在，但直到导入器出现才有代码去**写** Xcursor 文件 —— 之前只读过，
所以从没暴露。

发现方式是把「源图直接缩放」「新转出的主题」「别人早先转好的主题」三者的 RGB 均值并排比：

| | RGB 均值 |
|---|---|
| 源图直接缩放 | `[185.6, 141.3, 109.6]` |
| 新转出的 | `[108.5, 140.3, 184.5]` ← R/B 对调 |
| 别人早先转好的 | `[185.4, 141.2, 109.5]` |

现在 `selftest_import.py` 里有纯红/纯蓝的往返用例钉住它。

### 2. `AF_ICON` 标志会骗人

按 ANI 规范，`bfAttributes` 的 bit0 表示"帧是 ICO 而不是 CUR"，暗示没有热点。

实测那 17 个 `.ani` **全部**置了 `AF_ICON`，但内嵌容器**全是真正的 CUR(type 2)**，
各自带热点。一开始信了标志、把 (0,0) 覆盖成中心点，结果指针会点偏。

正确做法是只看内嵌容器类型：CUR 用容器里的值，ICO 才用中心。

交叉验证：别人早先转好的主题里 `left_ptr` 热点是 (0,0)、`xterm` 是 (2,4)、`hand2` 是 (3,0)，
正好是 256px 源按比例缩到 24px 的结果。

### 3. 标称尺寸 ≠ 实际像素宽度

主题里 `vertical-text` / `zoom-in` / `zoom-out` 标称 96 实际却是 128×128，
照标称选档会撑破预览格子。

所以有 `probe_size_map()`：只读 TOC 和图片头拿到真实宽高、**不解码像素**，
选档时按真实宽度选。这三个光标因此会被正确选到标称 72（实际 96px）那一档。

### 4. `struct` 偏移量错了两次

`probe_size_map` 一开始把 `version`（偏移 12）当成 `width`，所有纹理宽度变成 1px。
两次都是自检抓到的。

### 5. 图标主题 ≠ 光标主题

`hicolor` / `breeze` / `exo` 没有 `cursors/` 是因为它们本来就是图标主题，不该被标红。

`themes.py` 用"有没有 16x16 / scalable 这类尺寸子目录"和 `index.theme` 里的
`Directories=` 键把它们排除。

### 6. 测试里一个未捕获的异常会让主循环空转

GUI 自检的某个阶段调了不存在的 `get_action_group`，异常在 GLib 回调里被打印后，
回调返回 `None`，主循环就永远不退出 —— 表现为"卡住"而不是"失败"。

现在每个阶段都套了异常兜底，失败会明确报告并退出。

---

## 打包时踩的坑

### `compileall -d` 的语义容易搞错

`-d` 是"拼在**编译根目录**的相对路径前面"。编译根给 `.../site-packages/cursorkit` 时，
`-d` 也必须给到 `.../site-packages/cursorkit`；只给 `.../site-packages` 会得到
`/usr/lib/python3.14/site-packages/cli.py` —— 少一层，traceback 指向不存在的文件。

这个错误不会让任何东西报错，只会让报错信息指错地方，所以得主动去读 `.pyc` 里的
`co_filename` 才能发现：

```python
import marshal
code = marshal.loads(pyc_bytes[16:])   # PEP 552 header 16 字节
print(code.co_filename)
```

### pip 的 `--compile` 会把构建路径写进 `.pyc`

makepkg 会因此警告"包含对 `$pkgdir` 的引用"。
修法是 `--no-compile` 装完再用 `compileall -d` 按最终路径重新编译。

### 源码包滚雪球

`packaging/` 整个塞进 SOURCES 会导致：makepkg 在那里生成 `src/`（解包目录）和 `pkg/`，
下次 `make dist` 把它们卷进源码包，再下次构建又解包出来……

tarball 从 96K 涨到 1.3M，里面出现 `packaging/src/cursor-manager-1.0.0/packaging/src/...`
这种套娃路径。

现在 SOURCES 只列 `packaging/PKGBUILD` 和 `packaging/cursor-manager.desktop` 两个真源文件，
并且 `make dist` 末尾会自动检查包里是否混入构建产物，有就直接失败。

### `cp -a` 多文件时会拍平目录

`cp -a packaging/PKGBUILD ... $(STAGE)/` 是把文件直接放过去，`packaging/` 这层没了。
要用 `cp -a --parents`。

### wheel 文件名里 `-` 会变成 `_`

项目叫 `cursor-manager`，但产出的文件是 `cursor_manager-1.0.0-py3-none-any.whl`
（PEP 427 规范化）。Makefile 里用连字符通配会匹配不到。

### namcap 的 `library-no-package-associated` 对本项目是误报

它提示 `cursorkit.scan_themes` 之类的"未安装依赖"，原因有两层：

1. namcap 解析 Python 模块是**对照构建机上已安装的 pacman 包**做的，
   而 `cursorkit` 只存在于 `$pkgdir` 里，自然对不上；
2. `from X import Y` 里的 `Y` 也可能是子模块（`from os import path`），
   静态分析分不清，于是把两者都当模块检查。

`provides=('python-cursorkit')` 也压不掉。属已知噪声，不是打包缺陷。

---

## 测试时踩的坑

### `HOME=假目录` 隔离不了 gsettings

想在不碰用户真实配置的前提下测试 `apply`，第一反应是 `HOME=/tmp/fake ./cursor-manager apply ...`。
文件路径确实隔离了（`apply.py` 里的路径都在 import 时由 `Path.home()` 算出来），
**但 gsettings 没有**。

原因：dconf 是会话总线上的服务在写，客户端只是通过 D-Bus 把变更发过去，
真正落盘的是那个**已经用真实环境启动的** `dconf-service`。
所以 `HOME` 改了也没用 —— 用户的真实 `~/.config/dconf/user` 被改了。

更坑的是它**不会报错**，看起来一切正常，直到你发现真实系统的
`org.gnome.desktop.interface cursor-theme` 变成了测试里用的那个值。

**教训**：测试 `apply` 一律加 `--dry-run`。需要验证真实写入时，
用一次性容器或明确接受会改动本机配置。

`selftest_gui.py` 里的干跑检查现在还顺带断言"报告里每一项都标了『试运行』或『跳过』"，
就是防止哪天有人把 `dry_run=True` 去掉。

### 自检里别硬编码主题名

原来的 `selftest_gui.py` 断言"自动选中的是 Kinomoto-Sakura"。用户把主题换成
`Breeze_Light` 之后，自检立刻报两项失败 —— 但代码一行没改，是**断言本身不成立**。

现在改成先从 `read_state()` 读出"当前实际生效的主题"，再断言界面选中了它。
副作用是自检覆盖的主题形态变多了：`Breeze_Light` 有 47 个光标且**全是静态的**
（不像 Kinomoto-Sakura 那样 20 个光标全带动画），正好补上了另一条代码路径。

### 别在没有 niri 的机器上凭空建目录

`set_dms_cursor_kdl()` 原来无条件 `mkdir -p ~/.config/niri/dms/` 再写文件。
GNOME / KDE / X11 用户跑一次 `apply`，家目录里就多出一个 `~/.config/niri/`。

现在会先判断 `~/.config/niri/` 是否存在，不存在就跳过并在报告里标 `○`。

---

## 本项目最初的排查记录

见 [`DIAGNOSIS.md`](../DIAGNOSIS.md)：一个 Windows 光标包在 niri 上怎么设都不生效，
根因是目录布局缺一层 `cursors/`，加上主题名里混用了 U+2011 断字连字符。
