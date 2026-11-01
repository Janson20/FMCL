// FmMusicPlaylistDetail.qml —— 发现页歌单详情覆盖层（阶段 3.17，对齐 FMP PlaylistDetailPanel）。
//
// 覆盖层自己吃点击/滚轮（FmMusicOverlayBackdrop），不让事件穿到下面的发现页。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Item {
    id: control
    objectName: "musicPlaylistDetail"

    property var discover: null
    property var player: null
    signal closeRequested()

    readonly property var meta: control.discover ? control.discover.detailMeta : ({})
    readonly property var model: control.discover ? control.discover.detailModel : null
    readonly property bool loading: control.discover ? control.discover.detailLoading : false

    FmMusicOverlayBackdrop {
        anchors.fill: parent
        blockerObjectName: "musicPlaylistDetailBlocker"
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme?.spacingLg ?? 16
        spacing: Theme?.spacingMd ?? 12

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 8

            FmButton {
                objectName: "musicPlaylistDetailBack"
                primary: false
                iconName: "arrow-left"
                text: Tr?.map["music_playlist_detail_back"] ?? ""
                onClicked: control.closeRequested()
            }

            Item { Layout.fillWidth: true }

            Text {
                visible: control.loading
                text: Tr?.map["plugin_state_loading"] ?? ""
                color: Theme?.textTertiary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme?.spacingLg ?? 16

            Item {
                Layout.preferredWidth: 132
                Layout.preferredHeight: 132
                Layout.alignment: Qt.AlignTop

                Rectangle {
                    anchors.fill: parent
                    radius: Theme?.radiusLg ?? 0
                    color: Theme?.bgLight ?? "transparent"
                }
                FmIcon {
                    anchors.centerIn: parent
                    name: "music"
                    size: 34
                    color: Theme?.textTertiary ?? "transparent"
                }
                Image {
                    anchors.fill: parent
                    source: control.meta && control.meta.cover !== undefined ? String(control.meta.cover) : ""
                    fillMode: Image.PreserveAspectCrop
                    asynchronous: true
                    cache: true
                    visible: String(source).length > 0 && status === Image.Ready
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignVCenter
                spacing: Theme?.spacingXs ?? 4

                Text {
                    Layout.fillWidth: true
                    text: control.meta && control.meta.name !== undefined
                          && String(control.meta.name).length > 0
                          ? String(control.meta.name)
                          : (Tr?.map["music_playlists"] ?? "")
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeTitle ?? 18
                    font.bold: true
                    wrapMode: Text.WordWrap
                    maximumLineCount: 2
                    elide: Text.ElideRight
                }

                Text {
                    Layout.fillWidth: true
                    visible: control.meta && control.meta.creator !== undefined
                             && String(control.meta.creator).length > 0
                    text: control.meta ? String(control.meta.creator) : ""
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                    elide: Text.ElideRight
                }

                Text {
                    Layout.fillWidth: true
                    text: control.model
                          ? String(Tr?.map["music_song_count"] ?? "").replace("{count}", String(control.model.count))
                          : ""
                    color: Theme?.textTertiary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                }

                Text {
                    Layout.fillWidth: true
                    Layout.maximumHeight: 40
                    visible: control.meta && control.meta.description !== undefined
                             && String(control.meta.description).length > 0
                    text: control.meta ? String(control.meta.description) : ""
                    color: Theme?.textTertiary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                    wrapMode: Text.WordWrap
                    maximumLineCount: 2
                    elide: Text.ElideRight
                }

                RowLayout {
                    Layout.topMargin: Theme?.spacingXs ?? 4
                    spacing: Theme?.spacingSm ?? 8

                    FmButton {
                        objectName: "musicPlaylistDetailPlay"
                        text: Tr?.map["music_play_all"] ?? ""
                        iconName: "play"
                        enabled: control.model && control.model.count > 0
                        onClicked: {
                            if (control.player && control.model)
                                control.player.playTrackInList(control.model.allItems(), 0)
                        }
                    }

                    FmButton {
                        primary: false
                        text: Tr?.map["music_add_to_queue"] ?? ""
                        iconName: "add"
                        enabled: control.model && control.model.count > 0
                        onClicked: {
                            if (control.player && control.model)
                                control.player.extendQueue(control.model.allItems())
                        }
                    }
                }
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
                id: detailList
                anchors.fill: parent
                anchors.margins: Theme?.spacingSm ?? 8
                clip: true
                spacing: 1
                model: control.model
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: FmScrollBar { }

                delegate: FmMusicTrackRow {
                    width: detailList.width
                    current: !!(control.player && control.player.currentTrack
                             && control.player.currentTrack.uid === uid)
                    onActivated: {
                        if (control.player && control.model)
                            control.player.playTrackInList(control.model.allItems(), index)
                    }
                }
            }

            FmLoadingState {
                anchors.fill: parent
                visible: control.loading
                title: Tr?.map["plugin_state_loading"] ?? ""
            }

            FmEmptyState {
                anchors.fill: parent
                visible: !control.loading && control.model && control.model.count === 0
                iconName: "note"
                title: Tr?.map["music_playlist_detail_empty"] ?? ""
                description: Tr?.map["music_playlist_detail_empty_desc"] ?? ""
            }
        }
    }
}
