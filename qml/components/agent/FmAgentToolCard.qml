// FmAgentToolCard.qml —— 一次工具调用的卡片（阶段 3 任务 3.22）。
//
// 什么时候用它：对话流里 role === "tool" 的消息。运行中、完成、失败、拒绝四种
// 状态都用同一张卡；参数与结果默认收起，避免一屏被 JSON 占满。
// 数据来自 Agent.messages 的普通字典，本件不读服务、不执行工具。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

FmCard {
    id: card
    objectName: "fmAgentToolCard"

    property var message: ({})
    property bool expanded: false

    signal copyRequested(string text)

    readonly property string status: message && message.status !== undefined
                                     ? String(message.status) : "pending"
    readonly property string toolName: message && message.name !== undefined
                                       ? String(message.name) : "tool"
    readonly property string argsText: message && message.args !== undefined
                                       ? String(message.args) : ""
    readonly property string resultText: message && message.result !== undefined
                                         ? String(message.result) : ""

    function t(key) {
        return (typeof Tr !== "undefined" && Tr) ? (Tr.map[String(key)] ?? String(key)) : String(key)
    }

    function statusText() {
        if (status === "done")
            return t("agent_tool_done")
        if (status === "error")
            return t("agent_tool_failed")
        if (status === "denied")
            return t("agent_tool_denied")
        return t("agent_tool_pending")
    }

    function statusLevel() {
        if (status === "done")
            return "success"
        if (status === "error")
            return "error"
        if (status === "denied")
            return "warning"
        return "accent"
    }

    padding: Theme?.spacingMd ?? 10
    title: ""

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme?.spacingSm ?? 8

        Rectangle {
            Layout.preferredWidth: 28
            Layout.preferredHeight: 28
            radius: width / 2
            color: Theme?.bgMedium ?? "transparent"

            FmIcon {
                anchors.centerIn: parent
                name: status === "pending" ? "loading" : "tool"
                color: status === "pending"
                       ? (Theme?.textSecondary ?? "transparent")
                       : (Theme?.accent ?? "transparent")
                size: Math.round((Theme?.iconSize ?? 20) * 0.8)
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2

            Text {
                objectName: "agentToolName"
                Layout.fillWidth: true
                text: card.toolName
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeBase ?? 12
                font.bold: true
                elide: Text.ElideRight
            }

            Text {
                Layout.fillWidth: true
                visible: card.argsText.length > 0
                text: card.argsText
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                font.family: "Consolas"
                wrapMode: Text.Wrap
                maximumLineCount: 3
                elide: Text.ElideRight
            }
        }

        FmTag {
            objectName: "agentToolStatus"
            text: card.statusText()
            level: card.statusLevel()
        }

        FmToolButton {
            objectName: "agentToolCopy"
            visible: card.resultText.length > 0
            iconName: "copy"
            iconSize: Math.round((Theme?.iconSize ?? 20) * 0.75)
            side: 26
            onClicked: card.copyRequested(card.resultText)
        }

        FmToolButton {
            objectName: "agentToolToggle"
            visible: card.resultText.length > 0
            iconName: card.expanded ? "chevron-up" : "chevron-down"
            iconSize: Math.round((Theme?.iconSize ?? 20) * 0.75)
            side: 26
            onClicked: card.expanded = !card.expanded
        }
    }

    Rectangle {
        Layout.fillWidth: true
        visible: card.expanded && card.resultText.length > 0
        implicitHeight: resultScroll.height
        height: Math.min(220, Math.max(44, resultText.implicitHeight + 2 * (Theme?.spacingSm ?? 8)))
        radius: Theme?.radiusMd ?? 0
        color: Theme?.bgDark ?? "transparent"
        border.width: 1
        border.color: Theme?.cardBorder ?? "transparent"
        clip: true

        Flickable {
            id: resultScroll
            anchors.fill: parent
            clip: true
            contentWidth: width
            contentHeight: resultText.implicitHeight + 2 * (Theme?.spacingSm ?? 8)

            ScrollBar.vertical: FmScrollBar {
                thickness: 5
            }

            Text {
                id: resultText
                objectName: "agentToolResult"
                x: Theme?.spacingSm ?? 8
                y: Theme?.spacingSm ?? 8
                width: Math.max(0, parent.width - 2 * (Theme?.spacingSm ?? 8))
                text: card.resultText
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
                color: Theme?.textPrimary ?? "transparent"
                font.family: "Consolas"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                lineHeight: 1.25
            }
        }
    }
}
