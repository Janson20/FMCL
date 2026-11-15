# FMCL 项目架构

> 本文描述 FMCL 当前的分层、模块边界、启动链路与主要数据流。安装、构建与配置分别见
> [README.md](../README.md)、[SETUP.md](SETUP.md)、[CONFIGURATION.md](CONFIGURATION.md)。

---

## 1. 总览

FMCL 是 Python + Qt Quick 桌面应用。Python 侧提供业务服务、数据持久化与系统集成，
QML 侧只负责界面与交互，两者通过 `app/bridges/` 中的上下文属性连接。

```
┌──────────────────────────────────────────────────────────────┐
│ qml/                     QML 界面：壳层、页面、组件、浮层    │
└───────────────────────────┬──────────────────────────────────┘
                            │ 上下文属性 / 信号 / 槽
┌───────────────────────────▼──────────────────────────────────┐
│ app/bridges/             QML 适配层：属性、信号、命令入口    │
├──────────────────────────────────────────────────────────────┤
│ app/                     AppContext、TaskRunner、EventBus、   │
│                          UIPort、启动控制器                   │
├──────────────────────────────────────────────────────────────┤
│ services/                业务服务：零界面依赖                 │
├──────────────────────────────────────────────────────────────┤
│ launcher/ · fusion_music/ · plugin_manager/ · 根级核心模块    │
│                          安装、启动、音乐、插件、下载、账号…  │
└──────────────────────────────────────────────────────────────┘
```

设计约束：

- QML 不直接做业务判断，也不直接读写 `config.json`、`.minecraft` 或网络。
- `services/` 不导入 Qt、`tkinter`、`ui.*`，由静态检查固化。
- QML 与 Python 之间的适配集中在 `app/bridges/`；启动时序由 `app/startup.py`
  控制。桥只做参数转换、线程切换与信号通知。
- 长任务走 `TaskRunner`，结果通过主线程调度器回到 QML。

---

## 2. 目录结构

```
FMCL/
├── main.py                    # 统一入口：日志、CLI 解析、QML 启动
├── main_qml.py                # QML 装配：引擎、导入路径、桥注册、启动控制器
├── config.py                  # 配置对象、平台路径、数据目录回退
├── app/
│   ├── context.py             # AppContext：服务注册、懒加载、启动/停止
│   ├── tasks.py               # TaskRunner：后台任务、进度、取消、主线程回调
│   ├── ports.py               # UIPort：服务向界面报告进度/提示的窄接口
│   ├── events.py              # EventBus：同步发布订阅
│   ├── startup.py             # StartupController：启动时序与启动后链条
│   └── bridges/               # QML 上下文属性与 Qt 信号适配
├── services/                  # 业务服务（约 55 个顶层模块 + agent/、voice/）
├── fusion_music/              # 音乐子系统：播放核心、音源、歌单、歌词、安全存储
├── launcher/                  # Minecraft 安装、启动、服务器、整合包、校验
├── plugin_manager/            # 插件清单、权限、钩子、加载、安装、市场
├── qml/
│   ├── App.qml                # 根窗口
│   ├── Splash.qml             # 启动画面
│   ├── StartupDialogs.qml     # 协议、公告、语言选择
│   ├── shell/                 # 顶栏、导航、页面栈、状态栏、面包屑
│   ├── pages/                 # 12 个领域页面 + 开发自查画廊
│   ├── components/            # 自研通用组件与领域组件
│   ├── overlays/              # 桌面歌词、性能悬浮窗等独立窗口
│   └── assets/                # 图标、字体、图片
├── ui/
│   ├── locales/               # zh_CN / zh_TW / en_US / ja_JP
│   └── static/                # 等待小游戏等静态资源
├── scripts/                   # 构建、发布、安装、静态检查
├── tests/                     # 单元、接口、QML 冒烟与视觉回归
├── third_party/               # FluentUI 源码、自编译产物与来源记录
└── docs/                      # 用户与开发者文档
```

根级核心模块按职责保留：`downloader.py`、`modrinth.py`、`curseforge.py`、
`mirror.py`、`updater.py`、`backup_manager.py`、`secure_storage.py`、
`structured_logger.py`、`achievement_engine.py` 等。

