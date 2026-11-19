# FMCL - Fusion Minecraft Launcher
![License](https://img.shields.io/github/license/Janson20/FMCL?color=blue)
![GitHub Release](https://img.shields.io/github/v/release/Janson20/FMCL?include_prereleases&sort=semver)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)
![Downloads](https://img.shields.io/github/downloads/Janson20/FMCL/total?color=orange)
![Stars](https://img.shields.io/github/stars/Janson20/FMCL?style=social)

一个基于 **Qt Quick（QML，PySide6）** 的 Minecraft 启动器，覆盖版本管理、模组生态、开服、联机、备份、音乐、成就与 AI 助手。界面使用 QML 构建，命令行模式保留在 `main.py`。

---

## 功能特点

FMCL 按 12 个一级领域组织功能，核心能力如下：

- **版本与启动**：原版/模组版本安装、识别、重命名、校验与删除；8 种加载器安装、9 种加载器识别，支持 BMCLAPI 镜像加速，启动前自动匹配 Java，异常退出时收集现场并支持 AI 诊断。
- **多游戏目录**：默认列出程序安装目录与官方启动器目录；其他 `.minecraft` 目录可手动添加并命名。顶栏与「设置 → 游戏目录」均可切换，版本、资源、存档与服务器列表会跟随当前目录。
- **资源与整合包**：本地模组/资源包/地图/光影管理，Modrinth + CurseForge 在线浏览，六种客户端整合包与 `.mrpack` 开服。
- **服务器**：安装、启动、控制台、每服内存、端口防冲突、`server.properties` 可视化编辑与服务器模组管理。
- **联机与备份**：EasyTier 陶瓦 P2P 联机；存档手动/自动备份、校验、恢复与导出。
- **音乐**：本地扫描、五个在线音源、歌单、歌词、桌面歌词、SMTC 与全局热键。
- **AGENT 与语音**：多提供商模型、33 个工具、三级权限、Skills 与流式对话；SenseVoice 离线语音输入。
- **工具箱与系统能力**：9 个实用工具、性能悬浮窗、47 项成就、5 套主题、四语言、Windows 自动更新与插件系统。
- **基岩版**：UWP / GDK 版本下载、安装与启动（仅 Windows 10 19041+）。

完整交互、边界与平台限制见 [docs/FEATURES.md](docs/FEATURES.md)。

---

## 界面预览

```
┌──────────────────────────────────────────────────────────────┐
│ 顶部栏：面包屑 / 搜索 / 公告 / 更新 / 设置                   │
├────────────────┬─────────────────────────────────────────────┤
│ 一级导航       │ 页面内容                                    │
│ 游戏           │                                             │
│   游戏         │                                             │
│   已安装版本   │                                             │
│   已安装基岩版 │                                             │
│ 资源与联机     │                                             │
│   资源浏览     │                                             │
│   服务器       │                                             │
│   陶瓦联机     │                                             │
│   存档         │                                             │
│ 工具与扩展     │                                             │
│   音乐         │                                             │
│   工具         │                                             │
│   成就总览     │                                             │
│   AGENT        │                                             │
│   设置         │                                             │
├────────────────┴─────────────────────────────────────────────┤
│ 状态栏：任务进度 / 操作结果                                  │
└──────────────────────────────────────────────────────────────┘
```

> 界面由 QML 自绘，左侧一级导航按“游戏 / 资源与联机 / 工具与扩展”分组（插件页单独归入“插件”，基岩版入口仅 Windows 显示）；页面以导航栈切换，支持面包屑返回与 `fmcl://` 深链。

---

## 环境要求

| 依赖 | 说明 |
|------|------|
| Python 3.10+ | 运行启动器 |
| Java 8+ | 运行 Minecraft（启动器自动扫描系统 Java，推荐版本由 MC 版本决定） |
| Linux: Qt 6 运行库 / 构建工具 | 源码构建需要 C++ 编译器、cmake、ninja、OpenGL/XCB 头文件与 Qt 6.7.3 SDK；`scripts/install.sh` 会自动安装（Debian/Ubuntu: `build-essential cmake ninja-build libgl1-mesa-dev ...`） |

---

## 安装

### 方式一：从 Release 下载（推荐）

前往 [Releases](https://github.com/Janson20/FMCL/releases) 页面下载适合你平台的安装包：

| 平台 | 文件 | 说明 |
|------|------|------|
| Windows x64 | `FMCL-Setup-x.x.x.exe` | NSIS 安装包，双击运行 |
| Windows x64（不含 .NET SDK） | `FMCL-Setup-x.x.x-without-dotnetsdk.exe` | 体积更小；GDK 版首次使用时可另行引导安装 .NET 10 SDK |
| Linux x86_64 | `FMCL-x.x.x-linux-x86_64.tar.gz` | 目录产物（`FMCL/FMCL` + `_internal/`）；也可用 `scripts/install.sh` 安装 |

> QML 版只提供 **64 位** Windows 产物（PySide6 6.7.3 没有 win32 轮子）。32 位用户继续使用
> 2.12.6 安装包，数据目录与新版相同。

> **注意**：macOS（`.dmg`）二进制包暂不随发布提供，macOS 用户请参考「方式三：从源码运行」。Linux 现在提供 x86_64 目录产物 tarball，并可用下方「方式二：Linux 一键安装」脚本安装。

#### 安装说明

- **Windows x64**: 双击 `.exe` 安装包，按向导完成安装。安装包已内置 7-Zip（26.02 版本），无需联网即可自动安装；完整版还内置 .NET 10 SDK。安装器按当前用户安装到 `%LOCALAPPDATA%\Programs\FMCL`，**全程无需管理员权限**，安装后直接双击快捷方式即可正常使用。QML 产物是目录（`FMCL.exe` + `_internal/`），卸载时只删程序文件，保留 `config.json`、`.minecraft`、账号、歌单与成就等用户数据
- **Linux**: 请使用下方「方式二：Linux 一键安装」脚本
- **macOS**: 请参考下方「方式三：从源码运行」

> **数据目录说明**：启动器优先使用自身所在目录存放数据（便携模式，`.minecraft` 等数据跟随 exe）。若所在目录不可写（如手动把程序放入 `C:\Program Files` 等需要管理员权限的目录），会自动回退到用户数据目录（Windows: `%LOCALAPPDATA%\FMCL`；macOS: `~/Library/Application Support/FMCL`），并将旧目录中已有的数据自动迁移过去，无需管理员身份即可正常使用。游戏目录不限于这一处：启动器同时内置官方启动器目录，并允许在「设置 → 游戏目录」中添加任意命名的 `.minecraft` 目录。

### 方式二：Linux 一键安装

```bash
curl -fsSL https://raw.githubusercontent.com/Janson20/FMCL/main/scripts/install.sh | bash
```

脚本默认从源码构建 QML 目录产物：安装系统依赖 → 安装 uv → 克隆项目 → `uv sync` → 拉取第三方源码 → 用 aqtinstall 安装 Qt 6.7.3 SDK → 编译 FluentUI 插件 → PyInstaller 构建 `dist/FMCL` → 安装到 `<安装目录>/runtime` → 注册 `fmcl` 命令。

支持 Debian / Ubuntu / Fedora / RHEL / CentOS / Arch / openSUSE，安装完成后直接运行 `fmcl` 即可启动。
自定义安装目录：`./scripts/install.sh ~/.local/share/fmcl`

也支持直接安装 CI / Release 产出的 tarball（不编译）：

```bash
bash scripts/install.sh /opt/fmcl --version x.x.x
bash scripts/install.sh /opt/fmcl --tarball ./FMCL-x.x.x-linux-x86_64.tar.gz
```

### 方式三：从源码运行

依赖统一由 [uv](https://docs.astral.sh/uv/) 管理（`pyproject.toml` + `uv.lock`），
**不要**用 `pip` 直接装进 `.venv`，否则锁文件与实际环境会漂移。

```bash
# 克隆仓库
git clone https://github.com/Janson20/FMCL.git
cd FMCL

# 创建 .venv 并安装锁定版本的全部依赖
uv sync

# 运行启动器（GUI 模式）
uv run python main.py

# 运行 Agent CLI 模式
uv run python main.py --agent "帮我安装最新版"
uv run python main.py -A              # 交互模式
```

> 💡 Windows 上也可以直接用虚拟环境里的解释器：`.\.venv\Scripts\python.exe main.py`
> **Linux 用户注意**：
> - 首次运行前请安装系统依赖：`sudo apt install python3-venv libegl1 libgl1 libxkbcommon-x11-0 libxcb-cursor0`（Debian/Ubuntu）或对应的 Qt 运行库（Fedora）
> - 配置文件存储在 `~/.config/fmcl/config.json`，日志存储在 `~/.local/share/fmcl/fmcl.log`
> - 全局热键（音乐播放器/性能监控）在非 root 用户下不可用，不影响其他功能
> - 音乐静音使用 `Ctrl+Shift+Alt+M`；`Ctrl+Shift+M` 已归性能监控悬浮窗，避免一次按键同时触发两个功能

> **Python 版本要求**：本项目仅支持 Python >= 3.10, < 3.12（依赖与 PySide6 6.7.3 的锁定范围一致）。

#### UI 依赖组（PySide6）

`ui` 组（PySide6 6.7.3）已经写进 `pyproject.toml` 的 `[tool.uv] default-groups`，
所以 `uv sync` / `uv run` 会一并装上、也不会把它卸掉 —— 这是刻意的取舍：Qt 是 QML 界面
（以及部分组件）的运行依赖，而"一次 `uv sync` 悄悄卸掉 Qt"比"多占约 460 MB 磁盘"危险得多
（`ui` 若不是默认组，`uv sync` 默认会卸载 `pyside6-essentials/addons` 与 `shiboken6`，
让 QML 侧的功能与打包突然失效）。

```bash
uv sync                       # dev + ui 全部就位
uv sync --no-default-groups   # 只要最小运行集（不含 pytest 与 Qt，一般不需要）
```

---

## 快速开始

### 安装版本

1. 左侧导航进入「已安装版本」，点「安装新版本」
2. 输入版本 ID（如 `1.20.4`），或从「正式版 / 测试版」列表点选回填（每页 20 条）
3. 按需选择模组加载器：Forge / Fabric / NeoForge / Quilt / LiteLoader / LegacyFabric / Cleanroom / OptiFine
4. 点「安装版本」，进度卡显示下载与安装状态，可随时取消；完成后回到版本列表

### 启动游戏

1. 在左侧导航打开「游戏」，在「最近使用版本」卡片点「启动游戏」；或进入「已安装版本」选择实例后启动
2. 运行中可点「强制结束游戏」；退出后会按退出码与新生成的崩溃报告决定是否弹出诊断

### 常用操作

- **安装模组 / 资源**：进入「已安装版本」，选中实例后点「模组」或「资源管理」；也可从「资源浏览」在线查找并安装到指定实例
- **安装整合包**：进入「已安装版本」→「安装新版本」→「安装整合包」，支持 `.mrpack`、MultiMC、CurseForge、HMCL、MCBBS 与通用压缩包；自定义实例名会为该实例生成独立的版本 JSON，装完即可启动、也可直接进「资源管理」
- **开服**：进入「服务器」→「安装服务器」；也可从「整合包开服」用 `.mrpack` 创建服务器
- **服务器配置**：在服务器列表行或详情页点配置按钮，按 7 类可视化编辑 `server.properties`，支持搜索、恢复默认、原始文本编辑与未保存离开确认
- **服务器模组与资源包**：在服务器详情页进入「模组管理」或「资源包」；模组页可在线浏览并安装到当前服务器。整合包开服后无法判定用途的模组会集中弹窗，可逐项禁用或跳转处理
- **备份存档**：进入「存档」，选择存档后可创建、恢复、校验、导出或删除备份；设置页可改存储路径、压缩等级、保留数量与恢复方式
- **音乐 / 工具 / 成就总览 / AGENT**：从左侧导航进入对应领域

> 详细功能、边界与平台限制见 [docs/FEATURES.md](docs/FEATURES.md)
> 配置项说明详见 [docs/CONFIGURATION.md](docs/CONFIGURATION.md)

---

## 项目结构

> **分层约定**：`界面 → app（服务定位/任务调度/UI 端口/事件总线） → services（业务逻辑，零界面依赖） → launcher 与根模块`。
> `services/` 里**不允许**出现 `tkinter` / `customtkinter` / `PySide6` / `ui.*` 的导入，
> 由 `python scripts/check_services_purity.py` 在 CI 里固化 —— 业务逻辑与界面解耦，
> 换界面（或同时支持两套界面）时不必分叉出两套实现。

```
FMCL/
├── main.py                # 程序入口（启动链、CLI 与界面分发）
├── main_qml.py            # Qt Quick 视图入口：注册桥、加载 App.qml
├── app/                   # 应用层：服务定位、任务、端口、事件；bridges/ 为 QML 适配
│   ├── context.py         # AppContext 服务定位器（拓扑启动顺序）
│   ├── tasks.py           # TaskRunner：后台任务 + 主线程回调 + 进度 + 取消
│   ├── ports.py           # UIPort 协议（NullUIPort / RecordingUIPort）
│   ├── events.py          # EventBus
│   └── bridges/           # QML 上下文属性（Nav / Shell / 各域桥）
├── services/              # 服务层（业务逻辑，零界面依赖；由 CI 强制）
│   ├── base.py            # Service 基类（生命周期 / 依赖查找 / 事件）
│   ├── errors.py          # 统一异常
│   ├── agent/             # AI 智能助手（供应商 / 工具 / 权限 / 技能 / 会话）
│   ├── voice/             # 语音输入
│   ├── music_smtc.py      # Windows SMTC 系统媒体控制（零控件，主线程契约）
│   ├── music_risk_captcha.py # B站风控验证与凭据处理
│   └── *_service.py       # 版本、安装、资源、服务器、备份、音乐、主题、
│                          # i18n、监控、崩溃诊断、工具、联机、成就等域服务
├── fusion_music/          # 音乐子系统：播放核心、音源、歌单、歌词、账号
│   ├── core/              # QMediaPlayer 播放器、队列、歌词、缓存与响度
│   ├── sources/           # 网易云 / QQ / 酷我 / 酷狗 / 咪咕 / B站
│   ├── bridges/           # 音乐 QML 桥
│   └── security/          # 音源请求签名与凭据处理
├── config.py              # 跨平台配置管理（config.json）
├── launcher/              # 启动器核心逻辑
│   ├── core.py            # 环境检查、版本安装、游戏启动
│   ├── server.py          # 服务器安装/启动/停止
│   ├── server_config.py   # 服务器配置读写（server.properties/EULA/每服独立启动配置）
│   ├── mrpack.py          # 整合包安装/开服（.mrpack 格式）
│   ├── multimc.py          # MultiMC 整合包安装/开服（.zip 格式）
│   ├── multimc_types.py    # MultiMC 数据模型定义
│   ├── predownload.py      # 资源包预下载
│   └── verify.py          # 并发文件校验
├── ui/                    # 共享数据：locales/（4 语言）与 static/（等待小游戏）
├── plugin_manager/        # 插件系统（热加载、权限分级、20+ 钩子点）
├── downloader.py          # 多线程/异步下载器
├── modrinth.py            # Modrinth API 集成
├── curseforge.py          # CurseForge API 集成
├── mirror.py              # BMCLAPI 镜像源
├── backup_manager.py      # 存档备份管理
├── secure_storage.py      # 安全存储（Fernet 加密）
├── validation.py          # 输入验证
├── updater.py             # 自动更新
├── structured_logger.py   # 结构化日志（JSONL）
├── achievement_engine.py  # 成就引擎（47 项成就）
├── achievement_sync.py    # 成就云存档同步
├── version_utils.py       # 版本工具（SemVer/正则/YY.D.H）
├── cli_agent.py           # Agent CLI 核心逻辑
├── agent_cli.py           # 独立控制台入口
├── qml/                   # QML 界面（PySide6）：pages / components / shell / assets
├── scripts/               # 构建/发布/安装脚本 + 分层与契约静态检查器
├── tests/                 # 测试
└── docs/                  # 文档
    ├── FEATURES.md        # 完整功能列表
    ├── ARCHITECTURE.md    # 项目架构与技术栈
    ├── CONFIGURATION.md   # 配置说明
    ├── PLUGIN_DEV.md      # 插件开发指南
    └── ...
```

> `services/` 与 `fusion_music/` 不依赖 Qt 控件；QML 只通过 `app/bridges/` 的上下文属性
> 取数与发命令，业务判断留在服务层。完整模块边界与依赖关系详见
> [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

---

## 开发指南

### 环境设置

```bash
# 安装全部依赖（含 dev 组）到 .venv，使用锁文件里的精确版本
uv sync

# 需要 Qt/QML 界面依赖时（PySide6）：
uv sync --group ui

# 运行启动器
uv run python main.py

# 新增依赖（会同时更新 pyproject.toml 与 uv.lock）
uv add 包名
uv add --group dev 开发期包名

# 安装 Git hooks (Husky + Commitlint)
npm install
npm run prepare
```

### 常用命令

```bash
make dev              # 开发模式运行
make run              # 运行程序
make check            # 检查环境和依赖
make lint             # 代码检查 (flake8 + mypy)
make fix              # 运行常见问题修复工具
make clean            # 清理构建文件
```

```bash
# 直接跑测试
uv run pytest -q
```

> **测试默认并行**（`pytest-xdist`，见 `pyproject.toml` 的 `addopts`）：全量约 2.5 分钟，
> 串行用 `pytest -n0`。写「会装配真应用」的测试时记得隔离两样东西：真配置的写盘
> （`tests/config_isolation.py`）与单实例守卫（`tests/single_instance_isolation.py`）。

### 构建

```bash
make build            # PyInstaller 构建 QML 目录产物 dist/FMCL/（含 Qt 运行时与 QML 模块）
make build-qml        # 兼容旧命令，等价于 make build
make build-installer  # Windows x64 NSIS 安装包 (需要 NSIS)
make build-qml-installer  # 同 build-installer，保留旧命令
make build-dmg        # macOS DMG 磁盘映像 (仅 macOS)
make build-deb        # Linux DEB 包
make build-appimage   # Linux AppImage
```

> QML 产物有个**不入库**的构建前置：`third_party/` 下自编译的 FluentUI 模块（体积原因只入库
> 来源记录与脚本）。缺了它 `build.spec` 会**直接报错停下**并打印补救命令，而不是产出一个
> "看着正常、一启动就没有界面"的包。补齐方式：
>
> Windows：
> `powershell -NoProfile -ExecutionPolicy Bypass -File third_party/fetch_sources.ps1`
> 然后 `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_fluentui.ps1 -QtDir <Qt6.7.3>`。
>
> Linux：
> `bash scripts/fetch_sources_linux.sh`
> 然后 `bash scripts/build_fluentui_linux.sh --qt-dir "$HOME/Qt/6.7.3/gcc_64"`。

> 构建问题排查请参考 [docs/BUILD_FIXES.md](docs/BUILD_FIXES.md)

### 发布流程

1. 更新 `pyproject.toml` 和 `package.json` 中的版本号
2. 提交变更：`git commit -m "chore: release vX.X.X"`
3. 创建标签：`git tag vX.X.X`
4. 推送：`git push origin main --tags`

GitHub Actions 在 tag 推送时会构建 Windows x64 安装包 + Linux x86_64 tarball 并创建 Release；
手动触发 `workflow_dispatch` 只构建并上传 Actions artifacts，**不会创建 Release**，可用于测试安装包和 Linux 构建。
macOS 二进制包暂不随发布提供。

详见：[CONTRIBUTING.md](CONTRIBUTING.md) | [docs/SETUP.md](docs/SETUP.md)

### 提交规范

本项目使用 [约定式提交](https://www.conventionalcommits.org/)：

```bash
feat: 添加新功能
fix: 修复 bug
docs: 更新文档
refactor: 重构代码
perf: 性能优化
chore: 构建/工具变动
```

---

## 故障排除

查看完整指南：[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)

| 问题 | 解决方案 |
|------|----------|
| 镜像源连接失败 | 尝试关闭「国内镜像」开关使用 Mojang 官方源 |
| 版本安装失败 | 检查网络连接，查看 `latest.log` 日志 |
| 游戏启动失败 | 确保已安装 Java 运行时 |
| 启动失败提示找不到主类 `@C:\...` | Java 8 不支持 `@参数文件`，已自动回退直接启动；如仍失败请检查 Java 版本 |
| macOS 提示无法验证开发者 | `xattr -cr FMCL.app` 移除隔离属性 |
| Linux 配置文件存储位置 | `~/.config/fmcl/config.json`（自动创建，无需手动干预） |
| Linux 无图形环境崩溃 | WSL/无头服务器下鼠标检测线程会自动跳过，不会崩溃 |

---

## 许可证

- **v2.8.4 及以前版本**：使用 [MIT License](LICENSE)
- **v2.8.4 以后版本**：使用 [GNU General Public License v3.0](LICENSE)

Copyright (c) 2026 FMCL Team
