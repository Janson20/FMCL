# FMCL 插件开发指南

> 本文档介绍如何为 FMCL 开发第三方插件。

---

## 快速开始

### 1. 创建插件项目

```
my-plugin/
├── plugin.json         # 插件清单（必需）
├── __init__.py         # 入口模块（必需，必须导出 PluginBase 子类）
├── icon.png            # 插件图标（可选，推荐 128x128 PNG）
└── ...                 # 其他模块文件
```

### 2. 编写 plugin.json

```json
{
  "id": "com.example.my-plugin",
  "name": "我的插件",
  "version": "1.0.0",
  "author": "Your Name",
  "min_fmcl_version": "2.10.4",
  "description": {
    "zh_CN": "这是一个示例插件",
    "en_US": "An example plugin"
  },
  "permissions": ["ui.notification", "data.store"],
  "dependencies": {},
  "tags": ["utility"],
  "entry": "__init__"
}
```

| 字段 | 必需 | 说明 |
|------|------|------|
| `id` | 是 | 全局唯一标识，反向域名格式 |
| `name` | 是 | 显示名称，最长 64 字符 |
| `version` | 是 | SemVer 版本号，如 `1.0.0` |
| `author` | 是 | 作者名称 |
| `min_fmcl_version` | 是 | 最低兼容的 FMCL 版本 |
| `max_fmcl_version` | 否 | 最高兼容的 FMCL 版本，不填表示无上限 |
| `description` | 否 | 多语言描述字典 |
| `permissions` | 否 | 请求的权限列表 |
| `dependencies` | 否 | 依赖的插件及版本约束 |
| `conflicts` | 否 | 冲突的插件及版本约束 |
| `tags` | 否 | 标签列表 |
| `entry` | 否 | 入口模块名，默认 `__init__` |
| `ui_api` | 否 | 界面扩展协议版本：`1`（旧界面协议，仅兼容非 UI 钩子）或 `2`（QML 页面，见下文） |

### 3. 编写插件类

```python
# __init__.py
from plugin_manager.base import PluginBase, HookPoint


class MyPlugin(PluginBase):

    def get_default_config(self) -> dict:
        return {"enabled_feature": True}

    def on_load(self) -> None:
        """模块加载后调用"""
        self.log("插件模块已加载")

    def on_enable(self) -> None:
        """插件启用时调用，在此注册钩子、创建 UI"""
        self.log("插件已启用")

        # 注册钩子
        self._manager.register_hook(
            self.manifest.id,
            HookPoint.GAME_POST_LAUNCH,
            self._on_game_launched,
            priority=100,
        )

        # 使用通知
        self.notify("插件就绪", "我的插件已启动", "info")

    def on_disable(self) -> None:
        """插件停用时调用，清理资源"""
        self.log("插件已停用")

    def _on_game_launched(self, **kwargs):
        """游戏启动后的钩子处理器"""
        version_id = kwargs.get("version_id", "unknown")
        pid = kwargs.get("pid", 0)
        self.log(f"游戏已启动: {version_id} (PID: {pid})")
        self.notify("游戏已启动", f"版本: {version_id}", "info")
```

### 4. 打包为 .fmpl

将项目目录压缩为 `.zip`，然后重命名为 `.fmpl`：

```bash
# Windows PowerShell
Compress-Archive -Path my-plugin/* -DestinationPath my-plugin.fmpl

# Linux/macOS
cd my-plugin && zip -r ../my-plugin.fmpl .
```

### 5. 安装测试

1. 打开 FMCL → 设置 → 插件 → 打开插件管理
2. 点击「从文件安装」选择 `.fmpl` 文件
3. 在权限确认弹窗中授权
4. 插件将自动启用

---

## 插件生命周期

```
SCANNED → LOADING → LOADED → ENABLED → (DISABLED | ERROR)
```

| 阶段 | 说明 |
|------|------|
| SCANNED | 插件目录被发现，`plugin.json` 已读取 |
| LOADING | 正在通过 importlib 加载 Python 模块 |
| LOADED | 模块导入完成，`PluginBase` 实例创建，`on_load()` 已调用 |
| ENABLED | `on_enable()` 已成功执行，插件正常运行 |
| DISABLED | 用户主动禁用，`on_disable()` 已调用 |
| ERROR | 运行中出错 |

---

## 界面扩展（UI API v2）

> FMCL 当前只提供 QML 界面。插件要出现在导航中，必须提供 QML 组件并使用
> `ui_api: 2`。声明 `ui_api: 1` 的旧界面协议仍可安装、启用，但界面部分不会加载，
> 管理器会标出「界面不受支持」；非 UI 钩子（游戏启动、下载、通知等）不受影响。

### 1. 在 plugin.json 里声明

