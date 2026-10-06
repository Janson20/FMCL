// AppBar.qml —— **唯一的一条顶栏**（界面返工 B 组；取代阶段 2 的 `shell/TitleBar.qml`）。
//
// ## 为什么是"一条"而不是两条
//
// 阶段 2 的窗口顶部有两条横条：`FluWindow` 内置的 appBar（30px，只有标题与窗口按钮）
// **加上**自绘的 `TitleBar`（48px，返回/搜索/通知/账号）。两条的颜色还各走一套来源，
// 于是用户看到的是"白顶栏 + 黑正文"那种上下分裂的观感（返工 A 组截图）。
// 参考项目 `tmp/FusionMusicPlayer/` 的做法是**把应用内容塞进 FluAppBar**，
// 这里照做：本文件是 `FluAppBar` 的子类，一条 40px 的横条上同时放
// 「返回 + 面包屑」与「搜索 + 通知 + 账号」，右侧 120px 留给窗口按钮。
//
// ## 拖动与命中测试（不写这段，顶栏上的按钮会点不动）
//
// 无边框窗口的拖动由 `FluFrameless` 用 Win32 命中测试实现：光标落在 appBar 内
// **且不在** `_hitTestList` 里的项上时返回 `HTCAPTION`（= 系统拖动/双击最大化）。
// 也就是说：**凡是可交互的子项都必须登记**，否则点击会被系统当成"拖窗口"吃掉，
// QML 侧一个事件都收不到（不报错，只是没反应）。
//
// 登记走 `FluWindow.setHitTestVisible(item)`（内部就是往那个列表里加一项）。
// 窗口由 `App.qml` 在 `Component.onCompleted` 里统一登记（见那边的一行循环）——
// 放在那里是因为：本文件被独立实例化（测试、画廊）时**没有** FluWindow 可登记，
// 而 `App.qml` 一定是那个 FluWindow 的根。
//
// ## 约束（闸门 R1/R2/R3/R4/R8）
//
// 无渐变、无 emoji、无中文字面量（只允许 `Tr.map[...]`）、无颜色字面量（一律 `Theme.*`）。

import QtQuick
import QtQuick.Layouts
import FluentUI
import "../components"

FluAppBar {
    id: bar
    objectName: "appBar"

    //: 叫 appBar 而不是 titleBar：它现在**就是**窗口顶栏那一条（含窗口按钮区）。
    //: 阶段 2 的测试断言里那串名字（titleBar/navigation/pageStack/statusBar/…）
    //: 已同步改成 appBar —— objectName 是测试的契约面，改名必须两边一起改。

    // 应用内容区高度：40 = 参考项目的 34 + 我们的窗口按钮行高（30）留出的余量。
    // `Theme.titleBarHeight` 与窗口按钮的 30px、导航项 40px 是同一套档位。
    height: Theme?.titleBarHeight ?? 40

    // 三个系统按钮各 40px —— 右侧内容区必须让开这段宽度（参考项目的算法一致）
    readonly property int systemButtonsWidth: 120

    // 内置那一行（图标 + 标题）关掉：应用内容自己画，避免"标题重复两遍"
    titleVisible: false
    showDark: false
    showStayTop: false

    // 顶栏上用到的可交互项，供 `App.qml` 登记进 FluFrameless 的命中测试白名单。
    // **顺序无关**，但必须齐全：漏一个，那个按钮就点不动（见文件头）。
    readonly property var interactiveItems: [backButton, searchField, notifyButton, languageButton, accountButton]

    RowLayout {
        objectName: "appBarContent"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        anchors.leftMargin: Theme?.spacingSm ?? 0
        anchors.rightMargin: bar.systemButtonsWidth + (Theme?.spacingSm ?? 0)
        spacing: Theme?.spacingSm ?? 0

        FmToolButton {
            id: backButton
            objectName: "backButton"

            // 图标名与可用性都来自 Nav（栈里还有上一页才亮），与阶段 2 的 TitleBar 一致
            iconName: "arrow-left"
            enabled: Nav ? Nav.canGoBack : false
            onClicked: {
                if (Nav)
                    Nav.goBack()
            }
        }

        Breadcrumb {
            id: breadcrumb
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.leftMargin: Theme?.spacingXs ?? 0
        }

        FmSearchField {
            id: searchField
            objectName: "globalSearchBox"

            Layout.preferredWidth: 240
            Layout.preferredHeight: 30
            placeholderText: Tr?.map["search"] ?? "search"
            // 回车才发请求（搜索页在阶段 3 接真实数据）
            onAccepted: {
                if (Shell)
                    Shell.globalSearch(text)
            }
        }

        //: 未保存标记（阶段 3 任务 3.4 / M-Q1 的 B6）：设置域里还有没落盘的改动时
        //: 露一个标签。它是**提示**不是按钮 —— 保存/取消在设置页底部的动作条上。
        //: 判空写法与全仓一致：上下文属性在引擎析构时先被清成 null。
        FmTag {
            objectName: "unsavedTag"
            Layout.alignment: Qt.AlignVCenter
            visible: (typeof Settings !== "undefined" && Settings) ? Settings.dirty : false
            level: "warning"
            iconName: "warning"
            text: Tr?.map["settings_unsaved_tag"] ?? "settings_unsaved_tag"
        }

        FmToolButton {
            id: notifyButton
            objectName: "notificationButton"

            iconName: "notify"
            onClicked: {
                // 铃铛 = "查看公告"。原先它调的是 `toggleNotificationCenter()`，
                // 而那个信号**没有任何消费者**（通知中心浮层一直没做）—— 用户
                // 2026-10-06 实测报的"打开公告的按钮点不了"就是它。
                // 现在发 `Shell.requestNotice()`，由 `App.qml` 决定重看公告还是提示"暂无公告"。
                if (Shell)
                    Shell.requestNotice()
            }
        }

        //: 语言：随时可切（2026-10-06 验收反馈补的入口）。
        //: A-27 的语言浮层只在首次启动出现一次，设置页要到 3.4 才有 —— 在那之前
        //: 用户想换语言只能去手改 `config.json`。这个按钮复用**同一个浮层**
        //: （`StartupDialogs.qml` 里那份，一份实现两个入口）。
        FmToolButton {
            id: languageButton
            objectName: "languageButton"

            iconName: "language"
            onClicked: {
                if (Shell)
                    Shell.requestLanguage()
            }
        }

        // 账号：阶段 3 换成账号菜单/浮层，现在先进设置里的账号页
        FmToolButton {
            id: accountButton
            objectName: "accountButton"

            iconName: "account"
            onClicked: {
                if (Nav)
                    Nav.push("settings/account")
            }
        }
    }
}
