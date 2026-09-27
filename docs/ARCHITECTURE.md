# FMCL 项目架构

> 本文档包含 FMCL 的项目结构、模块依赖关系和技术栈说明。

---

## 项目结构

```
FMCL/
├── main.py                # 主程序入口，延迟导入优化、日志配置、UI 创建、线程管理，支持 -A/-agent CLI 模式（含 AttachConsole GUI 子系统支持）
├── cli_agent.py           # Agent CLI 核心逻辑（无 GUI，复用 agent 组件，支持单指令/交互模式）
├── agent_cli.py           # Agent CLI 独立入口（供 PyInstaller 打包为 FMCL-Agent.exe 控制台程序）
├── config.py              # 跨平台配置管理（26 项配置，含加密存储、平台路径、旧配置迁移）
├── downloader.py          # 多线程下载器 & 异步批量下载 & 模组加载器安装
│   ├── MultiThreadDownloader  # 多线程分段下载 + 文件合并
│   ├── AsyncBatchDownloader   # asyncio + aiohttp 异步并发下载
│   └── install_mod_loader     # Forge/Fabric/NeoForge 统一安装
├── mirror.py              # BMCLAPI 国内镜像源模块
│   ├── MirrorSource       # 镜像源管理器（URL 重写缓存）
│   ├── URL 重写规则       # 官方 URL -> BMCLAPI 映射（前缀长度排序）
│   └── Monkey Patch       # minecraft_launcher_lib 补丁
├── modrinth.py            # Modrinth API 集成 & 健壮下载引擎
│   ├── search_mods        # 搜索模组（关键词 + 版本/加载器筛选）
│   ├── search_resource_packs # 搜索资源包（关键词 + 版本筛选）
│   ├── search_shaders     # 搜索光影（关键词 + 版本筛选）
│   ├── search_modpacks    # 搜索整合包（关键词 + 版本筛选）
│   ├── ai_expand_search_keywords  # AI 优化搜索词为多个英文关键词
│   ├── ai_merged_search   # AI 多词搜索合并去重按热度排序
│   ├── get_mod_versions   # 获取版本列表
│   ├── get_modpack_versions # 获取整合包版本列表
│   ├── download_mod       # 下载文件（断点续传 + 指数退避重试）
│   ├── download_modpack_file  # 下载整合包 .mrpack 文件
│   ├── install_mod_with_deps  # 安装模组及依赖（递归）
│   ├── 连接池复用         # 共享 requests.Session + HTTPAdapter，复用 TCP 连接
│   ├── 指数退避重试       # 网络超时/中断自动重试 3 次，退避时间 2^retry 秒
│   ├── 断点续传下载       # Range 头支持，下载中断后从断点继续
│   └── 版本解析工具       # 从版本 ID 解析加载器/游戏版本（含 NeoForge 特殊处理 + YY.D.H 格式）
├── curseforge.py          # CurseForge API 集成（可选，需 API Key）
├── backup_manager.py      # 存档备份管理（备份/恢复/删除/校验/导出/ZIP 压缩）
├── updater.py             # 自动更新模块（代理服务器 + GitHub Release）
│   ├── check_for_update   # 检查新版本
│   ├── find_suitable_asset # 根据平台匹配安装包
│   ├── download_update    # 下载更新安装包（带进度回调 + SHA256 校验）
│   └── install_update     # 执行静默安装（/S 参数）
├── secure_storage.py      # 安全存储模块（Fernet 加密 Token，密钥文件管理，密码派生）
├── validation.py          # 输入验证模块（版本ID/IP/端口/内存校验，路径穿越防护）
├── structured_logger.py   # 结构化日志（JSONL 格式，核心流程结构化记录）
├── version_utils.py       # 版本工具（SemVer 比较、正则模式集、YY.D.H 格式解析）
├── achievement_engine.py  # 成就引擎（47 项成就，9 大分类，多阶段，SQLite 持久化）
├── achievement_defs.py    # 成就定义（成就数据结构、触发条件）
├── achievement_sync.py    # 成就云存档同步（REST API 推送/拉取/合并）
├── launcher/              # 启动器核心逻辑（包）
│   ├── __init__.py        # MinecraftLauncher 组合类（多继承自 core/server/mrpack）
│   ├── core.py            # MinecraftLauncherCore - 环境检查、版本安装、游戏启动、JVM 优化
│   ├── server.py          # ServerMixin - 服务器安装/启动/停止/管理
│   ├── server_config.py   # 服务器配置文件读写（server.properties / eula.txt / 每服独立启动配置）
│   ├── mrpack.py          # MrpackMixin - 整合包安装/开服（并行下载优化）
│   ├── predownload.py     # 预下载模块 - 首次启动资源包预下载
│   └── verify.py          # 并发文件校验（ThreadPoolExecutor 多线程哈希校验）
├── plugin_manager/        # 插件系统（包）- 第三方扩展框架
│   ├── __init__.py        # 插件总控导入
│   ├── manifest.py        # PluginManifest 数据模型 + plugin.json 规范
│   ├── permissions.py     # 权限枚举 + 三级风险分级 + 运行时确认逻辑
│   ├── base.py            # PluginBase 抽象基类 + HookPoint 枚举 + PluginState
│   ├── hook_bus.py        # 线程安全钩子总线（ALL/COLLECT/FIRST/SHORT_CIRCUIT 四种策略）
│   ├── dependency.py      # SemVer 依赖解析 + Kahn 拓扑排序 + 循环检测
│   ├── loader.py          # importlib 动态插件加载 + 热重载
│   ├── installer.py       # .fmpl 包安装/卸载/回滚
│   ├── market.py          # 插件市场（GitHub 索引获取、搜索筛选、源码下载）
│   └── manager.py         # PluginManager 统一入口（组合所有子模块，生命周期管理）
├── app/                   # 装配层（阶段 1 新增）- 服务定位器、任务调度、UI 端口、事件总线
│   ├── context.py         # AppContext：服务注册表 + 拓扑启动排序 + 旧 UI 回调汇总
│   ├── tasks.py           # TaskRunner：有界 daemon 线程池 + 进度上报 + 协作式取消
│   ├── ports.py           # UIPort 协议 + NullUIPort + RecordingUIPort
│   └── events.py          # EventBus：同步发布订阅 + 环形历史
├── services/              # 业务服务层（阶段 1 新增）- 零 UI 依赖，由 CI 强制
│   ├── base.py            # Service 基类（生命周期 / 依赖查找 / 事件 / UI 端口）
│   ├── errors.py          # 统一异常体系（10 个语义化子类 + wrap()）
│   ├── __init__.py        # 包导出面（只放文档与少量聚合导出）
│   ├── palette.py         # 12 键调色板（唯一 dict 对象，被主题引擎原地修改）
│   ├── theme_service.py   # 动态主题引擎（预设 / 导入 json / 自定义强调色 / 版本调色）
│   ├── i18n_service.py    # 多语言（JSON 表 + 热切换 + 可注入语言目录）
│   ├── user_agent.py      # HTTP User-Agent（LazyStr 惰性求值）
│   ├── monitor_service.py # GPU 检测/采样 + 系统指标 + 全局热键（原 ui/app_monitor.py）
│   ├── crash_service.py   # 崩溃诊断（11 类规则表 + 上下文收集 + AI 请求）
│   ├── voice_service.py   # 语音输入（原 ui/agent/voice_input.py 的逻辑部分）
│   ├── tool_service.py    # 工具箱 8 个工具的算法与 IO
│   ├── server_service.py  # 服务器生命周期 / 控制台流 / 配置读写
│   ├── server_config_schema.py  # 服务器配置项 schema（原 ui/）
│   ├── online_service.py  # 联机（EasyTier / 大厅码 / 端口转发 / 局域网扫描 / MC Ping）
│   ├── resource_service.py      # 资源与整合包（扫描/启停/更新检测/缩略图/导入导出）
│   ├── agent_service.py   # AGENT 的循环 / 流式 / 工具调用 / 权限判定 / 配置编排
│   ├── backup_manager.py  # 存档备份（世界扫描 / 备份恢复删除校验导出）
│   ├── achievement_*.py   # 成就定义 / 引擎（进度与解锁）/ 云同步（原仓库根）
│   ├── music_source/      # 5 个在线音源的检索与解析（base/bili/kg/kw/mg/tx/wy/utils）
│   ├── music_lyrics.py    # 歌词解析（LRC/翻译/罗马音）
│   ├── music_effects.py   # 音效 DSP（EQ/混响/变调/变速）
│   ├── music_playlist.py  # 歌单与播放历史持久化
│   ├── music_risk_captcha.py    # 音源风控验证码流程
│   ├── music_audio.py     # 音频元数据/校验/转码
│   ├── music_player.py    # 播放引擎状态机（淡入淡出/预取/进度/播放模式）
│   ├── music_state.py     # 音乐状态持久化（键名/默认值/容错）
│   ├── music_smtc.py      # Windows SMTC 系统媒体控制集成
│   ├── desktop_lyric.py   # 桌面歌词的纯逻辑（位置计算/当前行/透明度边界）
│   ├── voice/             # 语音模型（models / sensevoice）
│   └── agent/             # AGENT 的供应商 / 工具 / 权限 / 技能 / 会话（providers、tools 子包）
├── ui/                    # CustomTkinter 现代化 UI（包）
│   ├── __init__.py        # 向后兼容导出
│   ├── app.py             # ModernApp 组合类（12 个 Mixin 多继承）
│   ├── app_base.py        # ModernAppBase(ctk.CTk) - 主窗口 UI 构建、侧边栏、日志捕获
│   ├── app_handlers.py    # EventHandlerMixin - 版本管理、游戏操作、更新检查、队列处理
│   ├── app_server.py      # ServerTabMixin - 开服标签页（服务器安装/启动/停止 + 版本管理）
│   ├── app_crash.py       # CrashHandlerMixin - 崩溃诊断、AI 分析
│   ├── app_backup.py      # BackupTabMixin - 存档备份标签页
│   ├── app_online.py      # OnlineTabMixin - 陶瓦联机标签页（EasyTier P2P 组网）
│   ├── app_achievements.py # AchievementTabMixin - 成就系统标签页
│   ├── app_music.py       # MusicPlayerMixin - 音乐播放器标签页
│   ├── app_monitor.py     # MonitorMixin - 性能监控悬浮窗
│   ├── app_tools.py       # ToolsTabMixin - 工具标签页（清理/Hash/坐标/端口/冷知识等）
│   ├── app_about.py       # AboutTabMixin - 关于/协议标签页
│   ├── agent/             # AGENT 智能助手模块（包）
│   │   ├── __init__.py    # 模块导出（ModelInfo/Provider/ToolRegistry/AgentMixin）
│   │   ├── agent_mixin.py # AgentMixin - AGENT 标签页集成
│   │   ├── agent_chat.py  # 聊天 UI 组件 + 选项弹窗（原生 Function Calling，最大 50 轮）
│   │   ├── provider.py    # AI API 调用封装（OpenAI 兼容，返回完整 message 含 tool_calls）
│   │   ├── tools.py       # Tool 定义（Function Calling 格式）+ 系统提示词
│   │   ├── engine.py      # Tool 执行引擎（含高危命令检测 ASK_USER_MARKER）
│   │   ├── model.py       # 模型目录（ModelInfo、多供应商模型清单）
│   │   ├── config.py      # Agent 配置管理
│   │   ├── session.py     # 对话历史管理（多会话、持久化 JSON）
│   │   ├── stream.py      # 流式 SSE 输出处理
│   │   ├── system_prompt.py # 系统提示词模板
│   │   ├── tool_registry.py # 工具注册表
│   │   ├── permission.py  # 权限引擎（三级策略：allow/deny/ask）
│   │   ├── skill.py       # Skill 技能系统（自定义 SKILL.md 注入上下文）
│   │   ├── providers/     # AI 供应商实现（包）
│   │   │   ├── __init__.py
│   │   │   ├── jingdu.py      # JingduProvider - 净读 AI（DeepSeek V4 Flash/Pro）
│   │   │   ├── openai.py      # OpenAIProvider - GPT-4o / GPT-4o-mini / o3-mini
│   │   │   ├── anthropic.py   # AnthropicProvider - Claude Sonnet 4 / Haiku 3.5
│   │   │   └── custom.py      # CustomProvider - 自定义 OpenAI 兼容端点
│   │   └── tools/         # AI 工具实现（包）
│   │       ├── __init__.py    # 汇总注册所有工具
│   │       ├── base.py        # ToolInfo 基类
│   │       ├── versions.py    # 版本管理工具
│   │       ├── mods.py        # 模组管理工具
│   │       ├── modpack.py     # 整合包管理工具
│   │       ├── server.py      # 服务器管理工具
│   │       ├── resources.py   # 资源管理工具
│   │       ├── files.py       # 文件操作工具（读/写/替换/删除/搜索/列举）
│   │       ├── system.py      # 系统命令工具（含高危命令检测）
│   │       ├── user.py        # 用户交互工具
│   │       ├── web_search.py  # 联网搜索工具
│   │       ├── web_fetch.py   # 网页抓取工具
│   │       ├── skill.py       # 技能工具
│   │       └── todo_write.py  # 待办写入工具
│   ├── constants.py       # 颜色主题、字体检测、资源类型配置
│   ├── theme_engine.py    # 动态主题引擎（5 种预设 + 导入 .json + 版本动态调色）
│   ├── dialogs.py         # 通用对话框（确认/提示/版本选择）
│   ├── i18n.py            # 国际化模块（zh_CN/en_US/zh_TW/ja_JP）
│   ├── server_config_schema.py # 服务器配置项元数据（分类/控件类型/取值范围/默认值）
│   └── windows/           # 独立窗口类
│       ├── account_manager.py          # 账号管理窗口（微软/离线/Yggdrasil）
│       ├── launcher_settings.py        # 启动器设置窗口
│       ├── resource_manager.py         # 资源管理窗口（模组/资源包/地图/光影）
│       ├── mod_browser.py              # Modrinth 资源浏览与安装窗口（模组/资源包/光影）
│       ├── modpack_browser.py          # Modrinth 整合包浏览与下载窗口
│       ├── modpack_install.py          # 整合包安装窗口
│       ├── modpack_server.py           # 整合包开服窗口
│       ├── plugin_manager.py           # 插件管理窗口
│       ├── plugin_permission_dialog.py # 插件权限确认弹窗
│       ├── plugin_browser.py           # 插件市场浏览与一键安装窗口
│       ├── server_mod_browser.py       # 服务器模组浏览器
│       ├── server_resource_manager.py  # 服务器资源管理窗口
│       ├── server_config_editor.py     # 服务器配置编辑窗口（server.properties 可视化编辑）
│       └── backup_settings.py          # 备份设置窗口
├── scripts/
│   ├── install.sh         # Linux 一键安装脚本（支持 7 大发行版）
│   ├── release.py         # 自动发布脚本
│   ├── fix_common_issues.py  # 常见问题修复工具
│   ├── screenshot_tool.py # 截图工具（Ctrl+Alt+T 触发，区域截图；独立运行，不属于启动器进程）
│   ├── check_services_purity.py  # 分层纯净度静态检查（services 层禁 UI 依赖）
│   ├── check_qml_rules.py        # QML 规则闸门（R1~R7：禁渐变/emoji/硬编码中文、i18n 绑定、越界 import、transparent、桥线程红线）
├── tests/
│   ├── test_account.py
│   ├── test_imports.py
│   ├── test_modrinth_versions.py
│   ├── test_server_config.py
│   └── test_theme_engine.py
├── .github/workflows/
│   ├── ci.yml             # CI 工作流（代码检查 + 测试 + 构建）
│   └── release.yml        # 发布工作流（构建 + 打包 + Release + 自动更新）
├── config.json            # 用户配置（26 项持久化配置项）
├── requirements.txt       # Python 生产依赖
├── requirements-windows.txt  # Windows 附加依赖（winsdk）
├── requirements-unix.txt    # Unix 依赖（指向 requirements.txt）
├── requirements-dev.txt     # 开发依赖（pyinstaller/flake8/mypy/pytest）
├── pyproject.toml         # 项目元数据（名称 fmcl，版本 2.11.1，GPL-3.0-only）
├── build.spec             # PyInstaller 构建配置（含 FMCL GUI + FMCL-Agent 双程序）
├── installer.nsi          # Windows NSIS 安装脚本（内置 7-Zip 24.09）
├── Makefile               # 构建/开发命令集合
├── Dockerfile             # Docker 构建支持
├── package.json           # Node.js 开发工具配置（Husky + Commitlint）
├── icon.ico               # 应用图标（多尺寸 ICO）
├── LICENSE                # GPL-3.0 许可证
├── README.md              # 项目主文档
├── CONTRIBUTING.md        # 贡献指南
├── TERMS_OF_USE.md        # 用户协议（v1.1）
└── docs/
    ├── FEATURES.md        # 完整功能列表
    ├── USAGE.md           # 详细使用指南
    ├── ARCHITECTURE.md    # 项目架构与技术栈
    ├── CONFIGURATION.md   # 配置说明
    ├── SETUP.md           # 构建设置
    ├── BUILD_FIXES.md     # 构建问题修复记录
    ├── TROUBLESHOOTING.md # 故障排除指南
    ├── PLUGIN_DEV.md      # 插件开发指南
    └── LINUX_FILE_LOCATIONS.md # Linux FHS 文件存储说明
```

