// FmMusicFooterBar.qml —— 主窗口底部音乐控制条（A-12 / K-15）。
//
// 与音乐页里的 FmMusicPlayerBar 不是同一件东西：
//   * FmMusicPlayerBar 是页面内播放栏，带进度/音量/歌词/展开；
//   * 本组件只保留旧 Tk 底栏的三颗按钮与「歌名 - 歌手」一行（截断 40 字），
//     在任意页面都能看见，避免为了切歌先回音乐页。
//
// 可见性由壳层（App.qml）决定：有当前曲目且处于播放/暂停时显示；音乐路由内隐藏，
// 否则会和页内播放栏重复。颜色全部走 Theme，图标走 FmIcon，无渐变色。

import QtQuick
import QtQuick.Layouts
import ".."

Item {
    id: bar
    objectName: "musicFooterBar"

    implicitHeight: 34
    height: implicitHeight

    //: 当前播放器（由壳层注入；null 时整条不显示）
    property var player: null
    //: 旧实现只保留最后一段文字，超长截断（services/music_local.FOOTER_TEXT_MAX）
    readonly property string trackText: bar.player ? bar.formatText(String(bar.player.title || ""),
                                                                    String(bar.player.artist || "")) : ""
    readonly property bool hasTrack: bar.player !== null && String(bar.player.title || "").length > 0
    readonly property bool active: bar.hasTrack && (bar.player.playing || bar.player.paused)

    function formatText(title, artist) {
        var text = title
        if (artist.length > 0)
            text = title + " - " + artist
        if (text.length > 40)
            text = text.substring(0, 38) + "..."
        return text
    }

    Rectangle {
        anchors.fill: parent
        color: Theme?.barBg ?? "transparent"

        Rectangle {
            objectName: "musicFooterDivider"
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            height: 1
            color: Theme?.divider ?? "transparent"
        }
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: Theme?.spacingMd ?? 0
        anchors.rightMargin: Theme?.spacingMd ?? 0
        spacing: Theme?.spacingSm ?? 0

        FmIcon {
            name: "music"
            size: Theme?.fontSizeBase ?? 12
            color: Theme?.textSecondary ?? "transparent"
            Layout.alignment: Qt.AlignVCenter
        }

        Text {
            objectName: "musicFooterText"
            Layout.fillWidth: true
            text: bar.trackText
            color: Theme?.textPrimary ?? "transparent"
            font.pixelSize: Theme?.fontSizeSmall ?? 10
            elide: Text.ElideRight
        }

        FmToolButton {
            objectName: "musicFooterPrev"
            iconName: "prev"
            side: 24
            onClicked: if (bar.player)
                           bar.player.previous()
        }

        FmToolButton {
            objectName: "musicFooterPlay"
            iconName: (bar.player && bar.player.playing) ? "pause" : "play"
            side: 24
            onClicked: if (bar.player)
                           bar.player.toggle()
        }

        FmToolButton {
            objectName: "musicFooterNext"
            iconName: "next"
            side: 24
            onClicked: if (bar.player)
                           bar.player.next()
        }
    }
}
