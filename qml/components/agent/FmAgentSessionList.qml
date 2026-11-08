// FmAgentSessionList.qml —— 会话列表（阶段 3 任务 3.22）。
//
// 什么时候用它：Agent 页左侧。新会话、选择、重命名、删除都只发信号；
// 删除确认由页面弹，列表不自己删数据。
//
// 窄窗由页面决定是否隐藏本件（Agent 页有会话/待办切换），组件本身不猜宽度。

import QtQuick
import QtQuick.Controls
import ".."

Rectangle {
    id: panel
    objectName: "fmAgentSessionList"

    property var sessions: []
    property string currentSessionId: ""
    property bool busy: false

    signal newRequested()
    signal selectRequested(string sessionId)
    signal renameRequested(string sessionId, string title)
    signal deleteRequested(string sessionId, string title)

    radius: Theme?.radiusLg ?? 0
    color: Theme?.cardBg ?? "transparent"
    border.width: 1
    border.color: Theme?.cardBorder ?? "transparent"

    function t(key) {
        return (typeof Tr !== "undefined" && Tr) ? (Tr.map[String(key)] ?? String(key)) : String(key)
    }

    Column {
        anchors.fill: parent
        anchors.margins: Theme?.spacingSm ?? 8
        spacing: Theme?.spacingSm ?? 8

        Row {
            width: parent.width
            spacing: Theme?.spacingXs ?? 4

            Text {
                width: parent.width - newButton.width - (Theme?.spacingXs ?? 4)
                text: panel.t("agent_sessions")
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeBase ?? 12
                font.bold: true
                elide: Text.ElideRight
                anchors.verticalCenter: parent.verticalCenter
            }

            FmButton {
                id: newButton
                objectName: "agentSessionNew"
                primary: false
                iconName: "add"
                text: panel.t("agent_new_session")
                enabled: !panel.busy
                onClicked: panel.newRequested()
            }
        }

        ListView {
            id: list
            objectName: "agentSessionList"
            width: parent.width
            height: panel.sessions.length === 0 ? 0 : Math.max(0, parent.height - parent.spacing - 36)
            clip: true
            spacing: Theme?.spacingXs ?? 4
            model: panel.sessions

            ScrollBar.vertical: FmScrollBar {
                thickness: 5
            }

            delegate: Item {
                id: row
                required property int index
                required property var modelData

                width: list.width
                height: 42

                FmListItem {
                    width: parent.width - 62
                    height: parent.height
                    title: String(modelData && modelData.title || panel.t("agent_session_untitled"))
                    subtitle: modelData && modelData.updated_at
                              ? Qt.formatDateTime(new Date(Number(modelData.updated_at) * 1000), "MM-dd HH:mm")
                              : ""
                    selected: String(modelData && modelData.id || "") === panel.currentSessionId
                    clickable: !panel.busy
                    onClicked: panel.selectRequested(String(modelData && modelData.id || ""))
                }

                Row {
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 0

                    FmToolButton {
                        objectName: "agentSessionRename"
                        iconName: "edit"
                        iconSize: Math.round((Theme?.iconSize ?? 20) * 0.65)
                        side: 28
                        enabled: !panel.busy
                        onClicked: panel.renameRequested(
                                       String(modelData && modelData.id || ""),
                                       String(modelData && modelData.title || ""))
                    }

                    FmToolButton {
                        objectName: "agentSessionDelete"
                        iconName: "trash"
                        iconColor: Theme?.error ?? "transparent"
                        iconSize: Math.round((Theme?.iconSize ?? 20) * 0.65)
                        side: 28
                        enabled: !panel.busy
                        onClicked: panel.deleteRequested(
                                       String(modelData && modelData.id || ""),
                                       String(modelData && modelData.title || ""))
                    }
                }
            }
        }

        Text {
            width: parent.width
            visible: panel.sessions.length === 0
            text: panel.t("agent_sessions_empty")
            color: Theme?.textSecondary ?? "transparent"
            font.pixelSize: Theme?.fontSizeSmall ?? 10
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
        }

        Item { width: 1; height: 1 }
    }
}
