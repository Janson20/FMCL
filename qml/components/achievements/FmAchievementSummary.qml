// FmAchievementSummary.qml —— 成就总览卡（阶段 3.21）。
//
// 什么时候用它：成就首页 / 云端页顶部那一块"总进度 + 同步状态 + 操作按钮"。
// 什么时候不要用：分类页、详情页（那里只显示当前对象的进度，不需要全局操作）。
//
// 数据全部由 Achievements 桥传入；本件不读服务、不拼业务判断，只把
// "0~100 的 percent" 换成 0~1 的进度条值。文案键由本件自己取（页面不用重复拼）。

import QtQuick
import QtQuick.Layouts
import ".."

FmCard {
    id: summaryCard
    objectName: "fmAchievementSummary"

    property var summary: ({})
    property real lastSyncAt: 0
    property bool loggedIn: true
    property string syncState: "idle"
    property string statusText: ""
    property string statusLevel: "info"
    property bool showActions: true

    signal syncRequested()
    signal cloudRequested()
    signal loginRequested()

    readonly property int total: summary && summary.total !== undefined ? Number(summary.total) : 0
    readonly property int unlocked: summary && summary.unlocked !== undefined ? Number(summary.unlocked) : 0
    readonly property real percent: summary && summary.percent !== undefined ? Number(summary.percent) : 0
    readonly property bool busy: syncState === "syncing" || syncState === "resetting"

    title: t("ach_stats_title")

    function t(key) {
        return (typeof Tr !== "undefined" && Tr) ? (Tr.map[String(key)] ?? String(key)) : String(key)
    }

    function lastSyncText() {
        if (lastSyncAt <= 0)
            return t("ach_never_synced")
        return t("ach_last_sync_time").replace("{time}",
            Qt.formatDateTime(new Date(lastSyncAt * 1000), "yyyy-MM-dd HH:mm:ss"))
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme?.spacingLg ?? 16

        FmProgressRing {
            objectName: "achievementSummaryRing"
            Layout.preferredWidth: 78
            Layout.preferredHeight: 78
            value: summaryCard.percent / 100
            lineWidth: 8
            showPercent: true
            ringColor: Theme?.accent ?? "transparent"
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: Theme?.spacingXs ?? 4

            Text {
                objectName: "achievementSummaryCount"
                Layout.fillWidth: true
                text: summaryCard.t("ach_stats_detail")
                      .replace("{unlocked}", String(summaryCard.unlocked))
                      .replace("{total}", String(summaryCard.total))
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeLarge ?? 14
                font.bold: true
                elide: Text.ElideRight
            }

            Text {
                objectName: "achievementSummaryLastSync"
                Layout.fillWidth: true
                text: summaryCard.lastSyncText()
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                elide: Text.ElideRight
            }

        }
    }

    FmInfoBar {
        objectName: "achievementSummaryLoginHint"
        Layout.fillWidth: true
        visible: !summaryCard.loggedIn
        level: "warning"
        text: summaryCard.t("ach_sync_login_hint")
        actionText: summaryCard.t("ach_sync_login_action")
        onActionTriggered: summaryCard.loginRequested()
    }

    FmInfoBar {
        objectName: "achievementSummaryStatus"
        Layout.fillWidth: true
        visible: summaryCard.statusText.length > 0
        level: summaryCard.statusLevel
        text: summaryCard.statusText
    }

    footer: RowLayout {
        visible: summaryCard.showActions
        spacing: Theme?.spacingSm ?? 8

        FmButton {
            objectName: "achievementSyncButton"
            iconName: "upload"
            text: summaryCard.syncState === "syncing"
                  ? summaryCard.t("ach_syncing") : summaryCard.t("ach_sync_btn")
            loading: summaryCard.syncState === "syncing"
            enabled: summaryCard.showActions && !summaryCard.busy
            onClicked: summaryCard.syncRequested()
        }

        FmButton {
            objectName: "achievementCloudButton"
            primary: false
            iconName: "upload"
            text: summaryCard.t("ach_cloud_title")
            enabled: summaryCard.showActions && !summaryCard.busy
            onClicked: summaryCard.cloudRequested()
        }
    }
}
