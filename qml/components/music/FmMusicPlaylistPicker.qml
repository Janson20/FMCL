// FmMusicPlaylistPicker.qml —— 「加入歌单」选择层（阶段 3.17，3.18 补齐勾选与新建）。
//
// 什么时候用它：把一首在线/本地曲目加入自建歌单。
// 什么时候不要用：歌单本身的增删改（那是 FmMusicLibraryView 的左侧栏）。
//
// 只负责列出歌单、标出“已在歌单中”并把选择/新建请求发回页面；系统歌单由页面过滤。
// 不用 Popup：本项目 R9 禁原生视觉控件，页内浮层用普通 Item 组合。
// 没有自建歌单时不再弹一句“先到歌单页新建”，而是就地给一个命名输入框。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Item {
    id: picker
    objectName: "musicPlaylistPicker"

    property bool opened: false
    property var playlists: []
    property var containedIds: []
    property var track: null
    property bool creating: false

    signal closed()
    signal selected(string playlistId, var track)
    signal createRequested(string name, var track)

    function contains(playlistId) {
        for (var i = 0; i < picker.containedIds.length; ++i) {
            if (String(picker.containedIds[i]) === String(playlistId))
                return true
        }
        return false
    }

    function beginCreate() {
        picker.creating = true
        Qt.callLater(newField.forceActiveFocus)
    }

    function submitCreate() {
        var name = newField.text.trim()
        if (name.length === 0) {
            newField.forceActiveFocus()
            return
        }
        picker.createRequested(name, picker.track)
        picker.creating = false
        picker.closed()
    }

    onOpenedChanged: {
        if (!picker.opened) {
            picker.creating = false
            newField.text = ""
        }
    }

    visible: picker.opened
    z: 30
    focus: picker.opened
    Keys.onEscapePressed: picker.closed()

    Rectangle {
        anchors.fill: parent
        color: Theme?.scrim ?? "transparent"

        MouseArea {
            anchors.fill: parent
            onClicked: picker.closed()
        }
    }

    Rectangle {
        id: card
        objectName: "musicPlaylistPickerCard"
        anchors.centerIn: parent
        width: Math.min(400, Math.max(300, picker.width - 2 * (Theme?.spacingXl ?? 30)))
        height: Math.min(500, Math.max(240, list.contentHeight + (picker.creating ? 176 : 112)))
        radius: Theme?.radiusLg ?? 0
        color: Theme?.cardBg ?? "transparent"
        border.width: 1
        border.color: Theme?.cardBorder ?? "transparent"

        MouseArea {
            anchors.fill: parent
            onClicked: { }
        }

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: Theme?.spacingLg ?? 20
            spacing: Theme?.spacingSm ?? 8

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme?.spacingSm ?? 8

                FmIcon {
                    name: "note"
                    size: 16
                    color: Theme?.accent ?? "transparent"
                }

                Text {
                    Layout.fillWidth: true
                    text: Tr?.map["music_add_to_playlist"] ?? ""
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeLarge ?? 14
                    font.bold: true
                    elide: Text.ElideRight
                }

                FmToolButton {
                    objectName: "musicPlaylistPickerClose"
                    iconName: "close"
                    iconSize: 14
                    side: 28
                    onClicked: picker.closed()
                }
            }

            Text {
                Layout.fillWidth: true
                text: picker.track ? String(picker.track.name || "") : ""
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                elide: Text.ElideRight
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 100
                radius: Theme?.radiusMd ?? 0
                color: Theme?.bgLight ?? "transparent"

                ListView {
                    id: list
                    anchors.fill: parent
                    anchors.margins: 4
                    clip: true
                    spacing: 2
                    model: picker.playlists
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: FmScrollBar { }

                    delegate: Rectangle {
                        id: item
                        required property var modelData
                        readonly property bool already: picker.contains(modelData.id)
                        width: list.width
                        height: 40
                        radius: Theme?.radiusSm ?? 0
                        color: already
                               ? "transparent"
                               : (itemMouse.containsMouse ? (Theme?.cardHover ?? "transparent") : "transparent")

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 10
                            anchors.rightMargin: 8
                            spacing: Theme?.spacingSm ?? 8

                            FmIcon {
                                name: item.already ? "check" : "note"
                                size: 14
                                color: item.already
                                       ? (Theme?.accent ?? "transparent")
                                       : (Theme?.textSecondary ?? "transparent")
                            }

                            Text {
                                Layout.fillWidth: true
                                text: String(modelData.name || "")
                                color: item.already
                                       ? (Theme?.textTertiary ?? "transparent")
                                       : (Theme?.textPrimary ?? "transparent")
                                font.pixelSize: Theme?.fontSizeBase ?? 12
                                elide: Text.ElideRight
                            }

                            Text {
                                visible: item.already
                                text: Tr?.map["music_already_in_playlist"] ?? ""
                                color: Theme?.textTertiary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 10
                            }

                            Text {
                                visible: !item.already
                                text: String(modelData.count ?? 0)
                                color: Theme?.textTertiary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 10
                            }
                        }

                        MouseArea {
                            id: itemMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            enabled: !item.already
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                picker.selected(String(modelData.id || ""), picker.track);
                                picker.closed();
                            }
                        }
                    }
                }

                Text {
                    anchors.centerIn: parent
                    width: Math.min(300, parent.width - 24)
                    visible: picker.playlists.length === 0 && !picker.creating
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                    text: Tr?.map["music_no_user_playlists"] ?? ""
                    color: Theme?.textTertiary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                }
            }

            RowLayout {
                Layout.fillWidth: true
                visible: picker.creating
                spacing: Theme?.spacingSm ?? 8

                FmTextField {
                    id: newField
                    objectName: "musicPlaylistNewName"
                    Layout.fillWidth: true
                    placeholder: Tr?.map["music_playlist_name_placeholder"] ?? ""
                    onAccepted: picker.submitCreate()
                }

                FmButton {
                    primary: true
                    text: Tr?.map["confirm"] ?? ""
                    enabled: newField.text.trim().length > 0
                    onClicked: picker.submitCreate()
                }

                FmButton {
                    primary: false
                    text: Tr?.map["cancel"] ?? ""
                    onClicked: {
                        picker.creating = false
                        newField.text = ""
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme?.spacingSm ?? 8

                FmButton {
                    objectName: "musicPlaylistPickerNew"
                    primary: false
                    iconName: "add"
                    text: Tr?.map["music_new_playlist"] ?? ""
                    visible: !picker.creating
                    onClicked: picker.beginCreate()
                }

                Item { Layout.fillWidth: true }

                FmButton {
                    primary: false
                    text: Tr?.map["cancel"] ?? ""
                    visible: !picker.creating
                    onClicked: picker.closed()
                }
            }
        }
    }
}