## 分层架构（阶段 1 重构进行中）

> 本节描述**正在进行**的 UI 重构引入的新分层。旧的 Tk 界面仍然可用且行为不变，
> 两套界面共用同一份业务逻辑。完整计划见 `docs/refactor/`。

**当前规模（阶段 1 收口时的快照）**：`services/` **77 个文件 / 28559 行**
（35 个顶层模块 + `agent/` 30 个文件、`music_source/` 9 个、`voice/` 3 个；
仍在抽取的域落盘后还会增长，准确数字请跑 `python poc/_list_services.py`）；
`ui/` 侧对应位置要么留**转发 shim**（模块级搬迁），要么留**同名薄委托方法**（逻辑切分）。
分层检查覆盖 132 个文件、0 处未登记违规、0 处已登记例外。

```
QML 界面（阶段 2/3 建设中）     旧 Tk 界面（现役，阶段 4 退役）
        │                              │
        └──────────────┬───────────────┘
                       ▼
              app/          装配层：服务定位器、任务调度、UI 能力端口、事件总线
        ┌──────────────┼──────────────┬───────────────┐
        ▼              ▼              ▼               ▼
   AppContext      TaskRunner      UIPort          EventBus
  （服务注册表）  （有界线程池）  （界面能力协议）  （发布订阅）
                       │
                       ▼
              services/       业务服务层：零 UI 依赖（CI 强制）
                       │
                       ▼
              launcher/ 等    核心逻辑：安装、启动、下载、账号……

```

