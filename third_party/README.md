# third_party —— 第三方源码受控目录

本目录存放 FMCL UI 重构（PySide6 + QML）所需的第三方组件库源码，用于满足项目规则：

> 禁止引入来源不明的二进制；第三方依赖要么来自官方包管理器，要么由本项目自行编译
> 并记录来源与哈希。

---

## 1. 核心约定：源码树不入库

**本目录下的上游源码树不进入 Git 版本库。** 这是有意为之，不是遗漏。

原因：

- 两个上游仓库合计体积大（含大量图片、字体、示例资源），入库存放会持续膨胀仓库体积，
  且每次上游更新都会产生巨量 diff，淹没本项目的真实改动。
- 把第三方源码复制进本仓库，会使「这份代码到底是谁的、哪个版本、有没有被本地改过」
  变得不可验证。改为按 commit pin 重新拉取，任何一次构建都能从上游可复现地得到
  同一份源码。

### .gitignore 现状

仓库根 `.gitignore` 中相关规则如下（本目录不得被随意改动语义）：

```
# 第三方依赖源码（FluentUI C++ / PySide6-FluentUI-QML，MIT）
# 入库只保留来源记录与获取/构建脚本，源码树由脚本按 pin 的 commit 重新拉取
third_party/*
!third_party/README.md
!third_party/PROVENANCE.md
!third_party/fetch_sources.ps1
```

即：只有 `README.md`、`PROVENANCE.md`、`fetch_sources.ps1` 三个文件入库，
其余内容（上游源码树、`_install` 构建产物、`BUILD_PROVENANCE.md`）一律忽略。

验证某个文件是否会被忽略：

```powershell
git check-ignore -q -- third_party/README.md; $LASTEXITCODE   # 1 = 未忽略（会入库）
git check-ignore -q -- third_party/FluentUI; $LASTEXITCODE   # 0 = 已忽略
```

---

## 2. 如何在自己机器上取得源码

克隆本仓库后，`third_party/` 下只有本文档与 `PROVENANCE.md`、`fetch_sources.ps1`，
**没有源码**。执行拉取脚本即可：

```powershell
# 在仓库根目录执行（本机只有 Windows PowerShell 5.1，没有 PowerShell 7 的 pwsh）
powershell -NoProfile -ExecutionPolicy Bypass -File third_party/fetch_sources.ps1
```

脚本行为（幂等，可反复执行）：

1. 目标的目录不存在 → 执行 `git clone --recursive <上游 URL> <目标路径>`；
2. 目录已存在 → 执行 `git fetch --all --tags --prune`，再 `git checkout <pin 的 commit>`；
3. 两种情况都执行 `git submodule update --init --recursive`；
4. 最后校验实际 `HEAD` 是否等于 `PROVENANCE.md` 中 pin 的 commit，不一致则非 0 退出。

pin 的 commit 集中定义在脚本顶部 `$PinnedCommits` 变量中。**升级上游版本时，
先更新 `PROVENANCE.md` 的表格，再同步更新脚本顶部的 commit，两者必须一致。**

### 本地改动不会被静默接受

脚本以「源码树必须与 pin 的 commit 完全一致」为前提。只要 `git status --porcelain`
非空（无论 HEAD 是否已在 pin 上），脚本都会报错并以非 0 退出，而不会自动丢弃你的改动。
确认可以丢弃后加 `-Force`（会执行 `git reset --hard` 与 `git clean -fdx`，不可恢复）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File third_party/fetch_sources.ps1 -Force
```

### 只校验不拉取

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File third_party/fetch_sources.ps1 -VerifyOnly
```

### 只拉取单个仓库

脚本支持按名字过滤：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File third_party/fetch_sources.ps1 -Repository FluentUI
powershell -NoProfile -ExecutionPolicy Bypass -File third_party/fetch_sources.ps1 -Repository PySide6-FluentUI-QML
```

---

## 3. 目录用途一览

| 路径 | 用途 | 是否入库 |
| --- | --- | --- |
| `FluentUI/` | C++ Qt QML 组件库源码，编译出 `fluentuiplugin` 插件 | 否 |
| `PySide6-FluentUI-QML/` | PySide6 封装层与 QML 资源源码 | 否 |
| `_install/` | `scripts/build_fluentui.ps1` 的安装前缀，最终产物 `<prefix>/imports/FluentUI/` | 否 |
| `_stage/` | 插件 `OUTPUT_DIRECTORY`（构建中间产物），避免污染共享的 Qt SDK 目录 | 否 |
| `_build/` | CMake 构建目录（中间文件，可随时删除重建） | 否 |
| `PROVENANCE.md` | 上游 URL / 分支 / commit / 时间 / 许可 / 获取命令 | 是 |
| `README.md` | 本文档，目录约定说明 | 是 |
| `fetch_sources.ps1` | 幂等拉取脚本 | 是 |
| `BUILD_PROVENANCE.md` | 构建产物路径清单与 SHA256（构建时生成） | 否，见第 5 节 |

---

## 4. 拉取源码之后做什么

源码本身不产生任何可用的插件二进制。要得到本机可用的 `fluentuiplugin`，
需要在本机自行编译。

本机（2026-09-26 实测）已具备的条件：

| 项 | 状态 |
| --- | --- |
| Qt SDK | **已安装**：`D:\Qt\6.7.3\msvc2019_64` |
| Qt 组件 Core/Quick/Qml/Widgets/PrintSupport | 全部存在 |
| qt5compat（CMake 包 `Qt6Core5Compat`） | 已安装 |
| qtshadertools（CMake 包 `Qt6ShaderTools`） | 已安装 |
| CMake | 4.2.3，`D:\Program Files\CMake\bin\cmake.exe` |
| MSVC BuildTools | `C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools`，但 `cl.exe` 不在 PATH |
| ninja | **未安装**（脚本会自动回退到 Visual Studio 生成器） |
| PowerShell | **只有 Windows PowerShell 5.1**，没有 PowerShell 7（pwsh） |

```powershell
# 1. 先拉取源码
powershell -NoProfile -ExecutionPolicy Bypass -File third_party/fetch_sources.ps1

