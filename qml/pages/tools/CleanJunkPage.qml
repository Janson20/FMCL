// CleanJunkPage.qml —— 垃圾清理。
//
// 扫描与删除都在 Tools 桥的工作线程里跑；本页只负责目录/深度、分组展开、
// 文件复选与确认框。分组选择状态由桥持有，QML 不复制一份计数。

import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import "../../components"
import "../../components/dialogs"

FmPage {
    id: page
    objectName: "cleanJunkPage"

    readonly property bool live: (typeof Tools !== "undefined" && Tools && Tools.available) ? true : false
    property string folder: (page ? page.live : false) ? Tools.defaultFolder : ""

    function t(key) {
        return Tr ? (Tr?.map[key] ?? key) : key
    }

    function tf(key, params) {
        return Tr ? Tr.tf(key, params) : key
    }

    contentState: (page ? page.live : false) ? "ready" : "error"
    errorText: Tr?.map["plugin_state_error"] ?? "plugin_state_error"
    errorDetail: Tr?.map["tool_unavailable"] ?? "tool_unavailable"

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme?.spacingMd ?? 12

        FmCard {
            Layout.fillWidth: true
            title: page && page.t("tool_clean_junk_folder")
            subtitle: page && page.t("tool_clean_junk_skip_hint")

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme?.spacingSm ?? 8

                FmTextField {
                    id: folderField
                    objectName: "junkFolderField"
                    Layout.fillWidth: true
                    text: folder
                    placeholder: page && page.t("tool_clean_junk_folder")
                    onEdited: function(value) { page.folder = value }
                }

                FmButton {
                    primary: false
                    text: page && page.t("tool_select_folder")
                    onClicked: folderDialog.open()
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme?.spacingSm ?? 8

                Text {
                    text: page && page.t("tool_clean_junk_depth")
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                }

                FmSpinBox {
                    id: depthSpin
                    objectName: "junkDepthSpin"
                    from: 1
                    to: 999
                    value: 5
                    editable: true
                }

                Item { Layout.fillWidth: true }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 8

            FmButton {
                objectName: "junkScanButton"
                text: Tools && Tools.junkState === "scanning"
                      ? page && page.t("tool_clean_junk_scanning")
                      : page && page.t("tool_clean_junk_scan")
                loading: Tools && Tools.junkState === "scanning"
                enabled: (page ? page.live : false) && !(Tools && Tools.junkBusy)
                onClicked: {
                    if ((page ? page.live : false))
                        Tools.scanJunk(page.folder, String(depthSpin.value));
                }
            }

            FmButton {
                objectName: "junkDeleteButton"
                primary: false
                danger: true
                visible: (page ? page.live : false) && Tools.junkCount > 0
                enabled: (page ? page.live : false) && Tools.junkSelectedCount > 0 && !Tools.junkBusy
                text: page && page.t("tool_clean_junk_delete_selected")
                onClicked: confirmLoader.active = true
            }

            Item { Layout.fillWidth: true }

            Text {
                visible: (page ? page.live : false) && Tools.junkCount > 0
                text: page && page.tf("tool_clean_junk_found", {
                    "count": Tools.junkCount,
                    "size": Tools.junkTotalSizeText
                })
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
            }
        }

        FmInfoBar {
            Layout.fillWidth: true
            visible: (page ? page.live : false) && String(Tools.junkNotice || "").length > 0
            text: (page ? page.live : false) ? String(Tools.junkNotice || "") : ""
            level: (page ? page.live : false) ? String(Tools.junkNoticeLevel || "info") : "info"
        }

        FmInfoBar {
            Layout.fillWidth: true
            visible: (page ? page.live : false) && Tools.junkSelectedCount > 0
            text: page && page.tf("tool_clean_junk_selected", {
                "count": Tools.junkSelectedCount,
                "size": Tools.junkSelectedSizeText
            })
            level: "info"
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            ListView {
                id: groupList
                objectName: "junkList"
                anchors.fill: parent
                visible: (page ? page.live : false) && Tools.junkCount > 0
                clip: true
                model: (page ? page.live : false) ? Tools.junkGroups : []
                spacing: Theme?.spacingXs ?? 4
                ScrollBar.vertical: FmScrollBar {}

                delegate: Column {
                    id: groupDelegate
                    width: groupList.width
                    property var groupData: modelData
                    property string groupKey: modelData.key

                    Rectangle {
                        width: parent.width
                        height: 38
                        radius: Theme?.radiusSm ?? 6
                        color: groupData.expanded ? (Theme?.bgLight ?? "transparent") : "transparent"

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: Theme?.spacingXs ?? 4
                            anchors.rightMargin: Theme?.spacingSm ?? 8
                            spacing: Theme?.spacingXs ?? 4

                            FmToolButton {
                                objectName: "junkExpand_" + groupKey
                                iconName: groupData.expanded ? "chevron-down" : "arrow-right"
                                onClicked: Tools.toggleJunkGroupExpanded(groupKey)
                            }

                            FmCheckBox {
                                id: groupCheck
                                objectName: "junkGroupCheck_" + groupKey
                                checkState: groupData.checkState
                                onToggled: function() { Tools.setJunkGroupChecked(groupKey, groupCheck.checked) }
                            }

                            Text {
                                Layout.fillWidth: true
                                text: groupData.display
                                color: Theme?.textPrimary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeBase ?? 12
                                font.bold: true
                                elide: Text.ElideMiddle
                            }

                            Text {
                                text: page && page.tf("tool_clean_junk_dir_files", {
                                    "count": groupData.count,
                                    "size": groupData.sizeText
                                })
                                color: Theme?.textSecondary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 10
                            }
                        }
                    }

                    Column {
                        width: parent.width
                        visible: groupData.expanded

                        Repeater {
                            model: groupData.files

                            RowLayout {
                                width: groupDelegate.width - 2 * (Theme?.spacingLg ?? 16)
                                height: 32
                                anchors.left: parent.left
                                anchors.leftMargin: Theme?.spacingLg ?? 16
                                spacing: Theme?.spacingXs ?? 4

                                FmCheckBox {
                                    id: fileCheck
                                    objectName: "junkFileCheck_" + modelData.groupKey + "_" + index
                                    checkState: modelData.checked ? Qt.Checked : Qt.Unchecked
                                    onToggled: function() { Tools.setJunkFileChecked(modelData.groupKey, modelData.path, fileCheck.checked) }
                                }

                                Text {
                                    Layout.fillWidth: true
                                    text: modelData.name
                                    color: Theme?.textPrimary ?? "transparent"
                                    font.pixelSize: Theme?.fontSizeBase ?? 12
                                    elide: Text.ElideMiddle
                                }

                                Text {
                                    text: modelData.sizeText
                                    color: Theme?.textSecondary ?? "transparent"
                                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                                }
                            }
                        }
                    }
                }
            }

            FmLoadingState {
                anchors.fill: parent
                visible: (page ? page.live : false) && Tools.junkState === "scanning"
                title: page && page.t("tool_clean_junk_scanning")
            }

            FmEmptyState {
                anchors.fill: parent
                visible: (page ? page.live : false) && Tools.junkState === "ready" && Tools.junkCount === 0
                iconName: "trash"
                title: page && page.t("tool_clean_junk_none")
                description: page && page.t("tool_clean_junk_skip_hint")
            }

            FmEmptyState {
                anchors.fill: parent
                visible: (page ? page.live : false) && Tools.junkState === "idle"
                iconName: "trash"
                title: page && page.t("tool_clean_junk_title")
                description: page && page.t("tool_clean_junk_desc")
            }
        }
    }

    FolderDialog {
        id: folderDialog
        title: page && page.t("tool_clean_junk_folder_title")
        onAccepted: page.folder = Tools.localPath(selectedFolder.toString())
    }

    Loader {
        id: confirmLoader
        objectName: "junkConfirmLoader"
        anchors.fill: parent
        active: false
        sourceComponent: ConfirmDialog {
            anchors.fill: parent
            modalScrim: true
            request: ({
                "title": page && page.t("tool_clean_junk_confirm_title"),
                "message": page && page.tf("tool_clean_junk_confirm_text", {
                    "count": Tools.junkSelectedCount,
                    "size": Tools.junkSelectedSizeText
                }),
                "default": false
            })
            onAnswered: function (value) {
                confirmLoader.active = false;
                if (value)
                    Tools.deleteJunk();
            }
        }
    }
}
