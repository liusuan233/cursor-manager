# v1.0.3

修掉「没有 `.inf` 的光标包导入后缺 3 个角色」，重画了应用图标，
并补上两个能省时间的生成脚本（演示视频、图标）。

## 修复：没有 `.inf` 的包，17 个角色里会漏掉 3 个

Windows 光标包分两类：用安装器打包的带 `install.inf`，手工拼的没有。
有 `.inf` 时它的 `[Strings]` 段就是**微软官方的角色映射**，属于权威数据；
没有就只能按文件名猜。

实测：拿一个真实包（`ZUTOMAYO`）把 `.inf` 抽掉再导入 ——

```
修复前   14 / 17
         漏掉 Diagonal1.ani、Diagonal2.ani、Horizontal.ani
修复后   17 / 17
```

根因很直接：关键词表里写的是 `diag1` 和 `horz`，但

- `diag1` **不是** `diagonal1` 的子串
- `horz` **不是** `horizontal` 的子串

而原来的实现是纯子串匹配，所以这三个永远猜不出来。
反过来还有误判风险：`Small.ani` 会因为含 `all` 被猜成"移动"。

### 现在改成三层匹配（从严到宽）

```
① 与 .inf 键名归一化后完全相同      diagonal1 == diagonal1
② 与任一关键词归一化后完全相同       working == working
③ 关键词子串匹配，最长优先          precision ⊃ precise
   三个字母以内的弱关键词只许整词命中
```

**归一化**是这次的关键：`Diagonal-1` / `diagonal_1` / `Diagonal 1` / `Diagonal1`
现在都归一成 `diagonal1`；`SizeNWSE` 会按 camelCase 切成 `size` + `nwse`。

**弱关键词**（`ns` / `we` / `up` / `all` / `alt` / `pen` / `no`）必须整词命中，
否则 `Small.ani` 含 `all`、`Normal.ani` 含 `no`，会被硬拽到错误的角色上。

关键词表同时扩到覆盖三套命名习惯：

| 命名习惯 | 例子 |
|---|---|
| 微软官方方案 | `Normal` `Working` `Precision` `Handwriting` `Horizontal` `Diagonal1` `Pin` |
| X11 习惯 | `SizeNS` `SizeWE` `SizeNWSE` `SizeAll` `xterm` `crosshair` `left_ptr` |
| 中文 | `正常选择` `文本选择` `水平调整` `手写` `移动` |

### 猜不出来时

不会乱填 —— 认不出的角色保持空缺，由系统回落到默认光标，
同时在导入结果里明确提示：

```
这个包没有 .inf：17 个角色里 N 个没能从文件名认出来，会继承系统默认外观。
想要精确映射，把 .inf 放进包里重新导入一次（很多包的 .inf 是通用的，也可以手工写一个）
```

**想精确控制的话**，往包里放一个最小的 `install.inf` 就行（左边键名固定不能改）：

```ini
[Version]
signature="$CHICAGO$"

[Strings]
SCHEME_NAME = "My Cursor"
pointer     = "Normal.ani"
help        = "Help.ani"
work        = "Working.ani"
busy        = "Busy.ani"
cross       = "Precision.ani"
text        = "Text.ani"
hand        = "Handwriting.ani"
unavailable = "Unavailable.ani"
vert        = "Vertical.ani"
horz        = "Horizontal.ani"
dgn1        = "Diagonal1.ani"
dgn2        = "Diagonal2.ani"
move        = "Move.ani"
alternate   = "Alternate.ani"
link        = "Link.ani"
loc         = "Pin.cur"
person      = "Person.cur"
```

> 顺带一个很多人不知道的点：微软光标方案的 17 个文件名是**固定**的，
> 所以两个都用标准名的包，`.inf` 是可以直接互借的，连文件名都不用改。

## 修复：图标尺寸档错配

`install.sh` 里原来有这么一行：

```bash
cp -f "$HERE/icons/cursor-manager.png" "$HICOLOR/256x256/apps/cursor-manager.png"
```

