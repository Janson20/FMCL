// ServerConsolePage.qml —— 服务器控制台（阶段 3 任务 3.10；对照表 L-21 ~ L-25 / L-34 / L-35）。
//
// 数据全部来自 Servers 桥：日志（按 version 隔离的增量 + 清屏游标）、运行状态、
// 在线玩家、内存（每 2 秒采样一次，服务层用 psutil，不在主线程跑 tasklist）、
// 命令发送、退出质量确认与崩溃 AI 分析。本页不碰服务、不保存业务状态。
//
// 日志控件复用 qml/components/LogView.qml：只读、自动滚底、不换行、保留最近
// Servers.consoleMaxLines 行（与 ServerConsoleWatcher 的 LOG_BUFFER_MAX_LINES 同源）。
//
// 纪律：颜色只来自 Theme.*（R8）、图标走 FmIcon/FmToolButton（R2）、文案走
// Tr.map/桥翻译（R3/R4）、只用 qml/components 里的 Fm*（R9）。

import QtQuick
import QtQuick.Dialogs
import QtQuick.Layouts
import "../../components"

FmPage {
    id: page
    objectName: "serverConsolePage"

    readonly property bool live: (typeof Servers !== "undefined" && Servers) ? true : false
    property string serverId: ""
    property var info: ({})
    property int logSeq: 0
    property bool loadingLog: false
    property bool clearing: false
    property string statusText: ""
    property string statusLevel: "neutral"
    property string playersText: ""
    property string playerCountText: ""
    property string memoryText: ""
    property string exitInfo: ""
    property bool exitDialogVisible: false
    property int exitCode: -1
    property string exitTitle: ""
    property bool analyzing: false
    property bool resultVisible: false
    property string resultTitle: ""
    property string resultText: ""

    contentState: (!page || !page.live) ? "error"
                  : (page.serverId.length === 0 ? "error" : "ready")

    loadingText: Tr?.map["plugin_state_loading"] ?? "plugin_state_loading"
    errorText: Tr?.map["plugin_state_error"] ?? "plugin_state_error"
    errorDetail: (!page || !page.live) ? ""
                 : (page.serverId.length === 0
                    ? (Tr?.map["server_no_version_selected"] ?? "server_no_version_selected")
                    : (page.info && page.info.version ? "" : (Tr?.map["server_no_installed"] ?? "server_no_installed")))
    retryText: Tr?.map["version_refresh"] ?? "version_refresh"

    onRetried: page.applyRoute()

    function t(key) {
        return Tr ? (Tr?.map[key] ?? key) : key
    }

    function format(key, params) {
        var text = page.t(key)
        if (!params)
            return text
        for (var name in params)
            text = text.split("{" + name + "}").join(String(params[name]))
        return text
    }

    function activeRoute() {
        return typeof Nav !== "undefined" && Nav
                && String(Nav.currentRoute) === "servers/detail/console"
    }

    function applyRoute() {
        if (!page || !page.live)
            return
        var wanted = page.routeParams ? String(page.routeParams.server || "") : ""
        if (wanted.length === 0)
            wanted = String(Servers.selected || "")
        if (wanted.length === 0) {
            page.serverId = ""
            return
        }
        var changed = wanted !== page.serverId
        page.serverId = wanted
        Servers.activateConsole(wanted)
        if (changed)
            page.logSeq = 0
        page.loadLog()
        page.refreshInfo()
    }

    function loadLog() {
        if (!page || !page.live || page.serverId.length === 0)
            return
        var snapshot = Servers.consoleSnapshot(page.serverId)
        page.loadingLog = true
        consoleLog.clear()
        if (snapshot && snapshot.lines && snapshot.lines.length > 0)
            consoleLog.appendLines(snapshot.lines)
        page.logSeq = Number(snapshot && snapshot.seq ? snapshot.seq : 0)
        page.loadingLog = false
    }

    function appendIncoming(version, lines, endSeq) {
        if (version !== page.serverId || !lines || lines.length === 0)
            return
        var end = Number(endSeq || 0)
        var start = end - lines.length
        var skip = page.logSeq - start
        var payload = skip > 0 ? lines.slice(skip) : lines
        if (payload.length > 0)
            consoleLog.appendLines(payload)
        if (end > page.logSeq)
            page.logSeq = end
    }

    function refreshInfo() {
        if (!page || !page.live || page.serverId.length === 0)
            return
        var next = Servers.consoleInfo(page.serverId)
        if (!next || !next.version)
            return
        page.info = next
        // 服务器被重新启动时，服务层的绝对序号会从 0 重来；旧游标必须一起归零，
        // 否则新日志的 endSeq 永远小于旧游标，会被当成重复批丢掉。
        if (Number(next.seq || 0) < page.logSeq) {
            page.logSeq = 0
            page.loadLog()
        }
        var state = String(next.state || "not_running")
        if (state === "running") {
            page.statusText = page.format("server_status_running", {"version": page.serverId})
            page.statusLevel = "success"
        } else if (state === "stopped") {
            page.statusText = page.t("server_status_stopped")
            page.statusLevel = "warning"
        } else {
            page.statusText = page.t("server_not_running")
            // FmTag 的 neutral 文字用的是 cardBorder（深色主题下几乎与底色同色），
            // 旧详情页对“未运行”也用 warning；这里保持一致，保证标签可读。
            page.statusLevel = "warning"
        }
        page.playersText = (next.players && next.players.length > 0)
                           ? next.players.join(", ") : ""
        page.playerCountText = String(Number(next.playerCount || 0))
                               + " / " + String(Number(next.maxPlayers || 20))
        page.memoryText = String(next.memoryText || "")
        if (!next.running && page.memoryText.length === 0)
            page.memoryText = "0 " + page.t("mb")
        page.exitInfo = String(next.exitInfo || "")
        page.refreshExitTitle()
        if (next.pendingExit && page.activeRoute()
                && !page.exitDialogVisible && !page.analyzing) {
            var pending = Servers.takeExitPrompt(page.serverId)
            if (pending !== null && pending !== undefined)
                page.showExit(Number(pending))
        }
    }

    function refreshExitTitle() {
        page.exitTitle = page.t("server_exit_question_title") + String(page.exitInfo || "")
    }

    function showExit(code) {
        page.exitCode = Number(code)
        page.refreshInfo()
        page.refreshExitTitle()
        page.exitDialogVisible = true
    }

    function handleExit(version, code) {
        if (version !== page.serverId)
            return
        page.refreshInfo()
        if (!page.activeRoute())
            return
        var pending = Servers.takeExitPrompt(page.serverId)
        page.showExit(pending !== null && pending !== undefined ? Number(pending) : Number(code))
    }

    function startAnalyze() {
        var target = page.serverId
        var code = page.exitCode
        page.exitDialogVisible = false
        if (!page || !page.live || target.length === 0)
            return
        if (Servers.analyzeCrash(target, code))
            page.analyzing = true
    }

    function sendCommand() {
        if (!page || !page.live || page.serverId.length === 0)
            return
        var text = String(commandField.text || "").trim()
        if (text.length === 0)
            return
        commandField.text = ""
        Servers.sendCommand(page.serverId, text)
    }

    function localPath(url) {
        var text = String(url)
        text = text.replace(/^file:\/{2,3}/, "")
        try {
            return decodeURIComponent(text)
        } catch (e) {
            return text
        }
    }

    Component.onCompleted: page.applyRoute()
    onRouteParamsChanged: page.applyRoute()

    Timer {
        id: memoryTimer
        interval: 2000
        repeat: true
        running: page && page.live && page.serverId.length > 0
        onTriggered: {
            if (page)
                page.refreshInfo()
        }
    }

    Connections {
        id: serverConnections
        target: (typeof Servers !== "undefined" && Servers) ? Servers : null

        function onStatusMessage(text, level) {
            if (!page || !page.activeRoute())
                return
            if (typeof Shell !== "undefined" && Shell)
                Shell.setStatus(text, level)
        }

        function onNoticeRequested(text, level) {
            if (!page || !page.activeRoute())
                return
            if (typeof Dialogs !== "undefined" && Dialogs && Dialogs.available)
                Dialogs.notify({"level": String(level), "message": String(text), "icon": "server"})
        }

        function onRunChanged() {
            if (page)
                page.refreshInfo()
        }

        function onConsoleLines(version, lines, endSeq) {
            if (page)
                page.appendIncoming(String(version), lines, endSeq)
        }

        function onConsoleCleared(version, seq) {
            if (!page || String(version) !== page.serverId)
                return
            page.clearing = true
            page.loadingLog = true
            consoleLog.clear()
            page.loadingLog = false
            page.clearing = false
            page.logSeq = Number(seq || 0)
        }

        function onConsolePlayersChanged(version) {
            if (page && String(version) === page.serverId)
                page.refreshInfo()
        }

        function onServerExited(version, code) {
            if (page)
                page.handleExit(String(version), Number(code))
        }

        function onCommandEcho(version, command) {
            if (page && String(version) === page.serverId)
                consoleLog.append("> " + String(command), "info")
        }

        function onCommandFailed(version, message) {
            if (page && String(version) === page.serverId)
                consoleLog.append(String(message), "error")
        }

        function onCrashAnalysisReady(version, text) {
            if (!page || String(version) !== page.serverId)
                return
            page.analyzing = false
            page.resultTitle = page.t("crash_analyze_success")
            page.resultText = String(text)
            page.resultVisible = true
        }

        function onCrashAnalysisFailed(version, error) {
            if (page && String(version) === page.serverId)
                page.analyzing = false
        }

        function onSelectionChanged() {
            if (!page || !page.activeRoute())
                return
            if (!Servers.hasSelection)
                Servers.goBack()
        }
    }

    Connections {
        id: navConnections
        target: (typeof Nav !== "undefined" && Nav) ? Nav : null

        function onRouteChanged(route) {
            if (!page || String(route) !== "servers/detail/console")
                return
            page.refreshInfo()
            if (page.exitDialogVisible)
                page.refreshExitTitle()
        }
    }

    Connections {
        id: trConnections
        target: (typeof Tr !== "undefined" && Tr) ? Tr : null

        function onLanguageChanged() {
            if (!page)
                return
            page.refreshInfo()
            if (page.resultVisible)
                page.resultTitle = page.t("crash_analyze_success")
        }
    }

    Component.onDestruction: {
        if (serverConnections)
            serverConnections.target = null
        if (navConnections)
            navConnections.target = null
        if (trConnections)
            trConnections.target = null
    }

    ColumnLayout {
        objectName: "serverConsolePane"
        anchors.fill: parent
        spacing: Theme?.spacingSm ?? 8

        RowLayout {
            objectName: "serverConsoleHeader"
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 8

            FmToolButton {
                objectName: "serverConsoleBack"
                iconName: "arrow-left"
                onClicked: {
                    if (page && page.live)
                        Servers.goBack()
                }
            }

            Text {
                objectName: "serverConsoleName"
                Layout.fillWidth: true
                text: (page && page.serverId.length > 0)
                      ? page.serverId
                      : (Tr?.map["server_log_title"] ?? "server_log_title")
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeTitle ?? 18
                font.bold: true
                elide: Text.ElideRight
                Layout.alignment: Qt.AlignVCenter
            }

            FmTag {
                objectName: "serverConsoleLoaderTag"
                visible: page && String(page.info.loader || "").length > 0
                text: page ? String(page.info.loader || "") : ""
                level: "accent"
            }

            FmTag {
                objectName: "serverConsoleStatusTag"
                text: page ? page.statusText : ""
                level: page ? page.statusLevel : "warning"
            }

            Item { Layout.fillWidth: true }

            FmButton {
                objectName: "serverConsoleExportButton"
                primary: false
                text: Tr?.map["settings_log_export"] ?? "settings_log_export"
                iconName: "download"
                enabled: page && page.live && page.serverId.length > 0 && Number(page.info.lineCount || 0) > 0
                onClicked: {
                    if (page && page.live)
                        exportDialog.open()
                }
            }

            FmToolButton {
                objectName: "serverConsoleRefreshButton"
                iconName: "refresh"
                onClicked: {
                    if (page)
                        page.refreshInfo()
                }
            }
        }

        FmCard {
            objectName: "serverConsoleStatusCard"
            Layout.fillWidth: true
            Layout.fillHeight: false
            padding: Theme?.spacingSm ?? 8

            RowLayout {
                objectName: "serverConsoleStatusRow"
                Layout.fillWidth: true
                spacing: Theme?.spacingSm ?? 8

                FmIcon {
                    name: "players"
                    color: Theme?.textSecondary ?? "transparent"
                    size: Theme?.iconSize ?? 16
                }

                Text {
                    objectName: "serverConsolePlayerCount"
                    text: page ? page.playerCountText : ""
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                    font.bold: true
                }

                Text {
                    objectName: "serverConsolePlayers"
                    Layout.fillWidth: true
                    text: page ? page.playersText : ""
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 11
                    elide: Text.ElideRight
                }

                FmIcon {
                    name: "memory"
                    color: Theme?.textSecondary ?? "transparent"
                    size: Theme?.iconSize ?? 16
                }

                Text {
                    objectName: "serverConsoleMemory"
                    text: page ? page.memoryText : ""
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                    font.bold: true
                }

                Text {
                    objectName: "serverConsoleExitInfo"
                    visible: page && page.exitInfo.length > 0 && !page.info.running
                    text: page ? page.exitInfo : ""
                    color: Theme?.textTertiary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 11
                }
            }
        }

        LogView {
            id: consoleLog
            objectName: "serverConsoleLog"
            Layout.fillWidth: true
            Layout.fillHeight: true
            maxLines: (typeof Servers !== "undefined" && Servers) ? Math.max(1, Servers.consoleMaxLines) : 5000
            emptyText: Tr?.map["server_waiting_start"] ?? "server_waiting_start"
            showToolbar: true
            showLineNumbers: false
            onCleared: {
                if (!page || page.loadingLog || page.clearing)
                    return
                if (page.live && page.serverId.length > 0)
                    Servers.clearConsole(page.serverId)
            }
        }

        RowLayout {
            objectName: "serverConsoleCommandRow"
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 8

            Text {
                objectName: "serverConsolePrompt"
                text: ">"
                color: Theme?.accent ?? "transparent"
                font.family: consoleLog.monoFamily
                font.pixelSize: Theme?.fontSizeBase ?? 12
                font.bold: true
            }

            FmTextField {
                id: commandField
                objectName: "serverConsoleCommandField"
                Layout.fillWidth: true
                placeholder: Tr?.map["server_cmd_placeholder"] ?? "server_cmd_placeholder"
                onAccepted: {
                    if (page)
                        page.sendCommand()
                }
            }

            FmButton {
                objectName: "serverConsoleSendButton"
                text: Tr?.map["server_send_btn"] ?? "server_send_btn"
                enabled: page && page.live && page.serverId.length > 0
                         && String(commandField.text).trim().length > 0
                onClicked: {
                    if (page)
                        page.sendCommand()
                }
            }
        }
    }

    FileDialog {
        id: exportDialog
        objectName: "serverConsoleExportDialog"
        title: Tr?.map["settings_log_export_title"] ?? "settings_log_export_title"
        fileMode: FileDialog.SaveFile
        defaultSuffix: "log"
        nameFilters: ["*.log"]
        onAccepted: {
            if (page && page.live && page.serverId.length > 0)
                Servers.exportConsole(page.serverId, page.localPath(selectedFile))
        }
    }

    // ── 退出质量确认（旧 L-34 的原生 Tk 自绘对话框；这里用自研件）──
    Item {
        objectName: "serverExitOverlay"
        anchors.fill: parent
        visible: page && page.exitDialogVisible
        z: 50

        Rectangle {
            anchors.fill: parent
            color: Theme?.bgDark ?? "transparent"
            opacity: 0.55
        }

        MouseArea { anchors.fill: parent }

        FmCard {
            objectName: "serverExitDialog"
            anchors.centerIn: parent
            width: Math.min(480, Math.max(320, parent.width - 2 * (Theme?.spacingXl ?? 24)))
            padding: Theme?.spacingLg ?? 16

            Text {
                objectName: "serverExitTitle"
                Layout.fillWidth: true
                text: page ? page.exitTitle : ""
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeTitle ?? 18
                font.bold: true
                wrapMode: Text.WordWrap
            }

            Text {
                objectName: "serverExitMessage"
                Layout.fillWidth: true
                text: Tr?.map["server_exit_question_msg"] ?? "server_exit_question_msg"
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeBase ?? 12
                wrapMode: Text.WordWrap
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme?.spacingSm ?? 8

                Item { Layout.fillWidth: true }

                FmButton {
                    objectName: "serverExitAnalyzeButton"
                    primary: true
                    text: Tr?.map["server_exit_no_analyze"] ?? "server_exit_no_analyze"
                    onClicked: {
                        if (page)
                            page.startAnalyze()
                    }
                }

                FmButton {
                    objectName: "serverExitYesButton"
                    primary: false
                    text: Tr?.map["server_exit_yes"] ?? "server_exit_yes"
                    onClicked: {
                        if (page)
                            page.exitDialogVisible = false
                    }
                }
            }
        }
    }

    // ── AI 分析中 ──
    Item {
        objectName: "serverCrashAnalyzingOverlay"
        anchors.fill: parent
        visible: page && page.analyzing
        z: 60

        Rectangle {
            anchors.fill: parent
            color: Theme?.bgDark ?? "transparent"
            opacity: 0.55
        }

        MouseArea { anchors.fill: parent }

        FmCard {
            objectName: "serverCrashAnalyzingCard"
            anchors.centerIn: parent
            width: Math.min(420, Math.max(300, parent.width - 2 * (Theme?.spacingXl ?? 24)))
            height: 180

            FmLoadingState {
                Layout.fillWidth: true
                Layout.fillHeight: true
                title: Tr?.map["crash_analyzing"] ?? "crash_analyzing"
            }
        }
    }

    // ── AI 分析结果（长文可滚动）──
    Item {
        objectName: "serverCrashResultOverlay"
        anchors.fill: parent
        visible: page && page.resultVisible
        z: 70

        Rectangle {
            anchors.fill: parent
            color: Theme?.bgDark ?? "transparent"
            opacity: 0.55
        }

        MouseArea { anchors.fill: parent }

        FmCard {
            objectName: "serverCrashResultDialog"
            anchors.centerIn: parent
            width: Math.min(720, Math.max(360, parent.width - 2 * (Theme?.spacingXl ?? 24)))
            height: Math.min(560, Math.max(280, parent.height - 2 * (Theme?.spacingXl ?? 24)))

            Text {
                objectName: "serverCrashResultTitle"
                Layout.fillWidth: true
                text: page ? page.resultTitle : ""
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeTitle ?? 18
                font.bold: true
            }

            FmScrollView {
                id: resultScroll
                objectName: "serverCrashResultScroll"
                Layout.fillWidth: true
                Layout.fillHeight: true

                Text {
                    objectName: "serverCrashResultText"
                    width: Math.max(0, resultScroll.availableWidth)
                    text: page ? page.resultText : ""
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                    wrapMode: Text.WordWrap
                }
            }

            RowLayout {
                Layout.fillWidth: true

                Item { Layout.fillWidth: true }

                FmButton {
                    objectName: "serverCrashResultClose"
                    primary: true
                    text: Tr?.map["confirm"] ?? "confirm"
                    onClicked: {
                        if (page) {
                            page.resultVisible = false
                            page.resultText = ""
                        }
                    }
                }
            }
        }
    }
}
