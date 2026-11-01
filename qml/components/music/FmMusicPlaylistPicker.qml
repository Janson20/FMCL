// FmMusicPlaylistPicker.qml —— 「加入歌单」选择层（阶段 3.17）。
//
// 只负责列出自建歌单并把选择发回页面；系统歌单由页面过滤掉。
// 不用 Popup：本项目 R9 禁原生视觉控件，页内浮层用普通 Item 组合。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Item {
    id: picker
    objectName: "musicPlaylistPicker"

    property bool opened: false
    property var playlists: []
    property var track: null

    signal closed()
    signal selected(string playlistId, var track)

    visible: picker.opened
    z: 30

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
        height: Math.min(440, Math.max(220, list.contentHeight + 110))
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
                        width: list.width
                        height: 40
                        radius: Theme?.radiusSm ?? 0
                        color: itemMouse.containsMouse ? (Theme?.cardHover ?? "transparent") : "transparent"

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 10
                            anchors.rightMargin: 8
                            spacing: Theme?.spacingSm ?? 8

                            FmIcon {
                                name: "note"
                                size: 14
                                color: Theme?.textSecondary ?? "transparent"
                            }

                            Text {
                                Layout.fillWidth: true
                                text: String(modelData.name || "")
                                color: Theme?.textPrimary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeBase ?? 12
                                elide: Text.ElideRight
                            }

                            Text {
                                text: String(modelData.count ?? 0)
                                color: Theme?.textTertiary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 10
                            }
                        }

                        MouseArea {
                            id: itemMouse
                            anchors.fill: parent
                            hoverEnabled: true
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
                    visible: picker.playlists.length === 0
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                    text: Tr?.map["music_no_user_playlists"] ?? ""
                    color: Theme?.textTertiary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                }
            }

            FmButton {
                Layout.alignment: Qt.AlignRight
                primary: false
                text: Tr?.map["cancel"] ?? ""
                onClicked: picker.closed()
            }
        }
    }
}
