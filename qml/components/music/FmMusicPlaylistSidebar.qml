// FmMusicPlaylistSidebar.qml —— 我的音乐左侧歌单栏（阶段 3.18）。
//
// 什么时候用它：音乐页“歌单管理”视图的左侧；系统歌单、自建歌单、网易云远程歌单。
// 什么时候不要用：发现页的推荐歌单卡片、在线搜索结果（各有自己的卡片/行组件）。
//
// 只显示与发信号，不写业务：选中交给 Music.library，重命名/删除由页面弹窗。
// 右键菜单的坐标用场景坐标送出，页面里的 FmMusicMenu 再换算，避免 Sidebar
// 内部坐标系与菜单容器不一致。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Item {
    id: sidebar
    objectName: "musicPlaylistSidebar"

    property var lib: null
    property var account: null

    signal playlistActivated(string playlistId)
    signal playlistContextRequested(string playlistId, string name, bool system, real sceneX, real sceneY)
    signal createRequested()
    signal loginRequested()
    signal syncRequested()

    readonly property var allPlaylists: sidebar.lib ? sidebar.lib.playlists : []
    readonly property var systemPlaylists: filterPlaylists(true, false)
    readonly property var userPlaylists: filterPlaylists(false, false)
    readonly property var remotePlaylists: sidebar.lib ? sidebar.lib.remotePlaylists : []
    readonly property bool loggedIn: !!(sidebar.account && sidebar.account.loggedIn)
    //: 未登录时不展示远程歌单（登出后 AccountManager 会清空，这里再加一道显示闸门）。
    readonly property var visibleRemotePlaylists: sidebar.loggedIn ? sidebar.remotePlaylists : []

    function filterPlaylists(system, remote) {
        var source = sidebar.allPlaylists
        var out = []
        for (var i = 0; i < source.length; ++i) {
            var item = source[i]
            if (!!item.remote === remote && !!item.system === system)
                out.push(item)
        }
        return out
    }

    function displayName(item) {
        if (!item)
            return ""
        var key = String(item.nameKey || "")
        if (key.length > 0)
            return Tr?.map[key] ?? String(item.name || "")
        return String(item.name || "")
    }

    function selectionKey(item) {
        return item && item.remote ? ("wy:" + String(item.id || "")) : String(item && item.id || "")
    }

    function isActive(item) {
        return !!sidebar.lib && sidebar.lib.selectedId === selectionKey(item)
    }

    function countText(item) {
        return String(Tr?.map["music_song_count"] ?? "").replace("{count}", String(item && item.count || 0))
    }

    function iconFor(item) {
        if (!item)
            return "note"
        if (item.icon === "heart")
            return "music"
        if (item.icon === "history")
            return "history"
        if (item.icon === "folder")
            return "folder"
        if (item.remote)
            return "account"
        return "note"
    }

    Component {
        id: playlistRow

        Rectangle {
            id: row
            objectName: "musicPlaylistSidebarRow"
            required property var modelData
            readonly property bool active: sidebar.isActive(modelData)
            readonly property bool remote: !!modelData.remote
            Layout.fillWidth: true
            Layout.preferredHeight: 46
            radius: Theme?.radiusMd ?? 0
            color: row.active ? (Theme?.accentSoft ?? "transparent")
                              : (rowMouse.containsMouse ? (Theme?.cardHover ?? "transparent") : "transparent")

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 8
                anchors.rightMargin: 8
                spacing: Theme?.spacingSm ?? 8

                Rectangle {
                    Layout.preferredWidth: 28
                    Layout.preferredHeight: 28
                    Layout.alignment: Qt.AlignVCenter
                    radius: Theme?.radiusSm ?? 0
                    color: row.active ? (Theme?.accent ?? "transparent") : (Theme?.bgLight ?? "transparent")

                    Image {
                        anchors.fill: parent
                        source: row.remote ? String(modelData.cover || "") : ""
                        fillMode: Image.PreserveAspectCrop
                        asynchronous: true
                        cache: true
                        visible: row.remote && String(modelData.cover || "").length > 0 && status === Image.Ready
                    }

                    FmIcon {
                        anchors.centerIn: parent
                        visible: !row.remote || String(modelData.cover || "").length === 0
                        name: sidebar.iconFor(modelData)
                        size: 14
                        color: row.active ? (Theme?.accentText ?? "transparent")
                                          : (Theme?.textSecondary ?? "transparent")
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 1

                    Text {
                        Layout.fillWidth: true
                        text: sidebar.displayName(modelData)
                        color: row.active ? (Theme?.accent ?? "transparent") : (Theme?.textPrimary ?? "transparent")
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        font.bold: row.active
                        elide: Text.ElideRight
                    }

                    Text {
                        Layout.fillWidth: true
                        text: sidebar.countText(modelData)
                        color: Theme?.textTertiary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        elide: Text.ElideRight
                    }
                }
            }

            MouseArea {
                id: rowMouse
                anchors.fill: parent
                hoverEnabled: true
                acceptedButtons: Qt.LeftButton
                cursorShape: Qt.PointingHandCursor
                onClicked: sidebar.playlistActivated(sidebar.selectionKey(modelData))
            }

            MouseArea {
                anchors.fill: parent
                acceptedButtons: Qt.RightButton
                enabled: !row.remote
                onPressed: function (mouse) {
                    var point = row.mapToItem(null, mouse.x, mouse.y)
                    sidebar.playlistContextRequested(
                        String(modelData.id || ""),
                        sidebar.displayName(modelData),
                        !!modelData.system,
                        point.x,
                        point.y)
                }
            }
        }
    }

    Rectangle {
        anchors.fill: parent
        radius: Theme?.radiusLg ?? 0
        color: Theme?.cardBg ?? "transparent"
        border.width: 1
        border.color: Theme?.cardBorder ?? "transparent"

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: Theme?.spacingSm ?? 8
            spacing: Theme?.spacingXs ?? 4

            RowLayout {
                Layout.fillWidth: true
                Layout.leftMargin: 4
                Layout.rightMargin: 2
                spacing: Theme?.spacingXs ?? 4

                Text {
                    Layout.fillWidth: true
                    text: Tr?.map["music_my_playlists"] ?? ""
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                    font.bold: true
                    elide: Text.ElideRight
                }

                FmToolButton {
                    objectName: "musicPlaylistCreate"
                    iconName: "add"
                    iconSize: 14
                    side: 28
                    onClicked: sidebar.createRequested()
                }
            }

            FmScrollView {
                id: scroller
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentWidth: availableWidth

                ColumnLayout {
                    width: scroller.availableWidth
                    spacing: 2

                    Repeater {
                        model: sidebar.systemPlaylists
                        delegate: playlistRow
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 1
                        Layout.topMargin: 6
                        Layout.bottomMargin: 4
                        color: Theme?.divider ?? "transparent"
                    }

                    Repeater {
                        model: sidebar.userPlaylists
                        delegate: playlistRow
                    }

                    Text {
                        Layout.fillWidth: true
                        Layout.leftMargin: 8
                        Layout.rightMargin: 8
                        Layout.topMargin: 4
                        visible: sidebar.userPlaylists.length === 0
                        text: Tr?.map["music_no_user_playlists_sidebar"] ?? ""
                        color: Theme?.textTertiary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        wrapMode: Text.WordWrap
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 1
                        Layout.topMargin: 10
                        Layout.bottomMargin: 4
                        color: Theme?.divider ?? "transparent"
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Layout.leftMargin: 4
                        Layout.rightMargin: 2
                        spacing: Theme?.spacingXs ?? 4

                        Text {
                            Layout.fillWidth: true
                            text: Tr?.map["music_wy_playlists"] ?? ""
                            color: Theme?.textSecondary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                            font.bold: true
                            elide: Text.ElideRight
                        }

                        FmProgressRing {
                            visible: sidebar.loggedIn && sidebar.account && sidebar.account.busy
                            Layout.preferredWidth: 14
                            Layout.preferredHeight: 14
                            lineWidth: 2
                            indeterminate: true
                        }

                        FmToolButton {
                            objectName: "musicWySync"
                            visible: sidebar.loggedIn
                            iconName: "refresh"
                            iconSize: 12
                            side: 24
                            onClicked: sidebar.syncRequested()
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Layout.leftMargin: 6
                        Layout.rightMargin: 6
                        visible: sidebar.loggedIn && sidebar.account
                                 && String(sidebar.account.nickname || "").length > 0
                        spacing: 4

                        FmIcon {
                            name: "account"
                            size: 12
                            color: Theme?.textTertiary ?? "transparent"
                        }

                        Text {
                            Layout.fillWidth: true
                            text: sidebar.account ? String(sidebar.account.nickname || "") : ""
                            color: Theme?.textTertiary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                            elide: Text.ElideRight
                        }
                    }

                    FmButton {
                        objectName: "musicWyLoginButton"
                        Layout.fillWidth: true
                        visible: !sidebar.loggedIn
                        primary: false
                        iconName: "account"
                        text: Tr?.map["music_wy_login_hint"] ?? ""
                        onClicked: sidebar.loginRequested()
                    }

                    FmTag {
                        Layout.leftMargin: 6
                        visible: sidebar.loggedIn && sidebar.lib && sidebar.lib.remoteSyncFailed
                        text: Tr?.map["music_wy_sync_failed"] ?? ""
                        level: "error"
                    }

                    Repeater {
                        model: sidebar.visibleRemotePlaylists
                        delegate: playlistRow
                    }

                    Text {
                        Layout.fillWidth: true
                        Layout.leftMargin: 6
                        Layout.rightMargin: 6
                        Layout.topMargin: 4
                        visible: sidebar.loggedIn && !(sidebar.lib && sidebar.lib.remoteSyncFailed)
                                 && sidebar.visibleRemotePlaylists.length === 0
                        text: Tr?.map["music_no_remote_playlists"] ?? ""
                        color: Theme?.textTertiary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        wrapMode: Text.WordWrap
                    }
                }
            }
        }
    }
}
