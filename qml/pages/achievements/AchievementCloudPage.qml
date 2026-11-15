// AchievementCloudPage.qml —— 成就云同步与重置。
//
// 路由 achievements/cloud。同步按钮沿用总览卡；重置云端 / 重置本地都要经过
// FmAchievementResetDialog 的"危险确认 → 输入短语"两步，输入不匹配不会关窗。

import QtQuick
import QtQuick.Layouts
import "../../components"
import "../../components/achievements"

FmPage {
    id: page
    objectName: "achievementCloudPage"

    readonly property bool live: (typeof Achievements !== "undefined" && Achievements) ? true : false
    property string resetMode: ""

    title: page.t("ach_cloud_title")
    iconName: "upload"

    contentState: !page.live ? "error"
                  : (Achievements.state === "error" ? "error"
                  : (Achievements.state !== "ready" ? "loading" : "ready"))
    loadingText: page.t("plugin_state_loading")
    errorText: page.t("plugin_state_error")
    errorDetail: page.live ? String(Achievements.error || "") : ""
    retryText: page.t("refresh")

    onRetried: page.refresh()
    Component.onCompleted: Qt.callLater(page.activate)
    onVisibleChanged: {
        if (page.visible)
            Qt.callLater(page.activate)
    }

    function t(key) {
        return (typeof Tr !== "undefined" && Tr) ? (Tr.map[String(key)] ?? String(key)) : String(key)
    }

    function activate() {
        if (page.live)
            Achievements.refresh()
    }

    function refresh() {
        if (page.live)
            Achievements.refresh()
    }

    function back() {
        if (typeof Nav !== "undefined" && Nav) {
            if (!Nav.goBack())
                Nav.push("achievements")
        }
    }

    function resetPhrase() {
        return page.resetMode === "cloud"
               ? page.t("ach_reset_cloud_confirm_key")
               : page.t("ach_reset_local_confirm_key")
    }

    function startReset(mode) {
        if (!page.live || page.resetMode.length > 0)
            return
        if (mode === "cloud" && !Achievements.loggedIn)
            return
        page.resetMode = mode
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme?.spacingMd ?? 10

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 8

            FmButton {
                objectName: "achievementCloudBack"
                primary: false
                iconOnly: true
                iconName: "arrow-left"
                onClicked: page.back()
            }

            Item { Layout.fillWidth: true }
        }

        FmScrollView {
            id: scroller
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: availableWidth

            ColumnLayout {
                width: scroller.availableWidth
                spacing: Theme?.spacingMd ?? 10

                FmAchievementSummary {
                    objectName: "achievementCloudSummary"
                    Layout.fillWidth: true
                    summary: page.live ? Achievements.summary : ({})
                    lastSyncAt: page.live ? Number(Achievements.lastSyncAt || 0) : 0
                    loggedIn: page.live ? Boolean(Achievements.loggedIn) : false
                    syncState: page.live ? String(Achievements.syncState || "idle") : "idle"
                    statusText: page.live ? String(Achievements.statusText || "") : ""
                    statusLevel: page.live ? String(Achievements.statusLevel || "info") : "info"
                    showActions: false
                    onLoginRequested: {
                        if (typeof Nav !== "undefined" && Nav)
                            Nav.push("settings/ai")
                    }
                }

                FmCard {
                    objectName: "achievementCloudActions"
                    Layout.fillWidth: true
                    title: page.t("ach_cloud_title")
                    subtitle: page.t("ach_cloud_desc")

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme?.spacingSm ?? 8

                        FmButton {
                            objectName: "achievementCloudSyncButton"
                            iconName: "upload"
                            text: page.live && Achievements.syncState === "syncing"
                                  ? page.t("ach_syncing") : page.t("ach_sync_btn")
                            loading: page.live && Achievements.syncState === "syncing"
                            enabled: page.live && !Achievements.busy
                            onClicked: {
                                if (page.live)
                                    Achievements.syncNow()
                            }
                        }

                        FmButton {
                            objectName: "achievementCloudResetButton"
                            primary: false
                            danger: true
                            iconName: "trash"
                            text: page.t("ach_reset_cloud_btn")
                            enabled: page.live && !Achievements.busy && Achievements.loggedIn
                            onClicked: page.startReset("cloud")
                        }

                        FmButton {
                            objectName: "achievementLocalResetButton"
                            primary: false
                            danger: true
                            iconName: "trash"
                            text: page.t("ach_reset_local_btn")
                            enabled: page.live && !Achievements.busy
                            onClicked: page.startReset("local")
                        }
                    }
                }

                Item { Layout.preferredHeight: Theme?.spacingSm ?? 8 }
            }
        }
    }

    Loader {
        id: resetLoader
        objectName: "achievementResetLoader"
        anchors.fill: parent
        z: 40
        active: page.resetMode.length > 0
        sourceComponent: Component {
            FmAchievementResetDialog {
                id: resetDialog
                step1Title: page.t("ach_confirm_step1_title")
                step1Message: page.t("ach_confirm_step1_msg")
                step2Title: page.t("ach_confirm_step2_title")
                step2Message: page.t("ach_confirm_step2_msg").replace("{phrase}", page.resetPhrase())
                phrase: page.resetPhrase()
                continueText: page.t("ach_confirm_continue")
                confirmText: page.t("ach_confirm_final")
                cancelText: page.t("cancel")
                mismatchText: page.t("ach_confirm_mismatch")
                Component.onCompleted: resetDialog.open()
                onConfirmed: {
                    var mode = page.resetMode
                    page.resetMode = ""
                    if (!page.live)
                        return
                    if (mode === "cloud")
                        Achievements.resetCloud()
                    else if (mode === "local")
                        Achievements.resetLocal()
                }
                onCancelled: {
                    page.resetMode = ""
                }
            }
        }
    }
}
