// ServersPage.qml —— 服务器列表（阶段 3 任务 3.9；IA 见 docs/refactor/02 第 4.4 节）。
//
// 这一页承载：已安装服务器 + 数量 + 运行状态 + 安装入口 + 整合包开服入口。
// 选中一台服务器后可以进详情页（生命周期与每服内存都在那里）。
//
// 数据全部来自 Servers 桥（app/bridges/servers_bridge.py）—— 本文件不碰服务、
// 不做业务判断：列表排序、运行状态、可用性判定、删除前停服都在服务层。
//
// 三态：listState 的 loading / empty / error 直接交给 FmPage；空态的唯一动作是
// “安装服务器”。刷新按钮与安装按钮在 ready 态的工具条里。
//
// 纪律：颜色只来自 Theme.*（R8）、图标走 FmIcon/FmToolButton（R2）、文案走
// Tr.map（R3/R4）、只用 qml/components 里的 Fm*（R9）。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../components"
import "../../components/dialogs"

FmPage {
    id: page
    objectName: "serversPage"

    readonly property bool live: (typeof Servers !== "undefined" && Servers) ? true : false
    property string deleteTarget: ""

    contentState: !page.live ? "error"
                  : (Servers.listState === "loading" ? "loading"
                  : (Servers.listState === "error" ? "error"
                  : (Servers.listState === "empty" ? "empty" : "ready")))

    loadingText: Tr?.map["plugin_state_loading"] ?? "plugin_state_loading"
    emptyText: Tr?.map["server_no_installed"] ?? "server_no_installed"
    emptyActionText: Tr?.map["server_install"] ?? "server_install"
    emptyActionPrimary: true
    emptyDetail: ""
    //: 安装入口必须一直可见：空态是唯一没有工具条的状态，所以把动作放到空态上。
    errorText: Tr?.map["plugin_state_error"] ?? "plugin_state_error"
    errorDetail: page.live ? String(Servers.listError) : ""
    retryText: Tr?.map["version_refresh"] ?? "version_refresh"

    onRetried: page.reload()
    onEmptyActionTriggered: {
        if (page.live)
            Servers.openInstall()
    }

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

    function reload() {
        if (page.live)
            Servers.activateList()
    }

    function activeRoute() {
        return typeof Nav !== "undefined" && Nav && String(Nav.currentRoute) === "servers"
    }

    Component.onCompleted: page.reload()

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

    Component.onDestruction: {
        if (serverConnections)
            serverConnections.target = null
    }

    property string countText: page.format("server_version_count", {"count": page.live ? Servers.count : 0})

    ColumnLayout {
        objectName: "serversPane"
        anchors.fill: parent
        spacing: Theme?.spacingSm ?? 8

        RowLayout {
            objectName: "serversToolbar"
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 8

            Text {
                objectName: "serversCount"
                text: page.countText
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeBase ?? 12
                Layout.alignment: Qt.AlignVCenter
            }

            Item { Layout.fillWidth: true }

            FmButton {
                objectName: "serversRefreshButton"
                primary: false
                text: Tr?.map["version_refresh"] ?? "version_refresh"
                iconName: "refresh"
                enabled: page.live
                onClicked: page.reload()
            }

            FmButton {
                objectName: "serversInstallButton"
                primary: true
                text: Tr?.map["server_install"] ?? "server_install"
                iconName: "download"
                enabled: page.live
                onClicked: {
                    if (page.live)
                        Servers.openInstall()
                }
            }

            FmButton {
                objectName: "serversModpackButton"
                primary: false
                text: Tr?.map["server_modpack_btn"] ?? "server_modpack_btn"
                iconName: "modpack"
                enabled: page.live
                onClicked: {
                    if (page.live)
                        Servers.openModpack()
                }
            }
        }

        ListView {
            id: serverList
            objectName: "serversListView"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: Theme?.spacingXs ?? 4
            model: page.live ? Servers.servers : []
            ScrollBar.vertical: FmScrollBar {}

            delegate: Rectangle {
                id: rowItem
                objectName: "serverRow"
                required property var modelData
                required property int index

                readonly property bool selected: page.live && Servers.selected === modelData.id

                width: serverList.width
                height: 54
                radius: Theme?.radiusMd ?? 6
                color: rowItem.selected ? (Theme?.itemHover ?? "transparent")
                       : (rowHover.hovered ? (Theme?.itemHover ?? "transparent")
                       : (Theme?.bgMedium ?? "transparent"))

                HoverHandler { id: rowHover }

                MouseArea {
                    objectName: "serverRowHit"
                    anchors.fill: parent
                    onClicked: {
                        if (page.live)
                            Servers.select(modelData.id)
                    }
                }

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: Theme?.spacingSm ?? 8
                    anchors.rightMargin: Theme?.spacingXs ?? 4
                    spacing: Theme?.spacingXs ?? 4

                    FmIcon {
                        name: "server"
                        color: rowItem.selected ? (Theme?.accent ?? "transparent")
                               : (Theme?.textSecondary ?? "transparent")
                        size: Theme?.iconSize ?? 20
                        Layout.alignment: Qt.AlignVCenter
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 1

                        Text {
                            objectName: "serverRowTitle"
                            Layout.fillWidth: true
                            text: String(modelData.id || "")
                            color: Theme?.textPrimary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeBase ?? 12
                            font.bold: rowItem.selected
                            elide: Text.ElideRight
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme?.spacingXs ?? 4
                            visible: String(modelData.gameVersion || "").length > 0
                                     || String(modelData.loader || "").length > 0

                            FmTag {
                                objectName: "serverRowGameTag"
                                visible: String(modelData.gameVersion || "").length > 0
                                text: String(modelData.gameVersion || "")
                                level: "accent"
                            }

                            FmTag {
                                objectName: "serverRowLoaderTag"
                                visible: String(modelData.loader || "").length > 0
                                text: String(modelData.loader || "")
                                level: "success"
                            }
                        }
                    }

                    FmTag {
                        objectName: "serverRowRunningTag"
                        visible: !!modelData.running
                        text: page.format("server_status_running", {"version": String(modelData.id || "")})
                        level: "success"
                        Layout.alignment: Qt.AlignVCenter
                    }

                    FmTag {
                        objectName: "serverRowPortTag"
                        visible: Number(modelData.port || 25565) !== 25565
                        text: page.format("server_port_tag", {"port": Number(modelData.port || 25565)})
                        level: "warning"
                        Layout.alignment: Qt.AlignVCenter
                    }

                    FmToolButton {
                        objectName: "serverRowStartButton"
                        visible: !modelData.running
                        iconName: "play"
                        iconColor: Theme?.success ?? "transparent"
                        iconSize: (Theme?.iconSize ?? 20) - 4
                        enabled: page.live && !Servers.busy && !modelData.running
                        onClicked: {
                            if (page.live)
                                Servers.startVersion(modelData.id)
                        }
                    }

                    FmToolButton {
                        objectName: "serverRowStopButton"
                        visible: !!modelData.running
                        iconName: "stop"
                        iconColor: Theme?.error ?? "transparent"
                        iconSize: (Theme?.iconSize ?? 20) - 4
                        enabled: page.live && !Servers.busy
                        onClicked: {
                            if (page.live)
                                Servers.stopVersion(modelData.id)
                        }
                    }

                    FmToolButton {
                        objectName: "serverRowDetailButton"
                        iconName: "info"
                        iconSize: (Theme?.iconSize ?? 20) - 4
                        onClicked: {
                            if (page.live)
                                Servers.openDetail(modelData.id)
                        }
                    }

                    FmToolButton {
                        objectName: "serverRowConfigButton"
                        iconName: "slider"
                        iconSize: (Theme?.iconSize ?? 20) - 4
                        onClicked: {
                            if (page.live && typeof ServerConfig !== "undefined" && ServerConfig)
                                ServerConfig.open(modelData.id)
                        }
                    }

                    FmToolButton {
                        objectName: "serverRowFolderButton"
                        iconName: "folder-open"
                        iconSize: (Theme?.iconSize ?? 20) - 4
                        onClicked: {
                            if (page.live)
                                Servers.openDir(modelData.id)
                        }
                    }

                    FmToolButton {
                        objectName: "serverRowModsButton"
                        iconName: "mods"
                        iconSize: (Theme?.iconSize ?? 20) - 4
                        onClicked: {
                            if (page.live && typeof Nav !== "undefined" && Nav)
                                Nav.push("servers/detail/mods", {"version": String(modelData.id || "")})
                        }
                    }

                    FmToolButton {
                        objectName: "serverRowDeleteButton"
                        iconName: "trash"
                        iconSize: (Theme?.iconSize ?? 20) - 4
                        enabled: page.live && !Servers.busy
                        onClicked: {
                            if (page.live)
                                page.deleteTarget = String(modelData.id || "")
                        }
                    }
                }
            }
        }
    }

    //: 删除确认：与资源页同一个 ConfirmDialog（页面内浮层，不另外造对话框）。
    Item {
        objectName: "serversOverlayHost"
        anchors.fill: parent

        Loader {
            objectName: "serverDeleteLoader"
            anchors.fill: parent
            active: page.deleteTarget.length > 0
            sourceComponent: deleteComponent
        }
    }

    Component {
        id: deleteComponent

        ConfirmDialog {
            objectName: "serverDeleteDialog"
            request: ({
                "title": page.t("server_delete_confirm_title"),
                "message": page.format("server_delete_confirm_msg", {"version": page.deleteTarget}),
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
