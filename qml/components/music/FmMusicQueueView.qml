// FmMusicQueueView.qml —— 播放队列（阶段 3.16）。
//
// 数据源是 player.queueItems（QVariantList），每行可跳转 / 上移 / 下移 / 移除。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Item {
    id: control
    objectName: "musicQueueView"

    property var player: null

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme?.spacingSm ?? 8

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 8

            Text {
                Layout.fillWidth: true
                text: Tr?.map["music_queue_title"] ?? ""
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeLarge ?? 14
                font.bold: true
            }

            Text {
                text: control.player
                      ? control.player.queueCount + "  ·  " + control.modeNameText(control.player.modeName)
                      : ""
                color: Theme?.textTertiary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: Theme?.radiusLg ?? 0
            color: Theme?.cardBg ?? "transparent"
            border.width: 1
            border.color: Theme?.cardBorder ?? "transparent"

            ListView {
                id: queueList
                anchors.fill: parent
                anchors.margins: Theme?.spacingSm ?? 8
                clip: true
                spacing: 1
                model: control.player ? control.player.queueItems : []
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: FmScrollBar { }

                delegate: Rectangle {
                    id: qrow
                    required property var modelData
                    required property int index
                    width: queueList.width
                    height: 52
                    radius: Theme?.radiusSm ?? 0
                    color: control.player && control.player.queueIndex === index
                           ? (Theme?.accentSoft ?? "transparent")
                           : (qmouse.containsMouse ? (Theme?.cardHover ?? "transparent") : "transparent")

                    MouseArea {
                        id: qmouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onDoubleClicked: {
                            if (control.player)
                                control.player.playQueueAt(index);
                        }
                    }

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 10
                        anchors.rightMargin: 6
                        spacing: Theme?.spacingSm ?? 8

                        Text {
                            Layout.preferredWidth: 26
                            Layout.alignment: Qt.AlignVCenter
                            horizontalAlignment: Text.AlignRight
                            text: String(index + 1)
                            color: control.player && control.player.queueIndex === index
                                   ? (Theme?.accent ?? "transparent") : (Theme?.textTertiary ?? "transparent")
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                        }

                        Rectangle {
                            Layout.preferredWidth: 34
                            Layout.preferredHeight: 34
                            Layout.alignment: Qt.AlignVCenter
                            radius: Theme?.radiusSm ?? 0
                            color: Theme?.bgLight ?? "transparent"

                            FmIcon {
                                anchors.centerIn: parent
                                name: "music"
                                size: 16
                                color: Theme?.textTertiary ?? "transparent"
                            }
                            Image {
                                anchors.fill: parent
                                source: String(modelData.cover || "")
                                fillMode: Image.PreserveAspectCrop
                                asynchronous: true
                                visible: String(modelData.cover || "").length > 0 && status === Image.Ready
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2

                            Text {
                                Layout.fillWidth: true
                                text: String(modelData.name || "")
                                color: control.player && control.player.queueIndex === index
                                       ? (Theme?.accent ?? "transparent") : (Theme?.textPrimary ?? "transparent")
                                font.pixelSize: Theme?.fontSizeBase ?? 12
                                font.bold: control.player && control.player.queueIndex === index
                                elide: Text.ElideRight
                            }
                            Text {
                                Layout.fillWidth: true
                                text: {
                                    var a = String(modelData.singer || "");
                                    var b = String(modelData.album || "");
                                    return (a.length > 0 && b.length > 0) ? a + "  ·  " + b : (a || b);
                                }
                                color: Theme?.textSecondary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 10
                                elide: Text.ElideRight
                            }
                        }

                        Text {
                            Layout.preferredWidth: 46
                            Layout.alignment: Qt.AlignVCenter
                            horizontalAlignment: Text.AlignRight
                            text: String(modelData.durationText || "")
                            color: Theme?.textTertiary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                        }

                        FmToolButton {
                            iconName: "chevron-up"
                            iconSize: 12
                            side: 26
                            enabled: index > 0
                            opacity: qmouse.containsMouse ? 1 : 0
                            onClicked: control.player.moveQueueItem(index, index - 1)
                        }
                        FmToolButton {
                            iconName: "chevron-down"
                            iconSize: 12
                            side: 26
                            enabled: control.player && index < control.player.queueCount - 1
                            opacity: qmouse.containsMouse ? 1 : 0
                            onClicked: control.player.moveQueueItem(index, index + 1)
                        }
                        FmToolButton {
                            iconName: "close"
                            iconSize: 11
                            side: 26
                            opacity: qmouse.containsMouse ? 1 : 0
                            onClicked: control.player.removeQueueIndex(index)
                        }
                    }
                }
            }

            FmEmptyState {
                objectName: "musicQueueEmpty"
                anchors.fill: parent
                visible: !control.player || control.player.queueCount === 0
                iconName: "note"
                title: Tr?.map["music_queue_empty"] ?? ""
                description: Tr?.map["music_queue_empty_desc"] ?? ""
            }
        }
    }

    function modeNameText(name) {
        if (name === "order") return Tr?.map["music_mode_order"] ?? "";
        if (name === "loop_single") return Tr?.map["music_mode_loop_single"] ?? "";
        if (name === "shuffle") return Tr?.map["music_mode_shuffle"] ?? "";
        return Tr?.map["music_mode_loop_list"] ?? "";
    }
}
