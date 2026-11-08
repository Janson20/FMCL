// FmAgentMarkdown.qml —— AGENT 消息的 Markdown 渲染（阶段 3 任务 3.22）。
//
// 为什么不是 WebEngine：打包清单明确裁掉 Qt6WebEngineCore（单个 DLL 142 MB），
// 所以这里用 Python 侧解析出的“块”逐块画。段落/标题用 QML RichText 子集；
// 代码、表格、列表各自有独立布局，避免把整段 HTML 塞进 Text 后失去滚动与复制。
//
// 数据契约（services/agent_markdown.py）：
//   {type:"paragraph"|"heading"|"quote", html}
//   {type:"code", language, code, open}
//   {type:"list", ordered, start, items:[{html}]}
//   {type:"table", headers:[{html,align}], rows:[[{html,align}]]}
//   {type:"divider"}
//
// 本件不判断业务、不读服务；链接与复制都只发信号，由页面决定怎么处理。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Item {
    id: root
    objectName: "fmAgentMarkdown"

    property var blocks: []
    //: 页面收到后做 openUrlExternally / Agent.copyText
    signal linkActivated(string link)
    signal copyRequested(string text)

    implicitWidth: 320
    implicitHeight: layout.implicitHeight

    function t(key) {
        return (typeof Tr !== "undefined" && Tr) ? (Tr.map[String(key)] ?? String(key)) : String(key)
    }

    function isType(block, type) {
        return !!(block && String(block.type || "") === String(type))
    }

    function textAlignment(value) {
        if (value === "center")
            return Text.AlignHCenter
        if (value === "right")
            return Text.AlignRight
        return Text.AlignLeft
    }

    Column {
        id: layout
        width: root.width
        spacing: Theme?.spacingSm ?? 8

        Repeater {
            model: root.blocks

            delegate: Column {
                id: blockColumn
                required property var modelData

                width: layout.width
                spacing: Theme?.spacingXs ?? 4

                // ── 标题 ───────────────────────────────────────
                Text {
                    objectName: "agentHeading"
                    width: parent.width
                    visible: root.isType(blockColumn.modelData, "heading")
                    text: visible ? String(blockColumn.modelData.html || "") : ""
                    textFormat: Text.RichText
                    wrapMode: Text.WordWrap
                    color: Theme?.textPrimary ?? "transparent"
                    linkColor: Theme?.accent ?? "transparent"
                    font.pixelSize: {
                        var level = Number(blockColumn.modelData && blockColumn.modelData.level || 1)
                        if (level <= 1)
                            return Theme?.fontSizeTitle ?? 18
                        if (level === 2)
                            return Theme?.fontSizeLarge ?? 14
                        return Theme?.fontSizeBase ?? 12
                    }
                    font.bold: true
                    onLinkActivated: function (link) { root.linkActivated(String(link)) }
                }

                // ── 段落 ───────────────────────────────────────
                Text {
                    objectName: "agentParagraph"
                    width: parent.width
                    visible: root.isType(blockColumn.modelData, "paragraph")
                    text: visible ? String(blockColumn.modelData.html || "") : ""
                    textFormat: Text.RichText
                    wrapMode: Text.WordWrap
                    color: Theme?.textPrimary ?? "transparent"
                    linkColor: Theme?.accent ?? "transparent"
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                    lineHeight: 1.35
                    onLinkActivated: function (link) { root.linkActivated(String(link)) }
                }

                // ── 引用 ───────────────────────────────────────
                Row {
                    objectName: "agentQuote"
                    width: parent.width
                    visible: root.isType(blockColumn.modelData, "quote")
                    spacing: Theme?.spacingSm ?? 8

                    Rectangle {
                        width: 3
                        height: quoteText.implicitHeight
                        radius: 1
                        color: Theme?.accent ?? "transparent"
                    }

                    Text {
                        id: quoteText
                        width: parent.width - 3 - (Theme?.spacingSm ?? 8)
                        text: visible ? String(blockColumn.modelData.html || "") : ""
                        textFormat: Text.RichText
                        wrapMode: Text.WordWrap
                        color: Theme?.textSecondary ?? "transparent"
                        linkColor: Theme?.accent ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        lineHeight: 1.35
                        onLinkActivated: function (link) { root.linkActivated(String(link)) }
                    }
                }

                // ── 无序/有序列表 ──────────────────────────────
                Column {
                    objectName: "agentList"
                    width: parent.width
                    visible: root.isType(blockColumn.modelData, "list")
                    spacing: Theme?.spacingXs ?? 4

                    Repeater {
                        model: visible && blockColumn.modelData && blockColumn.modelData.items
                               ? blockColumn.modelData.items : []

                        Row {
                            required property int index
                            required property var modelData

                            width: parent.width
                            spacing: Theme?.spacingSm ?? 8

                            Text {
                                width: 20
                                text: blockColumn.modelData && blockColumn.modelData.ordered
                                      ? String(Number(blockColumn.modelData.start || 1) + index) + "."
                                      : "•"
                                color: Theme?.textSecondary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeBase ?? 12
                                horizontalAlignment: Text.AlignRight
                            }

                            Text {
                                width: parent.width - 28
                                text: String(modelData && modelData.html || "")
                                textFormat: Text.RichText
                                wrapMode: Text.WordWrap
                                color: Theme?.textPrimary ?? "transparent"
                                linkColor: Theme?.accent ?? "transparent"
                                font.pixelSize: Theme?.fontSizeBase ?? 12
                                lineHeight: 1.35
                                onLinkActivated: function (link) { root.linkActivated(String(link)) }
                            }
                        }
                    }
                }

                // ── 围栏代码 ───────────────────────────────────
                Column {
                    objectName: "agentCodeBlock"
                    width: parent.width
                    visible: root.isType(blockColumn.modelData, "code")
                    spacing: Theme?.spacingXs ?? 4

                    RowLayout {
                        width: parent.width
                        spacing: Theme?.spacingSm ?? 8

                        Text {
                            Layout.fillWidth: true
                            text: blockColumn.modelData && String(blockColumn.modelData.language || "").length > 0
                                  ? String(blockColumn.modelData.language)
                                  : "text"
                            color: Theme?.textSecondary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                            elide: Text.ElideRight
                        }

                        FmToolButton {
                            objectName: "agentCodeCopy"
                            iconName: "copy"
                            iconSize: Math.round((Theme?.iconSize ?? 20) * 0.8)
                            side: 24
                            onClicked: root.copyRequested(String(blockColumn.modelData.code || ""))
                        }
                    }

                    Rectangle {
                        width: parent.width
                        height: Math.min(
                                    360,
                                    Math.max(42, codeText.implicitHeight + 2 * (Theme?.spacingSm ?? 8)))
                        radius: Theme?.radiusMd ?? 0
                        color: Theme?.bgDark ?? "transparent"
                        border.width: 1
                        border.color: Theme?.cardBorder ?? "transparent"
                        clip: true

                        Flickable {
                            objectName: "agentCodeScroll"
                            anchors.fill: parent
                            anchors.margins: 1
                            clip: true
                            contentWidth: codeText.implicitWidth + 2 * (Theme?.spacingSm ?? 8)
                            contentHeight: codeText.implicitHeight + 2 * (Theme?.spacingSm ?? 8)

                            Text {
                                id: codeText
                                objectName: "agentCodeText"
                                x: Theme?.spacingSm ?? 8
                                y: Theme?.spacingSm ?? 8
                                width: implicitWidth
                                text: String(blockColumn.modelData && blockColumn.modelData.code || "")
                                textFormat: Text.PlainText
                                wrapMode: Text.NoWrap
                                color: Theme?.textPrimary ?? "transparent"
                                font.family: "Consolas"
                                font.pixelSize: Theme?.fontSizeSmall ?? 10
                                lineHeight: 1.25
                            }
                        }
                    }

                    Text {
                        width: parent.width
                        visible: blockColumn.modelData && blockColumn.modelData.open === true
                        text: root.t("agent_streaming")
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                    }
                }

                // ── 表格 ───────────────────────────────────────
                Column {
                    objectName: "agentTable"
                    width: parent.width
                    visible: root.isType(blockColumn.modelData, "table")
                    spacing: 0

                    Flickable {
                        width: parent.width
                        height: Math.max(32, tableColumn.implicitHeight)
                        clip: true
                        contentWidth: tableColumn.implicitWidth
                        contentHeight: tableColumn.implicitHeight

                        ScrollBar.horizontal: FmScrollBar {
                            thickness: 5
                            policy: contentWidth > width ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff
                        }

                        Column {
                            id: tableColumn
                            spacing: 0

                            Row {
                                Repeater {
                                    model: blockColumn.modelData && blockColumn.modelData.headers
                                           ? blockColumn.modelData.headers : []

                                    Rectangle {
                                        required property var modelData
                                        width: 156
                                        height: Math.max(32, headerText.implicitHeight + 10)
                                        color: Theme?.bgMedium ?? "transparent"
                                        border.width: 1
                                        border.color: Theme?.cardBorder ?? "transparent"

                                        Text {
                                            id: headerText
                                            anchors.fill: parent
                                            anchors.margins: 5
                                            text: String(modelData && modelData.html || "")
                                            textFormat: Text.RichText
                                            wrapMode: Text.WordWrap
                                            verticalAlignment: Text.AlignVCenter
                                            horizontalAlignment: root.textAlignment(modelData ? modelData.align : "left")
                                            color: Theme?.textPrimary ?? "transparent"
                                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                                            font.bold: true
                                        }
                                    }
                                }
                            }

                            Repeater {
                                model: blockColumn.modelData && blockColumn.modelData.rows
                                       ? blockColumn.modelData.rows : []

                                Row {
                                    required property int index
                                    required property var modelData

                                    Repeater {
                                        model: modelData || []

                                        Rectangle {
                                            required property var modelData
                                            width: 156
                                            height: Math.max(30, cellText.implicitHeight + 10)
                                            color: index % 2 === 0
                                                   ? (Theme?.cardBg ?? "transparent")
                                                   : (Theme?.bgMedium ?? "transparent")
                                            border.width: 1
                                            border.color: Theme?.cardBorder ?? "transparent"

                                            Text {
                                                id: cellText
                                                anchors.fill: parent
                                                anchors.margins: 5
                                                text: String(modelData && modelData.html || "")
                                                textFormat: Text.RichText
                                                wrapMode: Text.WordWrap
                                                verticalAlignment: Text.AlignVCenter
                                                horizontalAlignment: root.textAlignment(modelData ? modelData.align : "left")
                                                color: Theme?.textPrimary ?? "transparent"
                                                font.pixelSize: Theme?.fontSizeSmall ?? 10
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                // ── 水平线 ─────────────────────────────────────
                Rectangle {
                    objectName: "agentDivider"
                    width: parent.width
                    height: 1
                    visible: root.isType(blockColumn.modelData, "divider")
                    color: Theme?.divider ?? "transparent"
                }
            }
        }
    }
}
