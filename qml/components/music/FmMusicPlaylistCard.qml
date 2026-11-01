// FmMusicPlaylistCard.qml —— 发现页的歌单/榜单卡片（阶段 3.17，对齐 FMP PlaylistCard）。
//
// 封面 + 名称 + 副标题；悬停时显示播放按钮。只发信号，不自己读服务。

import QtQuick
import QtQuick.Layouts
import ".."

Item {
    id: control
    objectName: "musicPlaylistCard"

    property string title: ""
    property string subtitle: ""
    property string cover: ""
    property int trackCount: 0

    signal activated()
    signal playRequested()

    implicitHeight: width + 70
    implicitWidth: 154

    Rectangle {
        anchors.fill: parent
        radius: Theme?.radiusLg ?? 0
        color: cardMouse.containsMouse ? (Theme?.cardHover ?? "transparent")
                                       : (Theme?.cardBg ?? "transparent")
        border.width: 1
        border.color: cardMouse.containsMouse ? (Theme?.accentSoft ?? "transparent")
                                              : (Theme?.cardBorder ?? "transparent")
        Behavior on color { ColorAnimation { duration: Theme?.durationFast ?? 110 } }

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 10
            spacing: Theme?.spacingSm ?? 8

            Item {
                Layout.fillWidth: true
                Layout.preferredHeight: width

                Rectangle {
                    anchors.fill: parent
                    radius: Theme?.radiusMd ?? 0
                    color: Theme?.bgLight ?? "transparent"
                }

                FmIcon {
                    anchors.centerIn: parent
                    name: "music"
                    size: 32
                    color: Theme?.textTertiary ?? "transparent"
                }

                Image {
                    anchors.fill: parent
                    source: control.cover
                    fillMode: Image.PreserveAspectCrop
                    asynchronous: true
                    cache: true
                    visible: control.cover.length > 0 && status === Image.Ready
                }

                Rectangle {
                    id: playButton
                    z: 2
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    anchors.margins: 8
                    width: 36
                    height: 36
                    radius: 18
                    color: Theme?.accent ?? "transparent"
                    opacity: cardMouse.containsMouse ? 1 : 0
                    scale: cardMouse.containsMouse ? 1 : 0.7

                    Behavior on opacity { NumberAnimation { duration: Theme?.durationFast ?? 110 } }
                    Behavior on scale {
                        NumberAnimation {
                            duration: Theme?.durationNormal ?? 180
                            easing.type: Easing.OutBack
                        }
                    }

                    FmIcon {
                        anchors.centerIn: parent
                        name: "play"
                        size: 14
                        color: Theme?.bgDark ?? "transparent"
                    }

                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: function (mouse) {
                            mouse.accepted = true
                            control.playRequested()
                        }
                    }
                }
            }

            Text {
                Layout.fillWidth: true
                text: control.title
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeBase ?? 12
                font.bold: true
                elide: Text.ElideRight
                maximumLineCount: 2
                wrapMode: Text.WordWrap
            }

            Text {
                Layout.fillWidth: true
                visible: control.subtitle.length > 0
                text: control.subtitle
                color: Theme?.textTertiary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                elide: Text.ElideRight
            }
        }

        MouseArea {
            id: cardMouse
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: control.activated()
        }
    }
}
