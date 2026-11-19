// FmGameDirSection.qml —— 设置 -> 游戏目录（多 .minecraft 目录管理）。
//
// 什么时候用它：设置页需要管理/切换多个 .minecraft 目录时（当前只有 settings/dirs 分区）。
// 什么时候不要用：只需要展示一个固定路径（用 FmListItem/Text）；或需要同时编辑
//   多个版本的隔离目录（那是版本详情/资源页的职责，不是全局游戏目录）。
//
// 页面只做展示与转发：目录列表、当前项、是否可改名/删除都来自 GameDirs 桥，
// 校验与写盘在 services/game_dir_service.py。这里不拼中文、不写颜色、不用原生控件。
//
// 三块内容：
//   * 列表：内置目录 + 用户添加的目录；当前项带「使用中」标签；
//   * 添加对话框：名称 + 路径（FolderDialog 选择），错误就地显示；
//   * 重命名 / 移除：复用 TextInputDialog / ConfirmDialog，成功后由桥发状态文案。

import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import ".."
import "../dialogs"

Item {
    id: root
    objectName: "settingsGameDirsSection"

    readonly property bool live: (typeof GameDirs !== "undefined" && GameDirs) ? true : false

    property var rows: []
    property bool addVisible: false
    property string addName: ""
    property string addPath: ""
    property string addError: ""

    property string renameId: ""
    property bool removeVisible: false
    property string removeId: ""
    property string removeName: ""

    // ── 文案工具（只在函数里调；绑定直接用 Tr.map）────────────────

    function t(key) {
        return (typeof Tr !== "undefined" && Tr) ? (Tr.map[key] ?? key) : key
    }

    function format(key, params) {
        var text = root.t(key)
        if (!params)
            return text
        for (var name in params)
            text = text.replace("{" + name + "}", String(params[name]))
        return text
    }

    function status(text, level) {
        if (!text || String(text).length === 0)
            return
        if (typeof Shell !== "undefined" && Shell)
            Shell.setStatus(String(text), level || "info")
    }

    // ── 数据 ─────────────────────────────────────────────────

    function refresh() {
        if (!root.live) {
            root.rows = []
            return
        }
        root.rows = GameDirs.dirs
    }

    function useDir(row) {
        if (!root.live || !row || row.active === true)
            return
        if (!GameDirs.activate(String(row.id)))
            root.refresh()
    }

    function openDir(path) {
        if (!root.live)
            return
        var url = GameDirs.fileUrl(String(path || ""))
        if (url.length > 0)
            Qt.openUrlExternally(url)
    }

    // ── 添加 ─────────────────────────────────────────────────

    function beginAdd() {
        root.addName = ""
        root.addPath = ""
        root.addError = ""
        root.addVisible = true
        Qt.callLater(function () {
            if (addNameField)
                addNameField.forceActiveFocus()
        })
    }

    function closeAdd() {
        root.addVisible = false
        root.addError = ""
    }

    function submitAdd() {
        if (!root.live)
            return
        var result = GameDirs.add(root.addName, root.addPath)
        if (result && result.ok === true) {
            root.addVisible = false
            root.addError = ""
            return
        }
        root.addError = (result && result.error) ? String(result.error) : root.t("game_dir_io_error").replace("{error}", "")
    }

    // ── 重命名 / 移除 ────────────────────────────────────────

    function beginRename(row) {
        if (!root.live || !row || row.canRename !== true)
            return
        root.renameId = String(row.id)
        renameDialog.request = ({
            "title": root.t("game_dir_rename_title"),
            "prompt": root.t("game_dir_rename_prompt"),
            "initial": String(row.name || ""),
            "password": false
        })
        renameDialog.visible = true
    }

    function submitRename(value) {
        renameDialog.visible = false
        if (!root.live)
            return
        var result = GameDirs.rename(root.renameId, value)
        if (result && result.ok !== true)
            root.status((result && result.error) ? String(result.error) : "", "error")
    }

    function askRemove(row) {
        if (!root.live || !row || row.canRemove !== true)
            return
        root.removeId = String(row.id)
        root.removeName = String(row.name || "")
        removeDialog.request = ({
            "title": root.t("game_dir_remove_title"),
            "message": root.format("game_dir_remove_message", {"name": root.removeName}),
            "default": false
        })
        removeDialog.visible = true
    }

    function confirmRemove(value) {
        removeDialog.visible = false
        if (!value || !root.live)
            return
        var result = GameDirs.remove(root.removeId)
        if (result && result.ok !== true)
            root.status((result && result.error) ? String(result.error) : "", "error")
    }

    // ── 生命周期 ─────────────────────────────────────────────

    Component.onCompleted: root.refresh()

    Connections {
        target: root.live ? GameDirs : null

        function onDirsChanged() {
            if (root)
                root.refresh()
        }

        function onActiveChanged() {
            if (root)
                root.refresh()
        }

        function onStatusMessage(text, level) {
            if (root)
                root.status(text, level)
        }
    }

    // ── 顶部说明 + 列表 ─────────────────────────────────────

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme?.spacingMd ?? 12

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 8

            ColumnLayout {
                Layout.fillWidth: true
                spacing: Theme?.spacingXs ?? 4

                Text {
                    Layout.fillWidth: true
                    text: Tr?.map["settings_game_dirs_title"] ?? "settings_game_dirs_title"
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeTitle ?? 16
                    font.bold: true
                }

                Text {
                    Layout.fillWidth: true
                    text: Tr?.map["settings_game_dirs_hint"] ?? "settings_game_dirs_hint"
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 11
                    wrapMode: Text.WordWrap
                }
            }

            FmButton {
                objectName: "gameDirAddButton"
                Layout.alignment: Qt.AlignTop
                primary: true
                iconName: "add"
                text: Tr?.map["game_dir_add"] ?? "game_dir_add"
                enabled: root.live
                onClicked: root.beginAdd()
            }
        }

        FmScrollView {
            id: scroll
            objectName: "gameDirScroll"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: availableWidth

            Column {
                id: listColumn
                width: Math.max(360, scroll.availableWidth - 2 * (Theme?.spacingLg ?? 0))
                x: Math.max(0, (scroll.availableWidth - width) / 2)
                spacing: Theme?.spacingSm ?? 8

                Repeater {
                    model: root.rows

                    delegate: Rectangle {
                        id: rowItem
                        required property var modelData
                        required property int index

                        width: listColumn.width
                        height: rowLayout.implicitHeight + 2 * (Theme?.spacingMd ?? 0)
                        radius: Theme?.radiusMd ?? 6
                        color: rowItem.modelData.active === true
                               ? (Theme?.accentSoft ?? "transparent")
                               : (Theme?.bgMedium ?? "transparent")
                        border.width: 1
                        border.color: rowItem.modelData.active === true
                                      ? (Theme?.accent ?? "transparent")
                                      : (Theme?.cardBorder ?? "transparent")

                        RowLayout {
                            id: rowLayout
                            anchors.fill: parent
                            anchors.margins: Theme?.spacingMd ?? 0
                            spacing: Theme?.spacingSm ?? 8

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: Theme?.spacingXs ?? 4

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: Theme?.spacingXs ?? 4

                                    Text {
                                        Layout.fillWidth: true
                                        text: String(rowItem.modelData.name || "")
                                        color: Theme?.textPrimary ?? "transparent"
                                        font.pixelSize: Theme?.fontSizeBase ?? 12
                                        font.bold: true
                                        elide: Text.ElideRight
                                    }

                                    FmTag {
                                        visible: rowItem.modelData.active === true
                                        level: "accent"
                                        iconName: "check"
                                        text: Tr?.map["game_dir_active"] ?? "game_dir_active"
                                    }

                                    FmTag {
                                        level: "neutral"
                                        text: String(rowItem.modelData.kindLabel || "")
                                    }
                                }

                                Text {
                                    Layout.fillWidth: true
                                    text: String(rowItem.modelData.path || "")
                                    color: Theme?.textSecondary ?? "transparent"
                                    font.pixelSize: Theme?.fontSizeSmall ?? 11
                                    elide: Text.ElideMiddle
                                }

                                Text {
                                    Layout.fillWidth: true
                                    visible: rowItem.modelData.exists !== true
                                    text: Tr?.map["game_dir_missing"] ?? "game_dir_missing"
                                    color: Theme?.warning ?? "transparent"
                                    font.pixelSize: Theme?.fontSizeSmall ?? 11
                                }
                            }

                            FmButton {
                                objectName: "gameDirUseButton"
                                visible: rowItem.modelData.active !== true
                                primary: false
                                iconName: "check"
                                text: Tr?.map["game_dir_use"] ?? "game_dir_use"
                                onClicked: root.useDir(rowItem.modelData)
                            }

                            FmToolButton {
                                objectName: "gameDirOpenButton"
                                iconName: "folder-open"
                                enabled: rowItem.modelData.exists === true
                                onClicked: root.openDir(rowItem.modelData.path)
                            }

                            FmToolButton {
                                objectName: "gameDirRenameButton"
                                iconName: "edit"
                                visible: rowItem.modelData.canRename === true
                                onClicked: root.beginRename(rowItem.modelData)
                            }

                            FmToolButton {
                                objectName: "gameDirRemoveButton"
                                iconName: "trash"
                                visible: rowItem.modelData.canRemove === true
                                onClicked: root.askRemove(rowItem.modelData)
                            }
                        }
                    }
                }

                Text {
                    objectName: "gameDirEmpty"
                    width: parent.width
                    visible: root.rows.length === 0
                    text: Tr?.map["game_dir_empty"] ?? "game_dir_empty"
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                    horizontalAlignment: Text.AlignHCenter
                    topPadding: Theme?.spacingLg ?? 16
                }
            }
        }
    }

    // ── 添加对话框 ───────────────────────────────────────────

    Item {
        id: addLayer
        objectName: "gameDirAddLayer"
        anchors.fill: parent
        visible: root.addVisible
        z: 20

        Rectangle {
            anchors.fill: parent
            color: Theme?.scrim ?? "transparent"
        }

        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.AllButtons
        }

        FmCard {
            objectName: "gameDirAddDialog"
            anchors.centerIn: parent
            width: Math.min(560, Math.max(320, root.width - 2 * (Theme?.spacingXl ?? 24)))
            title: Tr?.map["game_dir_add_title"] ?? "game_dir_add_title"

            Text {
                Layout.fillWidth: true
                text: Tr?.map["game_dir_name"] ?? "game_dir_name"
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeBase ?? 12
            }

            FmTextField {
                id: addNameField
                objectName: "gameDirAddName"
                Layout.fillWidth: true
                placeholder: Tr?.map["game_dir_name_placeholder"] ?? "game_dir_name_placeholder"
                text: root.addName
                onEdited: root.addName = value
            }

            Text {
                Layout.fillWidth: true
                text: Tr?.map["game_dir_path"] ?? "game_dir_path"
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeBase ?? 12
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme?.spacingSm ?? 8

                FmTextField {
                    id: addPathField
                    objectName: "gameDirAddPath"
                    Layout.fillWidth: true
                    placeholder: Tr?.map["game_dir_path_placeholder"] ?? "game_dir_path_placeholder"
                    text: root.addPath
                    onEdited: root.addPath = value
                }

                FmButton {
                    objectName: "gameDirAddBrowse"
                    primary: false
                    iconName: "folder-open"
                    text: Tr?.map["game_dir_browse"] ?? "game_dir_browse"
                    onClicked: addFolderDialog.open()
                }
            }

            Text {
                Layout.fillWidth: true
                visible: root.addError.length > 0
                text: root.addError
                color: Theme?.error ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 11
                wrapMode: Text.WordWrap
            }

            footer: RowLayout {
                Layout.fillWidth: true
                spacing: Theme?.spacingSm ?? 8

                Item { Layout.fillWidth: true }

                FmButton {
                    objectName: "gameDirAddCancel"
                    primary: false
                    text: Tr?.map["cancel"] ?? "cancel"
                    onClicked: root.closeAdd()
                }

                FmButton {
                    objectName: "gameDirAddConfirm"
                    primary: true
                    text: Tr?.map["confirm"] ?? "confirm"
                    onClicked: root.submitAdd()
                }
            }
        }

        FolderDialog {
            id: addFolderDialog
            objectName: "gameDirAddFolderDialog"
            title: root.t("game_dir_add_title")
            onAccepted: {
                if (root.live)
                    root.addPath = GameDirs.localPath(String(selectedFolder))
            }
        }
    }

    // ── 重命名 / 移除对话框 ──────────────────────────────────

    TextInputDialog {
        id: renameDialog
        anchors.fill: parent
        modalScrim: true
        visible: false
        z: 30
        onSubmitted: function (value) { root.submitRename(value) }
        onCancelled: renameDialog.visible = false
    }

    ConfirmDialog {
        id: removeDialog
        anchors.fill: parent
        modalScrim: true
        visible: false
        z: 30
        onAnswered: function (value) { root.confirmRemove(value) }
    }
}
