// FortunePage.qml —— 今日人品（阶段 3 任务 3.20 / C-02）。
//
// 评级由日期种子决定，同一天结果相同；历史按日期去重，最近 60 条。
// 旧界面的 6 个表情改成 mood 图标，界面不再出现 emoji。

import QtQuick
import QtQuick.Layouts
import "../../components"

FmPage {
    id: page
    objectName: "fortunePage"

    readonly property bool live: (typeof Tools !== "undefined" && Tools && Tools.available) ? true : false

    function t(key) {
        return Tr ? (Tr?.map[key] ?? key) : key
    }

    function tf(key, params) {
        return Tr ? Tr.tf(key, params) : key
    }

    function iconFor(key) {
        if (key === "tool_fortune_terrible")
            return "mood-terrible";
        if (key === "tool_fortune_bad")
            return "mood-bad";
        if (key === "tool_fortune_normal")
            return "mood-normal";
        if (key === "tool_fortune_good")
            return "mood-good";
        if (key === "tool_fortune_great")
            return "mood-great";
        return "crown";
    }

    function colorFor(key) {
        if (key === "tool_fortune_terrible")
            return Theme?.error ?? "transparent";
        if (key === "tool_fortune_bad")
            return Theme?.warning ?? "transparent";
        if (key === "tool_fortune_normal")
            return Theme?.textSecondary ?? "transparent";
        if (key === "tool_fortune_good")
            return Theme?.success ?? "transparent";
        if (key === "tool_fortune_great")
            return Theme?.accent ?? "transparent";
        return Theme?.accentHover ?? "transparent";
    }

    contentState: (page ? page.live : false) ? "ready" : "error"
    errorText: Tr?.map["plugin_state_error"] ?? "plugin_state_error"
    errorDetail: Tr?.map["tool_unavailable"] ?? "tool_unavailable"

    Component.onCompleted: {
        if ((page ? page.live : false))
            Tools.loadFortune();
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
                        objectName: "fortuneCheckButton"
                        text: page && page.t("tool_fortune_check")
                        enabled: (page ? page.live : false)
                        onClicked: {
                            if ((page ? page.live : false))
                                Tools.checkFortune();
                        }
                    }

                    Item { Layout.fillWidth: true }
                }

                FmInfoBar {
                    Layout.fillWidth: true
                    visible: (page ? page.live : false) && String(Tools.fortuneError || "").length > 0
                    text: (page ? page.live : false) ? String(Tools.fortuneError || "") : ""
                    level: "error"
                }

                RowLayout {
                    Layout.fillWidth: true
                    visible: (page ? page.live : false) && String(Tools.fortuneResult.date || "").length > 0
                    spacing: Theme?.spacingMd ?? 12

                    FmIcon {
                        name: (page ? page.live : false) ? page && page.iconFor(String(Tools.fortuneResult.levelKey || "")) : "mood-normal"
                        color: (page ? page.live : false) ? page && page.colorFor(String(Tools.fortuneResult.levelKey || "")) : (Theme?.textSecondary ?? "transparent")
                        size: 56
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: Theme?.spacingXs ?? 4

                        Text {
                            text: (page ? page.live : false) ? String(Tools.fortuneResult.value || 0) : ""
                            color: (page ? page.live : false)
                                   ? page && page.colorFor(String(Tools.fortuneResult.levelKey || ""))
                                   : (Theme?.textPrimary ?? "transparent")
                            font.pixelSize: 36
                            font.bold: true
                        }

                        Text {
                            text: (page ? page.live : false) ? page && page.t(String(Tools.fortuneResult.levelKey || "")) : ""
                            color: (page ? page.live : false)
                                   ? page && page.colorFor(String(Tools.fortuneResult.levelKey || ""))
                                   : (Theme?.textSecondary ?? "transparent")
                            font.pixelSize: Theme?.fontSizeLarge ?? 14
                            font.bold: true
                        }

                        Text {
                            text: (page ? page.live : false)
                                  ? page && page.tf("tool_fortune_date", {"date": String(Tools.fortuneResult.date || "")})
                                  : ""
                            color: Theme?.textSecondary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                        }
                    }

                    Item { Layout.fillWidth: true }
                }
            }

            FmCard {
                Layout.fillWidth: true
                title: page && page.t("tool_fortune_history")

                Text {
                    Layout.fillWidth: true
                    visible: (page ? page.live : false) && Tools.fortuneHistoryCount === 0
                    text: page && page.t("tool_fortune_history_empty")
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                }

                Repeater {
                    model: (page ? page.live : false) ? Tools.fortuneHistory : []

                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: 40
                        radius: Theme?.radiusSm ?? 6
                        color: Theme?.bgMedium ?? "transparent"

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: Theme?.spacingSm ?? 8
                            anchors.rightMargin: Theme?.spacingSm ?? 8
                            spacing: Theme?.spacingSm ?? 8

                            FmIcon {
                                name: page && page.iconFor(String(modelData.levelKey || ""))
                                color: page && page.colorFor(String(modelData.levelKey || ""))
                                size: Theme?.iconSize ?? 20
                            }

                            Text {
                                Layout.fillWidth: true
                                text: String(modelData.date || "")
                                color: Theme?.textPrimary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeBase ?? 12
                            }

                            Text {
                                text: page && page.t(String(modelData.levelKey || ""))
                                color: page && page.colorFor(String(modelData.levelKey || ""))
                                font.pixelSize: Theme?.fontSizeBase ?? 12
                            }

                            Text {
                                text: String(modelData.value || 0)
                                color: Theme?.textSecondary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeBase ?? 12
                                font.bold: true
                            }
                        }
                    }
                }
            }
        }
    }
}
