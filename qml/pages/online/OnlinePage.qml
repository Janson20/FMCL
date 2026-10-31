// OnlinePage.qml —— 陶瓦联机（阶段 3 任务 3.13）。
//
// 布局与旧 Tk 标签页保持一致：左侧是环境 / 局域网 / 创建 / 加入 / 大厅 / 提示 /
// 兼容七张卡，右侧是 EasyTier 运行日志。5 条路由都进这一页，routeId 只负责
// 把左侧滚到对应卡片（online/log 聚焦日志），页头固定为"陶瓦联机"。
//
// 纪律：颜色只来自 Theme.*（R8）、图标走 FmIcon（R2）、文案走 Tr.map（R3/R4）、
// 自研件一律用 qml/components 里的 Fm*（R9）。状态与动作全部来自 Online 桥；
// 本页不判断"能不能创建/要不要删目录"，只绑定 can* 属性。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../../components"
import "../../components/dialogs"

FmPage {
    id: page
    objectName: "onlinePage"

    //: 子路由只做滚动定位；页头始终是联机主页的标题与描述。
    title: page.t("online_title")
    description: page.t("online_description")
    iconName: "online"

    readonly property bool bridgeReady: (typeof Online !== "undefined") && Online !== null
    property int lastLogSeq: 0

    contentState: !page.bridgeReady
                  ? "error"
                  : (Online.platformSupported ? "ready" : "empty")
    errorText: page.t("plugin_state_error")
    emptyText: page.t("online_unsupported_platform")
    emptyDetail: ""

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

    function createFromForm() {
        if (page.bridgeReady && Online.canCreate)
            Online.createLobby(portField.text)
    }

    function joinFromForm() {
        if (page.bridgeReady && Online.canJoin)
            Online.joinLobby(codeField.text)
    }

    function copyCode() {
        if (!page.bridgeReady || !Online.hasLobby)
            return
        var error = Online.copyLobbyCode()
        if (typeof Shell === "undefined" || !Shell)
            return
        if (String(error).length === 0)
            Shell.setStatus(page.format("online_copy_success", { "code": Online.lobbyCode }), "success")
        else
            Shell.setStatus(page.format("copy_failed", { "error": String(error) }), "error")
    }

    function tagLevel(level) {
        if (level === "success" || level === "warning" || level === "error"
                || level === "accent" || level === "neutral")
            return level
        return "accent"
    }

    function staleMessage() {
        if (!page.bridgeReady)
            return ""
        var paths = Online.staleVersions || []
        var lines = [page.format("version_count", { "count": paths.length })]
        for (var i = 0; i < paths.length; i++)
            lines.push(String(paths[i]))
        lines.push(page.t("ach_confirm_step1_msg"))
        return lines.join("\n\n")
    }

    function focusRoute() {
        if (!page || !page.bridgeReady)
            return
        var rid = String(page.routeId || "")
        if (rid === "online/log") {
            logCard.forceActiveFocus()
            return
        }
        var target = 0
        if (rid === "online/create")
            target = createCard.y
        else if (rid === "online/join")
            target = joinCard.y
        else if (rid === "online/lan")
            target = lanCard.y
        var flick = leftScroll.contentItem
        if (flick && flick.contentY !== undefined)
            flick.contentY = Math.max(0, target - 8)
    }

    Component.onCompleted: {
        if (!page.bridgeReady)
            return
        var snapshot = Online.logSnapshot()
        if (snapshot && snapshot.lines && snapshot.lines.length > 0) {
            page.lastLogSeq = snapshot.seq || 0
            logView.appendLines(snapshot.lines)
        }
        activateTimer.restart()
        Qt.callLater(function () {
            if (page)
                page.focusRoute()
        })
    }

    onVisibleChanged: {
        if (visible)
            activateTimer.restart()
    }

    Timer {
        id: activateTimer
        interval: 80
        repeat: false
        onTriggered: {
            if (page)
                page.ensureActivated()
        }
    }

    function ensureActivated() {
        if (!page || !page.bridgeReady)
            return
        Online.activate()
    }

    onRouteIdChanged: Qt.callLater(function () {
        if (page)
            page.focusRoute()
    })

    RowLayout {
        anchors.fill: parent
        spacing: Theme?.spacingMd ?? 0

        FmScrollView {
            id: leftScroll
            Layout.preferredWidth: Math.max(360, Math.min(520, page.width * 0.42))
            Layout.fillHeight: true
            contentWidth: availableWidth

            ColumnLayout {
                id: leftColumn
                width: leftScroll.availableWidth - 2 * (Theme?.spacingMd ?? 0)
                x: Theme?.spacingMd ?? 0
                spacing: Theme?.spacingMd ?? 0

                // ── 环境准备 ─────────────────────────────────────
                FmCard {
                    id: envCard
                    objectName: "onlineEnvCard"
                    Layout.fillWidth: true
                    title: page.t("online_env_title")

                    Text {
                        objectName: "onlineEnvDesc"
                        Layout.fillWidth: true
                        text: page.t("online_env_desc")
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 11
                        wrapMode: Text.WordWrap
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme?.spacingSm ?? 0

                        FmTag {
                            objectName: "onlineEnvTag"
                            text: page.t(Online.envTextKey)
                            level: page.tagLevel(Online.envLevel)
                            iconName: "package"
                        }

                        Item { Layout.fillWidth: true }

                        FmButton {
                            objectName: "onlineInstallButton"
                            text: page.t(Online.installButtonKey)
                            primary: Online.canInstall
                            loading: Online.installRunning
                            enabled: Online.canInstall
                            onClicked: Online.installEnv()
                        }
                    }
                }

                // ── 局域网世界 ───────────────────────────────────
                FmCard {
                    id: lanCard
                    objectName: "onlineLanCard"
                    Layout.fillWidth: true
                    title: page.t("online_discover_title")

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme?.spacingSm ?? 0

                        Text {
                            objectName: "onlineLanState"
                            Layout.fillWidth: true
                            text: Online.scanning
                                  ? page.t("online_discovering")
                                  : (Online.worlds.length > 0 ? "" : page.t("online_no_worlds"))
                            color: Theme?.textSecondary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 11
                            wrapMode: Text.WordWrap
                        }

                        FmButton {
                            objectName: "onlineScanButton"
                            text: Online.scanning
                                  ? page.t("online_discovering")
                                  : page.t("online_discover_btn")
                            primary: false
                            enabled: Online.canScan
                            onClicked: Online.scanWorlds()
                        }
                    }

                    ColumnLayout {
                        objectName: "onlineWorldList"
                        Layout.fillWidth: true
                        visible: Online.worlds.length > 0
                        spacing: Theme?.spacingXs ?? 0

                        Repeater {
                            model: Online.worlds
                            delegate: RowLayout {
                                Layout.fillWidth: true
                                spacing: Theme?.spacingSm ?? 0

                                FmIcon {
                                    name: "world"
                                    size: Theme?.fontSizeSmall ?? 12
                                    color: Theme?.textSecondary ?? "transparent"
                                }

                                Text {
                                    Layout.fillWidth: true
                                    text: modelData.motd.length > 0 ? modelData.motd : "?"
                                    color: Theme?.textPrimary ?? "transparent"
                                    font.pixelSize: Theme?.fontSizeSmall ?? 11
                                    elide: Text.ElideRight
                                }

                                Text {
                                    text: ":" + modelData.port
                                    color: Theme?.textSecondary ?? "transparent"
                                    font.pixelSize: Theme?.fontSizeSmall ?? 11
                                }
                            }
                        }
                    }
                }

                // ── 创建大厅 ─────────────────────────────────────
                FmCard {
                    id: createCard
                    objectName: "onlineCreateCard"
                    Layout.fillWidth: true
                    title: page.t("online_create_title")

                    Text {
                        Layout.fillWidth: true
                        text: page.t("online_create_desc")
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 11
                        wrapMode: Text.WordWrap
                    }

                    FmTextField {
                        id: portField
                        objectName: "onlinePortField"
                        Layout.fillWidth: true
                        label: page.t("online_port_label")
                        placeholder: page.t("online_port_placeholder")
                        text: "25565"
                        enabled: Online.canCreate
                        onAccepted: page.createFromForm()
                    }

                    FmButton {
                        objectName: "onlineCreateButton"
                        Layout.fillWidth: true
                        text: page.t("online_create_lobby")
                        primary: !Online.canInstall
                        enabled: Online.canCreate
                        loading: Online.lobbyState === "creating"
                        onClicked: page.createFromForm()
                    }
                }

                // ── 加入大厅 ─────────────────────────────────────
                FmCard {
                    id: joinCard
                    objectName: "onlineJoinCard"
                    Layout.fillWidth: true
                    title: page.t("online_join_title")

                    Text {
                        Layout.fillWidth: true
                        text: page.t("online_join_desc")
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 11
                        wrapMode: Text.WordWrap
                    }

                    FmTextField {
                        id: codeField
                        objectName: "onlineCodeField"
                        Layout.fillWidth: true
                        label: page.t("online_lobby_code_label")
                        placeholder: page.t("online_lobby_code_placeholder")
                        enabled: Online.canJoin
                        onAccepted: page.joinFromForm()
                    }

                    FmButton {
                        objectName: "onlineJoinButton"
                        Layout.fillWidth: true
                        text: page.t("online_join_lobby")
                        primary: false
                        enabled: Online.canJoin
                        loading: Online.lobbyState === "joining"
                        onClicked: page.joinFromForm()
                    }
                }

                // ── 大厅状态 ─────────────────────────────────────
                FmCard {
                    id: lobbyCard
                    objectName: "onlineLobbyCard"
                    Layout.fillWidth: true
                    title: page.t("online_lobby_info_title")

                    Text {
                        objectName: "onlineLobbyCode"
                        Layout.fillWidth: true
                        text: Online.hasLobby ? Online.lobbyCode : page.t("online_no_lobby")
                        color: Online.hasLobby
                               ? (Online.isHost ? (Theme?.success ?? "transparent")
                                                : (Theme?.accent ?? "transparent"))
                               : (Theme?.textSecondary ?? "transparent")
                        font.pixelSize: Theme?.fontSizeLarge ?? 14
                        font.bold: Online.hasLobby
                        wrapMode: Text.WrapAnywhere
                    }

                    FmTag {
                        objectName: "onlineLobbyStatus"
                        visible: Online.lobbyStatusText.length > 0
                        text: Online.lobbyStatusText
                        level: page.tagLevel(Online.lobbyStatusLevel)
                        iconName: "info"
                    }

                    ColumnLayout {
                        objectName: "onlineMemberList"
                        Layout.fillWidth: true
                        spacing: Theme?.spacingXs ?? 0

                        Repeater {
                            model: Online.members
                            delegate: RowLayout {
                                Layout.fillWidth: true
                                spacing: Theme?.spacingXs ?? 0

                                FmIcon {
                                    name: modelData.host ? "crown" : "account"
                                    size: Theme?.fontSizeSmall ?? 12
                                    color: modelData.host
                                           ? (Theme?.warning ?? "transparent")
                                           : (Theme?.textSecondary ?? "transparent")
                                }

                                Text {
                                    text: modelData.name
                                    color: Theme?.textPrimary ?? "transparent"
                                    font.pixelSize: Theme?.fontSizeSmall ?? 11
                                    elide: Text.ElideRight
                                    Layout.maximumWidth: 160
                                }

                                Text {
                                    text: "·"
                                    color: Theme?.textSecondary ?? "transparent"
                                    font.pixelSize: Theme?.fontSizeSmall ?? 11
                                }

                                Text {
                                    Layout.fillWidth: true
                                    text: modelData.vendor
                                    color: Theme?.textSecondary ?? "transparent"
                                    font.pixelSize: Theme?.fontSizeSmall ?? 11
                                    elide: Text.ElideRight
                                }
                            }
                        }

                        Text {
                            objectName: "onlineNoMembers"
                            Layout.fillWidth: true
                            visible: Online.members.length === 0
                            text: page.t("online_no_members")
                            color: Theme?.textSecondary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 11
                        }

                        RowLayout {
                            objectName: "onlineGuestLatency"
                            Layout.fillWidth: true
                            visible: Online.guestLatency > 0
                            spacing: Theme?.spacingXs ?? 0

                            FmIcon {
                                name: "timer"
                                size: Theme?.fontSizeSmall ?? 12
                                color: Theme?.textSecondary ?? "transparent"
                            }

                            Text {
                                text: Online.guestLatency + " ms"
                                color: Theme?.textSecondary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 11
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme?.spacingSm ?? 0

                        FmButton {
                            objectName: "onlineCopyButton"
                            Layout.fillWidth: true
                            text: page.t("online_copy_code")
                            primary: false
                            enabled: Online.hasLobby
                            onClicked: page.copyCode()
                        }

                        FmButton {
                            objectName: "onlineLeaveButton"
                            Layout.fillWidth: true
                            text: page.t("online_leave_lobby")
                            danger: true
                            enabled: Online.canLeave
                            onClicked: Online.leaveLobby()
                        }
                    }
                }

                // ── 提示 / 兼容 ──────────────────────────────────
                FmCard {
                    id: tipsCard
                    objectName: "onlineTipsCard"
                    Layout.fillWidth: true
                    title: page.t("online_tips_title")

                    Text {
                        Layout.fillWidth: true
                        text: page.t("online_tips_content")
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 11
                        wrapMode: Text.WordWrap
                    }
                }

                FmCard {
                    objectName: "onlineCompatCard"
                    Layout.fillWidth: true
                    title: page.t("online_compat_title")

                    Text {
                        Layout.fillWidth: true
                        text: page.t("online_compat_content")
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 11
                        wrapMode: Text.WordWrap
                    }
                }
            }
        }

        // ── 运行日志 ─────────────────────────────────────────
        FmCard {
            id: logCard
            objectName: "onlineLogCard"
            Layout.fillWidth: true
            Layout.fillHeight: true
            title: page.t("online_output_title")
            subtitle: page.t("online_log_note")

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: Theme?.spacingSm ?? 0

                RowLayout {
                    objectName: "onlineEtStatus"
                    Layout.fillWidth: true
                    spacing: Theme?.spacingXs ?? 0

                    FmIcon {
                        name: "network"
                        size: Theme?.fontSizeSmall ?? 12
                        color: Online.etRunning
                               ? (Theme?.success ?? "transparent")
                               : (Theme?.textSecondary ?? "transparent")
                    }

                    Text {
                        text: Online.etStatusText
                        color: Online.etRunning
                               ? (Theme?.success ?? "transparent")
                               : (Theme?.textSecondary ?? "transparent")
                        font.pixelSize: Theme?.fontSizeSmall ?? 11
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }
                }

                LogView {
                    id: logView
                    objectName: "onlineLogView"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    maxLines: 5000
                    showToolbar: false
                }
            }
        }
    }

    Connections {
        target: page.bridgeReady ? Online : null

        function onLogLines(lines, seq) {
            //: 页面可能在信号排队后就被销毁（切页/退出）；此时 page 与 logView
            //: 已经不在了，晚到的日志直接丢弃（tests/test_shell_qml.py 的
            //: "加载/走查零报错" 就是被这一条钉出来的）。
            if (!page || !logView)
                return
            if (seq <= page.lastLogSeq)
                return
            page.lastLogSeq = seq
            logView.appendLines(lines)
        }

        function onStatusMessage(text, level) {
            if (typeof Shell !== "undefined" && Shell)
                Shell.setStatus(String(text), String(level))
        }

        function onNoticeRequested(title, message, level) {
            if (typeof Dialogs === "undefined" || !Dialogs)
                return
            if (String(message).length === 0)
                return
            Dialogs.notify({
                "title": String(title),
                "message": String(message),
                "level": String(level),
                "icon": "online"
            })
        }
    }

    Loader {
        objectName: "onlineStaleLoader"
        anchors.fill: parent
        active: page.bridgeReady && Online.staleVersions.length > 0
        sourceComponent: staleComponent
    }

    Component {
        id: staleComponent

        ConfirmDialog {
            objectName: "onlineStaleDialog"
            modalScrim: true
            request: ({
                "title": page.t("backup_delete_confirm_title"),
                "message": page.staleMessage(),
                "default": false
            })
            onAnswered: function (value) {
                if (value === true)
                    Online.cleanupStaleVersions()
                else
                    Online.dismissStaleVersions()
            }
        }
    }
}
