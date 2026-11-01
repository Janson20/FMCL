// FmMusicPager.qml —— 在线搜索结果分页（阶段 3.17）。
//
// 与通用 FmPagination 的差别：在线搜索要「全量页码 + 横向滚动到当前页」，
// 且页码是搜索请求的输入，组件只发 pageRequested(page)，不自己改 page。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

RowLayout {
    id: pager
    objectName: "musicPager"

    property int page: 1
    property int pageCount: 1
    property bool loading: false

    signal pageRequested(int page)

    readonly property int current: Math.max(1, Math.min(Math.max(1, pageCount), page))
    readonly property int total: Math.max(1, pageCount)

    spacing: Theme?.spacingSm ?? 8

    FmToolButton {
        objectName: "musicPagerPrev"
        iconName: "arrow-left"
        iconSize: 14
        side: 30
        enabled: !pager.loading && pager.current > 1
        onClicked: pager.pageRequested(pager.current - 1)
    }

    ListView {
        id: pageList
        objectName: "musicPagerPages"
        Layout.fillWidth: true
        Layout.preferredHeight: 30
        orientation: ListView.Horizontal
        clip: true
        spacing: 3
        model: pager.total
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.horizontal: FmScrollBar { }

        delegate: Rectangle {
            id: pageFace
            required property int index
            width: 30
            height: 30
            radius: Theme?.radiusSm ?? 0
            color: pager.current === index + 1
                   ? (Theme?.accentSoft ?? "transparent")
                   : (pageMouse.containsMouse ? (Theme?.itemHover ?? "transparent") : "transparent")
            border.width: pager.current === index + 1 ? 1 : 0
            border.color: Theme?.accent ?? "transparent"

            Text {
                anchors.centerIn: parent
                text: String(index + 1)
                color: pager.current === index + 1
                       ? (Theme?.accent ?? "transparent")
                       : (Theme?.textSecondary ?? "transparent")
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                font.bold: pager.current === index + 1
            }

            MouseArea {
                id: pageMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                enabled: !pager.loading
                onClicked: pager.pageRequested(index + 1)
            }
        }

        onCountChanged: Qt.callLater(pager.ensureCurrentVisible)
    }

    Text {
        objectName: "musicPagerLabel"
        text: pager.pageLabel()
        color: Theme?.textTertiary ?? "transparent"
        font.pixelSize: Theme?.fontSizeSmall ?? 10
        Layout.alignment: Qt.AlignVCenter
    }

    FmToolButton {
        objectName: "musicPagerNext"
        iconName: "arrow-right"
        iconSize: 14
        side: 30
        enabled: !pager.loading && pager.current < pager.total
        onClicked: pager.pageRequested(pager.current + 1)
    }

    function pageLabel() {
        if (typeof Tr === "undefined" || !Tr)
            return ""
        return Tr.tf("music_search_page_of", {
            "page": pager.current,
            "total": pager.total
        })
    }

    function ensureCurrentVisible() {
        if (pager.total <= 1)
            return
        pageList.positionViewAtIndex(pager.current - 1, ListView.Center)
    }

    onCurrentChanged: Qt.callLater(pager.ensureCurrentVisible)
    Component.onCompleted: Qt.callLater(pager.ensureCurrentVisible)
}
