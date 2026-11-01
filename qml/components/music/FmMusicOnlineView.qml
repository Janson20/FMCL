// FmMusicOnlineView.qml —— 在线搜索（阶段 3.17）。
//
// 数据与搜索规则全在 fusion_music.bridges.search / core.player；本文件只组合
// 输入框、音源标签、结果行与分页。播放失败、跨源兜底由桥提示。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Item {
    id: control
    objectName: "musicOnlineView"

    property var search: null
    property var player: null
    property var lib: null

    property bool pickerOpen: false
    property var pickerTrack: null

    //: 联想词高亮项（-1 = 不选，回车搜输入框原文）
    property int suggestIndex: -1
    //: Esc / 已搜过之后先收起，输入框再次编辑才重新弹出
    property bool suggestDismissed: false

    readonly property var tabs: control.search ? control.search.sourceTabs : []
    readonly property var history: control.search ? control.search.history : []
    readonly property bool hasResults: control.search ? control.search.hasResults : false
    readonly property bool loading: control.search ? control.search.loading : false
    readonly property bool hasKeyword: control.search ? String(control.search.keyword || "").length > 0 : false
    readonly property var suggestItems: control.search ? control.search.suggestions : []
    readonly property bool suggestVisible: searchField.activeFocus && !control.suggestDismissed
        && String(searchField.text || "").trim().length > 0
        && control.suggestItems.length > 0 && !control.loading

    function sourceLabel(entry) {
        var key = entry && entry.nameKey ? String(entry.nameKey) : ""
        if (key.length > 0)
            return Tr?.map[key] ?? (entry.name ? String(entry.name) : "");
        return entry && entry.name ? String(entry.name) : "";
    }

    function suggestKeyword(index) {
        var items = control.suggestItems
        if (index < 0 || index >= items.length)
            return ""
        return String(items[index].keyword || "")
    }

    function moveSuggest(delta) {
        if (!control.suggestVisible)
            return
        var count = control.suggestItems.length
        var next = control.suggestIndex + delta
        if (control.suggestIndex < 0)
            next = delta > 0 ? 0 : count - 1
        control.suggestIndex = Math.max(0, Math.min(count - 1, next))
    }

    function submitSearch() {
        if (!control.search)
            return
        var word = control.suggestKeyword(control.suggestIndex)
        if (word.length === 0)
            word = String(searchField.text || "").trim()
        if (word.length === 0) {
            searchField.forceActiveFocus()
            return
        }
        control.applyKeyword(word)
    }

    function applyKeyword(word) {
        control.suggestDismissed = true
        control.suggestIndex = -1
        searchField.text = String(word || "")
        control.search.search(String(word || ""))
    }

    function playAt(index) {
        if (!control.player || !control.search)
            return
        control.player.playTrackInList(control.search.model.allItems(), index)
    }

    function playAll() {
        if (!control.player || !control.search || control.search.model.count === 0)
            return
        control.player.playTrackInList(control.search.model.allItems(), 0)
    }

    function totalCountText() {
        if (!control.search)
            return ""
        return String(Tr?.map["music_song_count"] ?? "").replace("{count}", String(control.search.totalCount))
    }

    function openPicker(track) {
        if (!track)
            return
        control.pickerTrack = track
        control.pickerOpen = true
    }

    function userPlaylists() {
        var all = control.lib ? control.lib.playlists : []
        var out = []
        for (var i = 0; i < all.length; ++i) {
            if (!all[i].system)
                out.push(all[i])
        }
        return out
    }

    Component.onCompleted: {
        if (control.search)
            searchField.text = String(control.search.keyword || "")
        Qt.callLater(searchField.forceActiveFocus)
    }
    onVisibleChanged: {
        if (visible)
            Qt.callLater(searchField.forceActiveFocus)
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme?.spacingSm ?? 8

        // ── 搜索框 ──────────────────────────────────────────
        RowLayout {
            id: searchRow
            z: 50
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 8

            FmSearchField {
                id: searchField
                objectName: "musicSearchInput"
                Layout.fillWidth: true
                Layout.maximumWidth: 560
                placeholderText: Tr?.map["music_search_placeholder"] ?? ""
                onAccepted: control.submitSearch()
                onTextEdited: {
                    control.suggestDismissed = false
                    control.suggestIndex = -1
                    if (control.search)
                        control.search.suggest(text)
                }
                onActiveFocusChanged: {
                    if (activeFocus && !control.suggestDismissed && text.trim().length > 0
                            && control.search)
                        control.search.suggest(text)
                }
                Keys.onDownPressed: function (event) {
                    control.moveSuggest(1)
                    event.accepted = true
                }
                Keys.onUpPressed: function (event) {
                    control.moveSuggest(-1)
                    event.accepted = true
                }
                Keys.onEscapePressed: function (event) {
                    control.suggestDismissed = true
                    control.suggestIndex = -1
                    if (control.search)
                        control.search.clearSuggestions()
                    event.accepted = true
                }
                onCleared: {
                    control.suggestDismissed = false
                    control.suggestIndex = -1
                    if (control.search) {
                        control.search.clearSuggestions()
                        control.search.clear()
                    }
                }
            }

            FmButton {
                objectName: "musicSearchButton"
                text: Tr?.map["music_search_btn"] ?? ""
                enabled: searchField.text.trim().length > 0 && !control.loading
                onClicked: control.submitSearch()
            }

            FmProgressRing {
                Layout.preferredWidth: 20
                Layout.preferredHeight: 20
                Layout.alignment: Qt.AlignVCenter
                visible: control.loading
                indeterminate: true
                lineWidth: 2
            }

            Item { Layout.fillWidth: true }
        }

        FmInfoBar {
            Layout.fillWidth: true
            visible: control.player && control.player.loading
            text: Tr?.map["music_loading_url"] ?? ""
            level: "info"
        }

        // ── 搜索历史（还没开始搜时）────────────────────────
        Flow {
            Layout.fillWidth: true
            Layout.preferredHeight: visible ? implicitHeight : 0
            visible: !control.hasKeyword && !control.loading && control.history.length > 0
            spacing: Theme?.spacingSm ?? 8

            Text {
                text: Tr?.map["music_recent_search"] ?? ""
                color: Theme?.textTertiary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
            }

            Repeater {
                model: control.history
                delegate: Rectangle {
                    required property string modelData
                    width: chipText.implicitWidth + 22
                    height: 26
                    radius: height / 2
                    color: chipMouse.containsMouse ? (Theme?.accentSoft ?? "transparent")
                                                   : (Theme?.bgLight ?? "transparent")

                    Text {
                        id: chipText
                        anchors.centerIn: parent
                        text: modelData
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                    }

                    MouseArea {
                        id: chipMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: control.applyKeyword(modelData)
                    }
                }
            }

            FmButton {
                primary: false
                text: Tr?.map["music_clear_history"] ?? ""
                onClicked: {
                    if (control.search)
                        control.search.clearHistory()
                }
            }
        }

        // ── 音源标签 ──────────────────────────────────────
        Flow {
            Layout.fillWidth: true
            Layout.preferredHeight: visible ? implicitHeight : 0
            visible: control.hasKeyword || control.loading
            spacing: Theme?.spacingXs ?? 4

            Repeater {
                model: control.tabs
                delegate: Rectangle {
                    required property var modelData
                    height: 28
                    width: tabRow.implicitWidth + 22
                    radius: height / 2
                    color: control.search && control.search.currentSource === modelData.id
                           ? (Theme?.accentSoft ?? "transparent")
                           : (tabMouse.containsMouse ? (Theme?.itemHover ?? "transparent")
                                                     : (Theme?.bgLight ?? "transparent"))
                    border.width: control.search && control.search.currentSource === modelData.id ? 1 : 0
                    border.color: Theme?.accent ?? "transparent"

                    Row {
                        id: tabRow
                        anchors.centerIn: parent
                        spacing: 4

                        Text {
                            text: control.sourceLabel(modelData)
                            color: control.search && control.search.currentSource === modelData.id
                                   ? (Theme?.accent ?? "transparent")
                                   : (Theme?.textSecondary ?? "transparent")
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                            font.bold: control.search && control.search.currentSource === modelData.id
                        }

                        Text {
                            text: String(modelData.count ?? 0)
                            color: Theme?.textTertiary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                        }
                    }

                    MouseArea {
                        id: tabMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            if (control.search)
                                control.search.setSource(String(modelData.id || "all"))
                        }
                    }
                }
            }
        }

        // ── 结果列表 ──────────────────────────────────────
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: Theme?.radiusLg ?? 0
            color: Theme?.cardBg ?? "transparent"
            border.width: 1
            border.color: Theme?.cardBorder ?? "transparent"

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: Theme?.spacingSm ?? 8
                spacing: 4

                RowLayout {
                    Layout.fillWidth: true
                    Layout.leftMargin: 6
                    Layout.rightMargin: 6
                    visible: control.hasKeyword

                    Text {
                        Layout.fillWidth: true
                        text: Tr?.map["music_search_results"] ?? ""
                        color: Theme?.textPrimary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        font.bold: true
                    }

                    Text {
                        visible: control.search && control.search.totalCount > 0
                        text: control.totalCountText()
                        color: Theme?.textTertiary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                    }

                    FmButton {
                        primary: false
                        text: Tr?.map["music_play_all"] ?? ""
                        iconName: "play"
                        visible: control.hasResults
                        onClicked: control.playAll()
                    }
                }

                Item {
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    ListView {
                        id: resultsList
                        objectName: "musicOnlineResults"
                        anchors.fill: parent
                        clip: true
                        spacing: 1
                        model: control.search ? control.search.model : null
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: FmScrollBar { }

                        delegate: FmMusicOnlineRow {
                            width: resultsList.width
                            current: !!(control.player && control.player.currentTrack
                                     && control.player.currentTrack.uid === uid)
                            onActivated: control.playAt(index)
                            onAddRequested: {
                                if (control.search)
                                    control.openPicker(control.search.model.get(index))
                            }
                        }
                    }

                    FmEmptyState {
                        objectName: "musicOnlineIntro"
                        anchors.fill: parent
                        visible: !control.hasKeyword && !control.loading
                        iconName: "search"
                        title: Tr?.map["music_search_hint_title"] ?? ""
                        description: Tr?.map["music_search_hint_desc"] ?? ""
                        actionText: Tr?.map["music_search_btn"] ?? ""
                        onActionTriggered: searchField.forceActiveFocus()
                    }

                    FmLoadingState {
                        objectName: "musicOnlineLoading"
                        anchors.fill: parent
                        visible: control.loading && !control.hasResults
                        title: Tr?.map["plugin_state_loading"] ?? ""
                    }

                    FmErrorState {
                        objectName: "musicOnlineError"
                        anchors.fill: parent
                        visible: control.search && !control.loading
                                 && control.search.lastError === "failed"
                        title: Tr?.map["music_search_failed"] ?? ""
                        retryText: Tr?.map["refresh"] ?? ""
                        onRetried: {
                            if (control.search)
                                control.search.retry()
                        }
                    }

                    FmEmptyState {
                        objectName: "musicOnlineEmpty"
                        anchors.fill: parent
                        visible: control.hasKeyword && !control.loading
                                 && control.search && control.search.hasSearched
                                 && control.search.lastError !== "failed"
                                 && !control.search.hasResults
                        iconName: "search"
                        title: Tr?.map["music_search_no_results"] ?? ""
                        description: Tr?.map["music_search_no_results_desc"] ?? ""
                    }
                }
            }
        }

        Text {
            Layout.fillWidth: true
            horizontalAlignment: Text.AlignHCenter
            visible: control.search && control.search.hasSearched
                     && control.search.hasResults && !control.search.hasMore
                     && control.search.page > 1
            text: Tr?.map["music_search_no_more"] ?? ""
            color: Theme?.textTertiary ?? "transparent"
            font.pixelSize: Theme?.fontSizeSmall ?? 10
        }

        // ── 分页 ──────────────────────────────────────────
        FmMusicPager {
            Layout.fillWidth: true
            visible: control.hasResults
            page: control.search ? control.search.page : 1
            pageCount: control.search ? control.search.pageCount : 1
            loading: control.loading
            onPageRequested: function (page) {
                if (control.search)
                    control.search.goToPage(page)
            }
        }
    }

    // 联想下拉挂在搜索框自己身上（FMP 同款）：用 anchors 跟随布局，
    // 不手算 mapToItem 坐标 —— 页面进场动画途中算出来的偏移会一直用下去。
    Rectangle {
        id: suggestPanel
        objectName: "musicSearchSuggestPanel"
        parent: searchField
        anchors.top: parent.bottom
        anchors.topMargin: 6
        anchors.left: parent.left
        width: parent.width
        height: Math.min(suggestList.contentHeight + 8, 300)
        visible: control.suggestVisible
        z: 50
        radius: Theme?.radiusMd ?? 0
        color: Theme?.cardBg ?? "transparent"
        border.width: 1
        border.color: Theme?.cardBorder ?? "transparent"
        clip: true

        ListView {
            id: suggestList
            objectName: "musicSearchSuggestList"
            anchors.fill: parent
            anchors.margins: 4
            model: control.suggestItems
            boundsBehavior: Flickable.StopAtBounds
            clip: true
            ScrollBar.vertical: FmScrollBar { }

            delegate: Rectangle {
                required property var modelData
                required property int index
                width: suggestList.width
                height: 32
                radius: Theme?.radiusSm ?? 0
                color: index === control.suggestIndex
                       ? (Theme?.accentSoft ?? "transparent")
                       : (suggestMouse.containsMouse ? (Theme?.itemHover ?? "transparent") : "transparent")

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 8
                    anchors.rightMargin: 10
                    spacing: Theme?.spacingSm ?? 8

                    FmIcon {
                        name: String(modelData.from || "") === "history" ? "history" : "search"
                        size: 12
                        color: index === control.suggestIndex
                               ? (Theme?.accent ?? "transparent")
                               : (Theme?.textTertiary ?? "transparent")
                    }

                    Text {
                        Layout.fillWidth: true
                        text: String(modelData.keyword || "")
                        color: index === control.suggestIndex
                               ? (Theme?.accent ?? "transparent")
                               : (Theme?.textPrimary ?? "transparent")
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        elide: Text.ElideRight
                    }
                }

                MouseArea {
                    id: suggestMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onEntered: control.suggestIndex = index
                    onClicked: control.applyKeyword(String(modelData.keyword || ""))
                }
            }
        }
    }

    FmMusicPlaylistPicker {
        anchors.fill: parent
        opened: control.pickerOpen
        track: control.pickerTrack
        playlists: control.userPlaylists()
        onClosed: control.pickerOpen = false
        onSelected: function (playlistId, track) {
            if (typeof Music !== "undefined" && Music)
                Music.addToPlaylist(String(playlistId), track)
        }
    }
}