### 硬性分层约束

| 层 | 允许依赖 | 禁止依赖 | 强制手段 |
|---|---|---|---|
| `services/` | 标准库、第三方库、`launcher/`、根级核心模块 | `tkinter` / `customtkinter` / `PySide6` / `ui.*` | `scripts/check_services_purity.py`（AST 分析，已接入测试） |
| `launcher/` | 标准库、第三方库、`services/` | 任何 GUI 库、`ui.*` | 同上 |
| `ui/` | 任何东西 | —（界面层是终端消费者） | — |
| `app/` | 标准库、`services/` | 任何 GUI 库 | `app/ports.py` 的协议设计 |

服务层需要"告诉用户一件事"时，只能通过注入的 `UIPort`（`app/ports.py`）；
需要后台线程时用 `TaskRunner`（`app/tasks.py`，有界 daemon 线程池 + 主线程回调）。

### 装配：`app/bootstrap.py`（阶段 1 任务 1.17）

`AppContext` 本身在阶段 1 很早就写完了，但**一直没有生产代码构造它** ——
后果是界面侧的 `_get_xxx_service(owner)` 永远拿到 `ctx is None`，
**每个窗口各自 new 一份服务**：那样"新旧 UI 共用同一份业务逻辑"在运行期并不成立
（只是共用同一份代码）。`app/bootstrap.py` 补上这段接线：

