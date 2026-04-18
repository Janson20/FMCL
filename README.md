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

### 🔗 链接标签页
- **三标签页布局**：主界面现在包含"🎮 游戏"、"🖥 开服"和"🔗 链接"三个标签页
- **默认游戏标签页**：保留原有的三栏布局（侧边栏、已安装版本、操作面板），所有游戏功能保持不变
- **链接标签页**：收录了15个常用的Minecraft相关网站，包括官方网站、中文社区、资源平台和实用工具
- **网站卡片**：每个网站以卡片形式展示，包含名称、描述、标签和直达链接
- **一键访问**：点击"🌐 打开链接"按钮在浏览器中打开网站
- **链接复制**：点击"📋 复制链接"按钮复制网站URL到剪贴板
- **智能标签**：每个网站显示最多3个分类标签，便于快速识别网站类型

### ⚡ 国内镜像加速
- 内置 [BMCLAPI](https://bmclapi2.bangbang93.com/) 镜像源（by bangbang93），国内下载速度大幅提升
- 一键开关镜像源，切换即时生效
- 自动连接测试，状态栏显示连接结果
- 覆盖范围：版本清单、游戏资源、库文件、Forge/Fabric/NeoForge 安装器

### 🔧 多模组加载器
- **Forge** - 最广泛使用的模组加载器
- **Fabric** - 轻量级模组加载器
- **NeoForge** - Forge 的社区分支
- 安装模组加载器时自动安装原版 Minecraft，无需重复操作
- **版本隔离**：安装了模组加载器的版本自动启用版本隔离，游戏从 `.minecraft/versions/{版本名}/` 读取 mods、config 等资源，各版本互不干扰

### 🧩 Modrinth 模组浏览与安装
- 集成 [Modrinth](https://modrinth.com/) API，在线搜索和安装模组
- 安装了模组加载器的版本自动显示 🧩 按钮，一键打开模组浏览器
- 自动识别游戏版本和模组加载器类型（Forge/Fabric/NeoForge），精准筛选兼容模组
- 支持关键词搜索，窗口打开时自动加载热门模组列表
- 分页浏览搜索结果，支持翻页查看更多模组
- 一键安装：自动获取兼容版本并下载到 `mods/` 目录
- 支持版本隔离：自动将模组安装到对应版本的独立目录
- **新版本命名适配**：支持 2026 年起的新版本格式 `YY.D.H`（如 `26.1`、`26.1.1`），兼容旧版 `1.X.Y` 格式

### 📦 资源管理
- 支持模组、资源包、地图、光影四种资源类型
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
- **删除管理**：支持删除已安装的服务器版本

### 🖥️ 现代化界面
- 基于 CustomTkinter 的深色主题，流畅美观
- **跨平台中文字体适配**：自动检测系统并选择合适的中文字体（Windows: Microsoft YaHei / macOS: PingFang SC / Linux: Noto Sans CJK SC 等）；Linux 下若无中文字体则自动通过包管理器安装
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
- 设置自动持久化到 `config.json`

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

### 📸 截图工具
- 内置区域截图工具，框选屏幕区域即可保存
- 快捷键 `Ctrl+Alt+T` 随时触发

### 📋 日志系统
- 基于 logzero 的完整日志记录
- **跨平台日志存储**：
  - Windows/macOS: `latest.log` 保留在程序目录
  - Linux: `/var/log/fmcl/latest.log`（遵循 FHS 标准）

### 💥 崩溃检测与报告
- 自动检测游戏异常退出（退出码非 0）
- **智能崩溃诊断**：基于日志关键词匹配，自动识别 11 种常见崩溃类型（Mixin 错误、依赖缺失、内存溢出、模组冲突等），并给出修复建议
- 崩溃时自动收集崩溃报告、游戏日志、JVM 崩溃日志等诊断信息
- 提供三个操作按钮：
  - **打开崩溃报告** - 直接查看 Minecraft 生成的崩溃报告文件
  - **打开游戏日志** - 查看游戏运行日志
  - **导出崩溃报告** - 将崩溃报告、游戏日志、JVM 崩溃日志、启动器日志、系统信息打包为 ZIP 文件，方便分享给他人分析

### 🌐 自动设置中文语言
- 启动时自动将 `.minecraft/options.txt` 中的语言设置修改为 `zh_cn`
- 确保首次启动游戏时即为中文界面，无需手动切换

### ⬆ 自动更新
- 启动时自动从 GitHub Release 检查新版本（可配置开关）
- 发现新版本时弹出更新对话框，展示版本号和更新日志
- 自动识别当前平台，下载对应的安装包（Windows NSIS / macOS DMG / Linux AppImage）
- 下载完成后自动执行静默安装（Windows 使用 `/S` 参数），安装程序启动后自动退出当前程序
- 也可手动点击顶栏「⬆ 更新」按钮检查更新

---

## 界面预览

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│  ⛏ FMCL   Minecraft Launcher   ⬆ 更新  🔄 刷新  ⚙ 设置  │
│  [🎮 游戏] [🖥 开服] [🔗 链接]                               │
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

# 运行启动器
python main.py
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

> 💡 安装模组加载器时，原版 Minecraft 会自动安装，无需重复操作。安装完成后两者均可独立启动。

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

> 💡 支持版本隔离模式：若 `.minecraft/versions/{版本名}/` 目录存在，资源将安装到版本独立目录下；否则使用全局 `.minecraft/` 目录。

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

#### 服务器管理

切换到"🖥 开服"标签页，可快速搭建本地 Minecraft 服务器。

**安装服务器：**

1. 在右侧面板「版本 ID」输入框中输入版本号（如 `1.21.4`），仅支持正式版
2. 或从「📋 快速选择」列表中点击版本号自动填入
3. 点击「📥 安装服务器」，等待进度条完成
4. 安装完成后，服务器文件将存放在 `.minecraft/server/<版本号>/` 目录下

> 💡 安装过程会自动安装同名客户端版本（含 Java runtime）、下载服务器 jar、同意 EULA 并生成默认配置。

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
├── main.py                # 主程序入口，延迟导入优化、日志配置、UI 创建、线程管理
├── launcher.py            # 启动器核心逻辑
│   ├── 环境检查与初始化
│   ├── 版本安装（原版 + 模组加载器）
│   ├── 版本删除
│   ├── 游戏启动（JVM 参数优化 + 模糊匹配版本 ID + 版本隔离）
│   ├── 服务器管理（安装/启动/停止服务器 + 自动同意 EULA + 版本隔离）
│   ├── 并发文件校验（ThreadPoolExecutor）
│   └── 镜像源管理
├── ui.py                  # CustomTkinter 现代化 UI
│   ├── ModernApp          # 主窗口（三栏布局 + 侧边栏 + 状态栏）
│   ├── 开服标签页         # 服务器安装/启动/停止 + 版本管理
│   ├── LauncherSettingsWindow # 启动器设置窗口（镜像源/最小化等）
│   ├── ResourceManagerWindow  # 资源管理窗口（模组/资源包/地图/光影）
│   ├── ModBrowserWindow   # Modrinth 模组浏览与安装窗口
│   ├── VersionSelectorDialog  # 版本选择弹出对话框
│   └── 辅助函数           # show_confirmation / show_alert
├── downloader.py          # 多线程下载器 & 异步批量下载 & 模组加载器安装
│   ├── MultiThreadDownloader  # 多线程分段下载 + 文件合并
│   ├── AsyncBatchDownloader   # asyncio + aiohttp 异步并发下载
│   └── install_mod_loader # Forge/Fabric/NeoForge 统一安装
├── modrinth.py            # Modrinth API 集成
│   ├── search_mods        # 搜索模组（关键词 + 版本/加载器筛选）
│   ├── get_mod_versions   # 获取模组版本列表
│   ├── download_mod       # 下载模组文件
│   ├── install_mod_with_deps  # 安装模组及依赖（递归）
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
├── config.py              # 配置管理
│   └── Config             # 配置类（持久化到 config.json）
├── screen_shot.py         # 截图工具（Ctrl+Alt+T 触发）
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
  ├── config.py (全局配置)
  ├── launcher.py (核心逻辑)
  │   ├── mirror.py (镜像源)
  │   └── downloader.py (下载器 & 模组加载器)
  ├── ui.py (界面)
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
| HTTP | requests | API 请求与文件下载 |
| 模组搜索 | Modrinth API V2 | 在线搜索和安装模组 |
| 下载 | 多线程分段下载 | 大文件并行下载加速 |
| 截图 | pyautogui + keyboard | 区域截图 + 快捷键监听 |
| 自动更新 | GitHub Release API | 版本检查 + 静默安装 |
| 构建打包 | PyInstaller + NSIS | 可执行文件 + Windows 安装包 |
| CI/CD | GitHub Actions | 多平台自动构建与发布 |
| 提交规范 | Husky + Commitlint | 约定式提交自动校验 |

---

## 配置说明

### 配置文件位置（跨平台）

**Windows/macOS:**
- 配置文件: `config.json`（程序根目录）
- 日志文件: `latest.log`（程序根目录）
- Minecraft 目录: `.minecraft/`（程序根目录）

**Linux (FHS 标准):**
- 配置文件: `/etc/fmcl/config.json`
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
  "skin_path": null
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
| **Linux 配置目录权限错误** | 运行 `sudo mkdir -p /etc/fmcl && sudo chown $USER:$USER /etc/fmcl` |
| **Linux 日志目录权限错误** | 运行 `./scripts/setup_linux.sh` 或手动创建 `/var/log/fmcl` |

---

## 许可证

[MIT License](LICENSE) - Copyright (c) 2026 FMCL Team
