# FMCL 项目架构

> 本文档包含 FMCL 的项目结构、模块依赖关系和技术栈说明。

---

## 项目结构

```
FMCL/
├── main.py                # 主程序入口，延迟导入优化、日志配置、UI 创建、线程管理，支持 -A/-agent CLI 模式（含 AttachConsole GUI 子系统支持）
├── cli_agent.py           # Agent CLI 核心逻辑（无 GUI，复用 agent 组件，支持单指令/交互模式）
├── agent_cli.py           # Agent CLI 独立入口（供 PyInstaller 打包为 FMCL-Agent.exe 控制台程序）
├── launcher/              # 启动器核心逻辑（包）
│   ├── __init__.py        # MinecraftLauncher 组合类（多继承自 core/server/mrpack）
│   ├── core.py            # MinecraftLauncherCore - 环境检查、版本安装、游戏启动、镜像源管理
│   ├── server.py          # ServerMixin - 服务器安装/启动/停止/管理
│   ├── mrpack.py          # MrpackMixin - 整合包安装/开服（并行下载优化）
│   ├── predownload.py      # 预下载模块 - 首次启动资源包预下载
│   └── verify.py          # 并发文件校验（ThreadPoolExecutor）
├── ui/                    # CustomTkinter 现代化 UI（包）
│   ├── __init__.py        # 向后兼容导出
│   ├── app.py             # ModernApp 组合类（多继承自 app_base/app_server/app_handlers/app_crash/agent）
│   ├── app_base.py        # ModernAppBase(ctk.CTk) - 主窗口 UI 构建、侧边栏、日志捕获
│   ├── app_server.py      # ServerTabMixin - 开服标签页（服务器安装/启动/停止 + 版本管理）
│   ├── app_handlers.py    # EventHandlerMixin - 版本管理、游戏操作、更新检查、队列处理
│   ├── app_crash.py       # CrashHandlerMixin - 崩溃诊断、AI 分析
│   ├── app_backup.py      # BackupTabMixin - 存档备份标签页
│   ├── agent/             # AGENT 智能助手模块（包）
│   │   ├── __init__.py    # 模块导出
│   │   ├── agent_mixin.py # AgentMixin - AGENT 标签页集成
│   │   ├── agent_chat.py  # 聊天 UI 组件 + 选项弹窗（原生 Function Calling，最大 50 轮）
│   │   ├── provider.py    # AI API 调用封装（OpenAI 兼容，返回完整 message 含 tool_calls）
│   │   ├── tools.py       # Tool 定义（Function Calling 格式） + 系统提示词
│   │   ├── engine.py      # Tool 执行引擎（含高危命令检测 ASK_USER_MARKER）
│   │   └── xml_parser.py  # XML 响应解析器（已弃用，保留用于测试兼容）
│   ├── constants.py       # 颜色主题、字体检测、资源类型配置
│   ├── theme_engine.py    # 动态主题引擎（主题加载/切换/导入、版本动态调色、预设主题）
│   ├── dialogs.py         # 通用对话框（确认/提示）、版本选择对话框
│   └── windows/           # 独立窗口类
│       ├── resource_manager.py   # 资源管理窗口（模组/资源包/地图/光影）
│       ├── launcher_settings.py  # 启动器设置窗口（镜像源/最小化等）
│       ├── modpack_install.py    # Modrinth 整合包安装窗口
│       ├── modpack_server.py     # 整合包开服窗口
│       ├── modpack_browser.py    # Modrinth 整合包浏览与下载窗口
│   │   ├── mod_browser.py        # Modrinth 资源浏览与安装窗口（模组/资源包/光影 三标签页）
│   │   ├── plugin_manager.py      # 插件管理窗口（已安装列表 + 启用/禁用/卸载/安装）
│   │   ├── plugin_permission_dialog.py  # 插件权限确认弹窗
│   │   ├── plugin_browser.py            # 插件市场浏览与一键安装窗口
│       └── backup_settings.py    # 备份设置窗口
├── downloader.py          # 多线程下载器 & 异步批量下载 & 模组加载器安装
│   ├── MultiThreadDownloader  # 多线程分段下载 + 文件合并
│   ├── AsyncBatchDownloader   # asyncio + aiohttp 异步并发下载
│   └── install_mod_loader # Forge/Fabric/NeoForge 统一安装
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
│   ├── 连接池复用         # 共享 requests.Session + HTTPAdapter，复用 TCP 连接避免重复 TLS 握手
│   ├── 指数退避重试       # 网络超时/中断自动重试 3 次，退避时间 2^retry 秒
│   ├── 断点续传下载       # Range 头支持，下载中断后从断点继续
│   └── 版本解析工具       # 从版本 ID 解析加载器/游戏版本（含 NeoForge 特殊处理 + 新版 YY.D.H 格式）
├── updater.py             # 自动更新模块
│   ├── check_for_update   # 从 GitHub Release 检查新版本
│   ├── find_suitable_asset # 根据平台匹配安装包
│   ├── download_update    # 下载更新安装包（带进度回调）
│   └── install_update     # 执行静默安装（/S 参数）
├── mirror.py              # BMCLAPI 国内镜像源模块
│   ├── MirrorSource       # 镜像源管理器（URL 重写缓存）
│   ├── URL 重写规则       # 官方 URL -> BMCLAPI 映射（前缀长度排序）
│   └── Monkey Patch       # minecraft_launcher_lib 补丁
├── backup_manager.py      # 存档备份管理（备份/恢复/删除/校验/导出）
├── secure_storage.py      # 安全存储模块（Fernet 加密 Token，密钥文件管理）
├── validation.py          # 输入验证模块（版本ID/IP/端口/内存校验，路径穿越防护）
├── screen_shot.py         # 截图工具（Ctrl+Alt+T 触发）
├── structured_logger.py   # 结构化日志（JSONL 格式，核心流程结构化记录）
├── config.json            # 用户配置（镜像源开关、下载线程数等）
├── requirements.txt       # Python 依赖
├── pyproject.toml         # 项目元数据（版本号等）
├── build.spec             # PyInstaller 构建配置（含 FMCL GUI 程序 + FMCL-Agent 独立控制台程序）
├── icon.ico               # 应用图标（多尺寸 ICO）
├── installer.nsi          # Windows NSIS 安装脚本
├── Makefile               # 构建/开发命令集合
├── package.json           # Node.js 开发工具配置
├── scripts/
│   ├── release.py         # 自动发布脚本
│   └── fix_common_issues.py  # 常见问题修复工具
└── docs/
    ├── SETUP.md           # 构建设置详细文档
    ├── BUILD_FIXES.md     # 构建问题修复记录
    └── TROUBLESHOOTING.md # 故障排除指南
```