```python
from app.bootstrap import attach, build_context

context = build_context(config=config, ui=ui_port, scheduler=lambda fn: app.after(0, fn))
attach(context, app)          # app.context = ctx，并登记为进程级 current
context.start_all()           # 目前是空操作（13 个服务都没覆盖 Service.start）
```

三个设计点：

1. **懒注册**（`AppContext.register_lazy`）。`SERVICE_FACTORIES` 存的是
   `"模块:类名"` 字符串，`app/bootstrap.py` 顶层不 import 任何服务；
   实例在**第一次被取用**时才建。理由是启动速度：服务模块里有几个
   import 就拉起重依赖（`onnxruntime` / `pygame` / `winsdk`），启动时多数用不到。
   工厂抛异常时 `try_get` 返回 `None`（界面走"自己 new"的旧兜底）且**不缓存失败**。
2. **进程级当前上下文**（`AppContext.current()` / 模块级别名 `current_context()`）。
   界面侧的辅助函数 `owner` 是**窗口自己**，而窗口没有 `context` 属性，所以写成
   `ctx = getattr(owner, "context", None) or current_context()` ——
   这一行让 12 个窗口/标签页复用同一批服务实例。
3. **完整性守卫**：`tests/test_app_context_wiring.py` 会扫 `services/` 下每个
   `Service` 子类，要求它在 `SERVICE_FACTORIES` 里、或在 `NOT_REGISTERED` 里写明理由；
   并且会检查每个 `_get_*_service` 辅助函数是否真的兜底到 `current_context()`
   （未接的要进 `PENDING_LOOKUP_FILES` 并写理由，接好后必须从名单里删掉）。
   这两条挡的都是"没有运行期报错"的缺陷。

