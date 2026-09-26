# 自编译产物来源记录（BUILD_PROVENANCE）

> 本文件由构建过程生成/更新，用于满足项目合规要求：
> **禁止引入来源不明的二进制**——凡进入分发产物的自编译二进制，都必须能追溯到源码 commit、工具链版本与哈希。
>
> 本文件入库（`third_party/` 下仅 4 个文件入库，源码树与构建产物由脚本重新生成）。

---

## 一、FluentUI QML 插件（fluentuiplugin.dll）

### 源码

| 项 | 值 |
|----|----|
| 上游仓库 | https://github.com/zhuzichu520/FluentUI |
| 本地路径 | `third_party/FluentUI` |
| 分支 | `main` |
| **pin 的 commit** | `7e33a2f672d18239ef49ab960075b1137a1d18e7` |
| 提交时间 | 2026-04-07 16:58:19 +0800 |
| 提交标题 | Merge pull request #627 from Polaris-Night/main |
| 许可证 | MIT（`Copyright (c) 2023 zhuzichu`），与本项目 GPL-3.0-only 兼容 |

### 构建环境

| 项 | 值 |
|----|----|
| 操作系统 | Windows |
| 编译器 | MSVC 14.50.35717（VS 18 BuildTools，`C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools`） |
| Windows SDK | 10.0.26100.0 |
| CMake | 4.2.3 |
| CMake 生成器 | `Visual Studio 18 2026`，`-A x64`（该生成器自行定位 MSVC，无需 vcvars64.bat） |
| Qt | **6.7.3** `msvc2019_64`，获取方式 aqtinstall 3.3.0（Qt 官方镜像） |
| Qt 路径 | `D:\Qt\6.7.3\msvc2019_64` |
| Qt 模块 | qtbase、qtdeclarative、qttools、qttranslations、qtsvg、qtimageformats、qt5compat、qtshadertools、d3dcompiler_47、openglsw |

### 构建命令

```powershell
cmake -S third_party\FluentUI -B third_party\_build `
  -G "Visual Studio 18 2026" -A x64 `
  -DCMAKE_PREFIX_PATH=D:/Qt/6.7.3/msvc2019_64 `
  -DFLUENTUI_BUILD_EXAMPLES=OFF `
  -DFLUENTUI_QML_PLUGIN_DIRECTORY="<项目>/third_party/_install/qml/FluentUI"

cmake --build third_party\_build --config Release --target fluentuiplugin
```

**两个必须显式设置的选项**：

1. `-DFLUENTUI_BUILD_EXAMPLES=OFF`
   上游只在 `example/` 里引用 `3rdparty/msvc|x86|mingw/*.dll`（签入源码树的 OpenSSL 1.1 与 MinGW 运行时，
   **构建来源不可验证，按项目规则禁止使用**）。关掉 example 即完全避开这些二进制。
2. `-DFLUENTUI_QML_PLUGIN_DIRECTORY=<项目内路径>`
   上游默认值是 `${QT_SDK_DIR}/qml/FluentUI`，**会把产物直接写进共享 Qt SDK 安装目录**（本项目实测已发生过一次，
   116 个文件被写入 `D:\Qt\6.7.3\msvc2019_64\qml\FluentUI`，已清理）。必须显式覆盖，让产物落在项目内以便哈希与打包。

### 产物

| 项 | 值 |
|----|----|
| 路径 | `third_party/_build/src/Release/fluentuiplugin.dll` |
| 大小 | 4,743,680 字节（4,633 KB） |
| **SHA256** | `E5660B0E313432B59F0210C05857917B36F17B24E72FA6C6D50F02C0F8279F7A` |
| 构建时间 | 2026-09-26 15:52:35 |
| 运行期部署位置 | `third_party/_install/qml/FluentUI/fluentuiplugin.dll` |

### 动态依赖（`dumpbin /dependents` 实测）

```
Qt6Quick.dll  Qt6Qml.dll  Qt6Widgets.dll  Qt6Gui.dll  Qt6Core.dll
MSVCP140.dll  VCRUNTIME140.dll  VCRUNTIME140_1.dll
USER32.dll  SHELL32.dll  GDI32.dll  KERNEL32.dll
```

**全部为动态链接，未静态链接 Qt**——这是满足 Qt LGPLv3 授权义务（允许用户替换 Qt 版本）的前提，不可改为静态链接。

