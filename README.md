# FMCL - Fusion Minecraft Launcher

一个功能丰富的 Minecraft 启动器，基于 CustomTkinter 现代化 UI，支持国内镜像加速、多模组加载器安装与版本管理。

---

## 功能特点

### 🎮 版本管理
- 浏览并安装所有 Minecraft 版本（正式版 + 测试版）
- 已安装版本列表，支持一键选择和启动
- 版本删除，释放磁盘空间
- 分页浏览可用版本列表（每页 20 个）
- 每个版本条目提供 ⚙ 版本设置、🧩 安装模组、X 删除三个快捷按钮

### 🎨 动态主题引擎
- **🎨 预设主题**：内置 5 种预设主题（默认深色、海洋蓝调、森林绿意、薰衣草紫、日落暖橙），一键切换
- **📂 导入 .json 主题**：支持用户导入自定义 `.json` 主题文件，主题文件存储在 `themes/` 目录
- **🎲 自定义强调色**：在设置中输入 Hex 色值（如 `#e94560`）自定义强调色，或点击「随机」生成随机颜色
- **📊 版本动态主题**：开启后，启动器会根据当前运行的 Minecraft 版本自动调整强调色（如 1.21 深紫色调）
- **主题持久化**：主题选择和自定义强调色自动保存到 `config.json`，下次启动自动恢复

### 🔗 链接标签页
- **五标签页布局**：主界面包含"🎮 游戏"、"💾 备份"、"🖥 开服"、"🔗 链接"、"🌐 联机"和"🤖 AGENT"六个标签页
- **默认游戏标签页**：保留原有的三栏布局（侧边栏、已安装版本、操作面板），所有游戏功能保持不变
- **链接标签页**：收录了29个常用的Minecraft相关网站，包括官方网站、中文社区、资源平台和实用工具
- **网站卡片**：每个网站以卡片形式展示，包含名称、描述、标签和直达链接
- **一键访问**：点击"🌐 打开链接"按钮在浏览器中打开网站
- **链接复制**：点击"📋 复制链接"按钮复制网站URL到剪贴板
- **智能标签**：每个网站显示最多3个分类标签，便于快速识别网站类型