### 模块搬运的三种形态

阶段 1 把 `ui/` 里的业务逻辑搬进 `services/`，按原模块的 UI 耦合度分三种做法：

1. **整体搬家 + 转发 shim** —— 适用于本来就不依赖界面的模块。
   内容逐字搬走，原位置留一层命名空间完全一致的 shim。已用于：
   `achievement_defs` / `achievement_engine` / `achievement_sync` /
   `server_config_schema` / `music_lyrics` / `music_effects` / `music_playlist` /
   `music_risk_captcha` / `backup_manager`、`ui/music_source/` 整包、
   `ui/agent/` 的 12 个模块与 `providers/`、`tools/` 两个子包。
   工具：`scripts/relocate_module.py`（`--list` / `--check` / `--apply` / `--apply-all` /
   `--regen-shims`），对每条搬迁做三重校验。
2. **逻辑/界面分离** —— 适用于方法里界面与逻辑混在一起的 Mixin。
   把不碰控件的逻辑搬进服务，Mixin 保留同名方法做薄委托（调用点不变）。已用于：
   `app_monitor`（GPU 检测/采样/热键 → `services/monitor_service.py`）、
   音乐播放（任务 1.4-A：播放状态机/淡入淡出步进/预取判定/进度换算/播放模式/
   目录扫描 → `services/music_player.py`；桌面歌词的位置与当前行规则 →
   `services/desktop_lyric.py`）。同一个宿主文件里，**本来就不碰控件的模块级函数与类
   走形态 1**（音频解析/校验/转码 → `services/music_audio.py`、SMTC →
   `services/music_smtc.py`）。注意这类"局部抽取"套不上
   `scripts/relocate_module.py` 的"整模块搬走 + 原路径留 shim"模型（宿主
   `ui/app_music.py` 必须原地保留，`ui/app.py` 靠 mixin 组装主窗口），
   所以改用与它同一套判据做局部搬运：逐字节等价 + 旧名指向同一对象，
   证据脚本是 `poc/_verify_1_4a_music.py`。
3. **服务 + 事件** —— 适用于需要主动通知界面的逻辑（阶段 2 起使用）。
   任务 1.4-A 另有 **形态 3：服务 + 持久化**：键名/默认值/容错分支进服务，
   取值与写盘留在界面（`services/music_state.py`）。

### 转发 shim 的命名空间约定

shim **必须同时做两件事**（缺一不可，两个都踩过实坑）：

```python
import sys
import services.xxx as _impl

sys.modules[__name__] = _impl                                     # ① 别名
globals().update({k: v for k, v in vars(_impl).items()            # ② 复制
                  if not k.startswith("__")})
```

① **别名**（`sys.modules[__name__] = _impl`）：让 `ui.xxx is services.xxx` 成立，
读、**写**、打补丁、`is` 判定全部与搬家前一致。
只做复制（`globals().update`）的话，"读"到的是同一批对象，但**"写"传不过去** ——
`m.FOO = fake` 只改到 shim 自己的命名空间，实现模块里的 `FOO` 不变，
**monkeypatch 会静默失效**。实测踩到过：`ui/music_source/` 搬走后，
`tests/test_music_fallback.py` 里的 `monkeypatch.setattr(ms, "MUSIC_SOURCES", ...)`
失效，测试**真的去请求了 QQ 音乐接口**。

② **复制**（`globals().update(...)`）：兼容另一种加载方式 ——
仓库里 `tests/test_music_playlist.py` 是按**文件路径**加载模块
（`spec_from_file_location` + 自己注册进 `sys.modules`）以绕开 `ui/__init__.py` 的
GUI 导入，它会**保留 exec 之前的那个模块对象**；只做别名的话那个对象仍是空壳
（实测报 `module 'ui.music_playlist' has no attribute 'PlaylistSong'`）。

**包子包（`ui/agent/providers/` 这类）还要额外别名所有子模块**，
否则 `import ui.agent.providers.jingdu` 会以旧包名**再加载一次**实现文件，
产生第二个类对象 → `isinstance` 判定失败。`scripts/relocate_module.py --check`
会把这三条都验一遍（主体 AST 逐节点一致、行数与原文一致、旧路径自定义名与实现同一对象）。

阶段 3 完成后所有调用点改为直接 `from services... import`，届时 shim 可删除。

---

## 静态守卫（迁移期的"防回归机器"）

重构期间最容易出的不是"编译不过"，而是**静默失效**：回调键写错导致按钮点了没反应、
成就触发点被搬丢导致永远解锁不了、搬走的模块在原路径变成空壳、`services/` 里悄悄
import 了界面栈。这些都不会报错，所以每一条都配了一个可机械执行的守卫：

