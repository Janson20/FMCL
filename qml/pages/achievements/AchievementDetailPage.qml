// AchievementDetailPage.qml —— 单个成就详情。
//
// 路由 achievements/detail，参数 id=成就 id。展示名称 / 描述 / 分类 / 总进度，
// 以及每个阶段的阈值与解锁状态。阶段名用"成就名 + 罗马数字"生成，不读定义表里
// 的中文 stage_names（那会让 en_US / ja_JP 出现中文）。

import QtQuick
import QtQuick.Layouts
import "../../components"
import "../../components/achievements"

FmPage {
    id: page
    objectName: "achievementDetailPage"

    readonly property bool live: (typeof Achievements !== "undefined" && Achievements) ? true : false
    property var detail: ({})

    title: page.t("ach_detail_title")
    iconName: page.detail && page.detail.icon ? String(page.detail.icon) : "target"

    contentState: !page.live ? "error"
                  : (Achievements.state === "error" ? "error"
                  : (Achievements.state !== "ready" ? "loading"
                  : (page.detail && page.detail.id !== undefined ? "ready" : "empty")))
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

    function achievementId() {
        return page.routeParam("id") || page.routeParam("achievement")
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
            page.detail = ({})
            return
        }
        var id = page.achievementId()
        page.detail = id.length > 0 ? Achievements.achievement(id) : ({})
    }

    function nameText() {
        return page.detail && page.detail.nameKey ? page.t(page.detail.nameKey) : ""
    }

    function descText() {
        return page.detail && page.detail.descKey ? page.t(page.detail.descKey) : ""
    }

    function roman(value) {
        var table = ["", "I", "II", "III", "IV", "V", "VI"]
        var index = Number(value || 0)
        return (index > 0 && index < table.length) ? table[index] : String(index)
    }

    function stageName(index) {
        if (!page.detail || Number(page.detail.maxStage || 0) <= 1)
            return page.nameText()
        return page.nameText() + " " + page.roman(index)
    }

    function back() {
        if (typeof Nav !== "undefined" && Nav) {
            if (!Nav.goBack())
                Nav.push("achievements")
        }
    }

    FmScrollView {
        id: scroller
        anchors.fill: parent
        clip: true
        contentWidth: availableWidth

        ColumnLayout {
            width: scroller.availableWidth
            spacing: Theme?.spacingMd ?? 10

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme?.spacingSm ?? 8

                FmButton {
                    objectName: "achievementDetailBack"
                    primary: false
                    iconOnly: true
                    iconName: "arrow-left"
                    onClicked: page.back()
                }

                Item { Layout.fillWidth: true }
            }

            FmCard {
                objectName: "achievementDetailHeaderCard"
                Layout.fillWidth: true

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingMd ?? 12

                    Rectangle {
                        Layout.preferredWidth: 54
                        Layout.preferredHeight: 54
                        radius: width / 2
                        color: page.detail && page.detail.unlocked
                               ? (Theme?.accentSoft ?? "transparent")
                               : (Theme?.bgLight ?? "transparent")
                        border.width: 1
                        border.color: page.detail && page.detail.unlocked
                                      ? (Theme?.accent ?? "transparent")
                                      : (Theme?.cardBorder ?? "transparent")

                        FmIcon {
                            anchors.centerIn: parent
                            name: page.detail && page.detail.icon ? String(page.detail.icon) : "trophy"
                            color: page.detail && page.detail.unlocked
                                   ? (Theme?.accent ?? "transparent")
                                   : (Theme?.textSecondary ?? "transparent")
                            size: Math.round((Theme?.iconSize ?? 20) * 1.4)
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: Theme?.spacingXs ?? 4

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme?.spacingSm ?? 8

                            Text {
                                objectName: "achievementDetailName"
                                Layout.fillWidth: true
                                text: page.nameText()
                                color: page.detail && page.detail.unlocked
                                       ? (Theme?.accent ?? "transparent")
                                       : (Theme?.textPrimary ?? "transparent")
                                font.pixelSize: Theme?.fontSizeTitle ?? 18
                                font.bold: true
                                elide: Text.ElideRight
                            }

                            FmTag {
                                objectName: "achievementDetailState"
                                visible: page.detail && page.detail.id !== undefined
                                text: page.detail && page.detail.maxed
                                      ? page.t("ach_maxed")
                                      : (page.detail && page.detail.unlocked
                                         ? page.t("ach_unlocked") : page.t("ach_locked"))
                                level: page.detail && page.detail.maxed
                                       ? "success" : (page.detail && page.detail.unlocked ? "accent" : "neutral")
                                iconName: page.detail && page.detail.maxed ? "check" : ""
                            }

                            FmTag {
                                objectName: "achievementDetailCategory"
                                visible: page.detail && page.detail.categoryNameKey !== undefined
                                         && String(page.detail.categoryNameKey).length > 0
                                text: page.detail && page.detail.categoryNameKey
                                      ? page.t(page.detail.categoryNameKey) : ""
                                level: "neutral"
                                iconName: page.detail && page.detail.categoryIcon
                                          ? String(page.detail.categoryIcon) : "folder"
                            }
                        }

                        Text {
                            objectName: "achievementDetailDesc"
                            Layout.fillWidth: true
                            text: page.descText()
                            color: Theme?.textSecondary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeBase ?? 12
                            wrapMode: Text.WordWrap
                        }
                    }
                }

                FmProgressBar {
                    objectName: "achievementDetailProgress"
                    Layout.fillWidth: true
                    value: page.detail ? Number(page.detail.percent || 0) / 100 : 0
                    label: page.detail
                           ? page.t("ach_stage_progress")
                             .replace("{current}", String(page.detail.stage || 0))
                             .replace("{total}", String(page.detail.maxStage || 0))
                           : ""
                    showPercent: true
                    barHeight: 8
                }
            }

            FmCard {
                objectName: "achievementDetailStagesCard"
                Layout.fillWidth: true
                title: page.t("ach_stages_title")

                Repeater {
                    model: page.detail && page.detail.stages ? page.detail.stages : []

                    delegate: Rectangle {
                        id: stageRow
                        required property var modelData
                        Layout.fillWidth: true
                        Layout.preferredHeight: 48
                        radius: Theme?.radiusMd ?? 8
                        color: stageRow.modelData.current
                               ? (Theme?.accentSoft ?? "transparent") : "transparent"
                        border.width: stageRow.modelData.current ? 1 : 0
                        border.color: Theme?.accent ?? "transparent"

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: Theme?.spacingSm ?? 8
                            anchors.rightMargin: Theme?.spacingSm ?? 8
                            spacing: Theme?.spacingSm ?? 8

                            Rectangle {
                                Layout.preferredWidth: 26
                                Layout.preferredHeight: 26
                                radius: width / 2
                                color: stageRow.modelData.unlocked
                                       ? (Theme?.accentSoft ?? "transparent")
                                       : (Theme?.bgMedium ?? "transparent")
                                border.width: 1
                                border.color: stageRow.modelData.unlocked
                                              ? (Theme?.accent ?? "transparent")
                                              : (Theme?.cardBorder ?? "transparent")

                                Text {
                                    anchors.centerIn: parent
                                    text: String(stageRow.modelData.index)
                                    color: stageRow.modelData.unlocked
                                           ? (Theme?.accent ?? "transparent")
                                           : (Theme?.textSecondary ?? "transparent")
                                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                                    font.bold: true
                                }
                            }

                            Text {
                                Layout.fillWidth: true
                                text: page.stageName(stageRow.modelData.index)
                                color: Theme?.textPrimary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeBase ?? 12
                                font.bold: Boolean(stageRow.modelData.current)
                                elide: Text.ElideRight
                            }

                            Text {
                                text: page.t("ach_stage_threshold")
                                      .replace("{value}", String(stageRow.modelData.threshold))
                                color: Theme?.textSecondary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 10
                                elide: Text.ElideRight
                            }

                            FmIcon {
                                Layout.preferredWidth: Theme?.iconSize ?? 20
                                Layout.preferredHeight: Theme?.iconSize ?? 20
                                name: stageRow.modelData.unlocked ? "check" : "lock"
                                color: stageRow.modelData.unlocked
                                       ? (Theme?.success ?? "transparent")
                                       : (Theme?.textSecondary ?? "transparent")
                                size: Theme?.iconSize ?? 20
                            }
                        }
                    }
                }
            }

            Item { Layout.preferredHeight: Theme?.spacingSm ?? 8 }
        }
    }
}
