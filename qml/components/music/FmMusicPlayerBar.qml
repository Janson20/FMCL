// FmMusicPlayerBar.qml —— 页面底部播放栏（阶段 3.16）。
//
// 左：封面/曲名/歌手；中：模式、上一首、播放、下一首、停止；右：音量/展开。
// 贴底细线进度可拖动；所有状态读 Music.player，不在这里保存业务状态。

import QtQuick
import QtQuick.Layouts
import ".."

Rectangle {
    id: control
    objectName: "musicPlayerBar"

    property var player: null
    property bool expanded: false

    implicitHeight: 92
    color: Theme?.barBg ?? "transparent"

    Rectangle {
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: 1
        color: Theme?.cardBorder ?? "transparent"
    }

    // ── 贴底进度线（可拖动）──────────────────────────────
    Item {
        id: progressTrack
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 4

        Rectangle {
            anchors.fill: parent
            color: Theme?.cardBorder ?? "transparent"
        }
        Rectangle {
            height: parent.height
            width: control.player && control.player.duration > 0
                   ? parent.width * Math.min(1, control.player.position / control.player.duration) : 0
            color: Theme?.accent ?? "transparent"
        }
        MouseArea {
            anchors.fill: parent
            anchors.topMargin: -6
            anchors.bottomMargin: -4
            cursorShape: Qt.PointingHandCursor
            onPressed: function (mouse) {
                control.seekAt(mouse.x, width);
            }
            onPositionChanged: function (mouse) {
                if (pressed)
                    control.seekAt(mouse.x, width);
            }
        }
    }

    function seekAt(x, width) {
        if (!control.player || control.player.duration <= 0)
            return;
        var ratio = Math.max(0, Math.min(1, x / Math.max(1, width)));
        control.player.seek(Math.round(ratio * control.player.duration));
    }

    function modeIcon(name) {
        if (name === "order") return "arrow-right";
        if (name === "loop_single") return "repeat-one";
        if (name === "shuffle") return "shuffle";
        return "repeat";
    }

    function modeText(name) {
        if (name === "order") return Tr?.map["music_mode_order"] ?? "";
        if (name === "loop_single") return Tr?.map["music_mode_loop_single"] ?? "";
        if (name === "shuffle") return Tr?.map["music_mode_shuffle"] ?? "";
        return Tr?.map["music_mode_loop_list"] ?? "";
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: Theme?.spacingMd ?? 12
        anchors.rightMargin: Theme?.spacingMd ?? 12
        anchors.topMargin: 4
        anchors.bottomMargin: 8
        spacing: Theme?.spacingMd ?? 12

        // ── 左：封面与曲目 ─────────────────────────────────
        RowLayout {
            Layout.preferredWidth: 300
            Layout.minimumWidth: 180
            Layout.fillHeight: true
            spacing: Theme?.spacingSm ?? 8

            Item {
                Layout.preferredWidth: 54
                Layout.preferredHeight: 54
                Layout.alignment: Qt.AlignVCenter

                Rectangle {
                    anchors.fill: parent
                    radius: Theme?.radiusMd ?? 0
                    color: Theme?.bgLight ?? "transparent"
                }
                FmIcon {
                    anchors.centerIn: parent
                    name: "music"
                    size: 22
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
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        if (typeof Music !== "undefined" && Music)
                            Music.setExpanded(true);
                    }
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignVCenter
                spacing: 2

                Text {
                    objectName: "musicPlayerTitle"
                    Layout.fillWidth: true
                    text: control.player && control.player.title.length > 0
                          ? control.player.title : (Tr?.map["music_no_track"] ?? "")
                    color: control.player && control.player.title.length > 0
                           ? (Theme?.textPrimary ?? "transparent") : (Theme?.textTertiary ?? "transparent")
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                    font.bold: true
                    elide: Text.ElideRight
                }

                Text {
                    Layout.fillWidth: true
                    text: control.player && control.player.artist.length > 0
                          ? control.player.artist
                          : (Tr?.map["music_player_hint"] ?? "")
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                    elide: Text.ElideRight
                }
            }
        }

        Item { Layout.fillWidth: true }

        // ── 中：传输控件 ───────────────────────────────────
        RowLayout {
            Layout.alignment: Qt.AlignVCenter
            spacing: Theme?.spacingXs ?? 4

            FmToolButton {
                iconName: control.modeIcon(control.player ? control.player.modeName : "")
                iconSize: 16
                side: 30
                iconColor: Theme?.textSecondary ?? "transparent"
                onClicked: {
                    if (control.player)
                        control.player.cycleMode();
                }
            }

            FmToolButton {
                iconName: "prev"
                iconSize: 18
                side: 32
                onClicked: {
                    if (control.player)
                        control.player.previous();
                }
            }

            FmButton {
                objectName: "musicPlayButton"
                iconOnly: true
                primary: true
                iconName: control.player && control.player.playing ? "pause" : "play"
                enabled: control.player && control.player.queueCount > 0
                onClicked: {
                    if (control.player)
                        control.player.toggle();
                }
            }

            FmToolButton {
                iconName: "next"
                iconSize: 18
                side: 32
                onClicked: {
                    if (control.player)
                        control.player.next();
                }
            }

            FmToolButton {
                iconName: "stop"
                iconSize: 14
                side: 28
                onClicked: {
                    if (control.player)
                        control.player.stop();
                }
            }
        }

        Item { Layout.fillWidth: true }

        // ── 右：时间 / 音量 / 展开 ─────────────────────────
        RowLayout {
            Layout.alignment: Qt.AlignVCenter
            spacing: Theme?.spacingXs ?? 4

            Text {
                text: control.player
                      ? control.player.positionText + " / " + control.player.durationText : "00:00 / 00:00"
                color: Theme?.textTertiary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
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

            FmSlider {
                id: volumeSlider
                Layout.preferredWidth: 86
                from: 0
                to: 100
                showValue: false
                value: 0
                onMoved: {
                    if (control.player)
                        control.player.setVolume(Math.round(value));
                }
            }

            FmToolButton {
                iconName: control.expanded ? "chevron-down" : "chevron-up"
                iconSize: 16
                side: 30
                onClicked: {
                    if (typeof Music !== "undefined" && Music)
                        Music.toggleExpanded();
                }
            }
        }
    }

    Binding {
        target: volumeSlider
        property: "value"
        value: control.player ? control.player.volume : 0
        when: !volumeSlider.pressed
        restoreMode: Binding.RestoreNone
    }
}