```json
{
  "id": "com.example.my-plugin",
  "name": "我的插件",
  "version": "2.0.0",
  "author": "Your Name",
  "min_fmcl_version": "2.10.4",
  "ui_api": 2,
  "permissions": ["ui.extend", "data.store"]
}
```

`ui_api` 缺省为 `1`。只有 `ui_api >= 2` 的插件才会被 QML 界面采集页面。

### 2. 注册页面（`ui.page.register` 钩子）

在 `on_enable()` 里注册一个处理器，返回**一个页面字典或它的列表**：

```python
from plugin_manager.base import HookPoint, PluginBase


class MyPlugin(PluginBase):

    def on_enable(self) -> None:
        self._manager.register_hook(
            self.manifest.id,
            HookPoint.UI_PAGE_REGISTER,
            self._register_pages,
        )

        # 给页面取数用（见第 3 节）
        self._manager.export_api(self.manifest.id, "get_stats", self._get_stats)

    def on_disable(self) -> None:
        pass

    def _register_pages(self, **kwargs):
        return [
            {
                "id": "main",                      # 页内唯一，路由 id 会拼成 plugin/<插件 id>/main
                "title": "我的插件",                # 直接显示的名字（不是启动器 i18n 键）
                "icon": "plugin",                   # 可选，只能用启动器已有的图标名
                "order": 100,                       # 可选，插件页之间的排序（小的在前）
                "qml_url": "ui/Main.qml",           # 相对插件目录，或绝对路径 / file:// URL
            }
        ]

    def _get_stats(self, **kwargs):
        return {"count": 42}
```

页面字典的硬性约束：

| 字段 | 说明 |
|------|------|
| `id` | 必填，`[A-Za-z0-9_.-]`，最长 64；最终路由是 `plugin/<插件 id>/<id>` |
| `title` | 必填（空则回落插件名 / id）；**直接显示**，不经过启动器语言文件 |
| `qml_url` | 必填，必须以 `.qml` 结尾，且解析后必须落在**插件安装目录内**（不允许指向启动器或系统其它文件） |
| `icon` | 可选；只接受 `qml/assets/icons` 里已有的名字，不认识的回落 `plugin` |
| `order` | 可选整数，插件页那一段按它稳定排序（不影响 12 个内置领域） |

页面会作为**一级导航项**出现在侧栏底部的「插件」分组；禁用/卸载插件时路由会一起撤销。

### 3. 页面取数：`Plugins.call`

插件页面是普通 QML 组件，运行在启动器引擎里，可以直接使用这些**上下文属性**：
`Theme`、`Tr`、`Nav`、`Shell`、`Dialogs`、`Plugins`（与内置页面同一套）。
往 Python 侧取数走导出的 API：

```qml
import QtQuick
import QtQuick.Layouts

Item {
    Column {
        Text { id: label; text: "..." }

        Component.onCompleted: {
            var data = Plugins.call("com.example.my-plugin", "get_stats", {})
            label.text = "count = " + (data ? data.count : "?")
        }
    }
}
```

- `Plugins.call(owner, api, args)`：`args` 是 dict（关键字参数）/ list（位置参数）；
  无参可省略第三个参数，或传 `{}` / `null`。QML 的 JS 对象与数组会自动还原成
  dict / list，不用自己转换。返回值是插件函数的返回值（dict/list/标量）；异常会
  转成状态条错误并返回 `null`。
- 调用在**主线程**执行，插件 API 里不要做长耗时工作（下载/压缩请放到自己的线程，
  完成后用第 4 节的事件通知页面刷新）。

### 4. 插件 → 页面推送：`emit_ui_event`

页面需要「后台数据变了，刷新一下」时，插件调用：

```python
self.emit_ui_event("stats_changed", {"count": 43})
```

QML 侧监听：

```qml
Connections {
    target: Plugins
    function onPageEvent(owner, name, payload) {
        if (owner === "com.example.my-plugin" && name === "stats_changed")
            refresh()
    }
}
```

### 5. `ui_api` 缺省为 1 的插件

* 非 UI 钩子照常工作；`ui.tab.register` / `get_tab_ui(parent)` 在 QML 界面下**不会**被调用；
* 管理器会显示「界面不受支持」与原因，不会崩溃；
* 要提供 QML 页面：清单声明 `ui_api: 2`，用 `ui.page.register` 登记页面并提供 QML 文件。

> 安全边界：QML 页面与插件 Python 代码**同等信任**（都能执行任意代码），
> 启动器不做沙箱。只安装来源可信的插件；权限弹窗管的是启动器提供的 API，
> 不是 Python 本身的隔离。

---

## 权限列表