---

## 3. 分层与依赖

| 层 | 职责 | 允许依赖 | 禁止依赖 |
|---|---|---|---|
| `qml/` | 界面、动画、输入、页面路由 | 上下文属性、QML 组件 | Python 业务实现 |
| `app/bridges/` | QML 与 Python 的适配 | PySide6、`app/`、`services/` | 直接操作 QML 对象内部结构 |
| `app/` | 服务定位、任务、事件、启动时序 | 标准库、`services/`、PySide6（启动控制器） | 具体页面或 QML 文件 |
| `services/` | 业务规则、数据编排、系统能力 | 标准库、第三方库、`launcher/` | Qt、`tkinter`、`ui.*` |
| `launcher/` | Minecraft 安装与启动核心 | 标准库、第三方库 | GUI 框架 |
| `fusion_music/` | 音乐播放、音源、歌词、凭据 | 标准库、第三方库、Qt Multimedia | QML 页面 |
| `plugin_manager/` | 插件生命周期与扩展协议 | 标准库、第三方库 | 启动器界面 |

强制手段：

- `python scripts/check_services_purity.py`：AST 检查 `services/`、`launcher/` 等层的界面依赖。
- `python scripts/check_callback_keys.py --fail-on-soft`：检查 QML 引用的回调键是否有提供方。
- `python scripts/check_i18n.py`：检查四语言键集合、占位符与调用参数。
- `python scripts/check_qml_rules.py`：检查 QML 的渐变、emoji、硬编码中文、越界 import、原生控件等规则。

---

## 4. 应用装配

### 4.1 AppContext

`app/context.py` 是服务注册表。`app/bootstrap.py` 用 `"模块:类名"` 字符串登记默认服务，
第一次取用时才导入并实例化，避免启动阶段拉起 ONNX、WinSDK 等重依赖。

```python
from app.bootstrap import build_context

context = build_context(config=config, ui=ui_port, scheduler=dispatch_to_main_thread)
context.start_all()
```

服务按拓扑顺序启动；单个服务工厂失败只影响依赖它的功能，不会阻止其他服务与主窗口启动。

### 4.2 TaskRunner

`app/tasks.py` 提供有界后台线程池：

- 提交任务时返回句柄，可查询进度、取消、等待；
- 进度与完成回调统一经主线程调度器排队，不直接在 worker 线程碰 QML；
- 任务异常转成结果对象，由桥转成状态栏、Toast 或对话框。

网络、下载、安装、校验等长任务都走这一层，QML 只订阅进度与结果。

### 4.3 UIPort 与 EventBus

`app/ports.py` 定义服务向界面报告能力的窄接口：进度、提示、确认、选择。
`launcher/` 中不依赖 Qt 的流程（例如预下载）通过 `UIPort` 报告，QML 与 CLI 可以注入不同实现。

`app/events.py` 提供进程内同步发布订阅，用于插件生命周期、成就解锁、任务状态等跨模块通知。
需要进入 QML 的事件由 `EventBridge` 转成 Qt 信号。

### 4.4 桥接层

`main_qml.py` 注册两类对象：

- `Theme`、`Tr`：通过 `qmlRegisterModule("FMCL", 1, 0)` 与上下文属性提供主题和翻译；
- 其余上下文属性：`Nav`、`Shell`、`Dialogs`、`Tasks`、`Events`、`Home`、`Versions`、
  `Install`、`Settings`、`Accounts`、`Resources`、`ModBrowser`、`Modpack`、`Servers`、
  `ServerConfig`、`ServerMods`、`ServerModBrowser`、`Online`、`Backups`、`Bedrock`、
  `Music`、`Tools`、`Links`、`Achievements`、`Jingdu`、`Agent`、`Plugins`、`Update` 等。

桥的职责保持单一：

- 把服务状态转成可绑定属性，变更时发 `notify` 信号；
- 把 QML 的 `@Slot` 调用转成服务命令；
- 把后台结果切回主线程，再更新属性或弹窗；
- 不复制业务规则，不另建一套状态。

---

## 5. 启动链路