## 模块依赖关系

```
main.py
  ├── -A / -agent 参数 → 调用 _attach_console() → cli_agent.py (CLI Agent)
  │   ├── agent_cli.py (独立控制台入口，打包为 FMCL-Agent.exe)
  │   ├── config.py (全局配置，读取 jdz_token)
  │   ├── launcher/ (核心逻辑包)
  │   └── ui/agent/ (复用 AIProvider、工具定义、执行引擎)
  │       ├── provider.py (AI API 调用，返回完整 message 含 tool_calls)
  │       ├── tools.py (Function Calling 工具定义 + system prompt)
  │       ├── engine.py (Tool 执行引擎 + 高危检测 + ask_user)
  │       └── agent_chat.py (GUI 循环调度，原生 Function Calling，最大 50 轮)
  ├── config.py (全局配置)
├── plugin_manager/            # ★ 插件系统（包）- 第三方扩展框架
│   ├── __init__.py            # 插件总控导入
│   ├── manifest.py            # PluginManifest 数据模型 + plugin.json 规范
│   ├── permissions.py         # 权限枚举 + 三级风险分级 + 运行时确认逻辑
│   ├── base.py                # PluginBase 抽象基类 + HookPoint 枚举 + PluginState
│   ├── hook_bus.py            # 线程安全钩子总线（4 种策略）
│   ├── dependency.py          # SemVer 依赖解析 + Kahn 拓扑排序 + 循环检测
│   ├── loader.py              # importlib 动态插件加载 + 热重载
│   ├── installer.py           # .fmpl 包安装/卸载/回滚
│   ├── market.py               # 插件市场（GitHub 索引获取、搜索筛选、源码下载）
│   ├── manager.py              # PluginManager 统一入口（组合所有子模块）
  │   └── secure_storage.py (Token 加密存储)
  ├── validation.py (输入验证)
  ├── backup_manager.py (存档备份管理)
  ├── launcher/ (核心逻辑包)
  │   ├── core.py (环境检查、版本安装、游戏启动)
  │   ├── server.py (服务器管理)
  │   ├── mrpack.py (整合包安装/开服)
  │   ├── verify.py (并发文件校验)
  │   ├── mirror.py (镜像源)
  │   └── downloader.py (下载器 & 模组加载器)
  ├── ui/ (界面包)
  │   ├── app.py → app_base.py / app_server.py / app_handlers.py / app_crash.py / app_backup.py
  │   ├── constants.py (颜色主题、字体)
  │   ├── dialogs.py (通用对话框)
  │   ├── windows/ (独立窗口)
  │   ├── launcher.get_callbacks() (通过回调与核心逻辑交互)
  │   ├── modrinth.py (Modrinth 模组搜索与安装)
  │   └── updater.py (自动更新)
  └── updater.py (自动更新 - GitHub Release)
```

