// CoordPage.qml —— 坐标转换器。
//
// 主世界与下界按 X/Z 8:1 换算，Y 不变；结果由桥按服务层同一套函数算出。

import QtQuick
import QtQuick.Layouts
import "../../components"

FmPage {
    id: page
    objectName: "coordPage"

    readonly property bool live: (typeof Tools !== "undefined" && Tools && Tools.available) ? true : false
    property string xText: "0"
    property string yText: "64"
    property string zText: "0"

    function t(key) {
        return Tr ? (Tr?.map[key] ?? key) : key
    }

    function tf(key, params) {
        return Tr ? Tr.tf(key, params) : key
    }

    contentState: (page ? page.live : false) ? "ready" : "error"
    errorText: Tr?.map["plugin_state_error"] ?? "plugin_state_error"
    errorDetail: Tr?.map["tool_unavailable"] ?? "tool_unavailable"

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
                    spacing: Theme?.spacingMd ?? 12

                    FmTextField {
                        objectName: "coordX"
                        Layout.fillWidth: true
                        label: "X"
                        text: xText
                        onEdited: function(value) { page.xText = value }
                    }

                    FmTextField {
                        objectName: "coordY"
                        Layout.fillWidth: true
                        label: "Y"
                        text: yText
                        onEdited: function(value) { page.yText = value }
                    }

                    FmTextField {
                        objectName: "coordZ"
                        Layout.fillWidth: true
                        label: "Z"
                        text: zText
                        onEdited: function(value) { page.zText = value }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 8

                    FmButton {
                        objectName: "coordToNether"
                        primary: false
                        text: page && page.t("tool_coord_to_nether")
                        enabled: (page ? page.live : false)
                        onClicked: Tools.convertCoordinates(page.xText, page.yText, page.zText, "nether")
                    }

                    FmButton {
                        objectName: "coordToOverworld"
                        primary: false
                        text: page && page.t("tool_coord_to_overworld")
                        enabled: (page ? page.live : false)
                        onClicked: Tools.convertCoordinates(page.xText, page.yText, page.zText, "overworld")
                    }

                    Item { Layout.fillWidth: true }
                }
            }

            FmCard {
                Layout.fillWidth: true
                visible: (page ? page.live : false) && String(Tools.coordResult.text || "").length > 0

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingMd ?? 12

                    Text {
                        Layout.fillWidth: true
                        text: (page ? page.live : false) ? String(Tools.coordResult.text || "") : ""
                        color: Theme?.accent ?? "transparent"
                        font.pixelSize: Theme?.fontSizeTitle ?? 18
                        font.bold: true
                    }

                    FmButton {
                        primary: false
                        text: page && page.t("tool_copy_result")
                        onClicked: {
                            if ((page ? page.live : false) && String(Tools.coordResult.text || "").length > 0) {
                                Dialogs.set_clipboard(String(Tools.coordResult.text));
                                Dialogs.notify({
                                    "level": "success",
                                    "message": page && page.t("tool_copy_copied"),
                                    "icon": "copy"
                                });
                            }
                        }
                    }
                }

                Text {
                    Layout.fillWidth: true
                    text: (page ? page.live : false) && Tools.coordResult.target === "nether"
                          ? page && page.tf("tool_coord_result_nether", {
                                "x": String(Tools.coordResult.x || 0),
                                "y": String(Tools.coordResult.y || 0),
                                "z": String(Tools.coordResult.z || 0),
                                "rx": String(Tools.coordResult.rx || 0),
                                "rz": String(Tools.coordResult.rz || 0)
                            })
                          : ((page ? page.live : false)
                             ? page && page.tf("tool_coord_result_overworld", {
                                   "x": String(Tools.coordResult.x || 0),
                                   "y": String(Tools.coordResult.y || 0),
                                   "z": String(Tools.coordResult.z || 0),
                                   "rx": String(Tools.coordResult.rx || 0),
                                   "rz": String(Tools.coordResult.rz || 0)
                               })
                             : "")
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                    wrapMode: Text.WordWrap
                }
            }
        }
    }
}
