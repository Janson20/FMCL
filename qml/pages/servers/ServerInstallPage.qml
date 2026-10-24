// ServerInstallPage.qml —— 安装服务器（阶段 3 任务 3.9）。
//
// 承载：版本 ID 输入（旧界面只收正式版 ID）+ 正式版快速选择（每页 10 条）+
// 安装进度 + 完成/失败重试 + 整合包开服入口。整页永远 ready：取不到版本清单
// （离线）时仍要能手输版本 ID，这与 3.3 的安装页同一个理由。
//
// 数据全部来自 Servers 桥；本页不碰服务、不做业务判断。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../components"

FmPage {
    id: page
    objectName: "serverInstallPage"

    readonly property bool live: (typeof Servers !== "undefined" && Servers) ? true : false
    readonly property bool installing: page.live && Servers.busyKind === "installing"
    readonly property bool finished: page.live
                                     && (Servers.installState === "done" || Servers.installState === "failed")

    //: 整页始终 ready；可用版本清单的 loading/empty/error 做在卡片内部。
    contentState: "ready"

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

    function progressLabel() {
        if (!page.live || !page.installing)
            return ""
        var label = page.format("server_install_loading", {"version_id": String(Servers.installVersion || "")})
        if (Servers.progressTotal > 0)
            return label + "  " + String(Servers.progressCurrent) + " / " + String(Servers.progressTotal)
        return label
    }

    function resultText() {
        if (!page.live)
            return ""
        var version = String(Servers.installVersion || "")
        if (Servers.installState === "done")
            return page.format("server_installed", {"version": version})
        if (Servers.installState === "failed")
            return page.format("server_install_failed", {"version": version})
        return ""
    }

    function listState() {
        if (!page.live)
            return "error"
        if (Servers.availableState === "loading")
            return "loading"
        if (Servers.availableState === "empty")
            return "empty"
        if (Servers.availableState === "error")
            return "error"
        return "ready"
    }

    function applyRoute() {
        if (!page.live)
            return
        var wanted = page.routeParams ? String(page.routeParams.version || "") : ""
        Servers.activateInstall(wanted)
        if (wanted.length > 0)
            versionField.text = wanted
    }

    function openInstalled() {
        if (!page.live)
            return
        var version = String(Servers.installVersion || "")
        if (version.length > 0)
            Servers.openDetail(version)
        else
            Servers.goBack()
    }

    function backToList() {
        if (typeof Nav === "undefined" || !Nav)
            return
        if (!Nav.goBack())
            Nav.push("servers")
    }

    function activeRoute() {
        return typeof Nav !== "undefined" && Nav && String(Nav.currentRoute) === "servers/install"
    }

    Component.onCompleted: page.applyRoute()
    onRouteParamsChanged: page.applyRoute()

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

        function onInstallChanged() {
            if (!page)
                return
            //: 快速选择会从桥里改输入框；用户手输时桥也会回写同一个值。
            if (versionField.text !== String(Servers.versionInput))
                versionField.text = String(Servers.versionInput)
        }
    }

    Component.onDestruction: {
        if (serverConnections)
            serverConnections.target = null
    }

    ColumnLayout {
        objectName: "serverInstallPane"
        anchors.fill: parent
        spacing: Theme?.spacingMd ?? 10

        // ── 安装表单 ──
        FmCard {
            objectName: "serverInstallFormCard"
            Layout.fillWidth: true
            Layout.fillHeight: false

            RowLayout {
                objectName: "serverInstallFormRow"
                Layout.fillWidth: true
                Layout.fillHeight: false
                spacing: Theme?.spacingSm ?? 8

                FmTextField {
                    id: versionField
                    objectName: "serverInstallVersionInput"
                    Layout.fillWidth: true
                    label: Tr?.map["server_version_label"] ?? "server_version_label"
                    placeholder: Tr?.map["server_version_placeholder"] ?? "server_version_placeholder"
                    text: page.live ? String(Servers.versionInput) : ""
                    enabled: page.live && !page.installing
                    onEdited: function (value) {
                        if (page.live)
                            Servers.setVersionInput(value)
                    }
                    onAccepted: {
                        if (page.live && Servers.canInstall)
                            Servers.install()
                    }
                }

                FmButton {
                    objectName: "serverInstallStartButton"
                    primary: true
                    text: Tr?.map["server_install_btn_text"] ?? "server_install_btn_text"
                    iconName: "download"
                    enabled: page.live && Servers.canInstall
                    onClicked: {
                        if (page.live)
                            Servers.install()
                    }
                }

                FmButton {
                    objectName: "serverInstallModpackButton"
                    primary: false
                    text: Tr?.map["server_modpack_btn"] ?? "server_modpack_btn"
                    iconName: "modpack"
                    enabled: page.live && !page.installing
                    onClicked: {
                        if (page.live)
                            Servers.openModpack()
                    }
                }
            }

            Text {
                objectName: "serverInstallBetaHint"
                Layout.fillWidth: true
                Layout.fillHeight: false
                text: Tr?.map["server_eula_hint"] ?? "server_eula_hint"
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                wrapMode: Text.WordWrap
            }
        }

        // ── 正式版快速选择（每页 10 条）──
        FmCard {
            objectName: "serverAvailableCard"
            Layout.fillWidth: true
            Layout.fillHeight: true
            title: Tr?.map["server_quick_select_title"] ?? "server_quick_select_title"

            RowLayout {
                objectName: "serverAvailableToolbar"
                Layout.fillWidth: true
                Layout.fillHeight: false
                spacing: Theme?.spacingSm ?? 8

                Text {
                    objectName: "serverAvailableCount"
                    text: page.live
                          ? page.format("server_version_count", {"count": Servers.availableTotal})
                          : ""
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                    Layout.alignment: Qt.AlignVCenter
                }

                Item { Layout.fillWidth: true }

                FmToolButton {
                    objectName: "serverAvailableRefresh"
                    iconName: "refresh"
                    enabled: page.live && !page.installing
                    onClicked: {
                        if (page.live)
                            Servers.refreshAvailable()
                    }
                }

                FmPagination {
                    id: pager
                    objectName: "serverAvailablePager"
                    Layout.alignment: Qt.AlignVCenter
                    page: page.live ? Servers.availablePage : 1
                    pageCount: page.live ? Servers.availablePageCount : 1
                    enabled: page.live && !page.installing
                    onPageRequested: function (target) {
                        if (page.live)
                            Servers.setAvailablePage(target)
                    }
                }
            }

            FmLoadingState {
                objectName: "serverAvailableLoading"
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: page.listState() === "loading"
                title: Tr?.map["plugin_state_loading"] ?? "plugin_state_loading"
            }

            FmEmptyState {
                objectName: "serverAvailableEmpty"
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: page.listState() === "empty"
                title: Tr?.map["server_no_available"] ?? "server_no_available"
                iconName: "download"
                actionText: Tr?.map["version_refresh"] ?? "version_refresh"
                onActionTriggered: {
                    if (page.live)
                        Servers.refreshAvailable()
                }
            }

            FmErrorState {
                objectName: "serverAvailableError"
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: page.listState() === "error"
                title: Tr?.map["plugin_state_error"] ?? "plugin_state_error"
                description: page.live ? String(Servers.availableError) : ""
                retryText: Tr?.map["version_refresh"] ?? "version_refresh"
                onRetried: {
                    if (page.live)
                        Servers.refreshAvailable()
                }
            }

            ListView {
                id: availableList
                objectName: "serverAvailableList"
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: page.listState() === "ready"
                clip: true
                spacing: Theme?.spacingXs ?? 4
                model: page.live && page.listState() === "ready" ? Servers.availableRows : []
                ScrollBar.vertical: FmScrollBar {}

                delegate: FmButton {
                    id: versionRow
                    objectName: "serverAvailableRow"
                    required property var modelData
                    width: availableList.width
                    height: 34
                    primary: false
                    text: String(modelData.id || "")
                    enabled: page.live && !page.installing
                    onClicked: {
                        if (page.live)
                            Servers.quickSelect(String(modelData.id || ""))
                    }
                }
            }
        }

        // ── 进度与结果 ──
        FmCard {
            objectName: "serverInstallProgressCard"
            Layout.fillWidth: true
            Layout.fillHeight: false
            visible: page.installing || page.finished

            FmProgressBar {
                objectName: "serverInstallProgressBar"
                Layout.fillWidth: true
                Layout.fillHeight: false
                visible: page.installing
                value: page.live ? Servers.progress : 0
                indeterminate: page.live ? !Servers.progressDeterminate : true
                label: page.progressLabel()
                showPercent: page.live && Servers.progressDeterminate
            }

            FmInfoBar {
                objectName: "serverInstallResultBar"
                Layout.fillWidth: true
                Layout.fillHeight: false
                visible: page.finished
                text: page.resultText()
                level: page.live && Servers.installState === "done" ? "success" : "error"
            }

            Text {
                objectName: "serverInstallErrorDetail"
                Layout.fillWidth: true
                Layout.fillHeight: false
                visible: page.finished && page.live
                         && Servers.installState === "failed"
                         && String(Servers.installError || "").length > 0
                text: page.live ? String(Servers.installError || "") : ""
                color: Theme?.error ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                wrapMode: Text.WordWrap
            }

            RowLayout {
                objectName: "serverInstallResultActions"
                Layout.fillWidth: true
                Layout.fillHeight: false
                spacing: Theme?.spacingSm ?? 8
                visible: page.finished

                FmButton {
                    objectName: "serverInstallViewButton"
                    primary: page.live && Servers.installState === "done"
                    text: Tr?.map["server_detail_title"] ?? "server_detail_title"
                    iconName: "info"
                    visible: page.live && Servers.installState === "done"
                    onClicked: page.openInstalled()
                }

                FmButton {
                    objectName: "serverInstallRetryButton"
                    primary: true
                    text: Tr?.map["version_install_retry"] ?? "version_install_retry"
                    iconName: "refresh"
                    visible: page.live && Servers.installState === "failed"
                    onClicked: {
                        if (page.live)
                            Servers.install()
                    }
                }

                FmButton {
                    objectName: "serverInstallBackButton"
                    primary: false
                    text: Tr?.map["version_back_to_list"] ?? "version_back_to_list"
                    onClicked: page.backToList()
                }

                Item { Layout.fillWidth: true }
            }
        }
    }
}
