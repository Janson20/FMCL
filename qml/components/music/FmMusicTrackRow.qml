// FmMusicTrackRow.qml —— 所有歌曲列表共用的曲目行（阶段 3.18 统一）。
//
// 什么时候用它：发现页短列表、本地曲库、歌单曲目、播放队列、在线搜索结果。
// 什么时候不要用：歌单/专辑卡片（FmMusicPlaylistCard）、非曲目实体。
//
// 同一份画法承载不同上下文：
//   * 序号、来源、时长恒定；
//   * 在线结果用 bestQuality / isOriginal / playCount 显示音质、原唱、播放量；
//   * 歌单/队列用 showRemove / showMove 打开移除与上移/下移；
//   * 所有页面统一单击播放、右键发 contextRequested、复制按钮一致。
// 颜色只来自 Theme.*，文案全部由调用方/Tr 提供。

import QtQuick
import QtQuick.Layouts
import ".."

Rectangle {
    id: control
    objectName: "musicTrackRow"

    required property int index
    required property string uid
    required property string name
    required property string singer
    required property string album
    required property string durationText
    required property string cover
    required property string source
    required property string sourceText
    required property bool isLocal
    required property string bestQuality
    required property bool isOriginal
    required property string originalName
    required property int playCount

    property bool current: false
    property bool hovered: false
    property bool showSequence: false
    property int sequence: 0
    property bool showAdd: false
    property bool showRemove: false
    property bool showMove: false
    property bool canMoveUp: true
    property bool canMoveDown: true
    property bool showCopy: true
    property bool showSource: true
    property bool showQuality: true
    property bool showOriginal: true
    property bool showPlayCount: true

    // 这些信号必须带上当前行的 index：FmMusicTrackList 的 delegate 把 index
    // 当作“第几首”转给页面。只发无参信号时，QML 会把 undefined 转成 0，
    // 结果就是“点哪首都是第一首”（3.19 验收反馈修复）。
    signal activated(int index)
    signal addRequested(int index)
    signal removeRequested(int index)
    signal moveUpRequested(int index)
    signal moveDownRequested(int index)
    signal contextRequested(int index, real sceneX, real sceneY)

    function sourceLabel() {
        if (control.source === "local")
            return Tr?.map["music_source_local"] ?? control.sourceText
        var key = "music_source_" + String(control.source || "")
        var label = Tr?.map[key] ?? ""
        return label.length > 0 ? label : control.sourceText
    }

    function qualityLabel() {
        var value = String(control.bestQuality || "")
        if (value === "flac24bit")
            return Tr?.map["music_quality_hires"] ?? ""
        if (value === "flac" || value === "lossless")
            return Tr?.map["music_quality_flac"] ?? ""
        if (value === "320k")
            return Tr?.map["music_quality_320k"] ?? ""
        if (value === "128k")
            return Tr?.map["music_quality_128k"] ?? ""
        return ""
    }

    function originalLabel() {
        if (!control.isOriginal)
            return ""
        if (typeof Tr === "undefined" || !Tr)
            return ""
        if (String(control.originalName || "").length > 0)
            return Tr.tf("music_original_tag_with_name", {"name": control.originalName})
        return Tr.map["music_original_tag"] ?? ""
    }

    function playCountLabel() {
        if (typeof Tr === "undefined" || !Tr)
            return ""
        return Tr.tf("music_play_count", {"count": control.playCount})
    }

    height: 56
    radius: Theme?.radiusSm ?? 0
    color: control.current ? (Theme?.accentSoft ?? "transparent")
                           : (control.hovered ? (Theme?.cardHover ?? "transparent") : "transparent")
    Behavior on color { ColorAnimation { duration: Theme?.durationFast ?? 110 } }

    Rectangle {
        width: 3
        height: 22
        radius: 1.5
        color: Theme?.accent ?? "transparent"
        visible: control.current
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
    }

    MouseArea {
        id: hit
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        acceptedButtons: Qt.LeftButton
        onEntered: control.hovered = true
        onExited: control.hovered = false
        onClicked: control.activated(control.index)
    }

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.RightButton
        onPressed: function (mouse) {
            var point = control.mapToItem(null, mouse.x, mouse.y);
            control.contextRequested(control.index, point.x, point.y);
        }
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 10
        anchors.rightMargin: 8
        spacing: Theme?.spacingSm ?? 8

        Text {
            Layout.preferredWidth: control.showSequence ? 26 : 0
            Layout.alignment: Qt.AlignVCenter
            visible: control.showSequence
            horizontalAlignment: Text.AlignRight
            text: control.sequence > 0 ? String(control.sequence) : String(control.index + 1)
            color: control.current ? (Theme?.accent ?? "transparent") : (Theme?.textTertiary ?? "transparent")
            font.pixelSize: Theme?.fontSizeSmall ?? 10
        }

        Item {
            Layout.preferredWidth: 38
            Layout.preferredHeight: 38
            Layout.alignment: Qt.AlignVCenter

            Rectangle {
                anchors.fill: parent
                radius: Theme?.radiusSm ?? 0
                color: Theme?.bgLight ?? "transparent"
            }

            FmIcon {
                anchors.centerIn: parent
                name: "music"
                size: 18
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
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.minimumWidth: 90
            spacing: 2

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme?.spacingXs ?? 4

                Text {
                    Layout.fillWidth: true
                    text: control.name
                    color: control.current ? (Theme?.accent ?? "transparent") : (Theme?.textPrimary ?? "transparent")
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                    font.bold: control.current
                    elide: Text.ElideRight
                }

                FmTag {
                    objectName: "musicTrackOriginalTag"
                    visible: control.showOriginal && control.isOriginal
                    text: control.originalLabel()
                    level: "accent"
                }
            }

            Text {
                Layout.fillWidth: true
                text: {
                    var parts = [];
                    if (control.singer.length > 0) parts.push(control.singer);
                    if (control.album.length > 0) parts.push(control.album);
                    if (parts.length === 0) parts.push(control.sourceLabel());
                    return parts.join("  ·  ");
                }
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                elide: Text.ElideRight
            }
        }

        Text {
            Layout.preferredWidth: 58
            Layout.alignment: Qt.AlignVCenter
            horizontalAlignment: Text.AlignRight
            visible: control.showSource && control.sourceLabel().length > 0
            text: control.sourceLabel()
            color: Theme?.textTertiary ?? "transparent"
            font.pixelSize: Theme?.fontSizeSmall ?? 10
            elide: Text.ElideRight
        }

        Text {
            objectName: "musicTrackQuality"
            Layout.alignment: Qt.AlignVCenter
            visible: control.showQuality && !control.isLocal && control.qualityLabel().length > 0
            text: control.qualityLabel()
            color: Theme?.textPrimary ?? "transparent"
            font.pixelSize: Theme?.fontSizeSmall ?? 10
            font.bold: true
        }

        Text {
            Layout.preferredWidth: 74
            Layout.alignment: Qt.AlignVCenter
            horizontalAlignment: Text.AlignRight
            visible: control.showPlayCount && control.playCount > 0
            text: control.playCountLabel()
            color: Theme?.textTertiary ?? "transparent"
            font.pixelSize: Theme?.fontSizeSmall ?? 10
            elide: Text.ElideRight
        }

        Text {
            Layout.preferredWidth: 50
            Layout.alignment: Qt.AlignVCenter
            horizontalAlignment: Text.AlignRight
            text: control.durationText
            color: Theme?.textTertiary ?? "transparent"
            font.pixelSize: Theme?.fontSizeSmall ?? 10
        }

        FmToolButton {
            objectName: "musicTrackAdd"
            visible: control.showAdd
            iconName: "add"
            iconSize: 13
            side: 26
            onClicked: control.addRequested(control.index)
        }

        FmToolButton {
            objectName: "musicTrackMoveUp"
            visible: control.showMove
            iconName: "chevron-up"
            iconSize: 12
            side: 26
            opacity: control.hovered ? 1 : 0
            enabled: control.hovered && control.canMoveUp
            onClicked: control.moveUpRequested(control.index)
        }

        FmToolButton {
            objectName: "musicTrackMoveDown"
            visible: control.showMove
            iconName: "chevron-down"
            iconSize: 12
            side: 26
            opacity: control.hovered ? 1 : 0
            enabled: control.hovered && control.canMoveDown
            onClicked: control.moveDownRequested(control.index)
        }

        FmToolButton {
            objectName: "musicTrackRemove"
            visible: control.showRemove
            iconName: "close"
            iconSize: 11
            side: 26
            opacity: control.hovered ? 1 : 0
            enabled: control.hovered
            onClicked: control.removeRequested(control.index)
        }

        FmToolButton {
            objectName: "musicTrackCopy"
            visible: control.showCopy
            iconName: "copy"
            iconSize: 14
            side: 28
            opacity: control.hovered ? 1 : 0
            enabled: control.hovered
            onClicked: {
                if (typeof Music !== "undefined" && Music)
                    Music.copyText(control.name + (control.singer.length > 0 ? " - " + control.singer : ""));
            }
        }
    }
}