# 2. 指定 Qt SDK 根目录（必须，脚本靠它定位 Qt）
$env:QTDIR = 'D:\Qt\6.7.3\msvc2019_64'

# 3. 先演练一遍，确认将要执行的 CMake 命令
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_fluentui.ps1 -WhatIf

# 4. 真正编译并安装到 third_party/_install
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_fluentui.ps1
```

构建脚本会打印产物清单，并把每个产物的 SHA256 写入 `third_party/BUILD_PROVENANCE.md`。
细节与前置条件请直接阅读 `scripts/build_fluentui.ps1` 顶部注释，或以
`-WhatIf` 方式先查看它将要执行的步骤。

---

## 5. 已知注意事项

### 5.1 必须覆盖 FLUENTUI_QML_PLUGIN_DIRECTORY，否则会污染 Qt SDK

上游 `src/CMakeLists.txt` 中该变量的默认值是 `${QT_SDK_DIR}/qml/FluentUI`，
即**直接写进共享的 Qt SDK 安装目录**。实测已经发生过：本机
`D:\Qt\6.7.3\msvc2019_64\qml\FluentUI` 下现在有 116 个文件，是一次未覆盖该变量
的 CMake 配置留下的。

后果：产物落在项目控制范围之外，无法哈希、无法追溯，也会影响同一台机器上其他
Qt 项目。`scripts/build_fluentui.ps1` 因此显式传入
`-DFLUENTUI_QML_PLUGIN_DIRECTORY=<third_party>/_stage/qml/FluentUI`。
**不要手工去掉这个参数。** 清理被污染目录前请先确认没有其他项目依赖它。

### 5.2 BUILD_PROVENANCE.md 目前会被忽略

`.gitignore` 的 `third_party/*` 规则未对 `third_party/BUILD_PROVENANCE.md` 开白名单，
所以构建脚本写出的哈希清单**不会入库**。如果希望把编译产物哈希纳入版本管理，
需要由仓库维护者在 `.gitignore` 中追加：

```
!third_party/BUILD_PROVENANCE.md
```

### 5.3 上游 Releases 里的二进制不可复用

上游 `FluentUI` 与 `PySide6-FluentUI-QML` 的 GitHub Releases 资产全部是**演示程序
安装包**（`.exe` / `.dmg` / `.AppImage` / `.zip`），不是可以放进本项目的插件。
详见 `PROVENANCE.md` 第 6 节。**不要下载它们，也不要从它们中提取任何 DLL。**

### 5.4 上游源码树里签入了来源不明的 DLL

`third_party/FluentUI/3rdparty/` 下有 9 个上游直接签入的 `.dll`（OpenSSL 1.1 与
MinGW 运行时）。它们没有可验证的构建来源记录，**按项目规则禁止使用**。
本项目只从中编译 `src/` 下的源码，不使用 `3rdparty/` 中的任何二进制。

### 5.5 PySide6 路径下 C++ 插件是 optional 的

`PySide6-FluentUI-QML/FluentUI/imports/FluentUI/qmldir` 第 2 至 3 行为：

```
linktarget fluentuiplugin
optional plugin fluentuiplugin
```

关键字 `optional` 表示 QML 引擎在找不到该插件时不会致命失败；Python 封装层
（`FluentUI/FluentUI.py`）会用纯 Python 实现重新注册同名类型，并从
`FluentUI/imports/resource.qrc` 经 `pyside6-rcc` 生成的 `resource_rc.py` 里加载 QML。

因此：**编译 C++ 插件主要是为了与上游保持一致、补齐类型信息（`plugins.qmltypes`）
以及未来可能的 C++ 侧需求，并非 PySide6 运行时的硬前提。**
如果只在 PySide6 下使用 QML，可以先不编译插件而直接跑通界面；
是否编译请由项目负责人确认。相关证据见 `PROVENANCE.md`。

### 5.6 目录不可就地修改

不要在 `third_party/FluentUI` 或 `third_party/PySide6-FluentUI-QML`
里直接改代码来做适配。那样会让「pin 的 commit」与实际构建的内容不一致，
破坏供应链可验证性。需要改动时，应把改动做成补丁文件单独存放，
并在 `BUILD_PROVENANCE.md` 中记录补丁内容与哈希。
