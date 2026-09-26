# 第三方依赖来源记录（PROVENANCE）

本文件是 FMCL 项目 UI 层（PySide6 + QML）所用第三方组件库的**供应链来源记录**。

用途：满足项目规则「禁止引入来源不明的二进制；第三方依赖要么来自官方包管理器，
要么由本项目自行编译并记录来源与哈希」。本文件记录**源码级**来源（上游 URL + 固定
commit），编译产物哈希请见同目录 `BUILD_PROVENANCE.md`（由构建脚本自动生成）。

审计日期：2026-09-26
审计工具：git 2.55.0.windows.5（Windows / PowerShell 7）
受控目录：`third_party/`

---

## 1. 仓库清单

| 项 | 仓库一 | 仓库二 |
| --- | --- | --- |
| 上游 URL | https://github.com/zhuzichu520/FluentUI | https://github.com/zhuzichu520/PySide6-FluentUI-QML |
| 本地路径 | `third_party/FluentUI` | `third_party/PySide6-FluentUI-QML` |
| 默认分支 | `main` | `main` |
| **pin 的 commit SHA** | `7e33a2f672d18239ef49ab960075b1137a1d18e7` | `617986a17c9850ecb365bc02713433abab79c7aa` |
| 提交时间 | 2026-04-07 16:58:19 +0800 | 2025-03-24 09:33:34 +0800 |
| 提交标题 | `Merge pull request #627 from Polaris-Night/main` | `Create LICENSE` |
| 提交作者 | zhuzichu `<zhuzichu520@gmail.com>` | zhuzichu `<zhuzichu520@gmail.com>` |
| 所处 tag | HEAD 未被 tag 命中；最近 tag 为 `1.7.7` | 当前 HEAD 即 tag `1.7.6` |
| 子模块 | `.gitmodules` 存在但为 **0 字节**，无任何 gitlink | 无 `.gitmodules`，无子模块 |
| 许可 | MIT License（文件名 `License`，无扩展名） | MIT License（文件名 `LICENSE`） |
| 版权行 | `Copyright (c) 2023 zhuzichu` | `Copyright (c) 2025 zhuzichu` |
| 语言/产物 | C++ / Qt QML 插件（`fluentuiplugin`） | Python / PySide6 封装层 |

`git describe --tags --abbrev=0` 结果：仓库一 = `1.7.7`；仓库二 = `1.7.6`。
仓库一 tag 总数 75，仓库二 tag 总数 7。

---

## 2. 子模块状态（重要更正）

任务书假设「C++ 仓库有子模块，必须带 `--recursive`」。**实测结论：该假设不成立。**

```
$ git -C third_party/FluentUI submodule status --recursive
(输出为空)

$ git -C third_party/FluentUI ls-tree -r HEAD | Select-String '160000'
(无匹配，即树中不存在任何 gitlink 条目)

$ git -C third_party/FluentUI cat-file -s HEAD:.gitmodules
0
```

`.gitmodules` 文件在 pin 的 commit 上长度为 0 字节。该文件内容被清空发生在
`3c924bb0de2c1eb466645430be5590dc0b162759`（2023-12-13 17:31:08 +0800，`update`），
历史提交 `2c16f6f7 mv framelsshelper and zxing-cpp` 表明上游早期确实用过子模块，
后已全部移除（改为 `3rdparty/` 目录内直接存放文件）。

因此 `--recursive` 参数在**当前 commit 上是空操作**，但保留它无害且符合上游 README
的写法，可作为对上游未来重新引入子模块的防御。`fetch_sources.ps1` 仍然保留
`--recursive` 与 `git submodule update --init --recursive`。

---

## 3. 精确的获取命令

仓库一（C++）：

```powershell
git clone --recursive https://github.com/zhuzichu520/FluentUI third_party/FluentUI
git -C third_party/FluentUI checkout 7e33a2f672d18239ef49ab960075b1137a1d18e7
git -C third_party/FluentUI submodule update --init --recursive
```

仓库二（QML/PySide6）：

```powershell
git clone --recursive https://github.com/zhuzichu520/PySide6-FluentUI-QML third_party/PySide6-FluentUI-QML
git -C third_party/PySide6-FluentUI-QML checkout 617986a17c9850ecb365bc02713433abab79c7aa
git -C third_party/PySide6-FluentUI-QML submodule update --init --recursive
```

