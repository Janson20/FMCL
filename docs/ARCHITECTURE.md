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
├── screen_shot.py         # 截图工具（Ctrl+Alt+T 触发，区域截图）
├── structured_logger.py   # 结构化日志（JSONL 格式，核心流程结构化记录）
├── version_utils.py       # 版本工具（SemVer 比较、正则模式集、YY.D.H 格式解析）
├── achievement_engine.py  # 成就引擎（47 项成就，9 大分类，多阶段，SQLite 持久化）
├── achievement_defs.py    # 成就定义（成就数据结构、触发条件）
├── achievement_sync.py    # 成就云存档同步（REST API 推送/拉取/合并）
├── launcher/              # 启动器核心逻辑（包）
│   ├── __init__.py        # MinecraftLauncher 组合类（多继承自 core/server/mrpack）
│   ├── core.py            # MinecraftLauncherCore - 环境检查、版本安装、游戏启动、JVM 优化
│   ├── server.py          # ServerMixin - 服务器安装/启动/停止/管理
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
│       └── backup_settings.py          # 备份设置窗口
├── scripts/
│   ├── install.sh         # Linux 一键安装脚本（支持 7 大发行版）
│   ├── release.py         # 自动发布脚本
│   └── fix_common_issues.py  # 常见问题修复工具
├── tests/
│   ├── test_account.py
│   ├── test_imports.py
│   ├── test_modrinth_versions.py
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
  │   │   └── windows/（14 个独立窗口）
  │   │
  │   ├── launcher/（核心逻辑包）
  │   │   ├── core.py（环境检查 + 版本安装 + 游戏启动 + JVM 优化）
  │   │   ├── server.py（服务器安装/启动/停止）
  │   │   ├── mrpack.py（整合包安装/开服）
  │   │   ├── predownload.py（预下载）
  │   │   └── verify.py（并发文件校验）
  │   │
  │   ├── modrinth.py（Modrinth API 搜索/下载/安装）
  │   ├── curseforge.py（CurseForge API，可选）
  │   ├── mirror.py（BMCLAPI 镜像 + Monkey Patch）
  │   ├── downloader.py（多线程 + 异步下载）
  │   ├── updater.py（自动更新）
  │   ├── backup_manager.py（存档备份管理）
  │   ├── achievement_engine.py（成就引擎）
  │   ├── achievement_sync.py（成就云同步）
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