## 技术栈

| 组件 | 技术 | 说明 |
|------|------|------|
| UI 框架 | CustomTkinter | 现代 Tkinter 封装，深色主题 |
| Minecraft 库 | minecraft-launcher-lib | 版本安装、启动命令生成 |
| 镜像源 | BMCLAPI (bangbang93) | 国内加速下载 |
| 启动优化 | 延迟导入 + 后台初始化 | 窗口先显示，核心后台加载 |
| JSON 解析 | orjson (回退 stdlib json) | 3-10 倍解析加速 |
| 并发校验 | ThreadPoolExecutor | 多线程文件哈希校验 |
| 异步下载 | asyncio + aiohttp | 批量并发下载 |
| JVM 优化 | G1GC + 固定堆内存 | 减少游戏卡顿 |
| Java 扫描 | 跨平台系统扫描 | Windows/macOS/Linux 自动检测最佳 Java 版本 |
| URL 缓存 | dict 缓存重写结果 | 避免重复匹配规则 |
| 拖拽支持 | tkinterdnd2 | 文件拖拽安装资源 |
| 日志 | logzero | 轻量级日志框架 |
| 结构化日志 | JSONL 格式 | 核心流程结构化记录，方便程序化分析 |
| HTTP | requests.Session + HTTPAdapter | 连接池复用（20 池/50 最大），避免重复 TLS 握手 |
| 加密 | cryptography (Fernet) | 敏感 Token AES-128-CBC + HMAC-SHA256 加密存储 |
| 密钥管理 | PBKDF2-HMAC-SHA256 | 密码派生密钥（600000 次迭代） |
| 输入验证 | 正则 + 白名单 | 防止命令注入和路径穿越攻击 |
| 文件完整性 | SHA1 / SHA256 / SHA512 | 模组和更新包下载后自动校验哈希 |
| 模组搜索 | Modrinth API V2 | 在线搜索和安装模组/整合包 |
| 下载 | 多线程分段下载 | 大文件并行下载加速 |
| 断点续传 | Range 头 + 追加写入 | 下载中断后自动从断点继续 |
| 指数退避重试 | 3 次重试，退避 2^retry 秒 | 网络超时/中断自动恢复 |
| 截图 | pyautogui + keyboard | 区域截图 + 快捷键监听 |
| 自动更新 | GitHub Release API | 版本检查 + 静默安装 |
| 构建打包 | PyInstaller + NSIS | 可执行文件 + Windows 安装包 |
| Linux 兼容构建 | Docker + manylinux_2_28 + Python `--enable-shared` | GLIBC 2.28 兼容，覆盖 Ubuntu 18.04+、Debian 10+、RHEL 8+ |
| CI/CD | GitHub Actions | 多平台自动构建与发布 |
| 提交规范 | Husky + Commitlint | 约定式提交自动校验 |
