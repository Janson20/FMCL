// MusicPage.qml —— 音乐（阶段 3.16：播放核心与本地音乐）。
//
// 三条路由共用本页：music / music/playlist（队列）。3.17 起再接入在线搜索与歌单。
// 后端是 fusion_music（从 tmp/FusionMusicPlayer 移植），本页只读 Music.player /
// Music.library，不解析歌词、不拼播放规则。

import QtQuick
import QtQuick.Layouts
import "../../components"
import "../../components/music"

Item {
    id: page
    objectName: "musicPage"

    // ── 路由帧（PageStack 只给声明过的属性赋值）────────────────
    property string routeId: ""
    property string routeTitleKey: ""
    property string routeDescriptionKey: ""
    property string routeIcon: ""
    property var routeParams: ({})

    readonly property bool live: (typeof Music !== "undefined" && Music) ? true : false
    readonly property bool ready: page.live && Music.ready
    readonly property var player: (page.live && Music.ready) ? Music.player : null
    readonly property var lib: (page.live && Music.ready) ? Music.library : null
    readonly property bool expanded: page.live && Music.expanded
    readonly property bool failed: page.live && !page.ready && String(Music.errorText || "").length > 0
    //: `FmPage` 同名的三态入口：强制切三态供冒烟/人工验收；正常时由后端状态派生。
    property string contentState: "ready"
    readonly property string effectiveState: page.contentState !== "ready"
                                                 ? page.contentState
                                                 : (!page.live ? "error"
                                                    : (!page.ready ? (page.failed ? "error" : "loading")
                                                       : "ready"))

    property string viewMode: "local"

    function routeMode() {
        var id = String(page.routeId || "");
        if (id.indexOf("music/playlist") >= 0)
            return "queue";
        return "local";
    }

    function syncMode() {
        page.viewMode = page.routeMode();
        if (page.live)
            Music.setPage(page.viewMode);
    }

    function countText(count) {
        return String(Tr?.map["music_song_count"] ?? "").replace("{count}", String(count));
    }

    onRouteIdChanged: Qt.callLater(page.syncMode)
    Component.onCompleted: {
        page.syncMode();
        if (page.live)
            Qt.callLater(Music.ensureReady);
    }
    onVisibleChanged: {
        if (visible && page.live)
            Qt.callLater(Music.ensureReady);
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme?.spacingLg ?? 16
        spacing: Theme?.spacingMd ?? 12

        // ── 页头 ───────────────────────────────────────────
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 8

            FmIcon {
                objectName: "pageIcon"
                name: "music"
                size: Theme?.fontSizeTitle ?? 18
                color: Theme?.textPrimary ?? "transparent"
                Layout.alignment: Qt.AlignTop
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 1

                Text {
                    objectName: "pageTitle"
                    text: Tr?.map["tab_music"] ?? ""
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeTitle ?? 18
                    font.bold: true
                }

                Text {
                    visible: text.length > 0
                    text: page.ready
                          ? (page.viewMode === "queue"
                             ? (page.player ? page.countText(page.player.queueCount) : "")
                             : (page.lib ? page.countText(page.lib.localModel.count) : ""))
                          : ""
                    color: Theme?.textTertiary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                }
            }

            Item { Layout.fillWidth: true }

            FmTabBar {
                visible: page.ready
                currentKey: page.viewMode
                items: [
                    {"key": "local", "labelKey": "music_tab_local"},
                    {"key": "queue", "labelKey": "music_queue_title"}
                ]
                onTabRequested: function (key) {
                    page.viewMode = String(key);
                    if (page.live)
                        Music.setPage(page.viewMode);
                }
            }
        }

        // ── 内容三态 / 两视图 ───────────────────────────────
        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            StackLayout {
                anchors.fill: parent
                currentIndex: page.viewMode === "queue" ? 1 : 0
                visible: page.effectiveState === "ready"

                FmMusicLocalView {
                    lib: page.lib
                    player: page.player
                }
                Loader {
                    active: page.viewMode === "queue"
                    source: "../../components/music/FmMusicQueueView.qml"
                    onLoaded: item.player = Qt.binding(function () { return page.player; })
                }
            }

            FmLoadingState {
                anchors.fill: parent
                visible: page.effectiveState === "loading"
                title: Tr?.map["plugin_state_loading"] ?? ""
            }

            FmEmptyState {
                anchors.fill: parent
                visible: page.effectiveState === "empty"
                iconName: "folder-open"
                title: Tr?.map["music_local_empty"] ?? ""
                description: Tr?.map["music_local_empty_desc"] ?? ""
            }

            FmErrorState {
                anchors.fill: parent
                visible: page.effectiveState === "error"
                title: Tr?.map["plugin_state_error"] ?? ""
                description: page.live ? String(Music.errorText || "") : (Tr?.map["music_backend_failed"] ?? "")
                retryText: page.live ? (Tr?.map["refresh"] ?? "") : ""
                onRetried: {
                    if (page.live)
                        Music.ensureReady();
                }
            }
        }

        FmMusicPlayerBar {
            Layout.fillWidth: true
            player: page.player
            expanded: page.expanded
        }
    }

    Loader {
        anchors.fill: parent
        z: 20
        active: page.effectiveState === "ready" && page.expanded
        source: "../../components/music/FmMusicNowPlaying.qml"
        onLoaded: {
            item.player = Qt.binding(function () { return page.player; });
            item.collapseRequested.connect(function () {
                if (page.live)
                    Music.setExpanded(false);
            });
        }
    }
}
