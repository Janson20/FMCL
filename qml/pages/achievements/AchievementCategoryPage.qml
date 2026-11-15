// AchievementCategoryPage.qml —— 单个成就分类。
//
// 路由 achievements/category，参数 category=分类 id。列表只显示该分类的成就；
// 点一行进 achievements/detail。分类不存在时走空态而不是白屏。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../components"
import "../../components/achievements"

FmPage {
    id: page
    objectName: "achievementCategoryPage"

    readonly property bool live: (typeof Achievements !== "undefined" && Achievements) ? true : false
    property var categoryData: ({})

    title: page.categoryData && page.categoryData.nameKey
           ? page.t(page.categoryData.nameKey) : page.t("ach_category_title")
    iconName: page.categoryData && page.categoryData.icon
              ? String(page.categoryData.icon) : "folder"

    contentState: !page.live ? "error"
                  : (Achievements.state === "error" ? "error"
                  : (Achievements.state !== "ready" ? "loading"
                  : (page.categoryData && page.categoryData.id !== undefined ? "ready" : "empty")))
    loadingText: page.t("plugin_state_loading")
    emptyText: page.t("ach_detail_not_found")
    errorText: page.t("plugin_state_error")
    errorDetail: page.live ? String(Achievements.error || "") : ""
    retryText: page.t("refresh")

    onRetried: page.refresh()
    Component.onCompleted: Qt.callLater(page.activate)
    onRouteParamsChanged: Qt.callLater(page.activate)
    onVisibleChanged: {
        if (page.visible)
            Qt.callLater(page.activate)
    }

    Connections {
        target: (typeof Achievements !== "undefined" && Achievements) ? Achievements : null
        function onChanged() {
            page.syncData()
        }
    }

    function t(key) {
        return (typeof Tr !== "undefined" && Tr) ? (Tr.map[String(key)] ?? String(key)) : String(key)
    }

    function routeParam(name) {
        var raw = page.routeParams
        if (raw === undefined || raw === null)
            return ""
        return String(raw[name] || "")
    }

    function categoryId() {
        var wanted = page.routeParam("category") || page.routeParam("id")
        if (wanted.length > 0)
            return wanted
        if (page.live && Achievements.categories.length > 0)
            return String(Achievements.categories[0].id || "")
        return ""
    }

    function activate() {
        page.refresh()
        page.syncData()
    }

    function refresh() {
        if (page.live)
            Achievements.refresh()
    }

    function syncData() {
        if (!page.live) {
            page.categoryData = ({})
            return
        }
        var id = page.categoryId()
        page.categoryData = id.length > 0 ? Achievements.category(id) : ({})
    }

    function back() {
        if (typeof Nav !== "undefined" && Nav) {
            if (!Nav.goBack())
                Nav.push("achievements")
        }
    }

    function openDetail(id) {
        if (typeof Nav !== "undefined" && Nav && String(id || "").length > 0)
            Nav.push("achievements/detail", {"id": String(id)})
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme?.spacingMd ?? 10

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 8

            FmButton {
                objectName: "achievementCategoryBack"
                primary: false
                iconOnly: true
                iconName: "arrow-left"
                onClicked: page.back()
            }

            FmProgressBar {
                Layout.fillWidth: true
                value: Number(page.categoryData.percent || 0) / 100
                label: page.t("ach_stats_detail")
                       .replace("{unlocked}", String(page.categoryData.unlocked || 0))
                       .replace("{total}", String(page.categoryData.total || 0))
                showPercent: true
                barHeight: 8
            }
        }

        ListView {
            id: achievementList
            objectName: "achievementCategoryList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: Theme?.spacingSm ?? 8
            model: page.categoryData && page.categoryData.achievements
                   ? page.categoryData.achievements : []
            ScrollBar.vertical: FmScrollBar {}

            delegate: FmAchievementRow {
                required property var modelData
                width: achievementList.width
                achievement: modelData
                onActivated: page.openDetail(modelData.id)
            }
        }
    }
}