| 权限 | 风险 | 说明 |
|------|------|------|
| `filesystem.read` | 低 | 读取文件（排除启动器核心目录） |
| `filesystem.write` | 中 | 写入文件（仅 .minecraft + 插件数据目录） |
| `network.http` | 低 | HTTP(S) 请求（通过启动器 UA 代理） |
| `network.socket` | 高 | 原始 Socket 连接 |
| `ui.extend` | 低 | 注册标签页/侧边栏/设置面板 |
| `ui.notification` | 低 | 弹窗/Toast 通知 |
| `core.download` | 中 | 注册自定义下载源 |
| `core.version` | 中 | 注册自定义版本源 |
| `core.launch_hook` | 高 | 游戏启动前/后钩子 |
| `core.process` | 高 | 执行外部进程 |
| `data.store` | 低 | 持久化存储（插件数据目录） |
| `data.settings` | 中 | 读取/修改启动器设置 |

---

## 钩子点

| 钩子 | 策略 | 参数 | 说明 |
|------|------|------|------|
| `app.startup` | ALL | — | 启动器初始化完成 |
| `app.shutdown` | ALL | — | 启动器关闭前 |
| `game.pre_launch` | COLLECT | version_id, command | 游戏启动前（可修改启动命令） |
| `game.post_launch` | ALL | version_id, pid | 游戏启动后 |
| `game.stopped` | ALL | exit_code | 游戏进程停止 |
| `game.crashed` | COLLECT | crash_report | 游戏崩溃后 |
| `version.pre_install` | FIRST | version_id, mod_loader | 版本安装前 |
| `version.post_install` | ALL | version_id, success | 版本安装后 |
| `version.pre_remove` | FIRST | version_id | 版本删除前 |
| `server.pre_start` | FIRST | server_name | 服务器启动前 |
| `server.post_start` | ALL | server_name, process | 服务器启动后 |
| `server.stopped` | ALL | server_name, exit_code | 服务器停止 |
| `ui.tab.register` | COLLECT | — | 注册主界面标签页 |
| `ui.sidebar.register` | COLLECT | — | 注册侧边栏项目 |
| `ui.settings.register` | COLLECT | — | 注册设置条目 |
| `download.pre_download` | COLLECT | — | 文件下载前（可修改 URL） |
| `download.post_download` | ALL | — | 文件下载完成 |

### 钩子策略

* **ALL**: 按优先级依次调用所有处理器，不关心返回值
* **COLLECT**: 依次调用，收集所有返回值到列表
* **FIRST**: 依次调用，返回第一个非 None 值（可用于阻止操作）
* **SHORT_CIRCUIT**: 依次调用，某个返回 True 则停止

---

## 插件配置

每个插件有独立的配置空间，自动持久化到 `plugins/configs/{plugin_id}.json`：

```python
class MyPlugin(PluginBase):

    def get_default_config(self) -> dict:
        return {"api_key": "", "interval": 60}

    def on_enable(self) -> None:
        # 读取配置
        api_key = self.config.get("api_key", "")
        self.log(f"API Key: {api_key[:4]}...")

        # 修改配置（修改 self.config 后调用）
        self.config["interval"] = 120
        self._manager.save_plugin_config(self.manifest.id)
```

---

## 插件数据目录

每个插件有独立的持久化数据目录 `plugins/data/{plugin_id}/`：

```python
class MyPlugin(PluginBase):

    def on_enable(self) -> None:
        # self.data_dir 自动指向 plugins/data/{plugin_id}/
        db_path = self.data_dir / "cache.db"
        self.log(f"数据目录: {self.data_dir}")
```

---

## 插件间通信

插件可以导出 API 供其他插件使用：

```python
# 插件 A 导出 API
class PluginA(PluginBase):
    def on_enable(self) -> None:
        self._manager.export_api(self.manifest.id, "get_version", self._get_version)

    def _get_version(self):
        return "1.0.0"

# 插件 B 导入 API
class PluginB(PluginBase):
    def on_enable(self) -> None:
        get_version = self._manager.get_plugin_api("com.example.plugin-a", "get_version")
        if get_version:
            self.log(f"插件 A 版本: {get_version()}")
```

在 `plugin.json` 中声明 exports 和 imports：
```json
{
  "id": "com.example.plugin-a",
  "exports": ["get_version"]
}
```

---

## 最佳实践

1. **最小权限原则**: 只请求插件实际需要的权限
2. **异常处理**: `on_enable()` 中的异常会使插件进入 ERROR 状态，需充分处理
3. **资源清理**: `on_disable()` 中释放所有资源（线程、文件句柄、网络连接）
4. **日志规范**: 使用 `self.log()` 而非 `print()`，方便统一管理和排查
5. **版本约束**: 使用 SemVer 语义化版本，dependencies 中使用 `>=1.0,<2.0` 约束
6. **UI 线程安全**: 所有界面操作都在主线程；长耗时工作放插件自己的线程，
   完成后用 `emit_ui_event` 通知 QML 页面刷新。
