// FmMusicDiscoverView.qml —— 发现首页（阶段 3.17，对齐 FMP DiscoverPage）。
//
// 推荐歌单 / 排行榜 / 最新音乐 / 每日推荐；点击歌单卡片打开详情覆盖层。
// 数据来自 fusion_music.bridges.discover，不在 QML 里拼接口。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Item {
    id: control
    objectName: "musicDiscoverView"

    property var discover: null
    property var player: null

    readonly property int cardWidth: 154
    readonly property int cardStep: control.cardWidth + 70 + 14
    readonly property int gridColumns: Math.max(1, Math.floor((width - 2 * (Theme?.spacingLg ?? 16)) / (control.cardWidth + 14)))
    readonly property var recommend: control.discover ? control.discover.recommendPlaylists : []
    readonly property var toplists: control.discover ? control.discover.toplists : []
    readonly property var newModel: control.discover ? control.discover.newSongsModel : null
    readonly property var dailyModel: control.discover ? control.discover.dailyModel : null
    readonly property bool loading: control.discover ? control.discover.loading : false
    readonly property bool hasContent: control.recommend.length + control.toplists.length
                                       + (control.newModel ? control.newModel.count : 0)
                                       + (control.dailyModel ? control.dailyModel.count : 0) > 0
    function greeting() {
        var hour = new Date().getHours()
        if (hour < 6)
            return Tr?.map["music_greeting_night"] ?? ""
        if (hour < 12)
            return Tr?.map["music_greeting_morning"] ?? ""
        if (hour < 18)
            return Tr?.map["music_greeting_afternoon"] ?? ""
        return Tr?.map["music_greeting_evening"] ?? ""
    }

    function countText(count) {
        return String(Tr?.map["music_song_count"] ?? "").replace("{count}", String(count))
    }

    function playModel(model, index) {
        if (!control.player || !model || model.count === 0)
            return
        control.player.playTrackInList(model.allItems(), index)
    }

    function openPlaylist(item) {
        if (control.discover && item)
            control.discover.openPlaylist(String(item.id || ""), String(item.name || ""))
    }

    //: 页面可能先于后端创建（Music.ensureReady 在 Component.onCompleted 才跑），
    //: 所以不能在 onCompleted 里只试一次；discover 后到时补一次 load。
    property bool loadRequested: false

    function loadIfReady() {
        if (control.discover && !control.loadRequested) {
            control.loadRequested = true
            control.discover.load()
        }
    }

    Component.onCompleted: control.loadIfReady()
    onDiscoverChanged: control.loadIfReady()

    // 发现页的内容面板：与 FMCL 其它页面一致，用一块卡片承载滚动内容，
    // 空数据/网络失败时也有稳定的页面底色（FMP 的卡片网格在真实网络下才出现）。
    Rectangle {
        anchors.fill: parent
        radius: Theme?.radiusLg ?? 0
        color: Theme?.cardBg ?? "transparent"
        border.width: 1
        border.color: Theme?.cardBorder ?? "transparent"
    }

    Flickable {
        anchors.fill: parent
        contentWidth: width
        contentHeight: contentColumn.height + 2 * (Theme?.spacingLg ?? 16)
        boundsBehavior: Flickable.StopAtBounds
        clip: true
        ScrollBar.vertical: FmScrollBar { }

        ColumnLayout {
            id: contentColumn
            x: Theme?.spacingLg ?? 16
            y: Theme?.spacingMd ?? 12
            width: parent.width - 2 * (Theme?.spacingLg ?? 16)
            spacing: Theme?.spacingLg ?? 20

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme?.spacingMd ?? 12

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2

                    Text {
                        Layout.fillWidth: true
                        text: control.greeting()
                        color: Theme?.textPrimary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeTitle ?? 18
                        font.bold: true
                    }

                    Text {
                        Layout.fillWidth: true
                        text: Tr?.map["music_discover_hint"] ?? ""
                        color: Theme?.textTertiary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        elide: Text.ElideRight
                    }
                }

                FmButton {
                    objectName: "musicDiscoverReload"
                    primary: false
                    iconName: "refresh"
                    text: Tr?.map["refresh"] ?? ""
                    enabled: !control.loading
                    onClicked: {
                        if (control.discover)
                            control.discover.reload()
                    }
                }
            }

            // ── 每日推荐（登录后才有）──────────────────────
            ColumnLayout {
                Layout.fillWidth: true
                spacing: Theme?.spacingSm ?? 8
                visible: control.dailyModel && control.dailyModel.count > 0

                RowLayout {
                    Layout.fillWidth: true

                    Text {
                        Layout.fillWidth: true
                        text: Tr?.map["music_daily"] ?? ""
                        color: Theme?.textPrimary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeLarge ?? 14
                        font.bold: true
                    }

                    FmButton {
                        primary: false
                        text: Tr?.map["music_play_all"] ?? ""
                        iconName: "play"
                        onClicked: control.playModel(control.dailyModel, 0)
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.min(300, (control.dailyModel ? control.dailyModel.count : 0) * 54 + 4)
                    radius: Theme?.radiusLg ?? 0
                    color: Theme?.cardBg ?? "transparent"
                    border.width: 1
                    border.color: Theme?.cardBorder ?? "transparent"

                    ListView {
                        id: dailyList
                        anchors.fill: parent
                        anchors.margins: Theme?.spacingSm ?? 8
                        clip: true
                        spacing: 1
                        model: control.dailyModel
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: FmScrollBar { }

                        delegate: FmMusicTrackRow {
                            width: dailyList.width
                            current: !!(control.player && control.player.currentTrack
                                     && control.player.currentTrack.uid === uid)
                            onActivated: control.playModel(control.dailyModel, index)
                        }
                    }
                }
            }

            // ── 推荐歌单 ──────────────────────────────────
            ColumnLayout {
                Layout.fillWidth: true
                spacing: Theme?.spacingSm ?? 8
                visible: control.recommend.length > 0

                Text {
                    Layout.fillWidth: true
                    text: Tr?.map["music_recommend_playlists"] ?? ""
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeLarge ?? 14
                    font.bold: true
                }

                GridView {
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.min(2, Math.ceil(control.recommend.length / control.gridColumns))
                                            * control.cardStep + 2
                    cellWidth: control.cardWidth + 14
                    cellHeight: control.cardStep
                    interactive: false
                    clip: true
                    model: control.recommend

                    delegate: FmMusicPlaylistCard {
                        required property var modelData
                        width: control.cardWidth
                        title: String(modelData.name || "")
                        subtitle: control.countText(modelData.track_count || 0)
                        cover: String(modelData.cover || "")
                        trackCount: Number(modelData.track_count || 0)
                        onActivated: control.openPlaylist(modelData)
                        onPlayRequested: control.openPlaylist(modelData)
                    }
                }
            }

            // ── 排行榜 ────────────────────────────────────
            ColumnLayout {
                Layout.fillWidth: true
                spacing: Theme?.spacingSm ?? 8
                visible: control.toplists.length > 0

                Text {
                    Layout.fillWidth: true
                    text: Tr?.map["music_toplists"] ?? ""
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeLarge ?? 14
                    font.bold: true
                }

                GridView {
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.min(2, Math.ceil(control.toplists.length / control.gridColumns))
                                            * control.cardStep + 2
                    cellWidth: control.cardWidth + 14
                    cellHeight: control.cardStep
                    interactive: false
                    clip: true
                    model: control.toplists

                    delegate: FmMusicPlaylistCard {
                        required property var modelData
                        width: control.cardWidth
                        title: String(modelData.name || "")
                        subtitle: String(modelData.update_frequency || "")
                        cover: String(modelData.cover || "")
                        trackCount: Number(modelData.track_count || 0)
                        onActivated: control.openPlaylist(modelData)
                        onPlayRequested: control.openPlaylist(modelData)
                    }
                }
            }

            // ── 最新音乐 ──────────────────────────────────
            ColumnLayout {
                Layout.fillWidth: true
                spacing: Theme?.spacingSm ?? 8
                visible: control.newModel && control.newModel.count > 0

                RowLayout {
                    Layout.fillWidth: true

                    Text {
                        Layout.fillWidth: true
                        text: Tr?.map["music_new_songs"] ?? ""
                        color: Theme?.textPrimary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeLarge ?? 14
                        font.bold: true
                    }

                    FmButton {
                        primary: false
                        text: Tr?.map["music_play_all"] ?? ""
                        iconName: "play"
                        onClicked: control.playModel(control.newModel, 0)
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.min(420, (control.newModel ? control.newModel.count : 0) * 54 + 4)
                    radius: Theme?.radiusLg ?? 0
                    color: Theme?.cardBg ?? "transparent"
                    border.width: 1
                    border.color: Theme?.cardBorder ?? "transparent"

                    ListView {
                        id: newList
                        anchors.fill: parent
                        anchors.margins: Theme?.spacingSm ?? 8
                        clip: true
                        spacing: 1
                        model: control.newModel
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: FmScrollBar { }

                        delegate: FmMusicTrackRow {
                            width: newList.width
                            current: !!(control.player && control.player.currentTrack
                                     && control.player.currentTrack.uid === uid)
                            onActivated: control.playModel(control.newModel, index)
                        }
                    }
                }
            }

            Item {
                Layout.fillWidth: true
                Layout.preferredHeight: 180
                visible: !control.hasContent

                FmLoadingState {
                    anchors.fill: parent
                    visible: control.loading
                    title: Tr?.map["music_discover_loading"] ?? ""
                }

                FmEmptyState {
                    anchors.fill: parent
                    visible: !control.loading
                    iconName: "world"
                    title: Tr?.map["music_discover_empty"] ?? ""
                    description: Tr?.map["music_discover_empty_desc"] ?? ""
                    actionText: Tr?.map["refresh"] ?? ""
                    onActionTriggered: {
                        if (control.discover)
                            control.discover.reload()
                    }
                }
            }
        }
    }

}
