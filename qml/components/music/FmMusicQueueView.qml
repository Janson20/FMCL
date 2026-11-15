// FmMusicQueueView.qml —— 播放队列。
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
    property var addHost: null

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

            FmMusicTrackList {
                anchors.fill: parent
                anchors.margins: Theme?.spacingSm ?? 8
                model: control.player ? control.player.queueModel : null
                player: control.player
                variant: "queue"
                showSequence: true
                showAdd: true
                showRemove: true
                showMove: true
                showQuality: true
                showOriginal: true
                showPlayCount: false
                emptyIcon: "note"
                emptyTitle: Tr?.map["music_queue_empty"] ?? ""
                emptyDescription: Tr?.map["music_queue_empty_desc"] ?? ""
                listObjectName: "musicQueueList"
                onActivated: function (rowIndex) {
                    if (control.player)
                        control.player.playQueueAt(rowIndex);
                }
                onAddRequested: function (rowIndex) {
                    if (!control.player || !control.addHost)
                        return
                    var track = control.player.queueItems[rowIndex]
                    if (track)
                        control.addHost.openFor(track)
                }
                onRemoveRequested: function (rowIndex) {
                    if (control.player)
                        control.player.removeQueueIndex(rowIndex)
                }
                onMoveUpRequested: function (rowIndex) {
                    if (control.player)
                        control.player.moveQueueItem(rowIndex, rowIndex - 1)
                }
                onMoveDownRequested: function (rowIndex) {
                    if (control.player)
                        control.player.moveQueueItem(rowIndex, rowIndex + 1)
                }
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
