// FmAgentTodoPanel.qml —— 对话的待办面板（阶段 3 任务 3.22）。
//
// 数据来自 todo_write 工具写到 data/agent/todos/<session>.json 的列表；
// 桥在工具结果后刷新，页面只负责显示。没有待办时显示空态，不隐藏整块面板
// （隐藏会让用户以为“这个功能不存在”）。

import QtQuick
import QtQuick.Controls
import ".."

Rectangle {
    id: panel
    objectName: "fmAgentTodoPanel"

    property var todos: []

    radius: Theme?.radiusLg ?? 0
    color: Theme?.cardBg ?? "transparent"
    border.width: 1
    border.color: Theme?.cardBorder ?? "transparent"

    function t(key) {
        return (typeof Tr !== "undefined" && Tr) ? (Tr.map[String(key)] ?? String(key)) : String(key)
    }

    function statusIcon(status) {
        if (status === "completed")
            return "check"
        if (status === "in_progress")
            return "loading"
        return "pending"
    }

    function statusColor(status) {
        if (status === "completed")
            return Theme?.success ?? "transparent"
        if (status === "in_progress")
            return Theme?.accent ?? "transparent"
        return Theme?.textSecondary ?? "transparent"
    }

    Column {
        anchors.fill: parent
        anchors.margins: Theme?.spacingSm ?? 8
        spacing: Theme?.spacingSm ?? 8

        Row {
            width: parent.width
            spacing: Theme?.spacingXs ?? 4

            Text {
                width: parent.width - doneTag.width - (Theme?.spacingXs ?? 4)
                text: panel.t("agent_todo_title")
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeBase ?? 12
                font.bold: true
                elide: Text.ElideRight
                anchors.verticalCenter: parent.verticalCenter
            }

            FmTag {
                id: doneTag
                objectName: "agentTodoCount"
                text: String(panel.todos.filter(function (item) {
                    return String(item && item.status || "") === "completed"
                }).length) + "/" + String(panel.todos.length)
                level: "accent"
            }
        }

        ListView {
            id: list
            objectName: "agentTodoList"
            width: parent.width
            height: panel.todos.length === 0 ? 0 : Math.max(0, parent.height - parent.spacing - 30)
            clip: true
            spacing: Theme?.spacingXs ?? 4
            model: panel.todos

            ScrollBar.vertical: FmScrollBar {
                thickness: 5
            }

            delegate: Row {
                id: row
                required property int index
                required property var modelData

                width: list.width
                spacing: Theme?.spacingXs ?? 4

                FmIcon {
                    name: panel.statusIcon(String(modelData && modelData.status || "pending"))
                    color: panel.statusColor(String(modelData && modelData.status || "pending"))
                    size: Math.round((Theme?.iconSize ?? 20) * 0.7)
                    anchors.top: parent.top
                    anchors.topMargin: 4
                }

                Text {
                    width: parent.width - 24
                    text: String(modelData && modelData.content || "")
                    color: String(modelData && modelData.status || "") === "completed"
                           ? (Theme?.textSecondary ?? "transparent")
                           : (Theme?.textPrimary ?? "transparent")
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                    wrapMode: Text.WordWrap
                    font.strikeout: String(modelData && modelData.status || "") === "completed"
                }
            }
        }

        Text {
            width: parent.width
            visible: panel.todos.length === 0
            text: panel.t("agent_todo_empty")
            color: Theme?.textSecondary ?? "transparent"
            font.pixelSize: Theme?.fontSizeSmall ?? 10
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
        }
    }
}
