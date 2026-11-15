// HashPage.qml —— Hash 计算器。
//
// 计算在桥的工作线程里按 64 KB 分块跑；本页只负责文件选择、算法单选和结果展示。

import QtQuick
import QtQuick.Dialogs
import QtQuick.Layouts
import "../../components"

FmPage {
    id: page
    objectName: "hashPage"

    readonly property bool live: (typeof Tools !== "undefined" && Tools && Tools.available) ? true : false
    property string filePath: ""
    property string algorithm: "SHA256"

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
                        objectName: "hashFileField"
                        Layout.fillWidth: true
                        text: filePath
                        readOnly: true
                        placeholder: page && page.t("tool_hash_select_file")
                    }

                    FmButton {
                        primary: false
                        text: page && page.t("resource_select_file")
                        onClicked: fileDialog.open()
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingLg ?? 16

                    Repeater {
                        model: ["MD5", "SHA1", "SHA256", "SHA512"]

                        FmRadio {
                            id: algorithmOption
                            text: modelData
                            checked: page.algorithm === modelData
                            onToggled: function() { if (algorithmOption.checked) page.algorithm = modelData }
                        }
                    }

                    Item { Layout.fillWidth: true }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 8

                    FmButton {
                        objectName: "hashCalculateButton"
                        text: (page ? page.live : false) && Tools.hashBusy
                              ? page && page.t("tool_hash_calculating")
                              : page && page.t("tool_hash_calculate")
                        loading: (page ? page.live : false) && Tools.hashBusy
                        enabled: (page ? page.live : false) && !Tools.hashBusy && page.filePath.length > 0
                        onClicked: {
                            if ((page ? page.live : false))
                                Tools.calculateHash(page.filePath, page.algorithm);
                        }
                    }

                    Item { Layout.fillWidth: true }
                }

                FmInfoBar {
                    Layout.fillWidth: true
                    visible: (page ? page.live : false) && String(Tools.hashError || "").length > 0
                    text: (page ? page.live : false) ? String(Tools.hashError || "") : ""
                    level: "error"
                }
            }

            FmCard {
                Layout.fillWidth: true
                visible: (page ? page.live : false) && String(Tools.hashResult.digest || "").length > 0
                title: (page ? page.live : false)
                       ? String(Tools.hashResult.algorithm || "") + " · " + String(Tools.hashResult.fileName || "")
                       : ""

                Text {
                    Layout.fillWidth: true
                    text: (page ? page.live : false) ? String(Tools.hashResult.digest || "") : ""
                    color: Theme?.accent ?? "transparent"
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                    wrapMode: Text.WrapAnywhere
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 8

                    Text {
                        Layout.fillWidth: true
                        text: (page ? page.live : false)
                              ? page && page.tf("tool_hash_file", {
                                    "name": String(Tools.hashResult.fileName || ""),
                                    "size": String(Tools.hashResult.fileSizeText || "")
                                })
                              : ""
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                    }

                    FmButton {
                        primary: false
                        text: page && page.t("tool_copy_result")
                        onClicked: {
                            if ((page ? page.live : false) && String(Tools.hashResult.copyText || "").length > 0) {
                                Dialogs.set_clipboard(String(Tools.hashResult.copyText));
                                Dialogs.notify({
                                    "level": "success",
                                    "message": page && page.t("tool_copy_copied"),
                                    "icon": "copy"
                                });
                            }
                        }
                    }
                }
            }
        }
    }

    FileDialog {
        id: fileDialog
        title: page && page.t("resource_select_file")
        fileMode: FileDialog.OpenFile
        onAccepted: page.filePath = Tools.localPath(selectedFile.toString())
    }
}
