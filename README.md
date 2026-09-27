# FMCL - Fusion Minecraft Launcher
![License](https://img.shields.io/github/license/Janson20/FMCL?color=blue)
![GitHub Release](https://img.shields.io/github/v/release/Janson20/FMCL?include_prereleases&sort=semver)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)
![Downloads](https://img.shields.io/github/downloads/Janson20/FMCL/total?color=orange)
![Stars](https://img.shields.io/github/stars/Janson20/FMCL?style=social)

一个基于 `CustomTkinter` 的**现代 Minecraft 启动器**，集 **版本管理、多模组加载器（Forge/Fabric/NeoForge/Quilt 等8种）、国内 BMCLAPI 镜像加速、模组与整合包管理、服务器一键开服、P2P 陶瓦联机、存档备份、AI Agent 助手、音乐播放器、性能监控悬浮窗、动态主题、成就系统、崩溃分析、安全加密、插件系统、多语言、自动更新** 于一身——用开源的力量，打造真正全能的游戏伴侣。

---

## 功能特点

| 分类 | 简介 | 详情 |
|------|------|------|
| 🎮 版本管理 | 浏览/安装/删除 Minecraft 版本，支持正式版与测试版 | 每页 20 个，支持一键选择、快捷操作，JSON 解析版本信息 |
| 🔧 多模组加载器 | Forge / Fabric / NeoForge / Quilt / LiteLoader / LegacyFabric / Cleanroom / OptiFine 一站式安装，版本隔离 | 与原版并行安装，自动适配 YY.D.H 新版本格式，JSON 检测加载器类型 |
| ⚡ 国内镜像加速 | 内置 BMCLAPI 镜像源，国内下载速度大幅提升 | 一键开关，覆盖版本清单/资源/库/安装器 |
| 🧩 模组管理 | Modrinth + CurseForge 在线搜索安装，AI 搜索 | 自动匹配版本和加载器，支持依赖递归安装 |
| 📦 整合包 | 支持 Modrinth / CurseForge / MultiMC / HMCL / MCBBS / 通用压缩包 安装与开服 | 并行安装优化，分段进度显示，增量更新，自动格式检测 |
| 🖥 服务器管理 | 一键安装/启动 MC 服务器，实时日志与命令交互 | 自动同意 EULA，智能 Java 管理，1G~16G 内存 |
| 🌐 陶瓦联机 | 基于 EasyTier P2P 虚拟组网，局域网广播模拟 | Base34 大厅编号，TCP 端口转发，成员管理，与 HMCL / PCL CE 房间互通 |
| 💾 存档备份 | 手动/自动备份，一键恢复，压缩/校验/导出 | 支持版本隔离目录扫描，备份索引记录 |
| ⛏ 基岩版 | 基岩版（Bedrock Edition）下载与启动（Windows 10 19041+） | 基于 BedrockBoot 方案：UWP/GDK 双类型全版本库（McAppx 多源回退），GDK XVD 容器解压（委托 .NET MIT 库 BedrockLauncher.Core，与 BedrockBoot 同源，需 .NET 10 SDK，缺失时引导下载），UWP AppX 注册，环境自动检测修复（开发者模式/GameService/VC 运行时/GameInput，Gaming Services 缺失时下载前自动用 winget 安装），多线程断点下载 + MD5 校验，游戏内 Xbox 登录；GDK 版认证注入启动（首次使用时经用户同意，从 BedrockBoot 官方 NuGet 按需下载闭源认证组件） |
| 🤖 AGENT 助手 | 自然语言控制启动器，33 个工具可供 AI 调用 | 多模型支持，流式 SSE 输出，50 轮工具循环 |
| 🎙 语音输入 | AGENT 输入框与主界面顶部输入框一键语音输入 | 基于 SenseVoice-Small 离线识别模型（自带标点与数字转换），DirectML/CPU 自适应加速，首次使用自动下载模型，可在设置中手动导入本地模型压缩包（GitHub 下载过慢时），点击开始/停止录音，识别结果自动填入输入框 |
| 🎵 音乐播放器 | 本地播放 + 在线搜索（酷我/酷狗/咪咕/QQ/网易） | 双模式切换、歌词、桌面歌词、音效、SMTC、搜索结果分页与播放量显示、原唱置顶识别、百度百科原唱兜底、跨音源兜底播放、自动音质选择、网易云 VIP 登录、网易云账号歌单同步（只读）、歌单预取秒播、歌名/歌手一键复制 |
| 🔧 工具集 | 垃圾清理、坐标转换、Hash 计算、端口检测、冷知识、知识问答 | 自定义扫描文件夹与深度，勾选列表逐个删除，计算结果一键复制，多线程下载，今日人品 |
| ⏱ 性能监控 | Ctrl+Shift+M 悬浮窗，CPU/内存/GPU 实时监控 | 半透明置顶窗口，拖拽移动，GPU 温度显存显示 |
| 🎨 动态主题 | 5 种预设 + 自定义 Hex + 版本动态颜色 | 支持导入 .json 主题，主题持久化保存 |
| 🏆 成就系统 | 47 项成就，9 大分类，Toast 通知，云存档同步 | 多阶段成就，进度条可视化，每日签到 |
| 💥 崩溃分析 | 智能诊断 + 净读 AI 深度分析 | 识别 11 种崩溃类型，支持导出报告和 AI 修复建议 |
| 🔒 安全 | SSL 验证、Fernet 加密、输入防注入、SHA 校验 | 密钥文件可迁移，断点续传，原子写入，安装回滚 |
| 🧩 插件系统 | 插件管理器 + 权限分级 + 钩子扩展 + 热加载 + 在线市场 | 支持 .fmpl 格式，本地安装/在线市场/权限管理，20+ 钩子点 |
| 🎮 等待小游戏 | 下载/安装时在浏览器游玩「深渊快线」 | 自动在底部显示入口按钮，完成后自动隐藏 |
| 💬 状态提示 | 底部状态栏即时反馈操作结果 | 成功/失败/警告等提示 10 秒后自动清空，加载中状态常驻 |
| 🌐 多语言 | 简体中文 / English / 繁體中文 / 日本語 | 自动检测系统语言，设置内一键切换即时生效 |
| ⬆ 自动更新 | GitHub Release 检测 + 静默安装 | 自动识别平台下载对应安装包，可配置开关 |

> 完整功能列表详见 [docs/FEATURES.md](docs/FEATURES.md)

---

## 界面预览

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│  ⛏ FMCL   Minecraft Launcher   ⬆ 更新  🔄 刷新  ⚙ 设置  │
│  [🎮 游戏] [基岩] [💾 备份] [🖥 开服] [🔗 链接] [🌐 联机] [🎵 音乐] [🤖 AGENT] [🏆 成就] [📜]│
├────────────┬──────────────────────────────┬──────────────────────────────────────┤
│ 👤 账号  │  📦 已安装版本  ⚙  3 个版本 │  📥 安装新版本               │
│ ⭐ Steve │  ─────────────────────────── │  ──────────────────────      │
│            │  │ 1.20.4          🧩⚙[X] │  │  版本 ID:  [1.20.4      ]   │
│ 🎨 皮肤   │  │ 1.20.4-forge-49🧩⚙[X] │  │  模组加载器: [无      ▼]     │
│ ✅ skin.png│  │ fabric-loader-0🧩⚙[X] │  │  提示: 安装 Forge 会同时... │
│ [选择][🗑] │  │                        │  │  [📥 安装版本]              │
│            │  │                        │  │                              │
│ 📋 日志   │  │                        │  │  📋 快速选择                 │
│ [14:30:01] │  │                        │  │  ──────────────────          │
│ 环境初始化 │  │                        │  │  📦 正式版  🔬 测试版        │
│ 完成       │  │                        │  │  [📦 1.21.4] [📦 1.21.3]    │
│            │  └────────────────────────┘  │  [📦 1.21.2] [📦 1.21.1]    │
│            │  [🚀 启动游戏] [⏹]           │  ◀  1/12  ▶                 │
│            │                              │                              │
│ [清空日志] │                              │                              │
├────────────┴──────────────────────────────┴──────────────────────────────┤
│  ✅ 已安装 3 个 | 正式版 842 个 | 测试版 312 个    ████░░ 45% │
└──────────────────────────────────────────────────────────────────────────────────┘
```

---

## 环境要求

| 依赖 | 说明 |
|------|------|
| Python 3.10+ | 运行启动器 |
| Java 8+ | 运行 Minecraft（启动器自动扫描系统 Java，推荐版本由 MC 版本决定） |
| Linux: `python3-tk` | 系统包（Ubuntu/Debian: `sudo apt install python3-tk python3-venv`） |

---

## 安装

### 方式一：从 Release 下载（推荐）

前往 [Releases](https://github.com/Janson20/FMCL/releases) 页面下载适合你平台的安装包：

| 平台 | 文件 | 说明 |
|------|------|------|
| Windows | `FMCL-Setup-x.x.x.exe` | NSIS 安装包，双击运行 |

> **注意**：自 **v2.10.3** 起，不再通过 GitHub Actions 构建并发布 macOS（`.dmg`）与 Linux（`.deb` / `.AppImage`）二进制包。Linux 用户请使用下方「方式二：Linux 一键安装」脚本进行安装；macOS 用户请参考「方式三：从源码运行」。

#### 安装说明

- **Windows**: 双击 `.exe` 安装包，按向导完成安装。安装包已内置 7-Zip（26.02 版本），无需联网即可自动安装。安装器按当前用户安装到 `%LOCALAPPDATA%\Programs\FMCL`，**全程无需管理员权限**，安装后直接双击快捷方式即可正常使用
- **Linux**: 请使用下方「方式二：Linux 一键安装」脚本
- **macOS**: 请参考下方「方式三：从源码运行」

> **数据目录说明**：启动器优先使用自身所在目录存放数据（便携模式，`.minecraft` 等数据跟随 exe）。若所在目录不可写（如手动把程序放入 `C:\Program Files` 等需要管理员权限的目录），会自动回退到用户数据目录（Windows: `%LOCALAPPDATA%\FMCL`；macOS: `~/Library/Application Support/FMCL`），并将旧目录中已有的数据自动迁移过去，无需管理员身份即可正常使用。

### 方式二：Linux 一键安装

```bash
curl -fsSL https://raw.githubusercontent.com/Janson20/FMCL/main/scripts/install.sh | bash
```

脚本自动完成：安装系统依赖 → 安装 uv → 克隆项目 → uv sync → 注册 `fmcl` 命令。

支持 Debian / Ubuntu / Fedora / RHEL / CentOS / Arch / openSUSE，安装完成后直接运行 `fmcl` 即可启动。
自定义安装目录：`./scripts/install.sh ~/.local/share/fmcl`

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
> - 首次运行前请安装系统依赖：`sudo apt install python3-tk python3-venv`（Debian/Ubuntu）或 `sudo dnf install python3-tkinter`（Fedora）
> - 配置文件存储在 `~/.config/fmcl/config.json`，日志存储在 `~/.local/share/fmcl/fmcl.log`
> - 全局热键（音乐播放器/性能监控）在非 root 用户下不可用，不影响其他功能

> **Python 版本要求**：本项目仅支持 Python >= 3.10, < 3.12。pygame 在 Windows Python 3.12+ 上无可用二进制包。

#### UI 依赖组（PySide6）

`ui` 组（PySide6 6.7.3）已经写进 `pyproject.toml` 的 `[tool.uv] default-groups`，
所以 `uv sync` / `uv run` 会一并装上、也不会把它卸掉 —— 这是刻意的取舍：迁移期旧 Tk 界面与
新的 QML 界面并存，而"一次 `uv sync` 悄悄卸掉 Qt"比"多占约 460 MB 磁盘"危险得多
（`ui` 若不是默认组，`uv sync` 默认会卸载 `pyside6-essentials/addons` 与 `shiboken6`，
让 QML 侧的 POC 与打包突然失效）。

```bash
uv sync                       # dev + ui 全部就位
uv sync --no-default-groups   # 只要最小运行集（不含 pytest 与 Qt，一般不需要）
```

---

## 快速开始

### 安装版本

1. 在右侧面板输入版本号（如 `1.20.4`）
2. 选择模组加载器（可选）：Forge / Fabric / NeoForge / Quilt / LiteLoader / LegacyFabric / Cleanroom / OptiFine
3. 点击「📥 安装版本」，等待完成

### 启动游戏

1. 在左侧「已安装版本」列表中点击要启动的版本
2. 点击底部「🚀 启动游戏」

### 常用操作

- **安装模组**：已安装加载器的版本右侧点击 🧩 按钮，搜索安装 Modrinth 模组
- **安装整合包**：点击「📦 安装整合包」，选择 .mrpack 文件或 .zip 文件（MultiMC 格式），或从 Modrinth 下载
- **开服**：切换到"🖥 开服"标签页，安装并启动 Minecraft 服务器
- **服务器配置**：在"🖥 开服"标签页点击某个服务器的 🔧 按钮，可视化编辑该服务器的 `server.properties`（每项都有说明，支持搜索与恢复默认）
- **备份存档**：切换到"💾 备份"标签页，手动或自动备份存档

> 完整使用说明详见 [docs/USAGE.md](docs/USAGE.md)
> 配置项说明详见 [docs/CONFIGURATION.md](docs/CONFIGURATION.md)

---

## 项目结构

> **分层约定（阶段 1 起）**：`界面 → app（服务定位/任务调度/UI 端口/事件总线） → services（业务逻辑，零界面依赖） → launcher 与根模块`。
> `services/` 里**不允许**出现 `tkinter` / `customtkinter` / `PySide6` / `ui.*` 的导入，由
> `python scripts/check_services_purity.py` 在 CI 里固化。这条约束的目的是：正在进行中的
> PySide6 + QML 界面重构可以与现有 Tk 界面**共用同一份业务逻辑**，而不是分叉出两套实现。

```
FMCL/
├── main.py                # 程序入口（支持 GUI / CLI 双模式）
├── app/                   # 应用层（界面无关）
│   ├── context.py         # AppContext 服务定位器（拓扑启动顺序）
│   ├── tasks.py           # TaskRunner：后台任务 + 主线程回调 + 进度 + 取消
│   ├── ports.py           # UIPort 协议（NullUIPort / RecordingUIPort）
│   └── events.py          # EventBus
├── services/              # 服务层（业务逻辑，零界面依赖；由 CI 强制）
│   ├── base.py            # Service 基类（生命周期 / 依赖查找 / 事件）
│   ├── errors.py          # 统一异常
│   ├── music_source/      # 5 个在线音源的检索与解析
│   ├── music_audio.py     # 音频元数据/时长校验/文件头魔数/m4a 转码（任务 1.4-A）
│   ├── music_smtc.py      # Windows SMTC 系统媒体控制（零控件，主线程契约）
│   ├── music_player.py    # 播放引擎状态机：淡入淡出/预取/进度/播放模式/目录扫描
│   ├── music_state.py     # 音乐状态读写规则（键名/默认值/容错/周期参数）
│   ├── music_online.py    # 在线搜索编排/自动音质/取流完成判定/正在播放取值（任务 1.4-B）
│   ├── music_download.py  # 多源回退下载编排、临时文件规则、B站风控重试编排（任务 1.4-B）
│   ├── music_wy_remote.py # 网易云远程歌单同步编排与分页（只读、不落盘，任务 1.4-B）
│   ├── desktop_lyric.py   # 桌面歌词的零界面逻辑（位置/当前行/透明度/锁定）
│   ├── agent/             # AI 智能助手（供应商 / 工具 / 权限 / 技能 / 会话）
│   ├── voice/             # 语音输入
│   └── *_service.py       # 主题、i18n、监控、崩溃诊断、语音、工具、服务器、
│                          # 联机、Agent、成就等各域服务
├── config.py              # 跨平台配置管理（26 项配置）
├── launcher/              # 启动器核心逻辑
│   ├── core.py            # 环境检查、版本安装、游戏启动
│   ├── server.py          # 服务器安装/启动/停止
│   ├── server_config.py   # 服务器配置读写（server.properties/EULA/每服独立启动配置）
│   ├── mrpack.py          # 整合包安装/开服（.mrpack 格式）
│   ├── multimc.py          # MultiMC 整合包安装/开服（.zip 格式）
│   ├── multimc_types.py    # MultiMC 数据模型定义
│   ├── predownload.py      # 资源包预下载
│   └── verify.py          # 并发文件校验
├── ui/                    # 当前界面（CustomTkinter；正在迁移到 PySide6 + QML）
│   ├── app.py             # 主窗口（12 Mixin 组合模式）
│   ├── agent/             # AI 助手界面部分（业务实现已搬进 services/agent/）
│   ├── windows/           # 15 个独立子窗口
│   ├── static/            # 静态资源（等待小游戏等）
│   ├── theme_engine.py    # 动态主题引擎
│   └── i18n.py            # 国际化（4 语言）
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
├── scripts/               # 构建/发布/安装脚本 + 分层与契约静态检查器
├── tests/                 # 测试
└── docs/                  # 文档
    ├── FEATURES.md        # 完整功能列表
    ├── USAGE.md           # 详细使用指南
    ├── ARCHITECTURE.md    # 项目架构与技术栈
    ├── CONFIGURATION.md   # 配置说明
    ├── PLUGIN_DEV.md      # 插件开发指南
    ├── refactor/          # 界面重构的过程文档（决策、对照表、缺陷清单、执行日志）
    └── ...
```

> 迁移期间，被搬进 `services/` 的模块会**在原路径留下兼容别名**（例如 `ui/music_lyrics.py`
> 实际是 `services/music_lyrics.py` 的别名），因此插件与第三方代码按旧路径导入仍然有效。
> 音乐播放这一域（任务 1.4-A）是**按能力切分**而不是整文件搬家：`ui/app_music.py` 仍是
> `MusicPlayerMixin` 的宿主（183 个方法名与签名一个都没变），但音频解析、SMTC、播放状态机、
> 状态持久化与桌面歌词的零界面逻辑分别住进了上表的 `services/music_*.py` 与
> `services/desktop_lyric.py`，旧私有名（`_extract_audio_metadata` 等）在 `ui/app_music.py`
> 里保留为**指向同一实现**的别名。
> 音乐播放域的**在线侧**（任务 1.4-B）沿用同一套切缝：在线检索与分页、自动音质解析与
> 音质信息补齐、取流完成判定与正在播放取值住进 `services/music_online.py`，多源回退下载、
> 临时文件的命名/裁剪/清理与 B站风控重试编排住进 `services/music_download.py`，
> 网易云账号歌单的同步状态机与分页住进 `services/music_wy_remote.py`（远程歌单只读、
> 不进 `PlaylistManager`、不落盘）。服务侧把这些值当参数收进来、把新值放在返回值里
> （`SearchOutcome` / `PagerPlan` / `SyncApplyPlan` 等数据类），**控件、线程、`after`
> 与全部文案仍留在界面**；网络入口（`requests.get`、音源表、网易云后端）都有注入缝，
> 默认值就是真实实现，因此在线侧整套逻辑可以离线单测。
> 基岩版与成就这两域（任务 1.11 / 1.12）也是**按能力切分**：`ui/app_bedrock.py` 与
> `ui/app_achievements.py` 仍是 `BedrockMixin`（28 个方法）/ `AchievementTabMixin`
> （19 个方法）的宿主，方法名与签名一个都没变，但版本过滤与分页、安装/启动/删除编排、
> GDK 的 .NET 10 与闭源认证组件前置检查、微软账户设备码登录流程住进了
> `services/bedrock_service.py`，进度统计、同步/重置编排、解锁载荷归一化住进了
> `services/achievement_service.py`；两个界面文件里的每个方法都退化成对服务的薄委托，
> 弹窗、控件、线程调度与**全部文案**仍留在界面。
> 完整项目结构与模块依赖关系详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

---

## 开发指南

### 环境设置

```bash
# 安装全部依赖（含 dev 组）到 .venv，使用锁文件里的精确版本
uv sync

# 需要做 QML 界面开发时，额外装 ui 依赖组（PySide6 6.7.3）
uv sync --group ui

# 运行 QML 版界面（阶段 2 的骨架：入口装配 / 主题 / i18n / 导航 / 对话框 / 悬浮窗）
# 注意：需要先编译 FluentUI 插件（阶段 0 的产物，不入库）：scripts/build_fluentui.ps1
uv run python main_qml.py

# 新增依赖（会同时更新 pyproject.toml 与 uv.lock）
uv add 包名
uv add --group dev 开发期包名

# 安装 Git hooks (Husky + Commitlint)
npm install
npm run prepare
```

### 静态检查（迁移期间新增，CI 固化）

```bash
# services/ 层不得依赖任何界面栈（tkinter / customtkinter / PySide6 / ui.*）
uv run python scripts/check_services_purity.py

# 界面注入给业务逻辑的回调键必须齐全（防止"按钮点了没反应"）
uv run python scripts/check_callback_keys.py --fail-on-soft

# i18n：4 语言键集合一致、无缺失键、占位符跨语言一致、调用点参数齐全
uv run python scripts/check_i18n.py

# 模块搬家的完整性（主体逐节点一致 / 行数一致 / 旧路径别名同一对象）
uv run python scripts/relocate_module.py --check

# QML 规则闸门（阶段 2 新增，R1~R9）：禁渐变与亚克力材质、禁 emoji、禁硬编码中文、
# 绑定必须走 Tr.map、页面不得越界 import、悬浮窗不得用 color: "transparent"、
# 桥的线程红线、QML 里不得出现颜色字面量（颜色只能来自 Theme.*）、
# 界面里不得出现 Qt 原生视觉控件（一律用 qml/components 里的 Fm*）
uv run python scripts/check_qml_rules.py
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

### QML 组件库（阶段 2 任务 2.16 / 2.17；返工 B/C 组各补过件）

阶段 2 之后的界面**一律用 `qml/components/` 里的通用件拼**，页面里不再出现"一次性控件"
（`03` 的 3.0 SOP 第 3 条），也**不再出现 Qt 原生控件**（闸门 **R9**，返工 C 组新增）。
清单与用法在 **[qml/components/COMPONENTS.md](qml/components/COMPONENTS.md)**（26 个件），
它同时是闸门 R5 的白名单数据源（页面用了白名单外的自研件即违规）。

**每个页面的根节点都是 `FmPage`**（返工 C 组新增）：它承担路由帧五件套、页头与内容区三态
（`contentState` = ready / loading / empty / error），12 个内置领域页因此各自只剩 22 行。
一个页面现在长这样：

```qml
import "../../components"

FmPage {
    objectName: "versionsPage"          // 冒烟测试与探针按它找页面，不能改
    contentState: versionsModel.state   // ready 时显示页面内容，其余显示三态块
    emptyText: Tr?.map["versions_none"] ?? "versions_none"
    retryText: Tr?.map["refresh"] ?? "refresh"
    onRetried: versionsModel.reload()

    FmTable { anchors.fill: parent; columns: page.cols; rows: page.rows }
}
```

```qml
import QtQuick
import "../../components"          // 相对当前 QML 文件；阶段 2 不引入 qmldir 模块声明

Item {
    FmCard {
        title: Tr?.map["account_manager_title"] ?? "account_manager_title"

        FmTextField { label: "ID"; text: page.versionId; errorText: page.idError }

        FmButton {                       // 主按钮：一个界面里最多一个
            text: Tr?.map["confirm"] ?? "confirm"
            loading: page.installing      // 三态：enabled / disabled / loading
            onClicked: page.install()
        }

        footer: [
            FmButton { primary: false; text: Tr?.map["cancel"] ?? "cancel"; onClicked: page.close() }
        ]
    }
}
```

**四条纪律**（都由 `scripts/check_qml_rules.py` 静态拦，别只靠自觉）：

1. **颜色**：只来自 `Theme.*` 的 12 个语义色键、15 个派生令牌与设计令牌
   （`bgDark/bgMedium/bgLight/accent/accentHover/success/warning/error/textPrimary/textSecondary/cardBg/cardBorder`
   ＋ `windowBg/windowBgInactive/navBg/barBg/cardHover/divider/overlayBg/scrim/textTertiary/accentSoft/accentPressed/accentText/itemHover/itemPress/itemCheck`
   ＋ `fontSizeSmall/Base/Large/Title`、`spacingXs/Sm/Md/Lg/Xl`、`radiusSm/Md/Lg`、`iconSize`、`fontFamily`、
   `durationFast/Normal/Slow`、`navWidth/titleBarHeight/statusBarHeight`）。
   派生令牌由 **Python 侧**从 12 个主题色算出来（`app/bridges/theme_bridge.py: derive_tokens`）——
   QML 里不许自己调 `Qt.rgba` / `Qt.lighter`，出现 `#e94560` 这类字面量即 **R8** 违规；
   渐变与亚克力材质是 **R1**（项目 UI 红线 5）。
   界面的明暗**锁深色**（5 个预设主题全是深色）：`FluTheme.darkMode` 被显式设成
   `FluThemeType::DarkMode::Dark`（**值 2**，不是 `Qt::ColorScheme` 的 1 —— 写成 1 是浅色，
   会让 FluentUI 控件与我们的壳层撞色）。
2. **文案**：绑定写 `Tr.map["键"] ?? "键"`，**不要**在绑定里写 `Tr.t("键")`——QML 只跟踪属性读取，
   语言切换时函数返回值不会重算（契约第六节决策 1，闸门 **R4**）；字符串里写死中文是 **R3**。
3. **图标**：一律 `FmIcon { name: "check"; color: Theme.accent }`，名字取自 `qml/assets/icons/*.svg`
   （小写 + 连字符、语义命名，见该目录的 [README](qml/assets/icons/README.md)）。**界面禁用 emoji**（**R2**）。
4. **控件**：从 `COMPONENTS.md` 第二节里挑，**不许直接用 Qt 原生控件**（**R9**，返工 C 组新增）。
   理由是实测出来的：本项目跑的是 QtQuick Controls 的 **Basic** 样式
   （`main_qml.py: create_application()` 设的 `QT_QUICK_CONTROLS_STYLE`），原生控件的底色与
   文字色来自**系统调色板** —— 锁深色的界面里必然是浅色的外来件，而且不跟随 `Theme.*`。
   三条边界：**同名包装器**放行（`FmSwitch.qml` 的根节点本来就是 `Switch`，白名单在闸门的
   `NATIVE_CONTROL_WRAPPERS` 里）；**附着属性的名字**不算（`ScrollBar.vertical:` 来自 Qt，
   换的是值那个实例）；**零依赖兜底窗** `qml/FatalError.qml` 豁免（它连 `Theme`/`Tr` 都没有，
   由测试钉住"确实不读任何上下文属性"）。FluentUI 自己的控件还没拦，登记为候选 R10。

**新增一个组件**：加 `qml/components/FmXxx.qml`（文件头写清"什么时候用它 / 什么时候不要用"、
根节点给稳定的 `objectName`）→ 在 `COMPONENTS.md` 的白名单表里登记一行 → 跑
`python scripts/check_qml_rules.py` 与 `python -m pytest tests/test_components_qml.py -q`
（后者会实例化**每一个** `Fm*.qml` 并核对 Gallery 里有没有对应实例）。

**图标上色（`image://fmcl-icon`）**：QtSvg 把 SVG 里的 `currentColor` 解析成**不透明黑**，
而 `ColorOverlay` / `MultiEffect` 在 `offscreen`（本仓库所有测试的跑法）下**静默失效**
（实测对比表见 `qml/assets/icons/README.md` 第三节）。所以上色在 **Python 侧**做：
`app/bridges/icon_provider.py` 把 SVG 文本里的 `currentColor` 换成目标色后用 `QSvgRenderer`
渲成 `QImage`，QML 侧按 `image://fmcl-icon/<名字>?color=%23RRGGBB` 取图（`FmIcon` 已经封装好）。
装配期需要在 `engine.load()` **之前**注册一次：

```python
from app.bridges.icon_provider import install as install_icon_provider
install_icon_provider(engine)          # 见 main_qml.assemble()
```

**组件画廊（开发自查页）**：`qml/pages/dev/Gallery.qml`，一页展示每个组件的"名字 + 说明 + 活的实例"。
它**刻意不在 12 个一级导航里露出**，进入方式是深链 `fmcl://dev/gallery` 或
`Nav.push("dev/gallery")`（路由定义在 `app/bridges/nav_bridge.py`，`parent` 挂 `settings`）。
跑一次自查并留下截图证据：

```bash
uv run python tests/test_components_qml.py     # 截图写到 poc/gallery_2_16/，清单写到 poc/components_2_16.txt
```

**当前组件清单是 21 个**（返工 B 组加了 `FmToolButton`：顶栏与行尾的纯图标命中区，
带选中/角标/禁用三态）。

### 启动画面与主题底座（界面返工 A 组）

启动流程的**时序**在 Python 侧（`app/startup.py` 的 `StartupController`，复刻旧 `main.py` 的
4 条互相竞争的退出路径：≥1 秒显示 / 30 秒硬超时 / 初始化失败仍显示主窗口 / 关画面失败也继续），
QML 只负责按 `Startup.startupActive` 显示：

* `startupActive === true` → 启动画面显示、**主窗口藏起来**（不再是"两层窗口叠在一起"）；
* 变成 false → 启动画面淡出后隐藏、主窗口显示，并由 `main_qml.raise_main_window()`
  把主窗口提到前面（对照旧实现的 `app.lift() + app.focus_force()`）。

启动流程**没开跑**时（测试装配、探针、`assemble(start_startup=False)`）这个属性恒为 false ——
不会出现"一个没人去关的加载窗"，那条路径以前正是"加载完不消失"的来源。

界面返工 A 组同时修掉了三处会让界面看起来"配色打架"的缺陷：
FluentUI 的暗色取值（原来写成了浅色）、注入 FluTheme 的写序（`darkMode` 必须先写，
否则它触发的 `refreshColors()` 会把刚注入的颜色全部冲掉）、以及图标默认色（原来是黑，
深色底上几乎看不见）。

```bash
# 真机启动时序的可见性走查（子进程跑真 QML：启动画面出现 → 消失 → 主窗口出现）
uv run python -m pytest tests/test_startup_visibility.py -q
uv run python tests/qml_startup_probe.py          # 直接看 JSON 观察结果
```

### 壳层（返工 B 组：一条顶栏 + 分组导航）

窗口顶部现在是**一条** 40px 的顶栏（`qml/shell/AppBar.qml`，`FluAppBar` 的子类）：
左边「返回 + 面包屑」、右边「搜索 + 通知 + 账号」，最右侧 120px 是窗口按钮 ——
返工前这里是**两条**横条（`FluWindow` 内置的 appBar + 自绘的 `TitleBar`），
颜色还各走一套来源，观感上就是"上下分裂"。

左侧导航按**分组**排（游戏 / 资源与联机 / 工具与扩展），选中项是
`accentSoft` 底 + 3px 强调色指示条 + 强调色图标与文字；分组数据来自桥
（`app/bridges/nav_bridge.py` 的 `NAV_GROUPS`），不在 QML 里写死。

> **顶栏上的按钮必须登记进命中测试白名单**：无边框窗口的拖动由 Win32 命中测试实现，
> 光标落在顶栏里且不在白名单项上时返回 `HTCAPTION`，QML 侧**收不到事件**（不报错、只是没反应）。
> 登记处是 `qml/App.qml` 的 `Component.onCompleted`（遍历 `AppBar.interactiveItems`），
> `tests/test_shell_qml.py` 会断言"声明的项恰好是界面上那四个"。

```bash
# 骨架走查（子进程跑真 QML：四层结构、分组顺序、选中态、点导航切页）
uv run python -m pytest tests/test_shell_qml.py -q
uv run python tests/qml_shell_probe.py            # 直接看 JSON 观察结果
```

### 页面骨架与原生控件清零（返工 C 组）

页面那一层做了两件事：**把 12 份重复收敛成一份**、**把 Qt 原生控件清零**。

- 12 个内置领域页原来是同一份 145 行代码各抄一遍（只有 `objectName` 与领域名不同，
  合计 1740 行）。现在每页 22 行，页头、路由帧与三态渲染都在 `qml/components/FmPage.qml` 里
  （合计 264 行）；重复度实测见 `poc/_gen_placeholder_pages.py`（归一化领域名/节号/objectName
  后比 SHA256，12 个文件全等）。
- 页面里的开发噪声（那行 `route: … params: …`）删掉了。深链与跳转的取证方式随之改成读页面的
  `routeId` / `routeParams` 属性（`tests/qml_shell_probe.py` 的 `page_frame()`）——
  断言的东西没变：参数必须真的落到页面上。
- 原生控件清零后新增闸门 **R9**：`qml/**` 里出现 Qt 原生控件（`Button` / `TextField` /
  `ScrollBar` / `CheckBox` / `SpinBox` / `ScrollView` …）即违规。为此补了五个自研件：
  `FmPage`、`FmScrollView`、`FmScrollBar`、`FmSpinBox`、`FmCheckBox`。
  一并换掉的还有 `LogView` 的工具栏与滚动条、`StartupDialogs` 的勾选框与滚动区、
  `TextInputDialog` 的输入框、`ProgressDialog` 的进度件。

```bash
# 闸门（R9 会拦住原生控件漏进界面；负例与变异证明在 tests/test_qml_rules_gate.py）
uv run python scripts/check_qml_rules.py
# 12 个占位页的重复度实测（归一化后比 SHA256）
uv run python poc/_gen_placeholder_pages.py
```

> **`FmIcon` 的一个坑（返工 C 组实测并修好）**：`Image.source` **不能**读一个"自身也依赖
> `name` 的派生属性"（原来是 `name.length > 0 ? providerUrl : ""`）—— 名字从空变成非空时，
> Qt 会先拿**上一次的缓存值**求值一次，于是每次新建页面都刷一对
> 「图标名不合法：`'?color=…'`」+「`QQuickImage: Failed to get image from provider`」。
> URL 直接拼在 `source:` 表达式上就没有这条多余请求；最小复现见
> `poc/_probe_fmicon_empty_request.py`，回归用例见
> `tests/test_icon_provider.py::test_icon_name_becoming_non_empty_never_requests_an_empty_name`。

### 构建

```bash
make build            # PyInstaller 构建可执行文件
make build-installer  # Windows NSIS 安装包 (需要 NSIS)
make build-dmg        # macOS DMG 磁盘映像 (仅 macOS)
make build-deb        # Linux DEB 包
make build-appimage   # Linux AppImage
```

> 构建问题排查请参考 [docs/BUILD_FIXES.md](docs/BUILD_FIXES.md)

### 发布流程

1. 更新 `pyproject.toml` 和 `package.json` 中的版本号
2. 提交变更：`git commit -m "chore: release vX.X.X"`
3. 创建标签：`git tag vX.X.X`
4. 推送：`git push origin main --tags`

GitHub Actions 会自动构建 Windows 安装包并创建 Release（自 v2.10.3 起不再自动构建 macOS / Linux 二进制包，如需可使用上方 `make build-dmg` / `make build-deb` / `make build-appimage` 在对应平台本地构建）。

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
