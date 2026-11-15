// FmUpdateSection.qml —— 设置 → 关于 的启动器更新入口。
//
// 只做展示与意图：检查/下载/安装都在 Update 桥的后台线程里。启动期自动检查到
// 新版本时，桥会直接更新这里的状态（version/notes），不需要用户再点一次检查。

import QtQuick
import QtQuick.Layouts
import ".."

FmCard {
    id: card
    objectName: "settingsUpdateSection"

    readonly property bool live: (typeof Update !== "undefined" && Update) ? true : false
    readonly property bool canUpdate: card.live && String(Update.version || "").length > 0
                                      && !Update.busy

    visible: card.live
    title: Tr?.map["about_update_title"] ?? "about_update_title"
    subtitle: Tr?.map["about_update_desc"] ?? "about_update_desc"

    ColumnLayout {
        Layout.fillWidth: true
        spacing: Theme?.spacingSm ?? 8

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 8

            Text {
                Layout.fillWidth: true
                text: (Tr?.map["about_update_current"] ?? "about_update_current")
                      + ": " + (card.live ? String(Update.currentVersion || "") : "")
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 11
                elide: Text.ElideRight
            }

            FmButton {
                objectName: "settingsUpdateCheck"
                primary: false
                text: Tr?.map["about_update_check"] ?? "about_update_check"
                loading: card.live ? Update.checking : false
                enabled: card.live && !Update.busy
                onClicked: if (card.live)
                               Update.check()
            }
        }

        Text {
            objectName: "settingsUpdateStatus"
            Layout.fillWidth: true
            visible: text.length > 0
            text: card.live ? String(Update.errorText || Update.statusText || "") : ""
            color: (card.live && Update.errorText.length > 0)
                   ? (Theme?.error ?? "transparent")
                   : (Theme?.textSecondary ?? "transparent")
            font.pixelSize: Theme?.fontSizeSmall ?? 11
            wrapMode: Text.WordWrap
        }

        FmProgressBar {
            objectName: "settingsUpdateProgress"
            Layout.fillWidth: true
            visible: card.live && Update.downloading
            value: card.live ? Update.progress : 0
            indeterminate: false
            showPercent: true
            barHeight: 6
        }

        Text {
            objectName: "settingsUpdateNotes"
            Layout.fillWidth: true
            visible: text.length > 0
            text: card.live ? String(Update.notes || "") : ""
            color: Theme?.textPrimary ?? "transparent"
            font.pixelSize: Theme?.fontSizeSmall ?? 11
            wrapMode: Text.WordWrap
            maximumLineCount: 12
            elide: Text.ElideRight
        }

        FmButton {
            objectName: "settingsUpdateNow"
            Layout.fillWidth: true
            visible: card.canUpdate
            text: Tr?.map["about_update_now"] ?? "about_update_now"
            loading: card.live ? Update.downloading : false
            enabled: card.canUpdate
            onClicked: if (card.live)
                           Update.start()
        }
    }
}
