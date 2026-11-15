// FactPage.qml —— Minecraft 冷知识。
//
// 题库在 services/tool_service.py 的 MC_FACTS（40 条，与现有测试一致）。
// 今日知识按日期种子取；随机一条使用 random.randint。

import QtQuick
import QtQuick.Layouts
import "../../components"

FmPage {
    id: page
    objectName: "factPage"

    readonly property bool live: (typeof Tools !== "undefined" && Tools && Tools.available) ? true : false

    function t(key) {
        return Tr ? (Tr?.map[key] ?? key) : key
    }

    function tf(key, params) {
        return Tr ? Tr.tf(key, params) : key
    }

    contentState: (page ? page.live : false) ? "ready" : "error"
    errorText: Tr?.map["plugin_state_error"] ?? "plugin_state_error"
    errorDetail: Tr?.map["tool_unavailable"] ?? "tool_unavailable"

    Component.onCompleted: {
        if ((page ? page.live : false))
            Tools.loadFact();
    }

    FmScrollView {
        id: scroller
        anchors.fill: parent
        contentWidth: availableWidth

        ColumnLayout {
            width: scroller.availableWidth
            spacing: Theme?.spacingMd ?? 12

            FmCard {
                Layout.fillWidth: true

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 8

                    FmButton {
                        objectName: "factTodayButton"
                        enabled: (page ? page.live : false)
                        text: page && page.t("tool_fact_new")
                        onClicked: {
                            if ((page ? page.live : false))
                                Tools.loadFact();
                        }
                    }

                    FmButton {
                        objectName: "factRandomButton"
                        primary: false
                        enabled: (page ? page.live : false)
                        text: page && page.t("tool_fact_random")
                        onClicked: {
                            if ((page ? page.live : false))
                                Tools.randomFact();
                        }
                    }

                    Item { Layout.fillWidth: true }
                }
            }

            FmCard {
                Layout.fillWidth: true
                visible: (page ? page.live : false) && String(Tools.fact.text || "").length > 0

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingMd ?? 12

                    FmIcon {
                        name: "gem"
                        color: Theme?.accent ?? "transparent"
                        size: 28
                        Layout.alignment: Qt.AlignTop
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: Theme?.spacingSm ?? 8

                        FmTag {
                            text: (page ? page.live : false) ? page && page.t(String(Tools.fact.tagKey || "")) : ""
                            level: "accent"
                        }

                        Text {
                            Layout.fillWidth: true
                            text: (page ? page.live : false) ? String(Tools.fact.text || "") : ""
                            color: Theme?.textPrimary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeLarge ?? 14
                            wrapMode: Text.WordWrap
                        }

                        Text {
                            Layout.fillWidth: true
                            text: (page ? page.live : false)
                                  ? page && page.tf("tool_fact_index", {
                                        "index": String(Tools.fact.index || 0),
                                        "total": String(Tools.fact.total || 0)
                                    })
                                  : ""
                            color: Theme?.textSecondary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                        }
                    }
                }
            }
        }
    }
}
