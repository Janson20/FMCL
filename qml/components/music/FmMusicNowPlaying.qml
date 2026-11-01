// FmMusicNowPlaying.qml —— 展开播放页（阶段 3.16）。
//
// 大封面 + 歌词滚动 + 传输控件。歌词来自 Music.player.lyricLines；本文件不解析 LRC。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Rectangle {
    id: control
    objectName: "musicNowPlaying"

    property var player: null
    signal collapseRequested()

    color: Theme?.windowBg ?? "transparent"

    function modeIcon(name) {
        if (name === "order") return "arrow-right";
        if (name === "loop_single") return "repeat-one";
        if (name === "shuffle") return "shuffle";
        return "repeat";
    }

    Keys.onEscapePressed: control.collapseRequested()
    focus: true

    // 覆盖层必须自己吃事件：根节点是普通 Item 时，空白处点击会穿到下面的页面。
    FmMusicOverlayBackdrop {
        anchors.fill: parent
        blockerObjectName: "musicNowPlayingBlocker"
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme?.spacingLg ?? 16
        spacing: Theme?.spacingMd ?? 12

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 8

            FmIcon {
                name: "music"
                size: Theme?.fontSizeTitle ?? 18
                color: Theme?.accent ?? "transparent"
            }
            Text {
                Layout.fillWidth: true
                text: Tr?.map["music_now_playing"] ?? ""
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeTitle ?? 18
                font.bold: true
            }
            FmToolButton {
                objectName: "musicNowPlayingCollapse"
                iconName: "chevron-down"
                iconSize: 18
                side: 34
                onClicked: control.collapseRequested()
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Theme?.spacingLg ?? 16

            // ── 左：封面与曲目信息 ─────────────────────────
            ColumnLayout {
                Layout.preferredWidth: Math.min(340, control.width * 0.36)
                Layout.minimumWidth: 240
                Layout.maximumWidth: 380
                Layout.fillWidth: false
                Layout.fillHeight: true
                spacing: Theme?.spacingSm ?? 8

                Item {
                    Layout.preferredWidth: Math.min(320, control.width * 0.34)
                    Layout.preferredHeight: Layout.preferredWidth
                    Layout.alignment: Qt.AlignHCenter

                    Rectangle {
                        anchors.fill: parent
                        radius: Theme?.radiusLg ?? 0
                        color: Theme?.bgLight ?? "transparent"
                    }
                    FmIcon {
                        anchors.centerIn: parent
                        name: "music"
                        size: 64
                        color: Theme?.textTertiary ?? "transparent"
                    }
                    Image {
                        anchors.fill: parent
                        source: control.player ? control.player.coverUrl : ""
                        fillMode: Image.PreserveAspectCrop
                        asynchronous: true
                        cache: true
                        visible: String(source).length > 0 && status === Image.Ready
                    }
                }

                Text {
                    Layout.fillWidth: true
                    text: control.player && control.player.title.length > 0
                          ? control.player.title : (Tr?.map["music_no_track"] ?? "")
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeLarge ?? 14
                    font.bold: true
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                }

                Text {
                    Layout.fillWidth: true
                    text: control.player ? control.player.artist : ""
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                    horizontalAlignment: Text.AlignHCenter
                    elide: Text.ElideRight
                }

                Text {
                    Layout.fillWidth: true
                    text: control.player && control.player.title.length > 0
                          ? (control.player.album + (control.player.qualityLabel.length > 0
                             ? "  ·  " + control.player.qualityLabel : ""))
                          : ""
                    color: Theme?.textTertiary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                    horizontalAlignment: Text.AlignHCenter
                    elide: Text.ElideRight
                }

                Item { Layout.fillHeight: true }

                FmButton {
                    Layout.alignment: Qt.AlignHCenter
                    primary: false
                    text: Tr?.map["music_copy_info"] ?? ""
                    iconName: "copy"
                    enabled: control.player && control.player.title.length > 0
                    onClicked: {
                        if (control.player)
                            Music.copyText(control.player.title
                                           + (control.player.artist.length > 0 ? " - " + control.player.artist : ""));
                    }
                }
            }

            // ── 右：歌词 ───────────────────────────────────
            Rectangle {
                Layout.fillWidth: true
                Layout.minimumWidth: 300
                Layout.fillHeight: true
                radius: Theme?.radiusLg ?? 0
                color: Theme?.cardBg ?? "transparent"
                border.width: 1
                border.color: Theme?.cardBorder ?? "transparent"

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: Theme?.spacingMd ?? 12
                    spacing: 0

                    RowLayout {
                        Layout.fillWidth: true
                        Layout.bottomMargin: 4

                        FmIcon {
                            name: "lyrics"
                            size: 15
                            color: Theme?.textSecondary ?? "transparent"
                        }
                        Text {
                            Layout.fillWidth: true
                            text: Tr?.map["music_lyrics"] ?? ""
                            color: Theme?.textSecondary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                            leftPadding: 6
                        }
                    }

                    Item {
                        Layout.fillWidth: true
                        Layout.fillHeight: true

                        ListView {
                            id: lyricList
                            anchors.fill: parent
                            clip: true
                            spacing: Theme?.spacingXs ?? 4
                            model: control.player ? control.player.lyricLines : []
                            currentIndex: control.player ? control.player.lyricIndex : -1
                            boundsBehavior: Flickable.StopAtBounds
                            ScrollBar.vertical: FmScrollBar { }

                            delegate: Item {
                                required property var modelData
                                required property int index
                                width: lyricList.width
                                height: lineColumn.implicitHeight + 8

                                Column {
                                    id: lineColumn
                                    width: parent.width
                                    spacing: 2

                                    Text {
                                        width: parent.width
                                        text: String(modelData.text || "")
                                        color: index === lyricList.currentIndex
                                               ? (Theme?.accent ?? "transparent") : (Theme?.textSecondary ?? "transparent")
                                        font.pixelSize: index === lyricList.currentIndex
                                                         ? (Theme?.fontSizeLarge ?? 14) : (Theme?.fontSizeBase ?? 12)
                                        font.bold: index === lyricList.currentIndex
                                        horizontalAlignment: Text.AlignHCenter
                                        wrapMode: Text.WordWrap
                                    }
                                    Text {
                                        width: parent.width
                                        visible: String(modelData.sub || "").length > 0
                                        text: String(modelData.sub || "")
                                        color: Theme?.textTertiary ?? "transparent"
                                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                                        horizontalAlignment: Text.AlignHCenter
                                        wrapMode: Text.WordWrap
                                    }
                                }
                            }

                            onCurrentIndexChanged: {
                                if (currentIndex >= 0)
                                    positionViewAtIndex(currentIndex, ListView.Center);
                            }
                        }

                        Text {
                            anchors.centerIn: parent
                            visible: !control.player || !control.player.hasLyrics
                            text: Tr?.map["music_lyric_empty"] ?? ""
                            color: Theme?.textTertiary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeBase ?? 12
                        }
                    }
                }
            }
        }

        // ── 底部控制 ───────────────────────────────────────
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 8

            Text {
                text: control.player ? control.player.positionText : "00:00"
                color: Theme?.textTertiary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
            }

            FmSlider {
                id: positionSlider
                Layout.fillWidth: true
                from: 0
                to: control.player && control.player.duration > 0 ? control.player.duration : 1
                showValue: false
                value: 0
                onMoved: {
                    if (control.player)
                        control.player.seek(Math.round(value));
                }
            }

            Text {
                text: control.player ? control.player.durationText : "00:00"
                color: Theme?.textTertiary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
            }

            FmToolButton {
                iconName: control.modeIcon(control.player ? control.player.modeName : "")
                iconSize: 15
                side: 30
                onClicked: {
                    if (control.player)
                        control.player.cycleMode();
                }
            }
            FmToolButton {
                iconName: "prev"
                iconSize: 17
                side: 32
                onClicked: {
                    if (control.player)
                        control.player.previous();
                }
            }
            FmButton {
                iconOnly: true
                primary: true
                iconName: control.player && control.player.playing ? "pause" : "play"
                onClicked: {
                    if (control.player)
                        control.player.toggle();
                }
            }
            FmToolButton {
                iconName: "next"
                iconSize: 17
                side: 32
                onClicked: {
                    if (control.player)
                        control.player.next();
                }
            }
            FmToolButton {
                iconName: "stop"
                iconSize: 13
                side: 28
                onClicked: {
                    if (control.player)
                        control.player.stop();
                }
            }
            FmToolButton {
                iconName: control.player && control.player.muted ? "mute" : "volume"
                iconSize: 15
                side: 28
                onClicked: {
                    if (control.player)
                        control.player.toggleMute();
                }
            }
        }
    }

    Binding {
        target: positionSlider
        property: "value"
        value: control.player ? control.player.position : 0
        when: !positionSlider.pressed
        restoreMode: Binding.RestoreNone
    }
}
