// ServerDetailPage.qml —— 服务器详情与生命周期（阶段 3 任务 3.9）。
//
// 承载：启动 / 停止 / 加入服务器、进入控制台、每服独立最大内存、打开目录、删除、
// 运行状态。配置编辑入口接 3.11，模组/资源包入口接 3.12。
//
// 数据全部来自 Servers 桥；本页只绑属性、发槽调用、按状态显示。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../components"
import "../../components/dialogs"

FmPage {
    id: page
    objectName: "serverDetailPage"

    readonly property bool live: (typeof Servers !== "undefined" && Servers) ? true : false
    readonly property var info: (page && page.live) ? Servers.selectedInfo : ({})
    readonly property string serverId: (page && page.info) ? String(page.info.id || "") : ""
    readonly property bool installed: (page && page.info) ? !!page.info.installed : false
    readonly property bool running: (page && page.live && page.info) ? !!page.info.running : false
    property string deleteTarget: ""

    contentState: (!page || !page.live) ? "error"
                  : (Servers.listState === "loading" ? "loading"
                  : (!Servers.hasSelection || !page.installed ? "error" : "ready"))

    loadingText: Tr?.map["plugin_state_loading"] ?? "plugin_state_loading"
    errorText: Tr?.map["plugin_state_error"] ?? "plugin_state_error"
    errorDetail: (page && page.live && Servers.hasSelection && !page.installed)
                 ? (Tr?.map["server_no_installed"] ?? "server_no_installed")
                 : ((page && page.live && !Servers.hasSelection)
                    ? (Tr?.map["server_no_version_selected"] ?? "server_no_version_selected")
                    : ((page && page.live) ? String(Servers.listError) : ""))
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

    function applyRoute() {
        if (!page || !page.live)
            return
        var wanted = page.routeParams ? String(page.routeParams.server || "") : ""
        if (wanted.length > 0)
            Servers.activateDetail(wanted)
        else if (String(Servers.selected || "").length > 0)
            Servers.activateDetail(String(Servers.selected))
        else
            Servers.activateList()
    }

    function activeRoute() {
        return typeof Nav !== "undefined" && Nav && String(Nav.currentRoute) === "servers/detail"
    }

    function memoryIndex() {
        if (!page || !page.live)
            return 0
        var current = String(Servers.memory || "2G")
        var options = Servers.memoryOptions
        for (var index = 0; index < options.length; index++) {
            if (String(options[index]) === current)
                return index
        }
        return 1
    }

    function statusText() {
        if (!page || !page.live)
            return ""
        if (page.running)
            return page.format("server_status_running", {"version": page.serverId})
        if (Servers.busyKind === "starting")
            return page.format("server_starting", {"version": page.serverId})
        if (Servers.busyKind === "stopping")
            return page.t("server_stopping")
        return page.t("server_not_running")
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
    }

    Connections {
        id: selectionConnections
        target: (typeof Servers !== "undefined" && Servers) ? Servers : null

        //: 删除成功后选中被清空（或外部把服务器目录删了）——详情页没有内容可显示，
        //: 自动回列表，而不是停在一张“请先选择服务器”的错误页上。
        function onSelectionChanged() {
            if (!page || !page.activeRoute())
                return
            if (!Servers.hasSelection)
                Servers.goBack()
        }
    }

    Component.onDestruction: {
        if (serverConnections)
            serverConnections.target = null
        if (selectionConnections)
            selectionConnections.target = null
    }

    ColumnLayout {
        objectName: "serverDetailPane"
        anchors.fill: parent
        spacing: Theme?.spacingMd ?? 10

        // ── 标题行 ──
        RowLayout {
            objectName: "serverDetailHeader"
            Layout.fillWidth: true
            Layout.fillHeight: false
            spacing: Theme?.spacingSm ?? 8

            FmToolButton {
                objectName: "serverDetailBack"
                iconName: "arrow-left"
                onClicked: {
                    if (page.live)
                        Servers.goBack()
                }
            }

            Text {
                objectName: "serverDetailName"
                Layout.fillWidth: true
                text: (page && page.serverId.length > 0)
                      ? page.serverId
                      : (Tr?.map["server_detail_title"] ?? "server_detail_title")
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeTitle ?? 18
                font.bold: true
                elide: Text.ElideRight
                Layout.alignment: Qt.AlignVCenter
            }

            FmTag {
                objectName: "serverDetailLoaderTag"
                visible: page && String(page.info.loader || "").length > 0
                text: page ? String(page.info.loader || "") : ""
                level: "accent"
            }

            FmTag {
                objectName: "serverDetailStatusTag"
                text: page ? page.statusText() : ""
                level: (page && page.running) ? "success" : "warning"
            }

            FmButton {
                objectName: "serverDetailRefreshButton"
                primary: false
                text: Tr?.map["version_refresh"] ?? "version_refresh"
                iconName: "refresh"
                enabled: page && page.live && !Servers.busy
                onClicked: page.applyRoute()
            }
        }

        // ── 生命周期 ──
        FmCard {
            objectName: "serverLifecycleCard"
            Layout.fillWidth: true
            Layout.fillHeight: false
            title: Tr?.map["server_tab"] ?? "server_tab"
            subtitle: page ? page.statusText() : ""

            RowLayout {
                objectName: "serverLifecycleActions"
                Layout.fillWidth: true
                Layout.fillHeight: false
                spacing: Theme?.spacingSm ?? 8

                FmButton {
                    objectName: "serverDetailStartButton"
                    primary: true
                    text: Tr?.map["server_start_btn"] ?? "server_start_btn"
                    iconName: "play"
                    enabled: page && page.live && Servers.canStart
                    loading: page && page.live && Servers.busyKind === "starting"
                    onClicked: {
                        if (page.live)
                            Servers.start()
                    }
                }

                FmButton {
                    objectName: "serverDetailStopButton"
                    danger: true
                    text: Tr?.map["server_stop_btn"] ?? "server_stop_btn"
                    iconName: "stop"
                    enabled: page && page.live && Servers.canStop
                    loading: page && page.live && Servers.busyKind === "stopping"
                    onClicked: {
                        if (page.live)
                            Servers.stop()
                    }
                }

                FmButton {
                    objectName: "serverDetailJoinButton"
                    primary: false
                    text: Tr?.map["server_join_btn"] ?? "server_join_btn"
                    iconName: "players"
                    enabled: page && page.live && Servers.canJoin
                    loading: page && page.live && Servers.busyKind === "joining"
                    onClicked: {
                        if (page.live)
                            Servers.join(page.serverId)
                    }
                }

                FmButton {
                    objectName: "serverDetailConsoleButton"
                    primary: false
                    text: Tr?.map["server_log_title"] ?? "server_log_title"
                    iconName: "monitor"
                    enabled: page && page.live && page.serverId.length > 0
                    onClicked: {
                        if (page.live && page.serverId.length > 0)
                            Servers.openConsole(page.serverId)
                    }
                }

                FmButton {
                    objectName: "serverDetailConfigButton"
                    primary: false
                    text: Tr?.map["server_config_panel_btn"] ?? "server_config_panel_btn"
                    iconName: "slider"
                    enabled: page && page.live && page.serverId.length > 0
                    onClicked: {
                        if (page.live && page.serverId.length > 0
                                && typeof ServerConfig !== "undefined" && ServerConfig)
                            ServerConfig.open(page.serverId)
                    }
                }

                Item { Layout.fillWidth: true }
            }

            RowLayout {
                objectName: "serverMemoryRow"
                Layout.fillWidth: true
                Layout.fillHeight: false
                spacing: Theme?.spacingSm ?? 8

                Text {
                    objectName: "serverMemoryLabel"
                    text: Tr?.map["server_max_memory_label"] ?? "server_max_memory_label"
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                    Layout.alignment: Qt.AlignVCenter
                }

                FmComboBox {
                    id: memoryCombo
                    objectName: "serverMemoryCombo"
                    Layout.preferredWidth: 130
                    model: page && page.live ? Servers.memoryOptions : []
                    currentIndex: page ? page.memoryIndex() : 0
                    enabled: page && page.live && !Servers.busy
                    onActivated: {
                        if (page.live)
                            Servers.setMemory(String(currentText))
                    }
                }
            }

            Text {
                objectName: "serverPortHint"
                Layout.fillWidth: true
                Layout.fillHeight: false
                text: Tr?.map["server_port_hint"] ?? "server_port_hint"
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                wrapMode: Text.WordWrap
            }

            Text {
                objectName: "serverPortCurrent"
                Layout.fillWidth: true
                Layout.fillHeight: false
                visible: page && page.live && Number(page.info.port || 25565) !== 25565
                text: page ? page.format("server_port_not_default", {"port": Number(page.info.port || 25565)}) : ""
                color: Theme?.warning ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                font.bold: true
                wrapMode: Text.WordWrap
            }

            Text {
                objectName: "serverEulaHint"
                Layout.fillWidth: true
                Layout.fillHeight: false
                text: Tr?.map["server_eula_hint"] ?? "server_eula_hint"
                color: Theme?.warning ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                wrapMode: Text.WordWrap
            }
        }

        // ── 目录与删除 ──
        FmCard {
            objectName: "serverInfoCard"
            Layout.fillWidth: true
            Layout.fillHeight: false
            title: Tr?.map["server_detail_title"] ?? "server_detail_title"

            Text {
                objectName: "serverDetailPath"
                Layout.fillWidth: true
                Layout.fillHeight: false
                text: page ? String(page.info.path || "") : ""
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                wrapMode: Text.WrapAnywhere
            }

            RowLayout {
                objectName: "serverInfoActions"
                Layout.fillWidth: true
                Layout.fillHeight: false
                spacing: Theme?.spacingSm ?? 8

                FmButton {
                    objectName: "serverDetailFolderButton"
                    primary: false
                    text: Tr?.map["server_config_open_folder"] ?? "server_config_open_folder"
                    iconName: "folder-open"
                    enabled: page && page.live && page.serverId.length > 0
                    onClicked: {
                        if (page.live)
                            Servers.openDir(page.serverId)
                    }
                }

                FmButton {
                    objectName: "serverDetailModsButton"
                    primary: false
                    text: Tr?.map["server_mod_manage_btn"] ?? "server_mod_manage_btn"
                    iconName: "mods"
                    enabled: page && page.live && page.serverId.length > 0
                    onClicked: {
                        if (page.live && page.serverId.length > 0 && typeof Nav !== "undefined" && Nav)
                            Nav.push("servers/detail/mods", {"version": page.serverId})
                    }
                }

                FmButton {
                    objectName: "serverDetailPacksButton"
                    primary: false
                    text: Tr?.map["resource_packs"] ?? "resource_packs"
                    iconName: "resourcepack"
                    enabled: page && page.live && page.serverId.length > 0
                    onClicked: {
                        if (page.live && page.serverId.length > 0 && typeof Nav !== "undefined" && Nav)
                            Nav.push("servers/detail/packs", {"version": page.serverId})
                    }
                }

                Item { Layout.fillWidth: true }

                FmButton {
                    objectName: "serverDetailDeleteButton"
                    danger: true
                    text: Tr?.map["version_row_delete"] ?? "version_row_delete"
                    iconName: "trash"
                    enabled: page && page.live && Servers.canRemove
                    onClicked: {
                        if (page.live && page.serverId.length > 0)
                            page.deleteTarget = page.serverId
                    }
                }
            }
        }

        Item { Layout.fillHeight: true }
    }

    Item {
        objectName: "serverDetailOverlayHost"
        anchors.fill: parent

        Loader {
            objectName: "serverDetailDeleteLoader"
            anchors.fill: parent
            active: page ? page.deleteTarget.length > 0 : false
            sourceComponent: deleteComponent
        }
    }

    Component {
        id: deleteComponent

        ConfirmDialog {
            objectName: "serverDetailDeleteDialog"
            request: ({
                "title": page ? page.t("server_delete_confirm_title") : "",
                "message": page ? page.format("server_delete_confirm_msg", {"version": page.deleteTarget}) : "",
                "default": false
            })
            onAnswered: function (value) {
                var target = page.deleteTarget;
                page.deleteTarget = "";
                if (value === true && page.live && target.length > 0)
                    Servers.remove(target);
            }
        }
    }
}
