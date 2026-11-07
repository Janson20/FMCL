// FmMusicLibraryView.qml —— 我的音乐 / 歌单管理（阶段 3.18）。
//
// 什么时候用它：音乐页的「歌单」标签页；左侧系统/自建/网易云歌单，右侧曲目。
// 什么时候不要用：发现页的推荐歌单（只读卡片）、本地文件夹管理（FmMusicLocalView）。
//
// 数据全部来自 Music.library / Music.player / Music.account；本文件只组合
// 列表、排序按钮、右键菜单、新建/重命名/删除弹窗与“加入歌单”浮层。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."
import "../dialogs"

Item {
    id: control
    objectName: "musicLibraryView"

    property var lib: null
    property var player: null
    property var account: null
    property var addHost: null

    property string nameMode: ""
    property string nameTargetId: ""
    property string nameTargetName: ""
    property bool deleteOpen: false
    property string deleteId: ""
    property string deleteName: ""
    property string menuMode: ""
    property var menuItems: []
    property string menuPlaylistId: ""
    property string menuPlaylistName: ""
    property int menuTrackIndex: -1
    property bool accountDialogOpen: false

    readonly property bool ready: !!control.lib
    readonly property bool hasSelection: control.ready && String(control.lib.selectedId || "").length > 0
    readonly property bool remote: control.ready && control.lib.selectedIsRemote
    readonly property bool selectedIsUser: control.ready && control.lib.selectedIsUser
    readonly property bool removable: control.ready && !control.remote
                                     && String(control.lib.selectedId || "") !== "__local__"
    readonly property int selectedCount: control.ready ? control.lib.selectedCount : 0

    function displayName(item) {
        if (!item)
            return ""
        var key = String(item.nameKey || "")
        if (key.length > 0)
            return Tr?.map[key] ?? String(item.name || "")
        return String(item.name || "")
    }

    function selectedTitle() {
        if (!control.hasSelection || !control.lib)
            return ""
        if (control.remote)
            return String(control.lib.selectedName || "")
        var all = control.lib.playlists
        for (var i = 0; i < all.length; ++i) {
            if (String(all[i].id) === String(control.lib.selectedId))
                return control.displayName(all[i])
        }
        return String(control.lib.selectedName || "")
    }

    function countText() {
        return String(Tr?.map["music_song_count"] ?? "").replace("{count}", String(control.selectedCount))
    }

    function currentModel() {
        if (!control.lib)
            return null
        return control.remote ? control.lib.remotePageModel : control.lib.tracksModel
    }

    function realIndex(index) {
        if (!control.lib || !control.remote)
            return index
        return (control.lib.remotePage - 1) * control.lib.remotePageSize + index
    }

    function fullItems() {
        if (!control.lib)
            return []
        return control.lib.tracksModel.allItems()
    }

    function playAll() {
        if (!control.player || control.selectedCount === 0)
            return
        control.player.playTrackInList(control.fullItems(), 0)
    }

    function playAt(index) {
        if (!control.player || !control.lib)
            return
        control.player.playTrackInList(control.fullItems(), control.realIndex(index))
    }

    function trackAtRow(index) {
        if (!control.lib)
            return null
        return control.lib.selectedTrackAt(control.realIndex(index))
    }

    function sortActive(field) {
        if (!control.lib)
            return false
        var mode = String(control.lib.selectedSortMode || "")
        return field === "add_time" ? (mode.indexOf("add_time") === 0)
                                    : (mode.indexOf("name") === 0)
    }

    function sortText(field) {
        var key = field === "add_time" ? "music_sort_add_time" : "music_sort_name"
        var text = Tr?.map[key] ?? ""
        if (!control.sortActive(field))
            return text
        var mode = String(control.lib ? control.lib.selectedSortMode : "")
        return text + (mode.indexOf("_asc") >= 0 ? " ↑" : " ↓")
    }

    function toggleSort(field) {
        if (control.lib && typeof Music !== "undefined" && Music)
            Music.toggleSort(String(control.lib.selectedId || ""), field)
    }

    function openCreateDialog() {
        control.nameMode = "create"
        control.nameTargetId = ""
        control.nameTargetName = ""
    }

    function openRenameDialog(playlistId, name) {
        control.nameMode = "rename"
        control.nameTargetId = String(playlistId || "")
        control.nameTargetName = String(name || "")
    }

    function closeNameDialog() {
        control.nameMode = ""
        control.nameTargetId = ""
        control.nameTargetName = ""
    }

    function submitName(value) {
        var name = String(value || "").trim()
        if (control.nameMode === "create") {
            var created = Music.createPlaylist(name)
            if (created && created.id)
                control.lib.select(String(created.id))
        } else if (control.nameMode === "rename") {
            Music.renamePlaylist(control.nameTargetId, name)
        }
        control.closeNameDialog()
    }

    function requestDelete(playlistId, name) {
        control.deleteId = String(playlistId || "")
        control.deleteName = String(name || "")
        control.deleteOpen = true
    }

    function confirmDelete() {
        var target = control.deleteId
        control.deleteOpen = false
        if (target.length > 0)
            Music.deletePlaylist(target)
    }

    function openRowMenu(index, sceneX, sceneY) {
        var track = control.trackAtRow(index)
        if (!track)
            return
        control.menuMode = "track"
        control.menuTrackIndex = index
        var items = [
            {"key": "add", "text": Tr?.map["music_add_to_playlist"] ?? "", "enabled": true}
        ]
        if (control.removable)
            items.push({"key": "remove", "text": Tr?.map["music_remove_from_playlist"] ?? "",
                        "enabled": true, "danger": true})
        control.menuItems = items
        contextMenu.openAtScene(sceneX, sceneY)
    }

    function openPlaylistMenu(playlistId, name, system, sceneX, sceneY) {
        control.menuMode = "playlist"
        control.menuPlaylistId = String(playlistId || "")
        control.menuPlaylistName = String(name || "")
        control.menuItems = [
            {"key": "rename", "text": Tr?.map["music_rename_playlist"] ?? "", "enabled": !system},
            {"key": "delete", "text": Tr?.map["music_delete_playlist"] ?? "",
             "enabled": !system, "danger": true}
        ]
        contextMenu.openAtScene(sceneX, sceneY)
    }

    function handleMenu(key) {
        if (control.menuMode === "playlist") {
            if (key === "rename")
                control.openRenameDialog(control.menuPlaylistId, control.menuPlaylistName)
            else if (key === "delete")
                control.requestDelete(control.menuPlaylistId, control.menuPlaylistName)
            return
        }
        if (control.menuMode !== "track")
            return
        var track = control.trackAtRow(control.menuTrackIndex)
        if (!track)
            return
        if (key === "add") {
            if (control.addHost)
                control.addHost.openFor(track)
        } else if (key === "remove" && control.removable) {
            Music.removeFromPlaylist(String(control.lib.selectedId || ""), track)
        }
    }

    function emptyTitle() {
        if (control.lib && String(control.lib.selectedId || "") === "__local__")
            return Tr?.map["music_local_empty"] ?? ""
        return Tr?.map["music_playlist_empty"] ?? ""
    }

    function emptyDescription() {
        if (control.lib && String(control.lib.selectedId || "") === "__local__")
            return Tr?.map["music_local_empty_desc"] ?? ""
        if (control.remote)
            return Tr?.map["music_playlist_detail_empty_desc"] ?? ""
        return Tr?.map["music_playlist_empty_desc"] ?? ""
    }

    RowLayout {
        anchors.fill: parent
        spacing: Theme?.spacingMd ?? 12

        FmMusicPlaylistSidebar {
            Layout.preferredWidth: 240
            Layout.fillHeight: true
            lib: control.lib
            account: control.account
            onPlaylistActivated: function (playlistId) {
                if (!control.lib)
                    return
                if (playlistId.indexOf("wy:") === 0)
                    control.lib.selectRemote(playlistId.substring(3), "")
                else
                    control.lib.select(playlistId)
            }
            onPlaylistContextRequested: function (playlistId, name, system, x, y) {
                control.openPlaylistMenu(playlistId, name, system, x, y)
            }
            onCreateRequested: control.openCreateDialog()
            onLoginRequested: control.accountDialogOpen = true
            onSyncRequested: {
                if (typeof Music !== "undefined" && Music)
                    Music.syncPlaylists()
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Theme?.spacingSm ?? 8

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme?.spacingSm ?? 8

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 1

                    Text {
                        Layout.fillWidth: true
                        text: control.hasSelection ? control.selectedTitle()
                                                   : (Tr?.map["music_playlists"] ?? "")
                        color: Theme?.textPrimary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeLarge ?? 14
                        font.bold: true
                        elide: Text.ElideRight
                    }

                    Text {
                        Layout.fillWidth: true
                        visible: control.hasSelection
                        text: control.countText()
                        color: Theme?.textTertiary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                    }
                }

                FmButton {
                    objectName: "musicSortAddTime"
                    visible: control.hasSelection && (control.selectedIsUser || control.remote)
                    primary: control.sortActive("add_time")
                    text: control.sortText("add_time")
                    onClicked: control.toggleSort("add_time")
                }

                FmButton {
                    objectName: "musicSortName"
                    visible: control.hasSelection && (control.selectedIsUser || control.remote)
                    primary: control.sortActive("name")
                    text: control.sortText("name")
                    onClicked: control.toggleSort("name")
                }

                FmButton {
                    objectName: "musicLibraryPlayAll"
                    visible: control.hasSelection && control.selectedCount > 0
                    iconName: "play"
                    text: Tr?.map["music_play_all"] ?? ""
                    onClicked: control.playAll()
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
                    model: control.currentModel()
                    player: control.player
                    variant: "playlist"
                    showSequence: true
                    sequenceOffset: control.remote && control.lib
                                    ? (control.lib.remotePage - 1) * control.lib.remotePageSize : 0
                    showAdd: true
                    showRemove: control.removable
                    loading: control.remote && control.lib && control.lib.remoteLoading
                    loadingTitle: Tr?.map["music_wy_playlist_loading"] ?? ""
                    error: control.remote && control.lib
                           && String(control.lib.remoteError || "").length > 0
                    errorTitle: Tr?.map["music_wy_playlist_load_failed"] ?? ""
                    errorDescription: control.lib ? String(control.lib.remoteError || "") : ""
                    retryText: Tr?.map["refresh"] ?? ""
                    showEmpty: control.hasSelection
                    emptyIcon: control.remote ? "account" : "note"
                    emptyTitle: control.emptyTitle()
                    emptyDescription: control.emptyDescription()
                    bottomMargin: control.remote && control.lib && control.lib.remotePageCount > 1 ? 44 : 0
                    listObjectName: "musicLibraryTrackList"
                    onActivated: function (index) { control.playAt(index) }
                    onAddRequested: function (index) {
                        var track = control.trackAtRow(index)
                        if (track && control.addHost)
                            control.addHost.openFor(track)
                    }
                    onRemoveRequested: function (index) {
                        var track = control.trackAtRow(index)
                        if (track)
                            Music.removeFromPlaylist(String(control.lib.selectedId || ""), track)
                    }
                    onContextRequested: function (rowIndex, sceneX, sceneY) {
                        control.openRowMenu(rowIndex, sceneX, sceneY)
                    }
                    onRetryRequested: {
                        if (!control.lib)
                            return
                        control.lib.selectRemote(String(control.lib.selectedId || "").substring(3),
                                                 String(control.lib.selectedName || ""))
                    }
                }

                FmEmptyState {
                    anchors.fill: parent
                    visible: !control.hasSelection
                    iconName: "note"
                    title: Tr?.map["music_select_playlist_hint"] ?? ""
                    description: ""
                }

                FmPagination {
                    objectName: "musicRemotePager"
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    anchors.margins: Theme?.spacingSm ?? 8
                    visible: control.remote && control.lib && control.lib.remotePageCount > 1
                    page: control.lib ? control.lib.remotePage : 1
                    pageCount: control.lib ? control.lib.remotePageCount : 1
                    onPageRequested: function (page) {
                        if (control.lib)
                            control.lib.goRemotePage(page)
                    }
                }
            }
        }
    }

    FmMusicMenu {
        id: contextMenu
        anchors.fill: parent
        items: control.menuItems
        onSelected: function (key) { control.handleMenu(key) }
    }

    TextInputDialog {
        id: nameDialog
        objectName: "musicPlaylistNameDialog"
        anchors.fill: parent
        z: 90
        modalScrim: true
        visible: control.nameMode.length > 0
        request: ({
            "title": control.nameMode === "create"
                     ? (Tr?.map["music_new_playlist"] ?? "")
                     : (Tr?.map["music_rename_playlist"] ?? ""),
            "prompt": Tr?.map["music_playlist_name_placeholder"] ?? "",
            "initial": control.nameMode === "rename" ? control.nameTargetName : "",
            "fallback": null
        })
        onSubmitted: function (value) { control.submitName(value) }
        onCancelled: control.closeNameDialog()
    }

    ConfirmDialog {
        id: deleteDialog
        objectName: "musicPlaylistDeleteDialog"
        anchors.fill: parent
        z: 90
        visible: control.deleteOpen
        modalScrim: true
        confirmText: Tr?.map["music_delete_playlist"] ?? ""
        request: ({
            "title": Tr?.map["music_delete_playlist"] ?? "",
            "message": String(Tr?.map["music_confirm_delete_playlist"] ?? "").replace("{name}", control.deleteName),
            "default": false,
            "fallback": false
        })
        onAnswered: function (ok) {
            if (ok)
                control.confirmDelete()
            else
                control.deleteOpen = false
        }
    }

    Loader {
        id: accountDialogLoader
        anchors.fill: parent
        active: control.accountDialogOpen
        sourceComponent: accountDialogComponent
    }

    Component {
        id: accountDialogComponent
        FmMusicAccountDialog {
            anchors.fill: parent
            opened: true
            account: control.account
            onClosed: control.accountDialogOpen = false
        }
    }
}