以上命令已封装在 `third_party/fetch_sources.ps1`（幂等，pin 的 commit 集中定义在脚本顶部）。

### 来源校验命令

```powershell
git -C third_party/FluentUI rev-parse HEAD
git -C third_party/FluentUI log -1 --format='%H%n%ci%n%s'
git -C third_party/FluentUI status --porcelain      # 应为空，确保工作区未被本地修改
```

---

## 4. LICENSE 原文摘录与关键条款摘要

两个仓库均为标准 MIT License，文本一致，仅版权年份与作者不同。

仓库一 `third_party/FluentUI/License` 原文前 13 行：

```
MIT License

Copyright (c) 2023 zhuzichu

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.
```

仓库二 `third_party/PySide6-FluentUI-QML/LICENSE` 原文前 3 行：

```
MIT License

Copyright (c) 2025 zhuzichu
```

关键条款摘要：

- 授予权利：使用、复制、修改、合并、发布、分发、再许可、销售软件副本，且无版税。
- **唯一实质义务**：上述版权声明与本许可声明必须包含在软件的所有副本或主要部分中。
- 免责声明：软件按「原样」提供，无任何明示或默示担保；作者不对任何索赔、损害或其他
  责任负责。
- MIT 允许被纳入专有软件，也允许被 GPL 项目使用（MIT 与 GPL-3.0 兼容，见第 5 节）。

---

## 5. 本项目如何合规使用（GPL-3.0-only 项目使用 MIT 库）

本项目为 GPL-3.0-only。两个上游库为 MIT。二者**许可兼容**，依据如下：

1. MIT 是宽松许可，不含 copyleft 条款，明确允许 `sublicense`，因此可以并入 GPL 作品。
2. FSF 官方将 MIT/X11 类许可列为「GPL-Compatible Free Software Licenses」，
   允许与 GPLv3 组合；组合后的整体作品按 GPL-3.0 分发。
3. **不产生许可冲突**：MIT 的义务（保留版权与许可声明）比 GPL 的义务弱，容易被满足。

本项目采取的合规动作：

- **保留原始声明**：不修改 `third_party/FluentUI/License` 与
  `third_party/PySide6-FluentUI-QML/LICENSE`；源码树原样保留，不做本地篡改。
- **随分发附带声明**：正式发行包中必须包含这两个 MIT 许可全文，以及本文件，
  建议位置为安装目录下的 `licenses/FluentUI-MIT.txt`、
  `licenses/PySide6-FluentUI-QML-MIT.txt`、`licenses/THIRD_PARTY_PROVENANCE.md`。
  注意 GPL-3.0 的「不附加限制」要求：不得对 MIT 部分施加比 MIT 更严格的限制。
- **来源可追溯**：源码树不入库，改为按 commit pin 重新拉取（见 `README.md`），
  保证任何构建都能从上游可验证地复现同一份源码。
- **二进制须自建**：本项目**不下载上游任何预编译二进制**（见第 6 节），
  所有 `.dll` / `.so` / `.dylib` 均由 `scripts/build_fluentui.ps1` 从上述 pin 的
  源码在本机编译产生，并把 SHA256 写入 `BUILD_PROVENANCE.md`。

### Qt 本身的许可（重要，与 FluentUI 无关但同属本目录的依赖链）

编译 FluentUI 插件会链接 Qt。Qt **不是 MIT**，这一点容易被忽略。
上游 `third_party/FluentUI/THIRD_PARTY_COPYRIGHT.txt` 原文摘录：

```
About Qt 6.5.0 Libraries Used in This Project:

This project, "FluentUI", utilizes the Qt 6.5.0 library.
Below is a list of the Qt modules used and their corresponding open-source licenses:

Qt Core - LGPL v3 / GPL v2 / Commercial License
Qt Quick - LGPL v3 / GPL v2 / Commercial License
Qt Quick Controls 2 - LGPL v3 / GPL v2 / Commercial License
Qt Gui - LGPL v3 / GPL v2 / Commercial License
Qt Multimedia - LGPL v3 / GPL v2 / Commercial License
Qt WebEngineQuick - LGPL v3 / GPL v3 / Commercial License
Qt Sql - LGPL v3 / GPL v2 / Commercial License
Qt Svg - LGPL v3 / GPL v2 / Commercial License
Qt Core5Compat - LGPL v3 / GPL v2 / Commercial License
```