---

## 二、加载验证（可复现的证据）

### 2.1 用 Qt SDK 自带的 QML 运行时加载

```
qml.exe -I third_party\_install\qml poc\plugin_load_test.qml
```

- 退出码 **42** = 成功（`import FluentUI` 成功，且插件注册的 `FluRectangle`、`FluTheme` 可用）
- 反向对照：故意写错类型名 → 退出码非 0，报 `Did not load any objects, exiting.`
  （证明 42 是有意义的通过信号，而非"任何情况都返回 42"）

### 2.2 用 PySide6 6.7.3 加载（本项目的真实运行形态）

```
.venv\Scripts\python.exe poc\poc_fluentui_cpp.py
```

- 退出码 **0**，输出 `OK: root object = QWindow title='FMCL FluentUI POC'`
- 使用的控件：`FluWindow`、`FluText`、`FluFilledButton`、`FluToggleSwitch`，并读取了插件单例 `FluTheme`
- **Qt 版本匹配**：PySide6 6.7.3 内置 Qt 6.7.3，与插件编译所用 Qt 完全一致（`qVersion()` 实测 6.7.3）

### 2.3 禁用亚克力材质（满足"禁止渐变"规范）

`FluWindow.effect` 为 `QString`，可选值来自 `src/FluFrameless.cpp:392`：

```
{"mica", "mica-alt", "acrylic", "dwm-blur", "normal"}
```

设为 `effect: "normal"` 即纯色无材质。POC 已按此设置并验证无告警。

---

## 三、关于 PyPI 包的重大发现（影响依赖选型）

`PyPI: PySide6-FluentUI-QML 1.6.7` **自带一个预编译的 `fluentuiplugin.dll`**：

| 项 | PyPI 包内 | 本项目自编译 |
|----|-----------|--------------|
| 路径 | `.venv/Lib/site-packages/FluentUI/qml/FluentUI/fluentuiplugin.dll` | `third_party/_build/src/Release/fluentuiplugin.dll` |
| 大小 | 3,892,224 B | 4,743,680 B |
| **SHA256** | `41A3D052189EE393B088F87A86C2813DEF71E7D3E5A94FEAC0030A3F88617E3F` | `E5660B0E313432B59F0210C05857917B36F17B24E72FA6C6D50F02C0F8279F7A` |
| Qt 依赖 | Quick/Qml/**Network**/Gui/Core | Quick/Qml/Widgets/Gui/Core |
| 源码版本 | 1.6.7（2024-01） | 1.7.7 系列（commit 2026-04） |

包内 `__init__.py` 仅有一个 `init(engine)` 函数，把 `site-packages/FluentUI/qml` 加入 QML 导入路径：

```python
def init(engine: QQmlApplicationEngine):
    current_module_path = os.path.dirname(os.path.abspath(__file__)) + "/qml"
    engine.addImportPath(current_module_path)
```

**结论与决策依据**：

1. 该 DLL 的**构建来源不可验证**（无构建记录、无 commit 对应关系、无哈希发布），按项目规则不宜作为分发产物依赖。
2. 该包版本停在 1.6.7，落后上游约 1.5 年。
3. 该包声明 `Requires-Dist: PySide6 >=6.6.1`，会连带拉入 `PySide6-Addons`（**117.9 MB**），与 200 MB 体积目标冲突。
4. 本项目 POC 已验证：**完全不需要该 PyPI 包**——直接把自己编译的模块目录加入导入路径即可，功能等价且来源可追溯。
5. 因此依赖收敛为 **仅 `pyside6-essentials==6.7.3`**，并从 `pyproject.toml` 的 `ui` 依赖组移除 `pyside6` 元包与 `PySide6-FluentUI-QML`。

---

## 四、尚未完成（后续补充）

| 项 | 状态 |
|----|------|
| PyInstaller 打包与"无 Python 环境"机器验证（阶段 0 的 0.6 / 0.7） | 未开始 |
| 安装包体积实测（对照 ≤200 MB 目标） | 未开始 |
| Linux 目标的插件编译 | 已确认延后到阶段 4 之前 |
| `scripts/build_fluentui.ps1` 与本记录的自动对接（脚本应自动写入本文件的产物段落） | 待接线 |
