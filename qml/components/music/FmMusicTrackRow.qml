// FmMusicTrackRow.qml —— 本地/队列共用的曲目行（阶段 3.16）。
//
// 只负责画一行与发信号；播放、复制由页面/桥决定。颜色全部 Theme.*，图标 FmIcon。

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
    required property string sourceText
    required property bool isLocal
    property bool current: false
    property bool hovered: false

    signal activated()

    height: 54
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
        onEntered: control.hovered = true
        onExited: control.hovered = false
        onClicked: control.activated()
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 10
        anchors.rightMargin: 8
        spacing: Theme?.spacingSm ?? 8

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
            Layout.minimumWidth: 80
            spacing: 2

            Text {
                Layout.fillWidth: true
                text: control.name
                color: control.current ? (Theme?.accent ?? "transparent") : (Theme?.textPrimary ?? "transparent")
                font.pixelSize: Theme?.fontSizeBase ?? 12
                font.bold: control.current
                elide: Text.ElideRight
            }

            Text {
                Layout.fillWidth: true
                text: {
                    var parts = [];
                    if (control.singer.length > 0) parts.push(control.singer);
                    if (control.album.length > 0) parts.push(control.album);
                    if (parts.length === 0) parts.push(control.sourceText);
                    return parts.join("  ·  ");
                }
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                elide: Text.ElideRight
            }
        }

        Text {
            Layout.preferredWidth: 52
            Layout.alignment: Qt.AlignVCenter
            horizontalAlignment: Text.AlignRight
            text: control.durationText
            color: Theme?.textTertiary ?? "transparent"
            font.pixelSize: Theme?.fontSizeSmall ?? 10
        }

        FmToolButton {
            objectName: "musicTrackCopy"
            iconName: "copy"
            iconSize: 14
            side: 28
            opacity: control.hovered ? 1 : 0
            onClicked: {
                if (typeof Music !== "undefined" && Music)
                    Music.copyText(control.name + (control.singer.length > 0 ? " - " + control.singer : ""));
            }
        }
    }
}