关键推论（本项目为 GPL-3.0-only）：

1. **必须选择 Qt 的 LGPL v3 授权**，不能选 GPL v2。GPLv2-only 与 GPLv3 不兼容，
   若把 Qt 当作 GPLv2 使用会与项目的 GPL-3.0-only 冲突；LGPLv3 与 GPLv3 兼容，
   选它才成立。
2. **LGPLv3 的额外义务**必须履行：
   - 动态链接 Qt（不要把 Qt 静态链进产物，那样会触发更严格的条款）；
   - 随分发提供 Qt 的许可证全文与版权声明；
   - 提供「可替换 Qt 版本」的能力（通常是提供目标文件或允许重新链接的说明）；
   - 若修改了 Qt 本身，必须公开修改后的 Qt 源码。
3. Qt 通过官方渠道获取（Qt 在线安装器或 `aqtinstall`），符合项目规则中
   「来自官方包管理器」这一条；本项目不自行编译 Qt。
4. 本机 `THIRD_PARTY_COPYRIGHT.txt` 引用的是 Qt 6.5.0，而实际安装的 Qt 是
   `D:\Qt\6.7.3\msvc2019_64`。上表的许可分组在各 Qt6 小版本间基本稳定，
   但正式发布前应按 https://doc.qt.io/qt-6/licenses-used-in-qt.html 复核一次。

### 需要注意的内部第三方文件

仓库一在 `src/qrcode`、`src/qhotkey`、`src/qmlcustomplot` 内**内联了第三方源码**
（qhotkey、qcustomplot、qrcodegen 等），并附带顶层 `THIRD_PARTY_COPYRIGHT.txt`。
这些是源码而非二进制，其许可随上游 MIT 分发，但若本项目引入更严格的合规审查，
应单独阅读该文件确认内联组件的实际许可。同样，仓库一 `3rdparty/` 下签入了
OpenSSL 1.1 的 `.dll`（见第 6 节），**本项目不得使用这些签入的 DLL**。

---

## 6. 预编译二进制调查结论

**结论：上游没有提供任何可直接复用的预编译 QML 插件二进制。一个都没有。**

判定依据（两条独立证据链）：

### 证据链 A：GitHub Releases 资产

仓库一 `zhuzichu520/FluentUI`：共 **72 个 release**，全部资产都是**最终用户演示程序**
（`example` 应用的打包件），而非可链接/可拷贝进项目的插件。资产命名模式为
`example_<版本>_<CI平台>_<Qt版本>.<扩展名>`，扩展名为 `.exe`（Inno Setup 安装器）、
`.zip`（windeployqt 打包的整个程序）、`.dmg`、`.AppImage`。

最新 release `1.7.7` 的 7 个资产：

| 资产名 | 大小(字节) |
| --- | --- |
| `example_1.7.7_win32_msvc2019_Qt5.15.2.exe` | 26709755 |
| `example_1.7.7_ubuntu-latest_Qt6.6.2.AppImage` | 52171968 |
| `example_1.7.7_win64_msvc2019_64_Qt6.6.2.exe` | 31209913 |
| `example_1.7.7_macos-latest_Qt6.6.2.dmg` | 71264761 |
| `FluentUI-Gallery_1.0.5_windows-2019_Qt6.7.2.exe` | 20013179 |
| `FluentUI-Gallery_1.0.5_macos-14_Qt6.7.2.dmg` | 56969110 |
| `FluentUI-Gallery_1.0.5_ubuntu-22.04_Qt6.7.2.deb` | 36299538 |

仓库二 `zhuzichu520/PySide6-FluentUI-QML`：共 **1 个 release**（tag `1.7.6`），
**1 个资产**：

| 资产名 | 大小(字节) |
| --- | --- |
| `example_1.7.5_win64_msvc2019_64_PySide6.exe` | 47430599 |

该资产是 PyInstaller 打包的**演示程序单体 exe**，不是插件，无法被本项目 import 复用。
上游 CI 逻辑（`.github/workflows/windows.yml`）证实这一点：release 上传的是
`./package/installer.exe`，即 Inno Setup 对 `dist/` 目录（windeployqt 之后的 example
程序）打的安装包，资产名 `example_${版本}_${arch}_Qt${qt_ver}.exe`。

