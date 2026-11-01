// FmMusicOnlineRow.qml —— 在线搜索结果行（阶段 3.17）。
//
// 与本地/队列共用的 FmMusicTrackRow 分开：在线行要序号、原唱徽章、播放量、
// 音源标签与「加入歌单」按钮，塞进旧行会把本地行也撑高。数据由页面传入。

import QtQuick
import QtQuick.Layouts
import ".."

Rectangle {
    id: control
    objectName: "musicOnlineRow"

    required property int index
    required property string uid
    required property string name
    required property string singer
    required property string source
    required property string bestQuality
    required property bool isOriginal
    required property string originalName
    required property int playCount
    required property string durationText
    required property string cover

    property bool current: false
    property bool hovered: false

    signal activated()
    signal addRequested()

    height: 58
    radius: Theme?.radiusSm ?? 0
    color: control.current ? (Theme?.accentSoft ?? "transparent")
                           : (control.hovered ? (Theme?.cardHover ?? "transparent") : "transparent")
    Behavior on color { ColorAnimation { duration: Theme?.durationFast ?? 110 } }

    function shortName() {
        var value = String(control.name || "")
        return value.length > 35 ? value.substring(0, 33) + "..." : value
    }

    function qualityLabel(value) {
        if (value === "flac24bit")
            return Tr?.map["music_quality_hires"] ?? ""
        if (value === "flac" || value === "lossless")
            return Tr?.map["music_quality_flac"] ?? ""
        if (value === "320k")
            return Tr?.map["music_quality_320k"] ?? ""
        if (value === "128k")
            return Tr?.map["music_quality_128k"] ?? ""
        return Tr?.map["music_quality_auto"] ?? ""
    }

    function sourceLabel(value) {
        var key = "music_source_" + String(value || "")
        return Tr?.map[key] ?? String(value || "")
    }

    function playCountLabel() {
        if (typeof Tr === "undefined" || !Tr)
            return ""
        return Tr.tf("music_play_count", {"count": control.playCount})
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

    MouseArea {
        id: hit
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onEntered: control.hovered = true
        onExited: control.hovered = false
        onClicked: control.activated()
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 8
        anchors.rightMargin: 6
        spacing: Theme?.spacingSm ?? 8

        Text {
            Layout.preferredWidth: 26
            Layout.alignment: Qt.AlignVCenter
            horizontalAlignment: Text.AlignRight
            text: String(control.index + 1)
            color: control.current ? (Theme?.accent ?? "transparent")
                                   : (Theme?.textTertiary ?? "transparent")
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
                size: 17
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
            Layout.minimumWidth: 120
            spacing: 2

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme?.spacingXs ?? 4

                Text {
                    Layout.fillWidth: true
                    text: control.shortName()
                    color: control.current ? (Theme?.accent ?? "transparent")
                                           : (Theme?.textPrimary ?? "transparent")
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                    font.bold: control.current
                    elide: Text.ElideRight
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme?.spacingXs ?? 4

                FmTag {
                    objectName: "musicOnlineOriginalTag"
                    visible: control.isOriginal
                    text: control.originalLabel()
                    level: "accent"
                }

                Text {
                    Layout.fillWidth: true
                    text: control.singer.length > 0
                          ? control.singer
                          : control.sourceLabel(control.source)
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                    elide: Text.ElideRight
                }

                Text {
                    text: control.sourceLabel(control.source)
                    color: Theme?.textTertiary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                }

                Text {
                    objectName: "musicOnlineQualityTag"
                    visible: String(control.bestQuality || "").length > 0
                    text: control.qualityLabel(control.bestQuality)
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                    font.bold: true
                }
            }
        }

        Text {
            Layout.preferredWidth: 96
            Layout.alignment: Qt.AlignVCenter
            horizontalAlignment: Text.AlignRight
            visible: control.playCount > 0
            text: control.playCountLabel()
            color: Theme?.textTertiary ?? "transparent"
            font.pixelSize: Theme?.fontSizeSmall ?? 10
            elide: Text.ElideRight
        }

        Text {
            Layout.preferredWidth: 46
            Layout.alignment: Qt.AlignVCenter
            horizontalAlignment: Text.AlignRight
            text: control.durationText
            color: Theme?.textTertiary ?? "transparent"
            font.pixelSize: Theme?.fontSizeSmall ?? 10
        }

        FmToolButton {
            objectName: "musicOnlineAdd"
            iconName: "add"
            iconSize: 14
            side: 28
            onClicked: control.addRequested()
        }
    }
}
