// AppBar.qml —— **唯一的一条顶栏**。
//
// ## 为什么是"一条"而不是两条
//
// 顶部只保留一条横条：`FluWindow` 内置的 appBar 与自绘的 TitleBar 各画一条时，
// 两条的颜色各走一套来源，会出现"白顶栏 + 黑正文"那种上下分裂的观感。
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

    //: 叫 appBar 而不是 titleBar：它**就是**窗口顶栏那一条（含窗口按钮区）。
    //: objectName 是测试的契约面，改名必须与断言一起改。

    // 应用内容区高度：40 = 参考项目的 34 + 我们的窗口按钮行高（30）留出的余量。
    // `Theme.titleBarHeight` 与窗口按钮的 30px、导航项 40px 是同一套档位。
    height: Theme?.titleBarHeight ?? 40

    // 三个系统按钮各 40px —— 右侧内容区必须让开这段宽度（参考项目的算法一致）
    readonly property int systemButtonsWidth: 120

    //: 把下拉的选中项对回 GameDirs.activeId（用户选择后、列表刷新后都调）。
    function syncGameDirCombo() {
        if (typeof GameDirs === "undefined" || !GameDirs)
            return
        var items = GameDirs.dirChoices
        var active = String(GameDirs.activeId || "")
        for (var i = 0; i < items.length; i++) {
            if (String(items[i].id) === active) {
                gameDirCombo.currentIndex = i
                return
            }
        }
        gameDirCombo.currentIndex = -1
    }

    // 内置那一行（图标 + 标题）关掉：应用内容自己画，避免"标题重复两遍"
    titleVisible: false
    showDark: false
    showStayTop: false

    // 顶栏上用到的可交互项，供 `App.qml` 登记进 FluFrameless 的命中测试白名单。
    // **顺序无关**，但必须齐全：漏一个，那个按钮就点不动（见文件头）。
    readonly property var interactiveItems: [backButton, gameDirCombo, agentQuickVoice, searchField,
                                            monitorButton, notifyButton, languageButton, accountButton]

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

            // 图标名与可用性都来自 Nav（栈里还有上一页才亮）
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

        //: 全局游戏目录选择器（多 .minecraft）。窄窗口隐藏，管理入口在设置 -> 游戏目录。
        //: 不用 currentIndex 绑定：ComboBox 的用户选择会打断绑定，这里由
        //: syncGameDirCombo() 在 dirsChanged / activeChanged / 用户选择后统一复位。
        FmComboBox {
            id: gameDirCombo
            objectName: "globalGameDirCombo"
            Layout.preferredWidth: 180
            Layout.preferredHeight: 30
            visible: (typeof GameDirs !== "undefined" && GameDirs)
                     ? (GameDirs.available && GameDirs.dirChoices.length > 0 && bar.width >= 1100) : false
            model: (typeof GameDirs !== "undefined" && GameDirs) ? GameDirs.dirChoices : []
            textRole: "name"
            valueRole: "id"
            popupMaxHeight: 260
            onActivated: {
                if (typeof GameDirs !== "undefined" && GameDirs) {
                    GameDirs.activate(String(currentValue))
                    bar.syncGameDirCombo()
                }
            }
            onModelChanged: bar.syncGameDirCombo()
            Component.onCompleted: bar.syncGameDirCombo()
        }

        FmToolButton {
            id: agentQuickVoice
            objectName: "agentQuickVoice"
            iconName: (typeof Agent !== "undefined" && Agent && Agent.voiceState === "recording")
                      ? "stop" : "microphone"
            visible: (typeof Agent !== "undefined" && Agent)
                     ? (Agent.available && bar.width >= 1180) : false
            onClicked: if (typeof Agent !== "undefined" && Agent)
                           Agent.toggleVoice()
        }

        //: 顶栏 AI 快速提问：回车 -> 切到 AGENT 页 -> 交给 `Agent.quickAsk`。
        //: 这里复用原全局搜索的位置（`Shell.searchRequested` 没有消费者）。
        //: objectName 保留 `globalSearchBox`，它是壳层结构断言的契约面。
        FmTextField {
            id: searchField
            objectName: "globalSearchBox"

            Layout.preferredWidth: 300
            Layout.preferredHeight: 30
            placeholder: Tr?.map["agent_quick_input_placeholder"] ?? "agent_quick_input_placeholder"
            onAccepted: {
                var value = String(text || "").trim()
                if (value.length === 0 || typeof Agent === "undefined" || !Agent)
                    return
                if (typeof Nav !== "undefined" && Nav
                        && String(Nav.currentRoute || "").indexOf("agent") !== 0)
                    Nav.push("agent")
                if (Agent.quickAsk(value))
                    text = ""
            }
        }

        //: 未保存标记：设置域里还有没落盘的改动时
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

        //: 性能监控入口。
        //: 顶栏这条入口打开时显示 10 秒快捷键提示；Ctrl+Shift+M 是另一条入口，
        //: 由 Overlay.bind_hotkeys() 在启动时注册。
        FmToolButton {
            id: monitorButton
            objectName: "monitorButton"
            iconName: "monitor"
            onClicked: {
                if (typeof Overlay !== "undefined" && Overlay)
                    Overlay.toggleMonitorWithHint()
            }
        }

        FmToolButton {
            id: notifyButton
            objectName: "notificationButton"

            iconName: "notify"
            onClicked: {
                // 铃铛 = "查看公告"。`toggleNotificationCenter()` 那个信号没有消费者
                // （通知中心浮层一直没做），点了也没反应。
                // 这里发 `Shell.requestNotice()`，由 `App.qml` 决定重看公告还是提示"暂无公告"。
                if (Shell)
                    Shell.requestNotice()
            }
        }

        //: 语言：随时可切（首次启动的语言浮层只出现一次，没有这个入口就只能手改
        //: `config.json`）。复用**同一个浮层**（`StartupDialogs.qml` 里那份，一份实现两个入口）。
        FmToolButton {
            id: languageButton
            objectName: "languageButton"

            iconName: "language"
            onClicked: {
                if (Shell)
                    Shell.requestLanguage()
            }
        }

        // 账号：先进设置里的账号页
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

    Connections {
        target: (typeof GameDirs !== "undefined" && GameDirs) ? GameDirs : null

        function onDirsChanged() {
            if (bar)
                bar.syncGameDirCombo()
        }

        function onActiveChanged() {
            if (bar)
                bar.syncGameDirCombo()
        }

        function onStatusMessage(text, level) {
            if (typeof Shell !== "undefined" && Shell)
                Shell.setStatus(text, level)
        }
    }

    Connections {
        target: (typeof Agent !== "undefined" && Agent) ? Agent : null

        function onVoiceText(text) {
            // 只在不在 AGENT 页时把识别结果填到顶栏快速提问框；在 AGENT 页时
            // 由 AgentPage 自己的监听填输入区，避免同一段文字填两处。
            if (!Nav || String(Nav.currentRoute || "").indexOf("agent") === 0)
                return
            var value = String(text || "")
            if (value.length === 0)
                return
            var current = String(searchField.text || "")
            searchField.text = current.length > 0 ? current + " " + value : value
        }
    }
}
