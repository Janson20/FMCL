// FmMusicLocalView.qml —— 本地音乐（阶段 3.16）。
//
// 文件夹管理 + 扫描进度 + 曲库列表。业务状态来自 Music.library；本文件不拼规则。

import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import ".."

Item {
    id: control
    objectName: "musicLocalView"

    property var lib: null
    property var player: null

    function countText(count) {
        return String(Tr?.map["music_song_count"] ?? "").replace("{count}", String(count));
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme?.spacingSm ?? 8

        // ── 文件夹与扫描 ───────────────────────────────────
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: folderColumn.implicitHeight + 2 * (Theme?.spacingMd ?? 0)
            radius: Theme?.radiusLg ?? 0
            color: Theme?.cardBg ?? "transparent"
            border.width: 1
            border.color: Theme?.cardBorder ?? "transparent"

            ColumnLayout {
                id: folderColumn
                anchors.fill: parent
                anchors.margins: Theme?.spacingMd ?? 0
                spacing: Theme?.spacingSm ?? 8

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 8

                    Text {
                        text: Tr?.map["music_tab_local"] ?? ""
                        color: Theme?.textPrimary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeLarge ?? 14
                        font.bold: true
                    }

                    Text {
                        text: control.lib ? control.countText(control.lib.localModel.count) : ""
                        color: Theme?.textTertiary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                    }

                    Item { Layout.fillWidth: true }

                    FmButton {
                        text: Tr?.map["music_add_folder"] ?? ""
                        iconName: "add"
                        onClicked: folderDialog.open()
                    }

                    FmButton {
                        primary: false
                        text: Tr?.map["music_rescan"] ?? ""
                        iconName: "refresh"
                        enabled: control.lib && !control.lib.scanning
                        onClicked: {
                            if (typeof Music !== "undefined" && Music)
                                Music.scan();
                        }
                    }
                }

                Flow {
                    Layout.fillWidth: true
                    Layout.preferredHeight: visible ? implicitHeight : 0
                    visible: control.lib && control.lib.localFolders.length > 0
                    spacing: Theme?.spacingXs ?? 4

                    Repeater {
                        model: control.lib ? control.lib.localFolders : []
                        delegate: Rectangle {
                            required property string modelData
                            width: Math.min(440, chipText.implicitWidth + 54)
                            height: 28
                            radius: 14
                            color: Theme?.bgLight ?? "transparent"

                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 10
                                anchors.rightMargin: 2
                                spacing: 4

                                FmIcon {
                                    name: "folder"
                                    size: 13
                                    color: Theme?.textSecondary ?? "transparent"
                                }

                                Text {
                                    id: chipText
                                    Layout.fillWidth: true
                                    text: modelData
                                    color: Theme?.textSecondary ?? "transparent"
                                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                                    elide: Text.ElideMiddle
                                }

                                FmToolButton {
                                    iconName: "close"
                                    iconSize: 11
                                    side: 22
                                    onClicked: control.lib.removeLocalFolder(modelData)
                                }
                            }
                        }
                    }
                }

                Text {
                    Layout.fillWidth: true
                    visible: control.lib && control.lib.localFolders.length === 0
                    text: Tr?.map["music_no_local_folder_hint"] ?? ""
                    color: Theme?.textTertiary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                    wrapMode: Text.WordWrap
                }

                FmProgressBar {
                    Layout.fillWidth: true
                    visible: control.lib && control.lib.scanning
                    value: control.lib ? control.lib.scanProgress / 100 : 0
                    label: (Tr?.map["music_scanning"] ?? "")
                           + (control.lib && control.lib.scanDetail.length > 0
                              ? "  " + control.lib.scanDetail : "")
                }
            }
        }

        // ── 曲库列表 ───────────────────────────────────────
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: Theme?.radiusLg ?? 0
            color: Theme?.cardBg ?? "transparent"
            border.width: 1
            border.color: Theme?.cardBorder ?? "transparent"

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: Theme?.spacingSm ?? 8
                spacing: 0

                RowLayout {
                    Layout.fillWidth: true
                    Layout.leftMargin: 6
                    Layout.rightMargin: 6
                    Layout.bottomMargin: 4

                    Text {
                        Layout.fillWidth: true
                        text: Tr?.map["music_all_songs"] ?? ""
                        color: Theme?.textPrimary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        font.bold: true
                    }

                    FmButton {
                        primary: false
                        text: Tr?.map["music_play_all"] ?? ""
                        iconName: "play"
                        visible: control.lib && control.lib.localModel.count > 0
                        onClicked: {
                            if (control.player && control.lib)
                                control.player.playTrackInList(control.lib.localModel.allItems(), 0);
                        }
                    }
                }

                Item {
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    ListView {
                        id: localList
                        anchors.fill: parent
                        clip: true
                        spacing: 1
                        model: control.lib ? control.lib.localModel : null
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: FmScrollBar { }

                        delegate: FmMusicTrackRow {
                            width: localList.width
                            current: !!(control.player && control.player.currentTrack
                                     && control.player.currentTrack.uid === uid)
                            onActivated: {
                                if (control.player && control.lib)
                                    control.player.playTrackInList(control.lib.localModel.allItems(), index);
                            }
                        }
                    }

                    FmEmptyState {
                        objectName: "musicLocalEmpty"
                        anchors.fill: parent
                        visible: control.lib && !control.lib.scanning && control.lib.localModel.count === 0
                        iconName: "folder-open"
                        title: Tr?.map["music_local_empty"] ?? ""
                        description: Tr?.map["music_local_empty_desc"] ?? ""
                        actionText: Tr?.map["music_add_folder"] ?? ""
                        actionPrimary: true
                        onActionTriggered: folderDialog.open()
                    }
                }
            }
        }
    }

    FolderDialog {
        id: folderDialog
        objectName: "musicFolderDialog"
        title: Tr?.map["music_select_folder"] ?? ""
        acceptLabel: Tr?.map["music_add_folder"] ?? ""
        rejectLabel: Tr?.map["cancel"] ?? ""
        onAccepted: {
            if (control.lib)
                control.lib.addLocalFolder(selectedFolder.toString());
        }
    }
}
