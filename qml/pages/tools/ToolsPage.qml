// ToolsPage.qml —— 工具箱首页。
//
// 8 个工具 + 相关网站各自是独立二级页（03 的 C-09；相关网站见 3.26）。这里只做
// 入口卡片，不承载任何工具逻辑；
// 卡片本身不可点，打开动作由卡内的 FmButton 触发（FmCard 的既有约束）。

import QtQuick
import QtQuick.Layouts
import "../../components"

FmPage {
    id: page
    objectName: "toolsPage"

    readonly property bool live: (typeof Tools !== "undefined" && Tools && Tools.available) ? true : false

    //: 顺序与 02 第 4.8 节的 L2 页面一致。
    readonly property var entries: [
        {"key": "junk", "route": "tools/junk", "titleKey": "tool_clean_junk_title", "descKey": "tool_clean_junk_desc", "icon": "trash"},
        {"key": "port", "route": "tools/port", "titleKey": "tool_port_title", "descKey": "tool_port_desc", "icon": "target"},
        {"key": "hash", "route": "tools/hash", "titleKey": "tool_hash_title", "descKey": "tool_hash_desc", "icon": "file"},
        {"key": "coord", "route": "tools/coord", "titleKey": "tool_coord_title", "descKey": "tool_coord_desc", "icon": "world"},
        {"key": "fortune", "route": "tools/fortune", "titleKey": "tool_fortune_title", "descKey": "tool_fortune_desc", "icon": "dice"},
        {"key": "fact", "route": "tools/fact", "titleKey": "tool_fact_title", "descKey": "tool_fact_desc", "icon": "bulb"},
        {"key": "quiz", "route": "tools/quiz", "titleKey": "tool_quiz_title", "descKey": "tool_quiz_desc", "icon": "guide"},
        {"key": "download", "route": "tools/download", "titleKey": "tool_download_title", "descKey": "tool_download_desc", "icon": "download"},
        //: 相关网站：工具箱的二级入口。
        {"key": "links", "route": "tools/links", "titleKey": "links_title", "descKey": "links_description", "icon": "link"}
    ]

    contentState: !(page ? page.live : false) ? "error" : (Tools.available ? "ready" : "error")
    errorText: Tr?.map["plugin_state_error"] ?? "plugin_state_error"
    errorDetail: Tr?.map["tool_unavailable"] ?? "tool_unavailable"

    function t(key) {
        return Tr ? (Tr?.map[key] ?? key) : key
    }

    FmScrollView {
        id: scroller
        anchors.fill: parent
        contentWidth: availableWidth

        GridLayout {
            width: scroller.availableWidth
            columns: page.width >= 900 ? 2 : 1
            columnSpacing: Theme?.spacingMd ?? 12
            rowSpacing: Theme?.spacingMd ?? 12

            Repeater {
                model: page.entries

                FmCard {
                    objectName: "toolCard_" + modelData.key
                    Layout.fillWidth: true
                    Layout.preferredHeight: 156

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme?.spacingSm ?? 8

                        FmIcon {
                            objectName: "toolCardIcon_" + modelData.key
                            name: modelData.icon
                            color: Theme?.accent ?? "transparent"
                            size: Theme?.iconSize ?? 20
                            Layout.alignment: Qt.AlignTop
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: Theme?.spacingXs ?? 4

                            Text {
                                Layout.fillWidth: true
                                text: page && page.t(modelData.titleKey)
                                color: Theme?.textPrimary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeLarge ?? 14
                                font.bold: true
                                elide: Text.ElideRight
                            }

                            Text {
                                Layout.fillWidth: true
                                text: page && page.t(modelData.descKey)
                                color: Theme?.textSecondary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 10
                                wrapMode: Text.WordWrap
                                maximumLineCount: 3
                                elide: Text.ElideRight
                            }
                        }
                    }

                    Item { Layout.fillHeight: true }

                    FmButton {
                        objectName: "toolOpen_" + modelData.key
                        Layout.alignment: Qt.AlignRight
                        primary: false
                        text: page && page.t("tool_open")
                        onClicked: Nav.push(modelData.route)
                    }
                }
            }
        }
    }
}
