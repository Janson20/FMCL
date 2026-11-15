// FmAgentMessage.qml —— 一条用户/助手消息。
//
// 工具调用不是这个组件：role === "tool" 由 FmAgentToolCard 画。这里只负责
// 用户气泡、助手 Markdown、思考块折叠、复制/重发动作。
//
// 行为边界：
//   * 思考块默认收起（旧 Tk 默认展开，流式长思考会挡住回答；用户点一下即可展开）；
//   * 代码块里的复制按钮发 copyTextRequested，整条消息的复制发 copyRequested；
//   * 重发只由页面在“最后一条助手消息”上打开，组件本身不判断会话状态。

import QtQuick
import QtQuick.Layouts
import ".."

Item {
    id: root
    objectName: "fmAgentMessage"

    property var message: ({})
    property bool isLast: false
    property bool canRetry: false

    signal copyRequested(string messageId)
    signal copyTextRequested(string text)
    signal retryRequested()
    signal linkActivated(string link)

    readonly property string role: message && message.role !== undefined ? String(message.role) : "assistant"
    readonly property string content: message && message.content !== undefined ? String(message.content) : ""
    readonly property string thinking: message && message.thinking !== undefined ? String(message.thinking) : ""
    readonly property bool streaming: message && message.streaming === true
    readonly property bool assistant: role === "assistant"
    readonly property bool user: role === "user"
    readonly property bool system: role === "system"

    property bool thinkingExpanded: false

    readonly property bool emptyAssistant: assistant && content.length === 0
                                          && thinking.length === 0 && !streaming
    readonly property bool hasBlocks: !!(message && message.blocks && message.blocks.length > 0)

    function applyStream(value, blockList, thinkingText) {
        var next = {}
        for (var key in message)
            next[key] = message[key]
        next.content = String(value || "")
        next.blocks = blockList || []
        next.thinking = String(thinkingText || "")
        next.streaming = true
        message = next
    }

    implicitWidth: 320
    implicitHeight: emptyAssistant ? 0 : body.implicitHeight

    function t(key) {
        return (typeof Tr !== "undefined" && Tr) ? (Tr.map[String(key)] ?? String(key)) : String(key)
    }

    Column {
        id: body
        width: parent.width
        visible: !root.emptyAssistant
        spacing: Theme?.spacingXs ?? 4

        // ── 用户气泡 ───────────────────────────────────────
        Row {
            width: parent.width
            visible: root.user
            layoutDirection: Qt.RightToLeft
            spacing: Theme?.spacingSm ?? 8

            Item { width: 1; height: 1 }

            Rectangle {
                width: Math.min(root.width * 0.82, 620)
                height: userColumn.implicitHeight + 2 * (Theme?.spacingMd ?? 10)
                radius: Theme?.radiusLg ?? 0
                color: Theme?.accentSoft ?? "transparent"
                border.width: 1
                border.color: Theme?.accent ?? "transparent"

                Column {
                    id: userColumn
                    x: Theme?.spacingMd ?? 10
                    y: Theme?.spacingMd ?? 10
                    width: parent.width - 2 * (Theme?.spacingMd ?? 10)
                    spacing: Theme?.spacingXs ?? 4

                    FmAgentMarkdown {
                        width: parent.width
                        blocks: root.message && root.message.blocks ? root.message.blocks : []
                        onLinkActivated: function (link) { root.linkActivated(String(link)) }
                        onCopyRequested: function (text) { root.copyTextRequested(String(text)) }
                    }
                }
            }
        }

        // ── 助手消息 ───────────────────────────────────────
        Row {
            width: parent.width
            visible: root.assistant
            spacing: Theme?.spacingSm ?? 8

            Rectangle {
                width: 28
                height: 28
                radius: width / 2
                color: Theme?.accentSoft ?? "transparent"

                FmIcon {
                    anchors.centerIn: parent
                    name: "agent"
                    color: Theme?.accent ?? "transparent"
                    size: Math.round((Theme?.iconSize ?? 20) * 0.8)
                }
            }

            Column {
                width: parent.width - 28 - (Theme?.spacingSm ?? 8)
                spacing: Theme?.spacingXs ?? 4

                Row {
                    width: parent.width
                    spacing: Theme?.spacingSm ?? 8

                    Text {
                        text: root.t("agent_assistant")
                        color: Theme?.textPrimary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        font.bold: true
                    }

                    FmIcon {
                        id: streamingIcon
                        visible: root.streaming
                        name: "loading"
                        color: Theme?.textSecondary ?? "transparent"
                        size: Math.round((Theme?.fontSizeSmall ?? 10) * 1.2)
                        anchors.verticalCenter: parent.verticalCenter

                        RotationAnimator {
                            target: streamingIcon
                            from: 0
                            to: 360
                            duration: 900
                            loops: Animation.Infinite
                            running: root.streaming
                        }
                    }
                }

                // 思考块（可折叠）
                Column {
                    width: parent.width
                    spacing: 2
                    visible: root.thinking.length > 0

                    FmButton {
                        objectName: "agentThinkingToggle"
                        primary: false
                        iconName: root.thinkingExpanded ? "chevron-up" : "chevron-down"
                        text: root.t("agent_thinking_title")
                        onClicked: root.thinkingExpanded = !root.thinkingExpanded
                    }

                    Rectangle {
                        width: parent.width
                        visible: root.thinkingExpanded
                        height: thinkingText.implicitHeight + 2 * (Theme?.spacingSm ?? 8)
                        radius: Theme?.radiusMd ?? 0
                        color: Theme?.bgDark ?? "transparent"
                        border.width: 1
                        border.color: Theme?.cardBorder ?? "transparent"

                        Text {
                            id: thinkingText
                            x: Theme?.spacingSm ?? 8
                            y: Theme?.spacingSm ?? 8
                            width: parent.width - 2 * (Theme?.spacingSm ?? 8)
                            text: root.thinking
                            textFormat: Text.PlainText
                            wrapMode: Text.Wrap
                            color: Theme?.textSecondary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                            lineHeight: 1.3
                        }
                    }
                }

                Text {
                    width: parent.width
                    visible: root.content.length > 0 && !root.hasBlocks
                    text: root.content
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                    lineHeight: 1.35
                }

                FmAgentMarkdown {
                    width: parent.width
                    visible: root.content.length > 0 && root.hasBlocks
                    blocks: root.message && root.message.blocks ? root.message.blocks : []
                    onLinkActivated: function (link) { root.linkActivated(String(link)) }
                    onCopyRequested: function (text) { root.copyTextRequested(String(text)) }
                }

                Text {
                    width: parent.width
                    visible: root.streaming && root.content.length === 0 && root.thinking.length === 0
                    text: root.t("agent_thinking")
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                }

                Row {
                    width: parent.width
                    spacing: Theme?.spacingXs ?? 4
                    visible: root.content.length > 0 || root.thinking.length > 0

                    FmToolButton {
                        objectName: "agentMessageCopy"
                        iconName: "copy"
                        iconSize: Math.round((Theme?.iconSize ?? 20) * 0.7)
                        side: 26
                        onClicked: root.copyRequested(String(root.message && root.message.id || ""))
                    }

                    FmButton {
                        objectName: "agentMessageRetry"
                        visible: root.assistant && root.isLast && root.canRetry
                        primary: false
                        iconName: "refresh"
                        text: root.t("agent_retry")
                        onClicked: root.retryRequested()
                    }
                }
            }
        }

        // ── 系统消息（错误/提示）───────────────────────────
        FmInfoBar {
            width: parent.width
            visible: root.system
            level: "warning"
            text: root.content
        }
    }
}