| 守卫 | 命令 | 它挡住什么 |
|---|---|---|
| 分层纯净度 | `python scripts/check_services_purity.py` | `services/` / `launcher/` / `main.py` / 移植中的根模块 import 了 GUI 栈或 `ui.*` |
| 回调键完整性 | `python scripts/check_callback_keys.py --fail-on-soft` | 界面引用了没人提供的回调键（功能静默失效），或提供了却没人引用（死键） |
| i18n 契约 | `python scripts/check_i18n.py` | 4 语言键集合不一致、代码引用了缺失键、占位符跨语言不一致、调用点少传参数 |
| 搬家完整性 | `python scripts/relocate_module.py --check` | 搬走的内容与原文件不一致、行数变了、旧路径不再是同一对象。**搬家完成后带任务号的缺陷修复会合法地改变行数**，这类偏差逐条登记在脚本的 `REGISTERED_LINE_DELTAS` 里（带任务号 + 缺陷号 + 理由），判据变成"新文行数 == 原文行数 + 已登记偏差"；不在表里的文件仍必须**完全一致**，且**每条例外都必须被命中**（命中 0 次 = 登记过期，同样报错）。同一事实在 `tests/test_services_relocation.py` 也登记了一份（那里按**非空行**算），两条守卫会互相核对键集合、并从 git 原文**现算**增量的真实值 |
| 入口可导入 | `pytest tests/test_entry_imports.py` | 某个入口模块 **import 不起来**（历史上 `cli_agent.py` 因此整条不可用很久） |
| 窗口可构造 | `pytest tests/test_window_smoke.py` | 薄委托改造把窗口改坏（漏搬属性/回调/i18n 键）→ 窗口打不开。14 个 `CTkToplevel` 子窗口逐个真构造一次；另含"新增窗口忘了进冒烟清单"的完整性守卫 |
| 依赖清单一致 | `python poc/_audit_dependency_drift.py` | `requirements*.txt` 与 `pyproject.toml` 漂移（历史上 `tkinterdnd2` 因此在 uv 环境里缺失、拖拽**静默**失效） |
| 成就可达性 | `pytest tests/test_achievement_wiring.py` | 成就的 id 在代码里从未被引用 → 永远解锁不了（历史上真的有两项） |
| 依赖不消失 | `uv sync --dry-run` | `uv sync` / `uv run` 静默卸载 PySide6（见 `pyproject.toml` 的 `[tool.uv] default-groups` 说明） |

**方法论上值得记住的两条**：
1. **迁移期的验证必须以"执行者落盘后的完整状态"为准** —— 一个"薄委托"改到一半时，
   方法体调用的是尚未写出的函数，抽样运行会看到假故障。
2. **测试替身自己也要被测试**：`tests/test_mod_browser_staleness.py` 用替身替换了渲染函数，
   于是"替身守住了、生产代码没守"是可能的 —— 所以另配了一组 AST 断言，
   直接读生产代码确认守卫函数确实被调用。

**第 12 轮补的第 3 条**：
3. **闸门自己也要被测试（变异测试）**。一条只会 PASS 的闸门不是闸门，只是一句安慰。
   新增或修改任何机械判据时，都要配一个"把它改坏 → 必须报红"的探针：
   * `poc/_probe_relocate_registry.py`：往 `REGISTERED_LINE_DELTAS` 里塞假登记项 /
     改错增量 / 取消豁免 / 用错键名 —— 四种都必须让 `--check` 退出码变 1；
   * `poc/_probe_d114_guard.py`：把 D-114 的两条 AST 守卫对应的生产代码改坏
     （去掉 `try`、把计数挪进分支），断言测试**确实会红**。
   **尤其要防"空断言"**：`RecordingUIPort.show_progress` 只记录 `report=`、
   悄悄丢掉 `detail` / `modal` / `on_cancel`，任何断言这些 kwargs 的测试都会
   拿不到数据或断言成一句永远成立的空话 —— 这类"替身削弱断言"是静默失效的另一种形态。

---

## 模块依赖关系

