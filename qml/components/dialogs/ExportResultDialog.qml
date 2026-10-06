// ExportResultDialog.qml —— 导出结果对话框（阶段 3 任务 3.5，对照表 M-29）；「打开所在目录」是本轮新增。
//
// ## 与旧实现的关系
//
// 旧界面导出成功后弹一句 `messagebox.showinfo(_("account_export_title"), _("account_export_success"))`
// （`account_manager.py:578`）—— 用户看完只知道自己"导出成功"，文件在哪还得自己去找。
// 用户 2026-10-06 裁决：在结果框上加一颗**「打开所在目录」**（本轮新增能力，
// 已登记在对照表 M-29 的备注里）。其余分支（失败 → `account_export_failed`）
// 仍走 Toast，与旧实现"失败弹 `showerror`"等价。
//
// 按钮文案复用 `version_open_folder`（3.4 的「打开目录」/「打开日志目录」两处已在用的键），
// 不新增 i18n 键 —— R3/R4 只增不减，能复用就不加。

import QtQuick
import QtQuick.Layouts
import ".."

Item {
    id: dialog
    objectName: "accountExportResultDialog"

    //: 导出到的文件路径（同时显示给用户，省得去猜目录）
    property string exportPath: ""

    signal closed()
    signal openFolderRequested()

    function t(key) {
        return Tr ? (Tr.map[key] ?? key) : key
    }

    focus: true
    Keys.onEscapePressed: dialog.closed()

    Rectangle {
        objectName: "accountExportResultScrim"
        anchors.fill: parent
        color: Theme?.bgDark ?? "transparent"
        opacity: 0.55
    }

    Rectangle {
        id: card
        objectName: "accountExportResultCard"
        anchors.centerIn: parent
        width: Math.min(520, Math.max(320, parent.width - 2 * (Theme?.spacingXl ?? 24)))
        height: layout.implicitHeight + 2 * (Theme?.spacingLg ?? 16)
        radius: Theme?.radiusLg ?? 8
        color: Theme?.cardBg ?? "transparent"
        border.width: 1
        border.color: Theme?.cardBorder ?? "transparent"

        MouseArea { anchors.fill: parent }

        ColumnLayout {
            id: layout
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: Theme?.spacingLg ?? 16
            spacing: Theme?.spacingSm ?? 8

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme?.spacingSm ?? 8

                FmIcon {
                    objectName: "accountExportResultIcon"
                    name: "success"
                    color: Theme?.success ?? "transparent"
                    size: Theme?.fontSizeTitle ?? 18
                }

                Text {
                    objectName: "accountExportResultTitle"
                    Layout.fillWidth: true
                    text: dialog.t("account_export_title")
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeTitle ?? 18
                    font.bold: true
                    elide: Text.ElideRight
                }
            }

            Text {
                objectName: "accountExportResultMessage"
                Layout.fillWidth: true
                text: dialog.t("account_export_success")
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeBase ?? 12
                wrapMode: Text.WordWrap
            }

            //: 导出到哪了 —— 旧界面没有这一行（新增），提示框里看得见路径才敢点"打开目录"
            Text {
                objectName: "accountExportResultPath"
                Layout.fillWidth: true
                text: dialog.exportPath
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 11
                elide: Text.ElideMiddle
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: Theme?.spacingXs ?? 4
                spacing: Theme?.spacingSm ?? 8

                Item { Layout.fillWidth: true }

                FmButton {
                    objectName: "accountExportOpenFolder"
                    primary: false
                    iconName: "folder-open"
                    text: dialog.t("version_open_folder")
                    onClicked: dialog.openFolderRequested()
                }

                FmButton {
                    objectName: "accountExportResultOk"
                    text: dialog.t("confirm")
                    onClicked: dialog.closed()
                }
            }
        }
    }
}
