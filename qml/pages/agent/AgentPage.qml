// AgentPage.qml —— AGENT 对话主页面（阶段 3 任务 3.22）。
//
// 三条 L2 路由（agent/sessions、agent/models、agent/tools、agent/skills）目前都
// 指向本页：3.22 只交付 I1（对话与流式输出），模型/工具权限/技能/语音设置在
// 3.23。会话管理已经在本页左栏落地，点击 agent/sessions 进来会先展开会话栏。
//
// 布局：宽窗三栏（会话 | 对话 | 待办），窄窗默认只显示对话，用顶部的
// 会话/待办按钮在同一屏切换。所有长活都通过 Agent 桥;本页不直接读服务、
// 不自己起线程、不拼业务错误文案。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../components"
import "../../components/agent"
import "../../components/dialogs"

FmPage {
    id: page
    objectName: "agentPage"

    readonly property bool live: (typeof Agent !== "undefined" && Agent) ? true : false
    readonly property bool wideSessions: page.width >= 900
    readonly property bool wideTodos: page.width >= 1000
    readonly property bool showSessions: page.wideSessions ? page.sessionsVisible
                                                           : page.sideMode === "sessions"
    readonly property bool showTodos: page.wideTodos ? page.todosVisible
                                                     : page.sideMode === "todos"
    readonly property bool hasApproval: page.live && Agent.hasApproval
    readonly property string approvalKind: page.hasApproval
                                           ? String(Agent.pendingApproval.kind || "")
                                           : ""

    property bool sessionsVisible: true
    property bool todosVisible: true
    property string sideMode: ""
    property bool followTail: true
    property bool initialScrollDone: false
    property bool autoScrolling: false
    property real scrollTargetY: 0
    property string lastSessionId: ""
    property string renameId: ""
    property string deleteId: ""

    title: page.t("agent_title")
    iconName: "agent"
    description: page.t("agent_welcome_hint")
    contentState: !page.live || !Agent.available ? "error" : "ready"
    //: 错误态标题不能为空（冒烟测试会手动切三态并读它）；真正不可用时换成更具体的文案。
    errorText: page.live && !Agent.available
               ? page.t("agent_service_unavailable")
               : page.t("plugin_state_error")
    retryText: page.t("refresh")

    onRetried: {
        if (page.live)
            Agent.refresh()
    }

    Component.onCompleted: page.refresh()
    Component.onDestruction: {
        // 页面被导航栈销毁时，worker 可能正停在“等用户确认”上；没有页面就没有
        // 人能作答。这里主动停止并放行确认，避免后台线程永久挂住（消息仍会落盘）。
        if (page.live && Agent.busy)
            Agent.stop()
    }
    onVisibleChanged: {
        if (page.visible && page.live)
            Agent.refresh()
    }
    onRouteIdChanged: {
        if (String(page.routeId) === "agent/sessions")
            page.sideMode = "sessions"
    }

    function t(key) {
        return (typeof Tr !== "undefined" && Tr) ? (Tr.map[String(key)] ?? String(key)) : String(key)
    }

    function format(key, values) {
        var text = page.t(key)
        var data = values || ({})
        for (var name in data)
            text = text.replace("{" + String(name) + "}", String(data[name]))
        return text
    }

    function refresh() {
        if (page.live)
            Agent.refresh()
    }

    function toggleSessions() {
        if (page.wideSessions) {
            page.sessionsVisible = !page.sessionsVisible
            return
        }
        page.sideMode = page.sideMode === "sessions" ? "" : "sessions"
    }

    function toggleTodos() {
        if (page.wideTodos) {
            page.todosVisible = !page.todosVisible
            return
        }
        page.sideMode = page.sideMode === "todos" ? "" : "todos"
    }

    function closeSidePanels() {
        page.sideMode = ""
    }

    function selectSession(sessionId) {
        if (page.live)
            Agent.selectSession(String(sessionId))
        if (!page.wideSessions)
            page.sideMode = ""
    }

    function openRename(sessionId, title) {
        page.renameId = String(sessionId || "")
        renameDialog.request = ({
            "title": page.t("agent_rename_title"),
            "prompt": page.t("agent_rename_prompt"),
            "initial": String(title || ""),
            "fallback": ""
        })
        renameDialog.visible = true
    }

    function openDelete(sessionId, title) {
        page.deleteId = String(sessionId || "")
        deleteDialog.request = ({
            "title": page.t("confirm_delete"),
            "message": page.t("agent_confirm_delete_session"),
            "default": false
        })
        deleteDialog.visible = true
    }

    function sendText(value) {
        var text = String(value || "").trim()
        if (!page.live || text.length === 0)
            return
        if (!Agent.loggedIn) {
            Agent.clearError()
            return
        }
        if (Agent.busy)
            return
        Agent.send(text)
        composer.text = ""
        page.followTail = true
    }

    function retry() {
        if (page.live && Agent.canRetry) {
            Agent.retryLast()
            page.followTail = true
        }
    }

    function copyMessage(messageId) {
        if (page.live)
            Agent.copyMessage(String(messageId))
    }

    function copyText(text) {
        if (page.live)
            Agent.copyText(String(text || ""))
    }

    function openLink(link) {
        var value = String(link || "")
        if (/^https?:\/\//i.test(value) || /^mailto:/i.test(value))
            Qt.openUrlExternally(value)
    }

    function exampleText(key) {
        return String(page.t(key) || "").replace("• ", "")
    }

    function useExample(key) {
        composer.text = page.exampleText(key)
        composer.focusInput()
    }

    function statusColor(level) {
        if (level === "error")
            return Theme?.error ?? "transparent"
        if (level === "warning")
            return Theme?.warning ?? "transparent"
        if (level === "success")
            return Theme?.success ?? "transparent"
        return Theme?.textSecondary ?? "transparent"
    }

    function scrollToEnd(immediate) {
        if (!page.live || messageList.count === 0)
            return
        if (immediate) {
            scrollTicker.stop()
            page.autoScrolling = false
            page.followTail = true
            messageList.positionViewAtEnd()
            return
        }
        if (!page.followTail)
            return
        scrollCoalesce.restart()
    }

    function animateScroll() {
        if (!page.followTail || messageList.count === 0)
            return
        page.scrollTargetY = Math.max(0, messageList.contentHeight - messageList.height)
        if (Math.abs(page.scrollTargetY - messageList.contentY) <= 0.5) {
            scrollTicker.stop()
            return
        }
        if (!scrollTicker.running)
            scrollTicker.start()
    }

    function tickScroll() {
        if (!page.followTail) {
            scrollTicker.stop()
            page.autoScrolling = false
            return
        }
        var target = Math.max(0, messageList.contentHeight - messageList.height)
        page.scrollTargetY = target
        var current = messageList.contentY
        var distance = target - current
        if (Math.abs(distance) <= 0.5) {
            scrollTicker.stop()
            page.autoScrolling = false
            return
        }
        // 指数逼近；每 16ms 最多推进剩余距离的 32%，内容继续长高时目标同步更新。
        var step = distance * 0.32
        if (Math.abs(step) < 0.8)
            step = distance > 0 ? 0.8 : -0.8
        page.autoScrolling = true
        messageList.contentY = current + step
    }

    Timer {
        id: scrollCoalesce
        objectName: "agentScrollTimer"
        interval: 60
        repeat: false
        onTriggered: page.animateScroll()
    }

    Timer {
        id: scrollTicker
        objectName: "agentScrollTicker"
        interval: 16
        repeat: true
        running: false
        onTriggered: page.tickScroll()
    }

    RowLayout {
        anchors.fill: parent
        spacing: Theme?.spacingMd ?? 10

        FmAgentSessionList {
            Layout.fillHeight: true
            Layout.preferredWidth: 220
            Layout.maximumWidth: 220
            visible: page.showSessions
            sessions: page.live ? Agent.sessions : []
            currentSessionId: page.live ? Agent.currentSessionId : ""
            busy: page.live ? Agent.busy : false
            onNewRequested: {
                if (page.live)
                    Agent.newSession()
                page.followTail = true
                if (!page.wideSessions)
                    page.sideMode = ""
            }
            onSelectRequested: function (sessionId) { page.selectSession(sessionId) }
            onRenameRequested: function (sessionId, title) { page.openRename(sessionId, title) }
            onDeleteRequested: function (sessionId, title) { page.openDelete(sessionId, title) }
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumWidth: 0
            Layout.preferredWidth: 1
            spacing: Theme?.spacingSm ?? 8

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme?.spacingSm ?? 8

                FmToolButton {
                    objectName: "agentSessionsToggle"
                    iconName: "history"
                    selected: page.showSessions
                    onClicked: page.toggleSessions()
                }

                //: 当前会话的强调色指示条：与账号页的“当前账号”标记同一语义。
                Rectangle {
                    objectName: "agentCurrentIndicator"
                    Layout.preferredWidth: 8
                    Layout.preferredHeight: 24
                    radius: 2
                    color: Theme?.accent ?? "transparent"
                }

                Text {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 80
                    text: page.live && Agent.currentTitle.length > 0
                          ? Agent.currentTitle : page.t("agent_chat_title")
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeLarge ?? 14
                    font.bold: true
                    elide: Text.ElideRight
                }

                FmTag {
                    objectName: "agentModelTag"
                    visible: page.live && Agent.modelLabel.length > 0 && page.width >= 900
                    text: page.live ? Agent.modelLabel : ""
                    level: "accent"
                }

                Text {
                    Layout.preferredWidth: Math.min(100, page.width * 0.15)
                    Layout.minimumWidth: 0
                    visible: page.live && Agent.statusText.length > 0
                    text: page.live ? Agent.statusText : ""
                    color: page.live ? page.statusColor(Agent.statusLevel) : "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                    elide: Text.ElideRight
                }

                FmToolButton {
                    objectName: "agentNewSessionButton"
                    iconName: "add"
                    enabled: page.live && !Agent.busy
                    onClicked: {
                        if (page.live)
                            Agent.newSession()
                        page.followTail = true
                    }
                }

                FmToolButton {
                    objectName: "agentTodosToggle"
                    iconName: "check"
                    selected: page.showTodos
                    onClicked: page.toggleTodos()
                }
            }

            FmInfoBar {
                Layout.fillWidth: true
                visible: page.live && !Agent.loggedIn
                level: "warning"
                text: page.t("agent_no_token_hint")
                actionText: page.t("ach_sync_login_action")
                onActionTriggered: {
                    if (typeof Nav !== "undefined" && Nav)
                        Nav.push("settings/ai")
                }
            }

            FmInfoBar {
                Layout.fillWidth: true
                visible: page.live && Agent.errorText.length > 0
                level: "error"
                text: page.live ? Agent.errorText : ""
                closable: true
                onClosed: {
                    if (page.live)
                        Agent.clearError()
                }
            }

            Item {
                Layout.fillWidth: true
                Layout.fillHeight: true

                ListView {
                    id: messageList
                    objectName: "agentMessageList"
                    anchors.fill: parent
                    visible: page.live && Agent.messages.length > 0
                    clip: true
                    spacing: Theme?.spacingMd ?? 10
                    model: page.live ? Agent.messages : []
                    onCountChanged: {
                        if (!page.initialScrollDone) {
                            page.followTail = true
                        } else {
                            page.followTail = true
                            page.scrollToEnd(false)
                        }
                    }
                    onContentHeightChanged: {
                        if (!page.initialScrollDone && messageList.count > 0 && messageList.contentHeight > 0) {
                            page.initialScrollDone = true
                            page.followTail = true
                            page.scrollToEnd(true)
                        } else if (page.initialScrollDone && page.followTail) {
                            page.scrollToEnd(false)
                        }
                    }
                    onFlickStarted: {
                        // 只有用户真正拖拽/甩动才退出跟随；内容长高本身不能让跟随失效。
                        scrollTicker.stop()
                        page.autoScrolling = false
                        page.followTail = false
                    }
                    onMovementEnded: {
                        if (!page.autoScrolling)
                            page.followTail = messageList.atYEnd
                    }

                    ScrollBar.vertical: FmScrollBar {
                        thickness: 6
                    }

                    delegate: Item {
                        id: delegateRoot
                        required property int index
                        required property var modelData

                        width: messageList.width
                        height: loader.implicitHeight

                        function applyStream(content, blocks, thinking) {
                            if (loader.item && loader.item.applyStream) {
                                loader.item.applyStream(content, blocks, thinking)
                                return
                            }
                            var next = {}
                            for (var key in loader.messageData)
                                next[key] = loader.messageData[key]
                            next.content = String(content || "")
                            next.blocks = blocks || []
                            next.thinking = String(thinking || "")
                            loader.messageData = next
                        }

                        Loader {
                            id: loader
                            property var messageData: delegateRoot.modelData
                            width: parent.width
                            sourceComponent: String(delegateRoot.modelData && delegateRoot.modelData.role || "") === "tool"
                                             ? toolComponent : messageComponent

                            onLoaded: page.bindMessageItem(
                                          item,
                                          messageData,
                                          delegateRoot.index,
                                          String(messageData && messageData.role || ""))
                            onMessageDataChanged: {
                                if (item)
                                    page.updateMessageItem(item, messageData, delegateRoot.index)
                            }
                        }
                    }
                }

                Column {
                    anchors.centerIn: parent
                    width: Math.min(parent.width, 640)
                    spacing: Theme?.spacingMd ?? 10
                    visible: !page.live || Agent.messages.length === 0

                    FmIcon {
                        anchors.horizontalCenter: parent.horizontalCenter
                        name: "agent"
                        color: Theme?.accent ?? "transparent"
                        size: 42
                    }

                    Text {
                        width: parent.width
                        text: page.t("agent_welcome_title")
                        color: Theme?.textPrimary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeTitle ?? 18
                        font.bold: true
                        horizontalAlignment: Text.AlignHCenter
                        wrapMode: Text.WordWrap
                    }

                    Text {
                        width: parent.width
                        text: page.t("agent_welcome_hint")
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        horizontalAlignment: Text.AlignHCenter
                        wrapMode: Text.WordWrap
                    }

                    Flow {
                        width: parent.width
                        spacing: Theme?.spacingXs ?? 4

                        Repeater {
                            model: ["agent_example1", "agent_example2", "agent_example3",
                                    "agent_example4", "agent_example5", "agent_example6"]

                            FmButton {
                                required property var modelData
                                primary: false
                                text: page.exampleText(modelData)
                                onClicked: page.useExample(modelData)
                            }
                        }
                    }

                    FmButton {
                        objectName: "agentWelcomeStart"
                        anchors.horizontalCenter: parent.horizontalCenter
                        primary: true
                        iconName: "chat"
                        text: page.t("agent_start_chat")
                        onClicked: composer.focusInput()
                    }
                }
            }

            FmAgentComposer {
                id: composer
                Layout.fillWidth: true
                busy: page.live ? Agent.busy : false
                loggedIn: page.live ? Agent.loggedIn : false
                voiceState: page.live ? Agent.voiceState : "unavailable"
                credits: page.live ? Agent.credits : 0
                tokenEstimate: page.live ? Agent.tokenEstimate : 0
                onSendRequested: function (text) { page.sendText(text) }
                onStopRequested: {
                    if (page.live)
                        Agent.stop()
                }
                onVoiceRequested: {
                    if (page.live)
                        Agent.toggleVoice()
                }
            }
        }

        FmAgentTodoPanel {
            Layout.fillHeight: true
            Layout.preferredWidth: 220
            Layout.maximumWidth: 220
            visible: page.showTodos
            todos: page.live ? Agent.todos : []
        }
    }

    // ─── 消息委托的回填 / 信号接线 ─────────────────────────
    //
    // Loader 的 onModelDataChanged 会在每次流式增量时触发；连接只做一次
    // （onLoaded），这里只更新普通属性，避免同一条消息被连上十几遍。

    function updateMessageItem(item, data, index) {
        if (!item)
            return
        item.message = data
        if (item.isLast !== undefined)
            item.isLast = index === messageList.count - 1
        if (item.canRetry !== undefined)
            item.canRetry = page.live ? Agent.canRetry : false
    }

    function bindMessageItem(item, data, index, role) {
        if (!item)
            return
        page.updateMessageItem(item, data, index)
        if (String(role || "") === "tool") {
            item.copyRequested.connect(function (text) { page.copyText(text) })
            return
        }
        item.copyRequested.connect(function (messageId) { page.copyMessage(messageId) })
        if (item.copyTextRequested !== undefined)
            item.copyTextRequested.connect(function (text) { page.copyText(text) })
        if (item.retryRequested !== undefined)
            item.retryRequested.connect(function () { page.retry() })
        if (item.linkActivated !== undefined)
            item.linkActivated.connect(function (link) { page.openLink(link) })
    }

    Component {
        id: messageComponent
        FmAgentMessage {}
    }

    Component {
        id: toolComponent
        FmAgentToolCard {}
    }

    Component {
        id: approvalComponent
        FmAgentApprovalDialog {}
    }

    Component {
        id: askComponent
        FmAgentAskDialog {}
    }

    // 工具确认浮层：worker 正在等这个答案，任何加载失败都必须回一个“拒绝”，
    // 否则一轮对话会永远停在“等待确认”。
    Loader {
        id: approvalLoader
        objectName: "agentApprovalLoader"
        anchors.fill: parent
        z: 80
        active: page.hasApproval
        sourceComponent: page.approvalKind === "ask_user" ? askComponent : approvalComponent

        onLoaded: {
            item.request = page.live ? Agent.pendingApproval : ({})
            item.answered.connect(function (accepted, answers) {
                var gateId = String((page.live ? Agent.pendingApproval.id : "") || "")
                if (gateId.length > 0)
                    Agent.answerApproval(gateId, accepted, answers || [])
            })
        }

        onStatusChanged: {
            if (status === Loader.Error && page.hasApproval) {
                var gateId = String((page.live ? Agent.pendingApproval.id : "") || "")
                if (gateId.length > 0)
                    Agent.answerApproval(gateId, false, [])
            }
        }
    }

    TextInputDialog {
        id: renameDialog
        objectName: "agentRenameDialog"
        anchors.fill: parent
        visible: false
        modalScrim: true
        z: 90

        onSubmitted: function (value) {
            var text = String(value || "").trim()
            if (text.length > 0 && page.renameId.length > 0 && page.live)
                Agent.renameSession(page.renameId, text)
            page.renameId = ""
            renameDialog.visible = false
        }

        onCancelled: {
            page.renameId = ""
            renameDialog.visible = false
        }
    }

    ConfirmDialog {
        id: deleteDialog
        objectName: "agentDeleteDialog"
        anchors.fill: parent
        visible: false
        modalScrim: true
        z: 90

        onAnswered: function (accepted) {
            if (accepted && page.deleteId.length > 0 && page.live)
                Agent.deleteSession(page.deleteId)
            page.deleteId = ""
            deleteDialog.visible = false
        }
    }

    Connections {
        target: page.live ? Agent : null

        function onChanged() {
            var sessionId = page.live ? Agent.currentSessionId : ""
            if (sessionId !== page.lastSessionId) {
                page.lastSessionId = sessionId
                page.initialScrollDone = false
                if (page.live && Agent.messages.length > 0)
                    Qt.callLater(function () { page.scrollToEnd(true) })
            } else if (page.followTail) {
                page.scrollToEnd(false)
            }
            if (page.hasApproval && approvalLoader.item)
                approvalLoader.item.request = Agent.pendingApproval
        }

        function onStreamUpdated(index, content, blocks, thinking) {
            var item = messageList.itemAtIndex(index)
            if (item && item.applyStream)
                item.applyStream(content, blocks, thinking)
            if (page.followTail)
                page.scrollToEnd(false)
        }

        function onVoiceText(text) {
            var value = String(text || "")
            if (value.length === 0)
                return
            var current = String(composer.text || "")
            composer.text = current.length > 0 ? current + " " + value : value
            composer.focusInput()
        }
    }
}
