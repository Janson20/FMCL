// FmMusicAddToPlaylist.qml —— 全局“加入歌单”宿主（阶段 3.18 统一）。
//
// 什么时候用它：音乐页根部或歌单详情覆盖层里放一个，所有列表的加入按钮
//   都走它的 openFor(track)；页面不要各自再复制一份 picker + create 逻辑。
// 什么时候不要用：歌单增删改页面本身（那是 FmMusicLibraryView 的侧栏）。
//
// 只做浮层生命周期与一次桥调用；歌单是否存在/是否勾选由 Music.library 返回。

import QtQuick

Item {
    id: host
    objectName: "musicAddToPlaylist"

    property bool opened: false
    property var track: null
    property var containedIds: []

    signal closed()

    readonly property var lib: (typeof Music !== "undefined" && Music && Music.ready)
                               ? Music.library : null

    function userPlaylists() {
        var all = host.lib ? host.lib.playlists : []
        var out = []
        for (var i = 0; i < all.length; ++i) {
            if (!all[i].system && !all[i].remote)
                out.push(all[i])
        }
        return out
    }

    function openFor(value) {
        if (!value || !host.lib)
            return
        host.track = value
        host.containedIds = host.lib.playlistsContaining(value)
        host.opened = true
    }

    function close() {
        if (!host.opened)
            return
        host.opened = false
        host.closed()
    }

    function createAndAdd(name, value) {
        if (typeof Music === "undefined" || !Music)
            return
        var created = Music.createPlaylist(String(name || ""))
        if (created && created.id)
            Music.addToPlaylist(String(created.id), value)
        host.close()
    }

    FmMusicPlaylistPicker {
        anchors.fill: parent
        opened: host.opened
        track: host.track
        playlists: host.userPlaylists()
        containedIds: host.containedIds
        onClosed: host.close()
        onSelected: function (playlistId, value) {
            if (typeof Music !== "undefined" && Music)
                Music.addToPlaylist(String(playlistId), value)
            host.close()
        }
        onCreateRequested: function (name, value) { host.createAndAdd(name, value) }
    }
}