```
main.py
  ├── 解析 CLI（login / agent / 普通启动）
  ├── 初始化日志与配置目录
  └── run_gui()
        └── main_qml.assemble()
              ├── 关闭 QML 磁盘缓存，避免热切换时的半初始化对象
              ├── 创建 QApplication / QQmlApplicationEngine
              ├── 装配 AppContext 与 QtUIPort
              ├── 注册 Theme / Tr 与各领域桥
              ├── 安装图标提供者与 Qt 消息处理器
              ├── 创建 StartupController
              ├── engine.load(qml/App.qml)
              └── StartupController.start()
```

`StartupController` 负责启动阶段的状态机：

1. 启动画面至少显示 1 秒；启动器核心与成就引擎并行初始化。
2. 两者都就绪后关闭启动画面，显示主窗口。
3. 30 秒硬超时后强制关闭启动画面，主窗口仍然可用。
4. 核心初始化失败时保留主窗口，状态栏与错误弹窗给出原因。
5. 启动完成后依次处理协议同意、公告、预下载；失败只记录并继续，不阻断主界面。
6. 启动后置任务包括成就同步、签到与静默更新检查。

QML 只读取 `Startup.phase`、`startupActive` 等属性决定启动画面与主窗口的可见性，
不参与计时和容错判断。

---

## 6. QML 界面结构

```
App.qml
├── AppBar.qml          顶栏：返回、面包屑、搜索、通知、账号、窗口按钮
├── Navigation.qml      一级导航：游戏 / 资源与联机 / 工具与扩展 / 插件
├── PageStack.qml       StackView：按路由压栈/出栈，承载页面参数
├── StatusBar.qml       状态栏：文本、进度、后台任务数
└── StartupDialogs.qml  协议、公告、首次语言选择
```

- 12 个一级领域：首页、已安装版本、已安装基岩版、资源浏览、服务器、陶瓦联机、
  存档、音乐、工具、成就总览、AGENT、设置。
- 每个领域页以 `FmPage` 为根，统一页头、路由帧与 `ready / loading / empty / error` 三态。
- 页面之间的跳转使用 `Nav.push(route, params)`，返回使用 `Nav.pop()`；深链使用 `fmcl://`。
- 通用控件集中在 `qml/components/`，页面不直接声明 Qt Quick Controls 的原生视觉控件。
- 主题颜色来自 `Theme`，文案来自 `Tr`；切换主题或语言时由属性通知驱动刷新。
- 独立窗口（桌面歌词、性能悬浮窗）放在 `qml/overlays/`，由 `OverlayBridge` 管理几何与可见性。

开发自查页 `qml/pages/dev/Gallery.qml` 可通过 `fmcl://dev/gallery` 打开，用于检查组件状态。

---

## 7. 业务服务

`services/` 按领域拆分，主要包括：

- 版本与安装：`version_service`、`install_service`、`game_service`、`bedrock_service`；
- 资源与整合包：`resource_service`、`mod_browser_service`、`modpack_service`、`browse_common`；
- 服务器：`server_service`、`server_config_service`、`server_session`、`server_mods_session`；
- 联机与备份：`online_service`、`backup_manager`、`backup_session`；
- 账号与设置：`account_service`、`settings_service`、`theme_service`、`i18n_service`、`font_service`；
- 音乐：`music_smtc`、`music_risk_captcha`、`desktop_lyric`，播放核心在 `fusion_music/`；
- AGENT 与语音：`agent_service`、`agent_chat_service`、`agent_markdown`、`voice_service`、`services/agent/`；
- 工具与系统：`tool_service`、`monitor_service`、`crash_service`、`log_service`、`update_service`；
- 插件、成就与关于：`plugin_service`、`plugin_browser_service`、`achievement_service`、`about_service`、`links_service`。

服务通过 `Service` 基类获得生命周期、依赖查找与事件发布能力；需要界面反馈时使用
`UIPort`，需要后台执行时由调用方提交给 `TaskRunner`。

---

## 8. 音乐子系统

`fusion_music/` 是相对独立的音乐包：