### 证据链 B：仓库内二进制文件扫描

仓库一工作区内 `.dll` / `.so` / `.dylib` / `.lib` / `.a` 扫描结果：仅有 9 个签入的
`.dll`，全部是**运行时依赖库，没有一个是 QML 插件**：

```
3rdparty/mingw/libcrypto-1_1-x64.dll      2866688   OpenSSL 1.1 运行时
3rdparty/mingw/libgcc_s_seh-1.dll           75776   MinGW GCC 运行时
3rdparty/mingw/libssl-1_1-x64.dll          688128   OpenSSL 1.1 运行时
3rdparty/mingw/libstdc++-6.dll            1957888   MinGW libstdc++ 运行时
3rdparty/mingw/libwinpthread-1.dll          53248   MinGW pthread 运行时
3rdparty/msvc/x64/libcrypto-1_1-x64.dll   2866688   OpenSSL 1.1 运行时
3rdparty/msvc/x64/libssl-1_1-x64.dll       688128   OpenSSL 1.1 运行时
3rdparty/msvc/x86/libcrypto-1_1.dll       2255360   OpenSSL 1.1 运行时
3rdparty/msvc/x86/libssl-1_1.dll           537088   OpenSSL 1.1 运行时
```

注：`3rdparty/msvc/x64/libcrypto-1_1-x64.dll` 与 `3rdparty/mingw/libcrypto-1_1-x64.dll`
的 blob 哈希相同（`f996a04c8dd78b0e478b4feea80d5486cf7354b9`），
`libssl-1_1-x64.dll` 同理（`f6cf40b9c25ac5ceb26141ba63c7c981bc9e5d44`），
即两处是同一文件。这些 DLL 由上游直接签入源码树、无构建来源说明，
**按本项目规则属于「来源不明的二进制」，禁止使用**。

仓库二 `git ls-tree -r --long HEAD` 扫描 `.dll|.so|.dylib|.pyd|.a|.lib|.exe|.zip|.pdb|.qmlc|.jsc`
**零匹配**；工作区递归扫描同样**零匹配**。该仓库不含任何二进制文件。

### 对本项目的直接含义

必须由本项目自行编译插件。`scripts/build_fluentui.ps1` 即是为此准备。
编译产物写入 `third_party/_install`，哈希写入 `third_party/BUILD_PROVENANCE.md`。

---

## 7. 目录用途

| 路径 | 用途 | 是否入库 |
| --- | --- | --- |
| `third_party/FluentUI` | C++ / Qt QML 插件源码（pin 的 commit） | 否，被 `.gitignore` 忽略 |
| `third_party/PySide6-FluentUI-QML` | PySide6 封装层与 QML 资源源码 | 否，被 `.gitignore` 忽略 |
| `third_party/_install` | 自行编译的插件产物（安装前缀，产物落在 `_install/imports/FluentUI/`） | 否 |
| `third_party/_stage` | 插件 `OUTPUT_DIRECTORY`（构建中间产物，避免写入共享 Qt SDK 目录） | 否 |
| `third_party/_build` | CMake 构建目录 | 否 |
| `third_party/PROVENANCE.md` | 本文件，来源记录 | 是 |
| `third_party/README.md` | 目录约定说明 | 是 |
| `third_party/fetch_sources.ps1` | 幂等拉取脚本 | 是 |
| `third_party/BUILD_PROVENANCE.md` | 构建产物清单与 SHA256（构建时生成） | 否，见下方警告 |

### 警告：BUILD_PROVENANCE.md 当前被 `.gitignore` 忽略

`.gitignore` 现有规则为：

```
third_party/*
!third_party/README.md
!third_party/PROVENANCE.md
!third_party/fetch_sources.ps1
```

`third_party/BUILD_PROVENANCE.md` 未被白名单覆盖，实测 `git check-ignore -q` 退出码为 0
（即被忽略）。若希望把编译产物哈希也纳入版本管理，需要在 `.gitignore` 追加一行
`!third_party/BUILD_PROVENANCE.md`。本任务被要求不得修改 `.gitignore`，故此处仅记录，
请由仓库维护者决定。
