// AchievementsPage.qml —— 成就总览。
//
// 路由 achievements：总进度 + 9 大分类入口。分类列表、详情、云端同步各自是
// 独立二级页（achievements/category、/detail、/cloud），本页只做入口，不再把
// 47 张成就卡一次铺满。
//
// 纪律：颜色只来自 Theme.*（R8）、图标走 FmIcon（R2）、文案走 Tr.map（R3/R4）、
// 控件只用 qml/components 的自研件（R9），所有上下文属性读取先判空。

import QtQuick
import QtQuick.Layouts
import "../../components"
import "../../components/achievements"

FmPage {
    id: page
    objectName: "achievementsPage"

    readonly property bool live: (typeof Achievements !== "undefined" && Achievements) ? true : false
    readonly property int gridColumns: page.width >= 980 ? 3 : (page.width >= 660 ? 2 : 1)

    title: page.t("ach_stats_title")
    iconName: "trophy"

    contentState: !page.live ? "error"
                  : (Achievements.state === "error" ? "error"
                  : (Achievements.state !== "ready" ? "loading"
                  : (Achievements.categories.length === 0 ? "empty" : "ready")))
    loadingText: page.t("plugin_state_loading")
    emptyText: page.t("mod_browser_no_results")
    errorText: page.t("plugin_state_error")
    errorDetail: page.live ? String(Achievements.error || "") : ""
    retryText: page.t("refresh")

    onRetried: page.refresh()
    Component.onCompleted: page.refresh()
    onVisibleChanged: {
        if (page.visible)
            page.refresh()
    }

    function t(key) {
        return (typeof Tr !== "undefined" && Tr) ? (Tr.map[String(key)] ?? String(key)) : String(key)
    }

    function refresh() {
        if (page.live)
            Achievements.refresh()
    }

    function openCloud() {
        if (typeof Nav !== "undefined" && Nav)
            Nav.push("achievements/cloud")
    }

    function openCategory(categoryId) {
        if (typeof Nav !== "undefined" && Nav)
            Nav.push("achievements/category", {"category": String(categoryId || "")})
    }

    FmScrollView {
        id: scroller
        anchors.fill: parent
        clip: true
        contentWidth: availableWidth

        ColumnLayout {
            width: scroller.availableWidth
            spacing: Theme?.spacingMd ?? 10

            FmAchievementSummary {
                objectName: "achievementSummaryCard"
                Layout.fillWidth: true
                summary: page.live ? Achievements.summary : ({})
                lastSyncAt: page.live ? Number(Achievements.lastSyncAt || 0) : 0
                loggedIn: page.live ? Boolean(Achievements.loggedIn) : false
                syncState: page.live ? String(Achievements.syncState || "idle") : "idle"
                statusText: page.live ? String(Achievements.statusText || "") : ""
                statusLevel: page.live ? String(Achievements.statusLevel || "info") : "info"
                showActions: true
                onSyncRequested: {
                    if (page.live)
                        Achievements.syncNow()
                }
                onCloudRequested: page.openCloud()
                onLoginRequested: {
                    if (typeof Nav !== "undefined" && Nav)
                        Nav.push("settings/ai")
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme?.spacingSm ?? 8

                Text {
                    Layout.fillWidth: true
                    text: page.t("ach_category_title")
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeLarge ?? 14
                    font.bold: true
                    elide: Text.ElideRight
                }

                Text {
                    text: page.live
                          ? page.t("ach_stats_detail")
                            .replace("{unlocked}", String(Achievements.summary.unlocked || 0))
                            .replace("{total}", String(Achievements.summary.total || 0))
                          : ""
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                    elide: Text.ElideRight
                }
            }

            GridLayout {
                objectName: "achievementCategoryGrid"
                Layout.fillWidth: true
                columns: page.gridColumns
                columnSpacing: Theme?.spacingMd ?? 10
                rowSpacing: Theme?.spacingMd ?? 10

                Repeater {
                    model: page.live ? Achievements.categories : []

                    delegate: FmCard {
                        id: categoryCard
                        required property var modelData

                        Layout.fillWidth: true
                        Layout.preferredHeight: 166
                        padding: Theme?.spacingMd ?? 12

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme?.spacingSm ?? 8

                            Rectangle {
                                Layout.preferredWidth: 36
                                Layout.preferredHeight: 36
                                radius: width / 2
                                color: Theme?.accentSoft ?? "transparent"

                                FmIcon {
                                    anchors.centerIn: parent
                                    name: String(categoryCard.modelData.icon || "trophy")
                                    color: Theme?.accent ?? "transparent"
                                    size: Theme?.iconSize ?? 20
                                }
                            }

                            Text {
                                objectName: "achievementCategoryName"
                                Layout.fillWidth: true
                                text: page.t(categoryCard.modelData.nameKey)
                                color: Theme?.textPrimary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeLarge ?? 14
                                font.bold: true
                                elide: Text.ElideRight
                            }

                            FmTag {
                                objectName: "achievementCategoryCount"
                                text: String(categoryCard.modelData.unlocked || 0) + "/"
                                      + String(categoryCard.modelData.total || 0)
                                level: Number(categoryCard.modelData.unlocked || 0) > 0 ? "accent" : "neutral"
                            }
                        }

                        FmProgressBar {
                            objectName: "achievementCategoryProgress"
                            Layout.fillWidth: true
                            value: Number(categoryCard.modelData.percent || 0) / 100
                            label: page.t("ach_stats_detail")
                                   .replace("{unlocked}", String(categoryCard.modelData.unlocked || 0))
                                   .replace("{total}", String(categoryCard.modelData.total || 0))
                            showPercent: true
                            barHeight: 6
                        }

                        FmButton {
                            objectName: "achievementCategoryOpen"
                            primary: false
                            iconName: "arrow-right"
                            text: page.t("ach_view_category")
                            onClicked: page.openCategory(categoryCard.modelData.id)
                        }
                    }
                }
            }

            Item { Layout.preferredHeight: Theme?.spacingSm ?? 8 }
        }
    }
}