### ⚡ 国内镜像加速
- 内置 [BMCLAPI](https://bmclapi2.bangbang93.com/) 镜像源（by bangbang93），国内下载速度大幅提升
- 一键开关镜像源，切换即时生效
- 自动连接测试，状态栏显示连接结果
- 覆盖范围：版本清单、游戏资源、库文件、Forge/Fabric/NeoForge 安装器
- **SSL 证书验证**：所有网络请求默认验证 SSL 证书，确保通信安全可靠

### 🔧 多模组加载器
- **Forge** - 最广泛使用的模组加载器
- **Fabric** - 轻量级模组加载器
- **NeoForge** - Forge 的社区分支
- 安装模组加载器时自动安装原版 Minecraft（与原版安装**并行执行**，无须额外等待），无需重复操作
- **版本隔离**：安装了模组加载器的版本自动启用版本隔离，游戏从 `.minecraft/versions/{版本名}/` 读取 mods、config 等资源，各版本互不干扰
- **新版本格式支持**：NeoForge 安装器已适配 Minecraft 2026 年起的新版本命名规则 `YY.D.H`（如 `26.1`、`26.1.1`），兼容旧版 `1.X.Y` 格式

### 🧩 Modrinth 模组浏览与安装
- 集成 [Modrinth](https://modrinth.com/) API，在线搜索和安装模组
- 安装了模组加载器的版本自动显示 🧩 按钮，一键打开模组浏览器
- 自动识别游戏版本和模组加载器类型（Forge/Fabric/NeoForge），精准筛选兼容模组
- 支持关键词搜索，窗口打开时自动加载热门模组列表
- 分页浏览搜索结果，支持翻页查看更多模组
- 一键安装：自动获取兼容版本并下载到 `mods/` 目录
- 支持版本隔离：自动将模组安装到对应版本的独立目录
- **新版本命名适配**：支持 2026 年起的新版本格式 `YY.D.H`（如 `26.1`、`26.1.1`），兼容旧版 `1.X.Y` 格式
- **模组加载器版本匹配优化**：Fabric/Quilt 格式（`fabric-loader-{version}-{mc}`）和 Forge/NeoForge 格式（`{mc}-{loader}-{version}`）均支持新版本号模糊匹配，启动和安装模组时自动识别

### 📦 Modrinth 整合包安装
- 支持安装 `.mrpack` 格式的 Modrinth 整合包
- **🌐 从 Modrinth 下载**：内置整合包浏览器，支持关键词搜索、按 MC 版本分组浏览、版本选择和一键下载
- 自动读取整合包元数据（名称、简介、Minecraft 版本）
- 可选组件支持：可选择安装整合包包含的可选文件
- **版本隔离**：整合包资源默认安装到 `versions/<版本ID>/` 目录，避免不同整合包之间的模组和配置冲突
- **并行安装优化**：整合包文件下载与原版 Minecraft 安装并行执行，模组文件多线程并行下载，大幅提升安装速度
- **分段进度显示**：实时显示整合包文件下载和原版安装的独立进度百分比及总进度条
- 自动下载整合包所需的所有模组、配置文件并处理依赖关系
- 安装完成后自动刷新版本列表，可直接启动游戏
- **多语言支持**：安装窗口界面文字跟随启动器语言设置切换

### 📦 整合包开服
- 支持将 `.mrpack` 整合包安装为服务器
- **🌐 从 Modrinth 下载**：内置整合包浏览器，与整合包安装窗口一致
- 自动安装整合包文件（mods、configs 等）到服务器目录
- 自动下载对应版本的 vanilla 服务器核心
- 自动同意 EULA，创建基本 server.properties
- 支持自定义服务器名称
- **自动检测并安装服务端 mod loader**：自动识别整合包中的 Forge/Fabric/NeoForge/Quilt，下载并运行对应的服务端安装器
- **自动下载 Fabric API**：Fabric 服务端安装完成后，自动从 Modrinth 下载 Fabric API 及其依赖
- **并行安装优化**：整合包文件下载与原版客户端安装并行执行，模组文件多线程并行下载
- **分段进度显示**：实时显示整合包文件下载和原版安装的独立进度百分比及总进度条
- 安装完成后自动刷新服务器列表，可直接启动
- **多语言支持**：开服窗口界面文字跟随启动器语言设置切换

### 📦 资源管理
- 支持模组、资源包、地图、光影四种资源类型
- **🔍 全标签页搜索**：每个标签页顶部都有搜索栏，支持按资源名称快速过滤
- **🧩 模组详情提取**：自动从模组 jar 文件中提取元数据（名称、modid、作者、简介、图标），无需联网
- **模组卡片布局**：左侧显示模组图标（自动提取或回退图标），右侧展示名称、作者与简介、modid 与文件名
- **加载进度提示**：首次打开模组标签页时显示「正在读取模组信息... (x/y)」进度，不阻塞界面
- 拖拽安装：将文件直接拖入窗口即可安装
- 地图 zip 自动解压到 `saves/` 目录
- 模组一键启用/禁用（`.disabled` 后缀）
- 各类型独立文件夹，一键打开
- 支持版本隔离模式

### 🖥 服务器管理
- **开服标签页**：独立的"🖥 开服"标签页，支持下载并启动 Minecraft 服务器
- **一键加入**：点击「加入服务器」按钮，自动下载对应客户端版本并直连 localhost:25565
- **一键安装**：输入版本号或从快速选择列表点击安装，自动下载服务器端文件
- **自动同意 EULA**：安装服务器时自动创建 `eula.txt` 并同意
- **自动安装 Java Runtime**：安装同名客户端版本自动下载所需 Java runtime 到 `.minecraft/runtime/`，启动时自动从 runtime 查找合适版本
- **版本隔离**：每个服务器版本独立目录 `.minecraft/server/<version>/`，互不干扰
- **自动生成配置**：安装时自动创建 `server.properties`（离线模式、最大 20 人等默认配置）
- **内存设置**：支持选择 1G ~ 16G 最大内存
- **启动/停止**：一键启动服务器，通过 stdin 发送 `stop` 命令优雅停止
- **实时日志**：左侧控制台实时显示服务器输出日志
- **命令发送**：支持在控制台输入任意服务器命令（如 `op`, `gamemode`, `stop` 等）
- **玩家列表**：状态栏实时显示在线玩家数量和玩家名称
- **内存监控**：状态栏实时显示服务器进程内存占用（每 2 秒刷新）
- **进程监控**：自动检测服务器进程退出，更新 UI 状态
- **Fabric API 自动安装**：启动 Fabric 服务器时自动检测 mods 目录，缺失则从 Modrinth 下载 Fabric API
- **删除管理**：支持删除已安装的服务器版本

### 🌐 NAT 穿透联机
- **联机标签页**：新增"🌐 联机"标签页，基于 [Natter](https://github.com/MikeWang000000/Natter) 实现 NAT 穿透联机，无需公网中转服务器即可实现点对点直连
- **Python 环境检测与安装**：启动时自动检测 Python 环境，未安装时提供一键安装（Windows 平台静默安装 Python 3.12）
- **Natter 管理**：内置 Git 克隆/更新 Natter 仓库功能，自动管理 Natter 到本地数据目录
- **NAT 类型检测**：一键检测当前网络 NAT 类型（NAT1/全锥型 完美支持，NAT2/受限锥型 可能不稳定，NAT3/4/对称型 不支持穿透）
- **灵活配置**：支持自定义 Minecraft 服务器端口（默认 25565）、目标内网 IP（可选，支持跨设备穿透）、保活间隔（默认 20 秒）
- **一键启停**：点击「🚀 启动 Natter」按钮启动穿透，点击「⏹ 停止 Natter」优雅停止
- **公网地址展示**：自动解析并醒目展示 Natter 输出的公网地址
- **一键复制地址**：点击「📋 复制地址」按钮将公网地址复制到剪贴板，方便分享给好友
- **实时日志查看**：右侧日志面板实时显示 Natter 运行输出，便于排查问题

### 💾 存档备份
- **备份管理标签页**：独立的"💾 备份"标签页，位于"游戏"和"开服"之间
- **手动备份**：选择存档，一键备份，支持添加备注
- **自动备份**：游戏启动前/退出后自动执行，可配置触发时机
- **备份列表**：查看某存档的所有备份，显示时间、大小、备注、游戏版本
- **一键恢复**：选择备份还原，自动保护当前存档（重命名为 `_bak_时间戳`）
- **备份删除**：删除过期或多余的备份（需二次确认）
- **备份导出**：将备份导出为 ZIP 文件，方便分享
- **存储设置**：自定义备份目录、压缩等级、最大备份数、恢复时旧存档处理方式
- **进度提示**：压缩/解压时实时显示进度条，不阻塞界面
- **完整性校验**：恢复前自动校验 ZIP 完整性，失败则阻止恢复并提示
- **磁盘空间检查**：备份前检查可用空间，不足时提前警告
- **自动清理**：超出最大备份数量时自动删除最旧的备份
- **版本隔离支持**：自动扫描全局 `saves/` 和版本隔离目录中的存档
- **备份索引**：使用 `index.json` 记录备份元数据，便于快速检索

### 🖥️ 现代化界面
- 基于 CustomTkinter 的深色主题，流畅美观
- **跨平台中文字体适配**：自动检测系统并选择合适的中文字体（Windows: Microsoft YaHei / macOS: PingFang SC / Linux: Noto Sans CJK SC 等）；Linux 下若无中文字体则自动通过包管理器安装
- **首次使用协议弹窗**：首次启动时最先弹出使用条款与隐私协议同意窗口，需勾选同意后方可继续使用，协议内容包含 Minecraft EULA、净读 AI 隐私协议及净读使用条款
- 启动画面：加载时在屏幕中央展示应用图标，加载完成后自动切换到主窗口
- 三栏布局：左侧边栏（角色名、皮肤、日志）、中间已安装版本、右侧操作面板
- 异步操作：所有网络与安装任务在后台线程执行，UI 不卡顿
- 实时进度条与状态提示
- 游戏启动后自动最小化启动器窗口（可选，检测到游戏窗口出现后执行）

### ⚙ 启动器设置
- 独立设置窗口，点击顶栏「⚙ 设置」按钮打开
- 🔽 启动后最小化开关
- 🇨🇳 使用国内镜像源开关
- ⚡ 下载线程数滑块（1-255），实时调整多线程下载并发数
- 🌐 界面语言切换（简体中文/English/繁體中文/日本語），切换后点击「应用」自动重启启动器生效
- 🤖 净读 AI 账号登录（用于崩溃智能分析功能）
- 🎨 动态主题引擎（详见下方「🎨 动态主题引擎」）
- 设置自动持久化到 `config.json`

### 🎨 动态主题引擎
- **🎨 预设主题**：内置 5 种预设主题（默认深色、海洋蓝调、森林绿意、薰衣草紫、日落暖橙），一键切换
- **📂 导入 .json 主题**：支持用户导入自定义 `.json` 主题文件，主题文件存储在 `themes/` 目录
- **🎲 自定义强调色**：在设置中输入 Hex 色值（如 `#e94560`）自定义强调色，或点击「随机」生成随机颜色
- **📊 版本动态主题**：开启后，启动器会根据当前运行的 Minecraft 版本自动调整强调色：
  - 1.21 深紫色调 | 1.20 樱花金 | 1.19 深绿色 | 1.18 天空蓝 | 1.17 铜绿色
  - 1.16 下界红 | 1.15 蜂蜜黄 | 1.14 竹绿 | 1.13 海洋蓝 | 等更多版本
- **主题持久化**：主题选择和自定义强调色自动保存到 `config.json`，下次启动自动恢复

### ℹ 关于
- 点击顶栏「ℹ 关于」按钮打开关于对话框
- 显示 FMCL 版本号、Python 版本、操作系统、系统架构等信息

### 👤 自定义角色
- 左侧边栏可设置自定义游戏角色名
- 角色名自动持久化到 `config.json`
- 启动游戏时自动注入自定义角色名

### 🎨 自定义皮肤
- 左侧边栏支持选择自定义皮肤文件（PNG 格式）
- 自动验证皮肤尺寸（支持 64x64 / 64x32 / 128x128 / 128x64）
- 皮肤文件自动复制到 `.minecraft/skins/` 目录

### 📋 启动器日志
- 左侧边栏内置实时日志查看器
- 自动捕获 logzero 日志输出
- 支持清空日志

### ⚡ 性能优化
- **JSON 高速解析**：使用 orjson 替代标准库 json，解析速度提升 3-10 倍（自动回退）
- **并发文件校验**：基于 ThreadPoolExecutor 的多线程哈希校验，校验大量文件时速度提升 3-5 倍
- **异步批量下载**：基于 asyncio + aiohttp 的并发下载器，单线程内高效处理数百个下载任务
- **JVM 参数优化**：自动注入 G1GC、固定堆内存等优化标志，减少游戏卡顿
- **stdout 管道管理**：检测到游戏窗口出现后自动关闭 stdout 管道，避免管道缓冲区满导致游戏最后加载阶段卡顿
- **延迟加载**：非首屏模块（pyautogui、keyboard、shutil 等）延迟导入，加快启动速度
- **URL 重写缓存**：镜像源 URL 转换结果缓存，避免重复匹配
- **算法优化**：版本查找使用 set 实现 O(1) 查找，替代列表 O(n) 线性搜索

### 📥 预下载
- 在协议同意并确认公告后，检查是否已预下载 Minecraft 资源包，加速后续版本安装
- 多线程分段下载，充分利用带宽
- 实时进度条显示下载/解压进度
- 支持取消下载，取消后仍可正常使用启动器
- 选择"是"或"取消"后均不再重复提示

### 📸 截图工具
- 内置区域截图工具，框选屏幕区域即可保存
- 快捷键 `Ctrl+Alt+T` 随时触发

### 📋 日志系统
- 基于 logzero 的完整日志记录
- **跨平台日志存储**：
  - Windows/macOS: `latest.log` 保留在程序目录
  - Linux: 优先 `/var/log/fmcl/latest.log`（遵循 FHS 标准）；若目录不可写则自动回退到 `~/.fmcl/latest.log`
- **结构化日志**：核心流程额外输出 JSONL 格式结构化日志到 `latest_structured.log`，方便程序化分析和统计
  - ★★★ 必须场景：崩溃捕获（含 error_type、version、loader、log_snippet）、关键下载失败（含 status_code、mirror_source）、模组安装决策（含 decision_reason、dependencies）
  - ★★☆ 推荐场景：服务器启动全流程（含 server_type、mods_count）、备份/恢复操作（含 size_bytes、reason）、游戏启动命令生成（含 jvm_args、game_args）、自动更新行为（含 failure_stage、error_message）

### 💥 崩溃检测与报告
- 自动检测游戏异常退出（退出码非 0）
- **智能崩溃诊断**：基于日志关键词匹配，自动识别 11 种常见崩溃类型（Mixin 错误、依赖缺失、内存溢出、模组冲突等），并给出修复建议
- 崩溃时自动收集崩溃报告、游戏日志、JVM 崩溃日志等诊断信息
- 提供五个操作按钮：
  - **打开崩溃报告** - 直接查看 Minecraft 生成的崩溃报告文件
  - **打开游戏日志** - 查看游戏运行日志
  - **导出崩溃报告** - 将崩溃报告、游戏日志、JVM 崩溃日志、启动器日志、系统信息打包为 ZIP 文件，方便分享给他人分析
  - **上传并分享日志** - 一键将游戏日志（latest.log）上传到 LogShare.CN，自动复制分享链接到剪贴板，方便快速分享给他人排查问题
  - **AI 智能分析（净读 AI）** - 接入净读 AI DeepSeek 模型，自动分析崩溃原因并给出具体修复建议，结果支持保存为 TXT 文件

### 🤖 净读 AI 集成
- 在设置中登录净读账号，获取 Token 用于 AI 功能
- 崩溃分析：将崩溃报告、游戏日志、启动器日志和系统信息发送至 DeepSeek 模型进行分析
- 分析结果以弹窗展示，包含崩溃原因分析和建议操作
- 支持将 AI 分析结果保存为 TXT 文件
- **隐私保护**：首次使用 AI 分析时弹出隐私说明，需用户勾选同意后才可使用；同意状态持久化保存，后续无需重复确认
- **安全存储**：登录 Token 使用 Fernet (AES-128-CBC + HMAC-SHA256) 加密存储于 `config.json`，密钥文件保存在 `<base_dir>/.fmcl_key`，支持跨机器迁移

### 🤖 AGENT 智能助手
- **快速输入框**：启动器顶部标题栏右侧新增 AI 快速输入框，输入内容后按回车直接跳转到"🤖 AGENT"标签页并自动发送消息，无需手动切换标签页
- **自然语言控制**：新增"🤖 AGENT"标签页，集成聊天界面，支持通过自然语言管理 Minecraft
- **AI 驱动**：基于净读 AI（OpenAI 兼容 API），通过 function calling 实现智能决策
- **核心工具集**：封装了版本获取、安装、删除、启动、模组搜索与安装、资源包搜索与安装、光影搜索与安装、版本资源查询、服务器管理、整合包搜索下载与安装开服、终端命令执行等 20 个工具供 AI 调用
- **智能工作流**：AI 自动分析用户意图 -> 顺序调用工具 -> 分析结果 -> 需要时弹出选项让用户选择 -> 继续执行
- **XML 标准回复**：AI 回复采用标准 XML 格式（`<thinking>`、`<message>`、`<action>` 等标签），前端解析后友好展示
- **选项弹窗**：当 AI 判断需要用户选择时（如多个版本匹配），弹出选项对话框供用户点选
- **使用场景示例**：
  1. "帮我下载最新版 Minecraft" -> AI 获取版本列表 -> 安装最新正式版
  2. "帮我启动 1.20.1" -> AI 获本地版本列表 -> 发现多个 1.20.1 版本 -> 弹出选项（原版/Forge/Fabric）-> 用户选择后启动
  3. "给 1.20.1 装个钠" -> AI 搜索 Modrinth -> 找到 Sodium -> 自动匹配版本和加载器 -> 安装
  4. "删除 1.19.2 版本" -> AI 获取已安装列表 -> 确认存在 -> 执行删除
  5. "帮我开个 1.21.4 的服务器" -> AI 获取服务器版本列表 -> 安装服务器 -> 启动服务器
  6. "帮我安装这个整合包 D:\\modpacks\\skyblock.mrpack" -> AI 读取整合包信息 -> 执行安装
  7. "帮我搜个空岛整合包并下载" -> AI 在 Modrinth 搜索 skyblock -> 展示结果 -> 用户选择 -> 下载 .mrpack -> 安装
  8. "给 1.20.1-forge 装个 Faithful 资源包" -> AI 获取已安装版本 -> 搜索 Modrinth 资源包 -> 找到 Faithful -> 安装到对应版本目录
  9. "给 1.20.1 装个 BSL 光影" -> AI 获取已安装版本 -> 搜索 Modrinth 光影 -> 找到 BSL Shaders -> 安装到对应版本目录
  10. "看看 1.20.1-forge 装了哪些模组" -> AI 获取已安装版本 -> 调用 list_version_resources(type=mods) -> 列出所有模组文件
  11. "帮我在 D:\\project 目录执行 npm install" -> AI 调用 exec_command -> 在指定路径执行命令并返回结果
- **终端命令执行**：支持通过 AI 在指定目录下执行终端命令，内置 60+ 高危命令前缀检测（如 rm -rf、dd、shutdown、DROP TABLE、docker run --privileged 等），检测到高危命令时弹出确认对话框要求用户手动确认，防止 AI 执行危险操作
- **Token 配置**：Token 通过启动器设置窗口（⚙ 设置 → 净读 AI 登录）统一管理，登录后 AGENT 标签页自动启用智能助手功能
- **隐私说明**：Token 仅保存在本地配置文件中，不会上传至任何第三方
- **命令行模式**：支持通过命令行参数使用，无需 GUI：
  - `python main.py login -name <用户名> -pwd <密码>` - 登录净读 AI（省略 `-pwd` 则交互输入密码）
  - `python main.py -agent "帮我安装最新版"` - 执行单条指令
  - `python main.py -A "给1.20.1装钠"` - 执行单条指令（简写）
  - `python main.py -A` - 进入交互模式，连续对话
  - 交互模式支持 `/quit` 退出、`/clear` 清空对话

### 🔒 安全特性
- **SSL 证书验证**：所有 HTTP 请求（Modrinth API、GitHub Release、镜像源等）均启用 SSL 证书验证，防止中间人攻击
- **数据加密存储**：敏感 Token 使用随机密钥文件 + Fernet 对称加密存储，支持密码派生密钥（环境变量 `FMCL_ENC_KEY_PASSWORD`）
- **密钥文件可迁移**：`.fmcl_key` 密钥文件可随 `config.json` 一起拷贝到新机器，Token 无缝迁移
- **输入验证与防注入**：所有用户输入（版本 ID、服务器 IP、内存参数等）均经过白名单验证，防止命令注入和路径穿越攻击
- **文件完整性校验**：Modrinth 模组下载后自动验证 SHA1/SHA512 哈希，更新包下载后验证文件大小和 SHA256，确保文件未被篡改
- **健壮下载引擎**：Modrinth 下载集成连接池复用（避免重复 TLS 握手）、指数退避重试（超时/中断自动重试 3 次）、断点续传（Range 头 + 追加写入），确保差劲网络下的下载可靠性
- **原子写入**：配置文件、备份索引等更新时使用临时文件 + 重命名机制，防止写入中断导致文件损坏
- **安装回滚**：整合包安装失败时自动清理部分下载的文件和目录，避免残留损坏

### 🌐 多语言界面
- 支持简体中文、English、繁體中文、日本語四种语言
- 从配置文件读取语言设置
- 启动时自动检测系统语言并切换对应界面语言（可通过设置窗口手动切换）
- 在设置窗口中切换语言后，点击「应用」按钮自动重启启动器，新语言即时生效
- **游戏语言**：启动时自动将 `.minecraft/options.txt` 中的语言设置修改为 `zh_cn`，确保首次启动游戏时即为中文界面
- **覆盖范围**：主界面、设置窗口、备份管理、服务器管理、整合包安装、整合包开服等所有窗口均支持多语言切换

### ⬆ 自动更新
- 启动时自动从 GitHub Release 检查新版本（可配置开关）
- 发现新版本时弹出更新对话框，展示版本号和更新日志
- 自动识别当前平台，下载对应的安装包（Windows NSIS / macOS DMG / Linux AppImage）
- 下载完成后自动执行静默安装（Windows 使用 `/S` 参数），安装程序启动后自动退出当前程序
- 也可手动点击顶栏「⬆ 更新」按钮检查更新

### 📢 启动公告
- 启动顺序：协议同意 → 自动从服务器获取最新公告 → 确认公告后进入预下载检查
- 公告内容以弹窗形式展示，包含滚动文本区域
- 获取失败时静默跳过，不影响正常使用

---

## 界面预览

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│  ⛏ FMCL   Minecraft Launcher   ⬆ 更新  🔄 刷新  ⚙ 设置  │
│  [🎮 游戏] [💾 备份] [🖥 开服] [🔗 链接] [🌐 联机] [🤖 AGENT]            │
├────────────┬──────────────────────────────┬──────────────────────────────────────┤
│ 👤 角色名  │  📦 已安装版本  ⚙  3 个版本 │  📥 安装新版本               │
│ [Steve   ] │  ─────────────────────────── │  ──────────────────────      │
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
| Java 8+ | 运行 Minecraft（启动器会自动检测系统 Java） |

---

## 安装

### 方式一：从 Release 下载（推荐）

前往 [Releases](https://github.com/Janson20/FMCL/releases) 页面下载适合你平台的安装包：

| 平台 | 文件 | 说明 |
|------|------|------|
| Windows | `FMCL-Setup-x.x.x.exe` | NSIS 安装包，双击运行 |
| macOS Intel | `FMCL-x.x.x-mac-amd64.dmg` | Intel 芯片 |
| macOS Apple Silicon | `FMCL-x.x.x-mac-arm64.dmg` | M1/M2/M3 芯片 |
| Linux | `FMCL-x.x.x-linux-amd64.deb` | Debian/Ubuntu 等发行版 |
| Linux | `FMCL-x.x.x-x86_64.AppImage` | 通用 Linux 格式 |

#### 安装说明

- **Windows**: 双击 `.exe` 安装包，按向导完成安装
- **macOS**: 双击 `.dmg` 文件，将 FMCL.app 拖入 Applications 文件夹。首次打开若提示"无法验证开发者"，请在系统设置 > 安全性与隐私中点击"仍要打开"，或运行：
  ```bash
  xattr -cr /Applications/FMCL.app
  ```
- **Linux DEB**:
  ```bash
  sudo dpkg -i FMCL-x.x.x-linux-amd64.deb
  ```
- **Linux AppImage**:
  ```bash
  chmod +x FMCL-x.x.x-x86_64.AppImage
  ./FMCL-x.x.x-x86_64.AppImage
  ```

### 方式二：从源码运行

```bash
# 克隆仓库
git clone https://github.com/Janson20/FMCL.git
cd FMCL

# 安装 Python 依赖
pip install -r requirements.txt

# 运行启动器（GUI 模式）
python main.py

# 运行 Agent CLI 模式
python main.py login -name <用户名> -pwd <密码>   # 登录净读 AI
python main.py -agent "帮我安装最新版"
python main.py -A              # 交互模式
```

> 💡 建议使用虚拟环境：`python -m venv .venv && source .venv/bin/activate`

---

## 使用方法

### 首次启动

1. 启动程序后自动检查运行环境
2. 首次运行会自动下载最新正式版 Minecraft
3. 下载完成后即可启动游戏

### 日常使用

#### 安装新版本

1. 在右侧面板「版本 ID」输入框中输入版本号（如 `1.20.4`）
2. 可选择模组加载器：
   - **无** - 仅安装原版 Minecraft
   - **Forge** - 安装 Forge 模组加载器（自动安装原版）
   - **Fabric** - 安装 Fabric 模组加载器（自动安装原版）
   - **NeoForge** - 安装 NeoForge 模组加载器（自动安装原版）
3. 点击「📥 安装版本」，等待进度条完成

> 💡 安装模组加载器时，原版 Minecraft 会自动安装并**与原版安装并行执行**（两者互不影响），显著缩短安装等待时间。安装完成后两者均可独立启动。

#### 快速选择版本

1. 在右侧「📋 快速选择」区域浏览可用版本
2. 点击「📦 正式版」/「🔬 测试版」切换版本类型
3. 点击版本条目自动填入版本 ID 输入框
4. 使用底部分页控件（◀ ▶）翻页浏览更多版本

#### 启动游戏

1. 在左侧「📦 已安装版本」面板中点击要启动的版本
2. 选中版本会高亮显示
3. 点击底部「🚀 启动游戏」按钮
4. 游戏运行期间「⏹」按钮会激活，点击可强制结束游戏进程

#### 删除版本

- 点击已安装版本条目右侧的 `X` 按钮
- 确认删除后版本将被移除（不可恢复）

#### 启动器设置

点击顶栏「⚙ 设置」按钮打开设置窗口，可配置以下选项：

- **🔽 启动后最小化**：开启后当检测到游戏窗口出现时自动最小化启动器到任务栏
- **🇨🇳 使用国内镜像源**：默认开启，国内用户建议保持开启，切换后自动测试连接
- **🌐 界面语言**：支持简体中文、English、繁體中文、日本語，切换后点击「应用」自动重启启动器使新语言生效
- **⚡ 下载线程数**：拖动滑块调整下载并发数（1-255）
- **🤖 净读 AI**：登录账号后可在游戏崩溃时使用 AI 智能分析功能
- 设置会自动持久化到 `config.json`

#### 自定义角色名

1. 在左侧边栏「👤 角色名」输入框中输入想要的游戏名称
2. 输入框失去焦点时自动保存
3. 下次启动游戏时将使用该角色名

#### 自定义皮肤

1. 在左侧边栏「🎨 自定义皮肤」区域点击「📂 选择皮肤」按钮
2. 选择一个 PNG 格式的皮肤文件（支持 64x64 / 64x32 / 128x128 / 128x64）
3. 皮肤文件会自动复制到 `.minecraft/skins/` 目录
4. 点击「🗑」按钮可移除当前皮肤

> ⚠️ 皮肤功能仅在正版（在线）模式下生效。

#### 启动器日志

- 左侧边栏「📋 启动器日志」区域实时显示启动器运行日志
- 日志自动捕获 logzero 输出，包括版本安装、游戏启动等操作记录
- 点击「清空日志」按钮可清除当前日志内容

#### 刷新版本列表

- 点击右上角「🔄 刷新」按钮
- 先加载已安装版本（本地，快速），再加载可用版本列表（需要网络）

#### 资源管理（模组/资源包/地图/光影）

选择一个已安装版本后，点击版本列表标题栏的 ⚙ 设置按钮，打开资源管理窗口。

**支持四种资源类型：**

| 标签页 | 支持格式 | 安装目录 |
|--------|----------|----------|
| 🧩 模组 | `.jar` `.zip` `.jar.disabled` | `mods/` |
| 🎨 资源包 | `.zip` | `resourcepacks/` |
| 🗺️ 地图 | `.zip`（自动解压）/ 文件夹 | `saves/` |
| ✨ 光影 | `.zip` | `shaderpacks/` |

**安装方式：**

- **拖拽安装**：将资源文件直接拖拽到窗口中即可安装
- **选择文件安装**：点击「➕ 选择文件安装」按钮，通过文件选择对话框选取文件

> 💡 地图 zip 文件会自动解压到 `saves/地图名/` 目录下，无需手动解压。支持 zip 内带一层包装目录的常见格式。

**其他操作：**

- 📂 **打开文件夹**：在系统文件管理器中打开对应的资源目录
- 🔕 **禁用/启用模组**：模组标签页中可一键切换 `.disabled` 状态
- 🗑️ **删除资源**：移除不需要的资源文件

> 💡 支持版本隔离模式：安装了模组加载器（Forge/Fabric/NeoForge）的版本自动启用版本隔离，资源将安装到 `.minecraft/versions/{版本名}/` 对应子目录下；原版客户端使用全局 `.minecraft/` 目录。

#### 安装 Modrinth 整合包

在右侧操作面板点击「📦 安装整合包」按钮，打开整合包安装窗口。

**方式一：从本地文件安装**

1. 点击「📂 选择 .mrpack 文件」，从本地选择一个 `.mrpack` 格式的整合包文件
2. 程序自动读取并显示整合包信息（名称、简介、Minecraft 版本）
3. 如有可选组件，可勾选需要安装的组件
4. 点击「📦 开始安装」，等待安装完成
5. 安装完成后刷新版本列表，选择对应版本即可启动游戏

**方式二：从 Modrinth 下载**

1. 点击「🌐 从 Modrinth 下载」按钮，打开整合包浏览窗口
2. 窗口打开时自动加载热门整合包列表，也可输入关键词搜索
3. 找到想要的整合包后点击「� 安装」→ 选择具体版本 → 下载 `.mrpack` 文件
4. 下载完成后自动回到安装窗口，显示整合包信息
5. 确认信息后点击「📦 开始安装」即可

> 💡 安装过程采用并行优化：整合包文件（模组等）与原版 Minecraft 同时下载，模组文件多线程并行下载，显著缩短等待时间。进度区域实时显示两个任务的百分比。另可通过 [Modrinth 网站](https://modrinth.com/modpacks) 手动下载 `.mrpack` 文件。

#### Modrinth 模组浏览与安装

安装了模组加载器（Forge/Fabric/NeoForge）的版本会在版本列表中显示 🧩 按钮，点击即可打开 Modrinth 模组浏览器。

**功能说明：**

- 🔍 **关键词搜索**：在搜索框中输入模组名称或关键词，按回车或点击搜索
- 📋 **热门模组**：窗口打开时自动加载当前版本和加载器兼容的热门模组列表
- 🏷️ **自动筛选**：自动识别游戏版本和加载器类型，只显示兼容的模组
- 📊 **智能版本显示**：模组支持的版本列表自动压缩展示（如 `1.16.x` 表示全版本覆盖，`1.20-1.20.2` 表示范围）
- 📄 **分页浏览**：使用「上一页」「下一页」按钮翻页查看更多模组
- 📥 **一键安装**：点击模组右侧的「安装」按钮，自动下载兼容版本到 `mods/` 目录
- 🔗 **依赖自动安装**：安装模组时自动递归安装 `required` 依赖，找不到兼容版本时跳过并提示

> 💡 模组浏览器会自动将模组安装到版本隔离目录（如 `.minecraft/versions/1.20.4-forge-49.0.26/mods/`）或全局 `mods/` 目录。

#### 存档备份

切换到"💾 备份"标签页，可管理 Minecraft 存档的备份和恢复。

**手动备份：**

1. 左侧列表会自动扫描所有存档（包括版本隔离目录中的存档）
2. 点击选择要备份的存档
3. 在顶部输入框中可选填写备注
4. 点击「📥 备份」按钮，等待进度条完成

**恢复备份：**

1. 选择存档后，右侧会显示该存档的所有备份
2. 找到要恢复的备份，点击「🔄」恢复按钮
3. 确认后，当前存档会自动重命名为 `_bak_时间戳` 保留
4. 备份解压恢复，自动校验 `level.dat` 完整性

**自动备份：**

1. 左侧底部可开启「启动游戏前备份」和「游戏退出后备份」
2. 自动备份会备份最近修改的存档，备注标记为"自动备份(启动前/退出后)"

**备份设置：**

点击右上角「⚙ 设置」按钮打开备份设置窗口，可配置：
- **备份存储路径**：自定义备份保存位置（默认为程序目录/backups）
- **压缩等级**：1（最快）到 9（最小体积），推荐 6（适中）
- **最大备份数**：每个存档保留的最大备份数，超出自动删除最旧的
- **恢复时旧存档处理**：重命名为 .bak / 直接覆盖 / 移至回收站

> 💡 备份文件存储在 `backups/<存档名>/` 目录下，使用 ZIP 格式压缩。每个备份都有对应的 `index.json` 索引文件记录元数据。

#### 服务器管理

切换到"🖥 开服"标签页，可快速搭建本地 Minecraft 服务器。

**安装服务器：**

1. 在右侧面板「版本 ID」输入框中输入版本号（如 `1.21.4`），仅支持正式版
2. 或从「📋 快速选择」列表中点击版本号自动填入
3. 点击「📥 安装服务器」，等待进度条完成
4. 安装完成后，服务器文件将存放在 `.minecraft/server/<版本号>/` 目录下

> 💡 安装过程会自动安装同名客户端版本（含 Java runtime）、下载服务器 jar、同意 EULA 并生成默认配置。

**整合包开服：**

1. 点击右侧面板「📦 整合包开服」按钮，打开整合包开服窗口
2. 选择本地 `.mrpack` 文件（或点击「🌐 从 Modrinth 下载」在线获取），程序自动读取整合包信息
3. 可选自定义服务器名称（留空则自动命名为 `整合包名-版本号`）
4. 如有可选组件，可勾选需要安装的组件
5. 点击「🚀 开始安装服务器」，等待安装完成
6. 安装完成后刷新服务器列表，选择该服务器即可启动

> 💡 安装过程与整合包安装一样采用并行优化，进度区域实时显示分段进度。程序会自动检测整合包中的 mod loader 并下载安装对应的服务端版本（Forge/Fabric/NeoForge/Quilt），无需手动操作。

**启动服务器：**

1. 在中间已安装服务器列表中选择要启动的版本
2. 在右侧设置最大内存（建议至少 2G）
3. 点击「🚀 启动服务器」按钮
4. 服务器启动后，左侧控制台实时显示日志，底部命令框可输入服务器命令
5. 服务器运行期间「⏹ 停止」按钮会激活，点击可优雅停止服务器

> 💡 服务器默认使用离线模式（`online-mode=false`），无需正版账号即可加入。

---

## 项目结构

```
FMCL/
├── main.py                # 主程序入口，延迟导入优化、日志配置、UI 创建、线程管理，支持 -A/-agent CLI 模式
├── cli_agent.py           # Agent CLI 模式（无 GUI，复用 agent 核心组件，支持单指令/交互模式）
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
│   │   ├── agent_chat.py  # 聊天 UI 组件 + 选项弹窗
│   │   ├── provider.py    # AI API 调用封装（OpenAI 兼容）
│   │   ├── tools.py       # Tool 定义 + 系统提示词
│   │   ├── engine.py      # Tool 执行引擎
│   │   └── xml_parser.py  # XML 响应解析器
│   ├── constants.py       # 颜色主题、字体检测、资源类型配置
│   ├── theme_engine.py    # 动态主题引擎（主题加载/切换/导入、版本动态调色、预设主题）
│   ├── dialogs.py         # 通用对话框（确认/提示）、版本选择对话框
│   └── windows/           # 独立窗口类
│       ├── resource_manager.py   # 资源管理窗口（模组/资源包/地图/光影）
│       ├── launcher_settings.py  # 启动器设置窗口（镜像源/最小化等）
│       ├── modpack_install.py    # Modrinth 整合包安装窗口
│       ├── modpack_server.py     # 整合包开服窗口
│       ├── modpack_browser.py    # Modrinth 整合包浏览与下载窗口
│       ├── mod_browser.py        # Modrinth 资源浏览与安装窗口（模组/资源包/光影 三标签页）
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
│   ├── get_mod_versions   # 获取版本列表
│   ├── get_modpack_versions # 获取整合包版本列表
│   ├── download_mod       # 下载文件（断点续传 + 指数退避重试）
│   ├── download_modpack_file  # 下载整合包 .mrpack 文件（断点续传 + 指数退避重试）
│   ├── install_mod_with_deps  # 安装模组及依赖（递归）
│   ├── install_resource_pack  # 安装资源包
│   ├── install_shader     # 安装光影
│   ├── 连接池复用         # 共享 requests.Session + HTTPAdapter，复用 TCP 连接避免重复 TLS 握手
│   ├── 指数退避重试       # 网络超时/中断自动重试 3 次，退避时间 2^retry 秒
│   ├── 断点续传下载       # Range 头支持，下载中断后从断点继续
│   ├── 版本解析工具       # 从版本 ID 解析加载器/游戏版本（含 NeoForge 特殊处理 + 新版 YY.D.H 格式）
│   └── 版本压缩展示       # 智能压缩版本列表（基于 Modrinth 完整版本判断全版本覆盖，支持旧版 + 新版格式）
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
├── build.spec             # PyInstaller 构建配置（含应用图标）
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

### 模块依赖关系

```
main.py
  ├── -A / -agent 参数 → cli_agent.py (CLI Agent 模式，无 GUI)
  │   ├── config.py (全局配置，读取 jdz_token)
  │   ├── launcher/ (核心逻辑包)
  │   └── ui/agent/ (复用 AI Provider、工具定义、执行引擎、XML 解析器)
  ├── config.py (全局配置)
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

---

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

---

## 配置说明

### 配置文件位置（跨平台）

**Windows/macOS:**
- 配置文件: `config.json`（程序根目录）
- 密钥文件: `.fmcl_key`（程序根目录，用于加密存储敏感 Token）
- 日志文件: `latest.log`（程序根目录）
- Minecraft 目录: `.minecraft/`（程序根目录）

**Linux (FHS 标准):**
- 配置文件: `/etc/fmcl/config.json`
- 密钥文件: `~/.fmcl/.fmcl_key`
- 日志文件: `/var/log/fmcl/latest.log`
- Minecraft 目录: `~/.minecraft/`
- 运行时目录: `~/.fmcl/`

> 💡 **Linux 首次运行**: 
> - **日志目录** (`/var/log/fmcl/`) 会自动创建（如权限不足会提示）
> - **配置目录** (`/etc/fmcl/`) 需要手动创建或使用初始化脚本：
>   ```bash
>   chmod +x scripts/setup_linux.sh
>   ./scripts/setup_linux.sh
>   ```
>   或手动创建：
>   ```bash
>   sudo mkdir -p /etc/fmcl /var/log/fmcl
>   sudo chown $USER:$USER /etc/fmcl /var/log/fmcl
>   ```

### 配置项说明

配置文件 `config.json` 启动时自动加载：

```json
{
  "mirror_enabled": true,
  "download_threads": 4,
  "minimize_on_game_launch": false,
  "auto_check_update": true,
  "player_name": "Steve",
  "skin_path": null,
  "jdz_token": "gAAAAABm...（Fernet 加密密文）",
  "language": "zh_CN",
  "ai_privacy_consent": false,
  "terms_consent": false,
  "backup_dir": null,
  "backup_compress_level": 6,
  "backup_max_per_world": 10,
  "backup_restore_mode": "rename",
  "backup_auto_launch": false,
  "backup_auto_exit": false
}
```

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `mirror_enabled` | bool | `true` | 是否启用 BMCLAPI 国内镜像 |
| `download_threads` | int | `4` | 多线程下载的线程数 |
| `minimize_on_game_launch` | bool | `false` | 游戏启动后是否最小化启动器窗口 |
| `auto_check_update` | bool | `true` | 启动时是否自动检查更新 |
| `player_name` | string | `"Steve"` | 自定义游戏角色名 |
| `skin_path` | string/null | `null` | 自定义皮肤文件路径 |
| `jdz_token` | string/null | `null` | 净读 AI Token（Fernet 加密存储，不可手动编辑） |
| `language` | string | `"zh_CN"` | 界面语言（zh_CN/en_US/ja_JP/zh_TW） |
| `theme_name` | string | `"default"` | 主题名称（default/ocean/forest/lavender/sunset 或用户导入的主题名） |
| `accent_color` | string/null | `null` | 自定义强调色 Hex 值（如 `"#e94560"`，null 使用主题默认） |
| `dynamic_version_theme` | bool | `false` | 是否启用 Minecraft 版本动态主题 |
| `ai_privacy_consent` | bool | `false` | 是否已同意 AI 分析隐私说明 |
| `terms_consent` | bool | `false` | 是否已同意使用条款（Minecraft EULA + 净读协议），首次启动时弹窗确认 |
| `backup_dir` | string/null | `null` | 备份存储路径（null 则使用程序目录/backups） |
| `backup_compress_level` | int | `6` | 备份压缩等级（1=最快, 9=最小体积） |
| `backup_max_per_world` | int | `10` | 每个存档最大备份数（0=不限制） |
| `backup_restore_mode` | string | `"rename"` | 恢复时旧存档处理方式（rename/overwrite/trash） |
| `backup_auto_launch` | bool | `false` | 是否在游戏启动前自动备份 |
| `backup_auto_exit` | bool | `false` | 是否在游戏退出后自动备份 |

---

## 开发指南

### 环境设置

```bash
# 安装 Python 依赖
pip install -r requirements.txt

# 安装开发依赖（可选）
pip install -r requirements-dev.txt

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

### Linux 初始化

Linux 平台首次运行前，建议创建系统目录（日志目录会自动创建）：

```bash
# 方法一：使用初始化脚本（推荐）
chmod +x scripts/setup_linux.sh
./scripts/setup_linux.sh

# 方法二：手动创建配置目录（日志目录会自动创建）
sudo mkdir -p /etc/fmcl
sudo chown $USER:$USER /etc/fmcl
mkdir -p ~/.minecraft ~/.fmcl
```

详见：[Linux 文件存储说明](docs/LINUX_FILE_LOCATIONS.md)

### 构建

```bash
make build            # PyInstaller 构建可执行文件
make build-installer  # Windows NSIS 安装包 (需要 NSIS)
make build-dmg        # macOS DMG 磁盘映像 (仅 macOS)
make build-deb        # Linux DEB 包
make build-appimage   # Linux AppImage
```

### 发布流程

1. 更新 `pyproject.toml` 和 `package.json` 中的版本号
2. 提交变更：`git commit -m "chore: release vX.X.X"`
3. 创建标签：`git tag vX.X.X`
4. 推送：`git push origin main --tags`

GitHub Actions 会自动：
- Windows: 构建 `.exe` 安装包 (NSIS)
- macOS: 构建 `.dmg` 磁盘映像 (Intel + Apple Silicon)
- Linux: 构建 `.deb` 和 `.AppImage` 安装包
- 创建 Release 并上传所有安装包

详见：[CONTRIBUTING.md](CONTRIBUTING.md) | [SETUP.md](docs/SETUP.md)

### CI 测试

项目使用 GitHub Actions 进行持续集成（仅 Linux）：

- **Lint**: flake8 代码检查
- **Type Check**: mypy 类型检查
- **Test**: pytest 运行测试（使用 xvfb 虚拟显示支持 GUI 依赖）

CI 在 `main` 分支的 push 和 pull request 时自动触发。

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

详见：[CONTRIBUTING.md](CONTRIBUTING.md)

---

## 故障排除

查看完整指南：[TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)

| 问题 | 解决方案 |
|------|----------|
| 镜像源连接失败 | 尝试关闭「国内镜像」开关使用 Mojang 官方源 |
| 版本安装失败 | 检查网络连接，查看 `latest.log` 日志 |
| 游戏启动失败 | 确保已安装 Java 运行时 |
| macOS 提示无法验证开发者 | `xattr -cr FMCL.app` 移除隔离属性 |
| Windows 杀毒误报 | 添加到杀毒软件排除列表 |
| Windows 异常退出 | 尝试以管理员权限运行程序 |
| 依赖安装失败 | `pip install -r requirements.txt --force-reinstall` |
| **Linux GLIBC 版本错误** | 需要 GLIBC >= 2.28（Ubuntu 18.04+、Debian 10+、RHEL 8+），旧版 Linux 会出现 `GLIBC_X.XX not found` 错误 |
| **Linux 配置目录权限错误** | 运行 `sudo mkdir -p /etc/fmcl && sudo chown $USER:$USER /etc/fmcl` |
| **Linux 日志目录权限错误** | 程序会自动回退到 `~/.fmcl/latest.log`，无需手动处理 |
| **Linux 无图形环境崩溃** | 在 WSL/无头服务器等无 X11/Wayland 环境时，鼠标检测线程会自动跳过，不会崩溃 |
| **Linux emoji 表情无法显示** | 程序会自动检测并安装 emoji 字体（如 fonts-noto-color-emoji），如提示权限请手动安装字体包 |

---

## 许可证

- **v2.8.4 及以前版本**：使用 [MIT License](LICENSE)
- **v2.8.4 以后版本**：使用 [GNU General Public License v3.0](LICENSE)

Copyright (c) 2026 FMCL Team