`cursor-manager.png` 是 512px 的，塞进 `256x256/` 目录会让图标加载器拿到
尺寸不符的位图，也**覆盖掉了上面刚写的回退逻辑**。现在每档只进自己的目录。

另外 `install.sh` 和 `PKGBUILD` 都只装 5 档（32/48/64/128/256），
**缺 512**（现代桌面会找它）。两处都补上了。

`install.sh` 还加了：清掉不属于当前档位的旧文件 + 刷新图标缓存 ——
不然换了图标可能还显示老的。

## 新图标

旧图标是一个深色底上的光标箭头，比较平。新的是圆角方块 + 蓝紫渐变底，
白色箭头占画布 62%，底部三个色点当主题色板。

尺寸档 32/48/64/128/256/512 全部由脚本生成，改设计跑一遍就行：

```bash
make icon           # 重新生成 6 个尺寸档
make icon-preview   # 只出预览图（含 32/48/64 放大 4 倍的像素对照）
```

## 新增：两个生成脚本

| 脚本 | 作用 |
|---|---|
| `tools/make-demo.py` | `make demo` 自动生成演示视频（真跑命令、真解码光标，不需要录屏和剪辑软件） |
| `tools/make-icon.py` | `make icon` 重新生成应用图标（6 个尺寸档） |

两个脚本都进了源码包（`MANIFEST.in` 里 `recursive-include tools *.py`），
克隆下来就能跑。生成的 `demo/*.mp4` 和 `icons/_preview.png` 是构建产物，不进仓库。

另外新增了 `docs/VIDEO-SCRIPT.md` —— 想自己录介绍视频的话有分镜表和完整旁白稿。

## 从 v1.0.2 升级

```bash
sudo pacman -U cursor-manager-1.0.3-1-any.pkg.tar.zst
```

**已经导入过不带 `.inf` 的光标包的，建议重新导入一次**才能拿到修好的角色映射：

```bash
cursor-manager --import <原来的包> --name <原来的名字> --overwrite
```

## 下载哪个

| 文件 | 用途 |
|---|---|
| `cursor-manager-1.0.3-1-any.pkg.tar.zst` | **Arch Linux**：`sudo pacman -U <文件>` |
| `cursor-manager-1.0.3.tar.gz` | 通用源码包，解包即用，也能被 `pip install` |
| `cursor_manager-1.0.3-py3-none-any.whl` | Python wheel，配合 `pipx install` |
| `SHA256SUMS` | 上面三个文件的校验和 |

## 安装

**Arch Linux**

```bash
sudo pacman -U cursor-manager-1.0.3-1-any.pkg.tar.zst
```

**其他发行版（源码包，解包即用）**

```bash
tar -xzf cursor-manager-1.0.3.tar.gz
cd cursor-manager-1.0.3
./cursor-manager            # 直接跑
./install.sh                # 或装到 ~/.local（含桌面入口和图标）
```

**pipx**

```bash
pipx install cursor_manager-1.0.3-py3-none-any.whl
```

PyGObject 无法用 pip 可靠安装，需要走发行版包管理器
（它只影响图形界面，命令行功能都不需要它）。

## 校验下载

```bash
sha256sum -c SHA256SUMS
```

## 自检

```bash
make test        # 导入器 55 + 删除 24 + 检查更新 25 + GUI 42 项
```

四套自检都不需要人看图，退出码非 0 即失败。
这次新增的 `test_guess_roles()` 专门钉住角色猜测：36 个正向用例
（标准名 / X11 名 / 分隔符变体 / 中文）、7 个反向用例（无关文件名**不许**误判）、
一个无 `.inf` 的 17 角色端到端包（并断言对角线、水平/垂直没有搞反）。

## 已知限制

- **Arch 包是按当前 Python 版本编译的**（3.14）。Arch 换 Python 大版本后需等重新构建；
  源码包不受影响
- `apply` 覆盖 niri / DMS / GTK / XWayland / gsettings；KDE 的 `kcminputrc` 未处理
- 导入功能对 RLE 压缩的老式光标仍依赖 Pillow
- 角色映射还不能在图形界面里手动指定 —— 猜不出来只能靠补 `.inf` 或改文件名
