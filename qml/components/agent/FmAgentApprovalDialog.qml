// FmAgentApprovalDialog.qml —— 高危命令 / 文件修改的确认浮层。
//
// 工具循环在 worker 里停住等这个答案；所以任何关闭路径都必须发 answered(false)，
// 不能只是把 visible 改掉。页面在 answered 里调 Agent.answerApproval。
//
// 危险命令与文件修改共用一张卡：两者都是“A 会做一件有副作用的事，B 先确认”，
// 区别只在正文；不复制两套遮罩/按钮/键盘处理。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Item {
    id: dialog
    objectName: "fmAgentApprovalDialog"

    property var request: ({})

    signal answered(bool accepted)

    readonly property string kind: request && request.kind !== undefined
                                   ? String(request.kind) : "dangerous"
    readonly property bool dangerous: kind === "dangerous"
    readonly property string titleText: dangerous
                                        ? t("agent_exec_dangerous_title")
                                        : t("agent_file_edit_title")
    readonly property string messageText: {
        if (dangerous) {
            var template = t("agent_exec_dangerous_warning")
            return template.replace("{path}", String(request.path || ""))
                           .replace("{command}", String(request.command || ""))
        }
        return t("agent_file_edit_hint")
    }

    function t(key) {
        return (typeof Tr !== "undefined" && Tr) ? (Tr.map[String(key)] ?? String(key)) : String(key)
    }

    function accept() {
        dialog.answered(true)
    }

    function deny() {
        dialog.answered(false)
    }

    focus: visible
    Keys.onEscapePressed: dialog.deny()
    Keys.onReturnPressed: dialog.deny()
    Keys.onEnterPressed: dialog.deny()

    Rectangle {
        anchors.fill: parent
        color: Theme?.scrim ?? "transparent"
    }

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.AllButtons
    }

    Rectangle {
        id: card
        anchors.centerIn: parent
        width: Math.min(620, Math.max(320, parent.width - 2 * (Theme?.spacingXl ?? 24)))
        height: Math.min(560, layout.implicitHeight + 2 * (Theme?.spacingLg ?? 16))
        radius: Theme?.radiusLg ?? 0
        color: Theme?.cardBg ?? "transparent"
        border.width: 1
        border.color: Theme?.cardBorder ?? "transparent"

        ColumnLayout {
            id: layout
            anchors.fill: parent
            anchors.margins: Theme?.spacingLg ?? 16
            spacing: Theme?.spacingMd ?? 12

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme?.spacingSm ?? 8

                FmIcon {
                    name: "warning"
                    color: Theme?.warning ?? "transparent"
                    size: Theme?.iconSize ?? 20
                }

                Text {
                    Layout.fillWidth: true
                    text: dialog.titleText
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeTitle ?? 18
                    font.bold: true
                    elide: Text.ElideRight
                }

                FmToolButton {
                    objectName: "agentApprovalClose"
                    iconName: "close"
                    side: 28
                    onClicked: dialog.deny()
                }
            }

            Text {
                Layout.fillWidth: true
                text: dialog.messageText
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeBase ?? 12
                wrapMode: Text.WordWrap
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(260, Math.max(90, detailText.implicitHeight + 2 * (Theme?.spacingSm ?? 8)))
                radius: Theme?.radiusMd ?? 0
                color: Theme?.bgDark ?? "transparent"
                border.width: 1
                border.color: Theme?.cardBorder ?? "transparent"
                clip: true

                Flickable {
                    anchors.fill: parent
                    clip: true
                    contentWidth: width
                    contentHeight: detailText.implicitHeight + 2 * (Theme?.spacingSm ?? 8)

                    ScrollBar.vertical: FmScrollBar {
                        thickness: 5
                    }

                    Text {
                        id: detailText
                        objectName: "agentApprovalDetail"
                        x: Theme?.spacingSm ?? 8
                        y: Theme?.spacingSm ?? 8
                        width: parent.width - 2 * (Theme?.spacingSm ?? 8)
                        text: dialog.dangerous
                              ? String("$ " + String(dialog.request.command || ""))
                              : String(dialog.request.summary || dialog.request.path || "")
                        textFormat: Text.PlainText
                        wrapMode: Text.Wrap
                        color: Theme?.textPrimary ?? "transparent"
                        font.family: "Consolas"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        lineHeight: 1.25
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme?.spacingSm ?? 8

                Item { Layout.fillWidth: true }

                FmButton {
                    objectName: "agentApprovalDeny"
                    primary: false
                    danger: true
                    text: dialog.t("agent_permission_deny")
                    onClicked: dialog.deny()
                }

                FmButton {
                    objectName: "agentApprovalAllow"
                    primary: true
                    text: dialog.t("agent_permission_allow")
                    onClicked: dialog.accept()
                }
            }
        }
    }
}