```
fusion_music/
├── core/          QMediaPlayer 播放器、队列、歌词、缓存、响度、更新
├── sources/       网易云、QQ、酷我、酷狗、咪咕、B 站等音源
├── bridges/       音乐 QML 桥与事件
├── security/      请求签名、凭据与本地加密
├── config.py      音乐配置
├── paths.py       音乐数据目录
└── migration.py   旧数据格式读取
```

播放由 Qt Multimedia 的 `QMediaPlayer` 驱动；歌单、歌词缓存、播放状态与网易云凭据
分别持久化。桌面歌词与系统媒体控制由 `app/bridges/music_bridge.py` 和
`services/music_smtc.py` 接到 QML。

---

## 9. 插件系统

`plugin_manager/` 提供：

- `manifest.py`：`plugin.json` 数据模型与 `ui_api` 版本；
- `permissions.py`：权限枚举与低/中/高三级风险；
- `hook_bus.py`：线程安全钩子总线（ALL / COLLECT / FIRST / SHORT_CIRCUIT）；
- `dependency.py`：SemVer 约束、拓扑排序与循环检测；
- `loader.py`、`installer.py`：动态加载、`.fmpl` 安装/卸载/回滚；
- `market.py`：插件市场索引、搜索与下载；
- `manager.py`：生命周期、权限确认、配置与数据目录。

QML 页面通过 `ui_api >= 2` 的 `ui.page.register` 钩子登记，页面取数走
`Plugins.call`，插件推送走 `emit_ui_event`。非 UI 钩子不依赖界面版本。

---

## 10. 数据与持久化

| 数据 | 位置 |
|---|---|
| 配置 | 程序目录 `config.json`；不可写时回退到用户数据目录 |
| 账号 | `accounts.json`（路径可由配置覆盖） |
| 成就 | `achievements.db`（SQLite） |
| 日志 | `latest.log`；Linux 为 XDG 数据目录下的 `fmcl.log` |
| 结构化日志 | `latest_structured.log`（JSONL） |
| 游戏数据 | `.minecraft/` |
| 音乐 | `data/music/`、歌单、歌词缓存、响度数据 |
| 插件 | `plugins/`，配置与数据按插件 id 分目录 |
| 备份 | 默认 `backups/`，可在设置中修改 |

Windows 与 macOS 优先使用程序所在目录（便携模式）；目录不可写时回退到用户数据目录，
并把已有数据复制过去，不删除原目录。Linux 使用 XDG 路径。

敏感值（净读 Token、网易云 Cookie 等）使用 Fernet 加密后再写入配置。

---

## 11. 构建与发布

- 运行依赖由 `uv` 与 `uv.lock` 管理，Python 版本范围 3.10–3.11。
- QML 依赖 PySide6 6.7.3；FluentUI 由 `third_party/` 中的源码自行编译为 QML 插件。
- `build.spec` 生成 PyInstaller **onedir** 产物 `dist/FMCL/`，入口为 `main_qml.py`。
- Windows 安装包由 NSIS 生成；Linux 发布 x86_64 目录产物 tarball。
- GitHub Actions 在标签推送时构建发布资产；手动触发只上传构建产物。

构建前置与命令见 [SETUP.md](SETUP.md) 与 [BUILD_FIXES.md](BUILD_FIXES.md)。

---

## 12. 技术栈

| 领域 | 技术 |
|---|---|
| 界面 | Qt Quick / QML，PySide6 6.7.3，FluentUI 自编译插件 |
| 音视频 | Qt Multimedia（`QMediaPlayer`、`QAudioOutput`） |
| 网络 | `requests`、`aiohttp`、连接池与断点续传 |
| JSON | `orjson`，缺失时回退标准库 |
| 加密 | `cryptography`（Fernet），PBKDF2 派生密钥 |
| 成就 | SQLite |
| 语音 | SenseVoice + ONNX Runtime |
| 系统监控 | `psutil`、`nvidia-ml-py` |
| 全局热键 | `keyboard` |
| 打包 | PyInstaller（onedir）+ NSIS |
| CI/CD | GitHub Actions |
| 测试 | pytest、pytest-xdist、QML 冒烟、视觉回归 |
| 国际化 | JSON 语言表，四语言运行时切换 |