```
main.py（程序入口）
  ├── config.py（全局配置，26 项持久化）
  │   └── secure_storage.py（Fernet 加解密 Token）
  ├── i18n.py（初始化国际化：zh_CN/en_US/zh_TW/ja_JP）
  │
  ├── GUI 模式：
  │   ├── ui/app.py → ModernApp（12 个 Mixin 组合）
  │   │   ├── app_base.py（主窗口骨架 + 侧边栏 + 日志）
  │   │   ├── app_handlers.py（版本管理 + 游戏操作）
  │   │   ├── app_server.py（开服标签页）
  │   │   ├── app_crash.py（崩溃诊断 + AI 分析）
  │   │   ├── app_backup.py（备份标签页）
  │   │   ├── app_online.py（联机标签页 - EasyTier）
  │   │   ├── app_achievements.py（成就标签页）
  │   │   ├── app_music.py（音乐播放器标签页）
  │   │   ├── app_monitor.py（性能监控悬浮窗）
  │   │   ├── app_tools.py（工具标签页）
  │   │   ├── app_about.py（关于标签页）
  │   │   ├── agent/agent_mixin.py（AGENT 标签页）
  │   │   ├── theme_engine.py（动态主题引擎）
  │   │   ├── launcher.get_callbacks()（回调连接核心逻辑）
  │   │   └── windows/（15 个独立窗口）
  │   │
  │   ├── app/（装配层，阶段 1 新增）
  │   │   ├── context.py（AppContext 服务定位器 + 拓扑启动排序 + 旧 UI 回调汇总）
  │   │   ├── tasks.py（TaskRunner：有界 daemon 线程池 + 进度 + 协作式取消）
  │   │   ├── ports.py（UIPort 协议 + NullUIPort + RecordingUIPort）
  │   │   └── events.py（EventBus 同步发布订阅）
  │   │
  │   ├── services/（业务服务层，零 UI 依赖，CI 强制）
  │   │   ├── base.py / errors.py（Service 基类 + 统一异常体系）
  │   │   ├── palette.py / theme_service.py / i18n_service.py / user_agent.py
  │   │   ├── achievement_defs.py / achievement_engine.py / achievement_sync.py
  │   │   ├── server_config_schema.py / music_lyrics.py / music_effects.py
  │   │   ├── music_source/（在线音源适配）
  │   │   ├── music_audio.py / music_smtc.py / music_player.py / music_state.py
  │   │   │   （任务 1.4-A：音频解析 · SMTC · 播放状态机 · 状态读写规则）
  │   │   ├── desktop_lyric.py（桌面歌词零界面逻辑）
  │   │   └── monitor_service.py（GPU 检测/采样 + 指标采集 + 全局热键）
  │   │
  │   ├── launcher/（核心逻辑包）
  │   │   ├── core.py（环境检查 + 版本安装 + 游戏启动 + JVM 优化）
  │   │   ├── server.py（服务器安装/启动/停止）
  │   │   ├── mrpack.py（整合包安装/开服）
  │   │   ├── predownload.py（预下载，已改走 UIPort）
  │   │   └── verify.py（并发文件校验）
  │   │
  │   ├── modrinth.py（Modrinth API 搜索/下载/安装）
  │   ├── curseforge.py（CurseForge API，可选）
  │   ├── mirror.py（BMCLAPI 镜像 + Monkey Patch）
  │   ├── downloader.py（多线程 + 异步下载）
  │   ├── updater.py（自动更新）
  │   ├── backup_manager.py（存档备份管理）
  │   ├── achievement_engine.py（转发 shim → services/achievement_engine.py）
  │   ├── achievement_sync.py（转发 shim → services/achievement_sync.py）
  │   └── structured_logger.py（结构化日志记录）
  │
  ├── CLI 模式（-A / -agent）：
  │   └── cli_agent.py（复用 agent 组件）
  │       ├── config.py
  │       ├── launcher/
  │       └── ui/agent/（复用 provider/tools/engine）
  │
  └── plugin_manager/（插件系统，独立加载）
      ├── manifest.py（plugin.json 数据模型）
      ├── permissions.py（权限枚举 + 三级风险分级）
      ├── base.py（PluginBase + HookPoint）
      ├── hook_bus.py（线程安全钩子总线）
      ├── dependency.py（依赖解析 + 拓扑排序）
      ├── loader.py（importlib 动态加载）
      ├── installer.py（.fmpl 安装/回滚）
      ├── market.py（在线市场）
      └── manager.py（统一入口，组合所有子模块）
```

## ModernApp Mixin 继承链

```python
class ModernApp(                # 12 个 Mixin + 1 个基类
    CrashHandlerMixin,          # ui.app_crash - 崩溃诊断与 AI 分析
    EventHandlerMixin,          # ui.app_handlers - 版本管理与游戏操作
    BackupTabMixin,             # ui.app_backup - 存档备份标签页
    OnlineTabMixin,             # ui.app_online - 陶瓦联机标签页
    ServerTabMixin,             # ui.app_server - 开服标签页
    AchievementTabMixin,        # ui.app_achievements - 成就系统标签页
    MusicPlayerMixin,           # ui.app_music - 音乐播放器标签页
    MonitorMixin,               # ui.app_monitor - 性能监控悬浮窗
    ToolsTabMixin,              # ui.app_tools - 工具标签页
    AboutTabMixin,              # ui.app_about - 关于/协议标签页
    AgentMixin,                 # ui.agent.agent_mixin - AGENT 标签页
    ModernAppBase               # ui.app_base - 主窗口骨架（ctk.CTk）
):
```

## 启动流程

```
main() → setup_logging() → config.ensure_directories() → migrate_accounts()
  → init_i18n() → set_chinese_language()
  → 延迟导入 customtkinter + UI 模块
  → 创建 splash 启动画面
  → 后台线程并行初始化：
      ├── MinecraftLauncher（核心逻辑包）
      └── AchievementEngine（成就引擎）
  → splash 关闭后（两者就绪 + 至少 1 秒）：
      ├── 显示主窗口
      ├── 注入账号系统（加载 accounts.json）
      ├── 初始化插件系统（扫描 → 加载 → 启用）
      ├── 连接进度回调 + 同步开关状态
      ├── 启动协议同意 → 公告 → 预下载流程
      ├── 后台同步成就云存档 + 每日签到
      └── 静默检查更新（GitHub Release）
```

### QML 版（`main_qml.py`，阶段 2 起）

时序语义与上表**逐条对齐**，但实现在 `app/startup.py` 的 `StartupController` 里（可注入时钟，
能确定性地测 30 秒超时这类路径），QML 只按 `Startup.startupActive` 决定两个窗口谁可见：

```
main_qml.assemble(start_startup=True)
  → build_qt_context()（QtUIPort / MainThreadDispatcher / AppContext）
  → register_bridges()（Theme / Tr / Runtime / Nav / Shell / Dialogs / Hotkeys / Overlay / Tasks / Events）
  → install_icon_provider()（必须早于 engine.load）
  → StartupController(splash_expected=True) + engine.load(App.qml)
  → StartupController.start()
      ├── 后台任务：MinecraftLauncher、AchievementEngine（两者就绪 + ≥1 秒 → dismissed）
      ├── 30 秒硬超时 → 强制关启动画面
      └── 初始化失败 → 关启动画面但**仍然显示主窗口** + 状态条报错 + 错误 Toast
  → 收尾链条：协议同意 → 公告 → 预下载 → chainFinished
  → 每个节点都经 MainThreadDispatcher 把主窗口提到前面（对照旧实现的 lift + focus_force）
```

### QML 界面结构（返工 B / C 组之后）

