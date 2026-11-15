// PortPage.qml —— 端口检测与 MC 服务器查询。
//
// 两种动作共用主机/端口：TCP 连通性检测 + Minecraft Server List Ping。
// 最近 10 条检测记录只保存在桥的内存里（旧实现也不落盘）。

import QtQuick
import QtQuick.Layouts
import "../../components"

FmPage {
    id: page
    objectName: "portPage"

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

                    FmTextField {
                        id: hostField
                        objectName: "portHostField"
                        Layout.fillWidth: true
                        label: page && page.t("tool_port_host")
                        placeholder: page && page.t("tool_port_host_placeholder")
                    }

                    FmTextField {
                        id: portField
                        objectName: "portPortField"
                        Layout.preferredWidth: 118
                        label: page && page.t("tool_port_port")
                        text: "25565"
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingXs ?? 4

                    Text {
                        text: page && page.t("tool_port_presets")
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                    }

                    Repeater {
                        model: [
                            {"label": "MC Java", "port": "25565"},
                            {"label": "MC Bedrock", "port": "19132"},
                            {"label": "HTTP", "port": "80"},
                            {"label": "HTTPS", "port": "443"}
                        ]

                        FmButton {
                            primary: false
                            text: modelData.label + " (" + modelData.port + ")"
                            onClicked: portField.text = modelData.port
                        }
                    }

                    Item { Layout.fillWidth: true }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 8

                    FmButton {
                        objectName: "portTestButton"
                        enabled: (page ? page.live : false) && !Tools.portBusy
                        loading: (page ? page.live : false) && Tools.portBusy && Tools.portAction === "test"
                        text: page && page.t("tool_port_test")
                        onClicked: {
                            if ((page ? page.live : false))
                                Tools.testPort(hostField.text, portField.text);
                        }
                    }

                    FmButton {
                        objectName: "portPeekButton"
                        primary: false
                        enabled: (page ? page.live : false) && !Tools.portBusy
                        loading: (page ? page.live : false) && Tools.portBusy && Tools.portAction === "peek"
                        text: page && page.t("tool_port_peek")
                        onClicked: {
                            if ((page ? page.live : false))
                                Tools.peekServer(hostField.text, portField.text);
                        }
                    }

                    Item { Layout.fillWidth: true }
                }

                FmInfoBar {
                    Layout.fillWidth: true
                    visible: (page ? page.live : false) && String(Tools.portError || "").length > 0
                    text: (page ? page.live : false) ? String(Tools.portError || "") : ""
                    level: "error"
                }
            }

            FmCard {
                Layout.fillWidth: true
                visible: (page ? page.live : false) && String(Tools.serverInfo.motd || "").length > 0
                title: page && page.t("tool_port_server_info")

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingMd ?? 12

                    Image {
                        id: favicon
                        visible: (page ? page.live : false) && String(Tools.serverInfo.favicon || "").length > 0
                        source: (page ? page.live : false) ? String(Tools.serverInfo.favicon || "") : ""
                        sourceSize.width: 48
                        sourceSize.height: 48
                        fillMode: Image.PreserveAspectFit
                    }

                    FmIcon {
                        visible: !favicon.visible
                        name: "server"
                        color: Theme?.textSecondary ?? "transparent"
                        size: 48
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: Theme?.spacingXs ?? 4

                        Text {
                            Layout.fillWidth: true
                            text: (page ? page.live : false) ? String(Tools.serverInfo.motd || "") : ""
                            color: Theme?.textPrimary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeLarge ?? 14
                            font.bold: true
                            wrapMode: Text.WordWrap
                        }

                        Text {
                            Layout.fillWidth: true
                            text: (page ? page.live : false)
                                  ? page && page.tf("tool_peek_detail", {
                                        "version": String(Tools.serverInfo.version || "?"),
                                        "online": String(Tools.serverInfo.online || 0),
                                        "max_p": String(Tools.serverInfo.maxPlayers || 0),
                                        "latency": String(Tools.serverInfo.latency || 0)
                                    })
                                  : ""
                            color: (page ? page.live : false) && Tools.serverInfo.latencyLevel === "warning"
                                   ? (Theme?.warning ?? "transparent")
                                   : ((page ? page.live : false) && Tools.serverInfo.latencyLevel === "error"
                                      ? (Theme?.error ?? "transparent")
                                      : (Theme?.success ?? "transparent"))
                            font.pixelSize: Theme?.fontSizeBase ?? 12
                        }

                        Text {
                            Layout.fillWidth: true
                            visible: (page ? page.live : false) && String(Tools.serverInfo.sample || "").length > 0
                            text: (page ? page.live : false) ? String(Tools.serverInfo.sample || "") : ""
                            color: Theme?.textSecondary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                            wrapMode: Text.WordWrap
                        }
                    }
                }
            }

            FmCard {
                Layout.fillWidth: true
                title: page && page.t("tool_port_history")

                Text {
                    Layout.fillWidth: true
                    visible: (page ? page.live : false) && Tools.portHistory.length === 0
                    text: page && page.t("tool_port_history_empty")
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                }

                Repeater {
                    model: (page ? page.live : false) ? Tools.portHistory : []

                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: 36
                        radius: Theme?.radiusSm ?? 6
                        color: Theme?.bgMedium ?? "transparent"

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: Theme?.spacingSm ?? 8
                            anchors.rightMargin: Theme?.spacingSm ?? 8
                            spacing: Theme?.spacingSm ?? 8

                            FmIcon {
                                name: modelData.open ? "success" : "error"
                                color: modelData.open
                                       ? (Theme?.success ?? "transparent")
                                       : (Theme?.error ?? "transparent")
                                size: Theme?.iconSize ?? 20
                            }

                            Text {
                                Layout.fillWidth: true
                                text: modelData.label
                                color: Theme?.textPrimary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeBase ?? 12
                                font.bold: true
                            }

                            Text {
                                text: page && page.t(modelData.statusKey)
                                color: modelData.open
                                       ? (Theme?.success ?? "transparent")
                                       : (Theme?.error ?? "transparent")
                                font.pixelSize: Theme?.fontSizeBase ?? 12
                            }

                            Text {
                                visible: modelData.open
                                text: modelData.latencyText
                                color: Theme?.textSecondary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 10
                            }

                            Text {
                                visible: !modelData.open && String(modelData.error || "").length > 0
                                text: modelData.error
                                color: Theme?.textSecondary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 10
                                elide: Text.ElideRight
                                Layout.maximumWidth: 260
                            }
                        }
                    }
                }
            }
        }
    }
}