```
qml/App.qml                FluWindow + effect:"normal" + appBar: AppBar
  └ shell/AppBar.qml       FluAppBar 子类，40px：返回 / 面包屑 / 搜索 / 通知 / 账号（右侧 120px 留给窗口按钮）
  └ shell/Navigation.qml   分组导航（组标题来自 nav_bridge.NAV_GROUPS），选中态 = accentSoft + 3px 指示条
  └ shell/PageStack.qml    StackView（Nav 的镜像）；每条路由一个页面对象
  └ shell/StatusBar.qml    28px：顶边 1px divider + 状态文本 + 不确定进度条 + 后台任务数
  └ pages/<领域>/<X>Page.qml   每个页面都是 components/FmPage.qml 的薄壳（22 行）：
                               路由帧 + 页头 + 三态（contentState）都在 FmPage 里
  └ components/Fm*.qml     26 个自研组件（页面与壳层只允许用这些，闸门 R5 + R9 守）
```

**页面那一层的收敛（返工 C 组）**：12 个内置领域页原来各 145 行、内容逐字相同，现在各 22 行，
页头 / 路由帧 / 三态渲染只有 `FmPage` 一份实现。三态用 `FmLoadingState` / `FmEmptyState` /
`FmErrorState`，`contentState` 是 `ready | loading | empty | error` 四值。

**原生控件禁令（闸门 R9）**：`qml/**` 里不许**声明** QtQuick.Controls 的原生控件
（`Button` / `TextField` / `CheckBox` / `ScrollBar` / `ScrollView` / `SpinBox` …）。
原因是本项目跑的是 **Basic** 样式（`main_qml.py` 设的 `QT_QUICK_CONTROLS_STYLE`），
原生控件的颜色来自系统调色板，锁深色的界面里必然是浅色外来件。三条边界：同名包装器放行、
附着属性名（`ScrollBar.vertical:`）不算、`qml/FatalError.qml` 零依赖豁免。

窗口的拖动由 `FluFrameless` 的 Win32 命中测试实现：**顶栏上每个可交互项都要登记**
（`App.qml` 遍历 `AppBar.interactiveItems` 调 `setHitTestVisible`），
否则点击会被系统当成拖窗口吃掉 —— 不报错、只是没反应。

## 技术栈

| 组件 | 技术 | 说明 |
|------|------|------|
| UI 框架 | CustomTkinter 5.2+ | 现代 Tkinter 封装，深色主题 |
| Minecraft 库 | minecraft-launcher-lib | 版本安装、启动命令生成 |
| 镜像源 | BMCLAPI (bangbang93) | 国内加速下载 |
| 启动优化 | 延迟导入 + 后台初始化 | 窗口先显示，核心后台加载 |
| JSON 解析 | orjson 3.9+ (回退 stdlib json) | 3-10 倍解析加速 |
| 并发校验 | ThreadPoolExecutor | 多线程文件哈希校验 |
| 异步下载 | asyncio + aiohttp 3.9+ | 批量并发下载 |
| JVM 优化 | G1GC + 固定堆内存 | 减少游戏卡顿 |
| Java 扫描 | 跨平台系统扫描 | Windows/macOS/Linux 自动检测最佳 Java 版本 |
| URL 缓存 | dict 缓存重写结果 | 避免重复匹配规则 |
| 拖拽支持 | tkinterdnd2 | 文件拖拽安装资源 |
| 日志 | logzero 1.7+ | 轻量级日志框架 |
| 结构化日志 | JSONL 格式 | 核心流程结构化记录，方便程序化分析 |
| HTTP | requests.Session + HTTPAdapter | 连接池复用（20 池/50 最大），避免重复 TLS 握手 |
| 加密 | cryptography (Fernet) | 敏感 Token AES-128-CBC + HMAC-SHA256 加密存储 |
| 密钥管理 | PBKDF2-HMAC-SHA256 | 密码派生密钥（600000 次迭代） |
| 输入验证 | 正则 + 白名单 | 防止命令注入和路径穿越攻击 |
| 文件完整性 | SHA1 / SHA256 / SHA512 | 模组和更新包下载后自动校验哈希 |
| 模组搜索 | Modrinth API V2 + CurseForge API | 双源搜索模组/整合包 |
| 下载 | 多线程分段下载 | 大文件并行下载加速 |
| 断点续传 | Range 头 + 追加写入 | 下载中断后自动从断点继续 |
| 指数退避重试 | 3 次重试，退避 2^retry 秒 | 网络超时/中断自动恢复 |
| 截图 | pyautogui + keyboard | 区域截图 + 快捷键监听 |
| 自动更新 | 代理服务器 + GitHub Release API | 版本检查 + 静默安装 |
| 构建打包 | PyInstaller + NSIS | 可执行文件 + Windows 安装包 |
| Linux 兼容构建 | Docker + manylinux_2_28 + Python `--enable-shared` | GLIBC 2.28 兼容 |
| CI/CD | GitHub Actions | 多平台自动构建与发布（Windows/macOS/Linux AMD64+ARM64） |
| 提交规范 | Husky + Commitlint | 约定式提交自动校验 |
| 音乐播放 | pygame 2.6+ + mutagen | 多格式音频播放 + 元数据提取 |
| 全局热键 | keyboard 0.13+ | 后台控制音乐/截图/监控 |
| 系统监控 | psutil 7.2+ + nvidia-ml-py | CPU/内存/GPU 实时监控 |
| 安全存储 | Fernet + PBKDF2 | Token 加密存储 + 密码派生 |
