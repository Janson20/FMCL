// FmPluginPermissionDialog.qml —— 插件权限确认 / 管理。
//
// 旧实现是 ui/windows/plugin_permission_dialog.py（198 行 Tk Toplevel）：
// 标题、副标题、按高/中/低风险分组的权限列表（每组带一句说明）、底部提示、
// 「拒绝 / 批准」两个按钮。安装、市场安装、启用前确认、权限管理四处共用。
//
// 为什么不做成 DialogHost 的一种：它不是"服务层发起、需要回填给调用方"的请求，
// 而是页面自己的局部状态（批准后要接着跑安装/启用），与 ConfirmDialog 同一类；
// 页面直接嵌一个本组件、听 approved()/denied() 即可（与 3.18 的 FmMusicMenu 同思路）。
//
// 数据形态：groups = [{risk, label_key, items: [{id, label_key, granted}]}]。
// 所有译文在**根属性**里一次算好（displayGroups / labels / titleValue），委托只读
// 普通字符串 —— 3.27 实测：委托里写 dialog.t(...) 会在页面销毁时刷
// "Property 't' … is not a function"（方法先于绑定消失），换成纯数据后消失。
//
// 纪律：颜色只来自 Theme.*、图标走 FmIcon、文案走 Tr.map（本组件没有硬编码文本）。

import QtQuick
import QtQuick.Layouts
import ".."

Item {
    id: dialog
    objectName: "fmPluginPermissionDialog"

    property string pluginName: ""
    property var groups: []
    property bool busy: false

    signal approved()
    signal denied()

    anchors.fill: parent
    z: 40
    visible: false
    focus: dialog.visible

    Keys.onEscapePressed: {
        if (dialog.visible && !dialog.busy)
            dialog.denied()
    }

    readonly property var textMap: (typeof Tr !== "undefined" && Tr) ? Tr.map : ({})

    readonly property string titleValue: {
        var template = dialog.textMap["plugin_permission_dialog_title"] ?? "plugin_permission_dialog_title"
        var name = dialog.pluginName.length > 0 ? dialog.pluginName : "?"
        return String(template).replace("{name}", name)
    }

    readonly property var labels: ({
        "subtitle": dialog.textMap["plugin_permission_dialog_subtitle"] ?? "plugin_permission_dialog_subtitle",
        "footer": dialog.textMap["plugin_permission_dialog_footer"] ?? "plugin_permission_dialog_footer",
        "approve": dialog.textMap["plugin_permission_approve"] ?? "plugin_permission_approve",
        "deny": dialog.textMap["plugin_permission_deny"] ?? "plugin_permission_deny",
        "granted": dialog.textMap["plugin_permission_granted"] ?? "plugin_permission_granted",
        "empty": dialog.textMap["plugin_no_permissions"] ?? "plugin_no_permissions"
    })

    //: 把 groups 展开成"只含显示字符串"的结构：label / tip / color / items[].name。
    //: 不调用任何本对象的方法，销毁期重算绑定也不会踩"not a function"。
    readonly property var displayGroups: {
        var source = dialog.groups || []
        var map = dialog.textMap
        var out = []
        for (var i = 0; i < source.length; i++) {
            var group = source[i] || ({})
            var risk = String(group.risk || "low")
            var color = risk === "high" ? (Theme?.error ?? "transparent")
                      : (risk === "medium" ? (Theme?.warning ?? "transparent")
                                           : (Theme?.success ?? "transparent"))
            var tipKey = risk === "high" ? "plugin_permission_risk_high_tip"
                       : (risk === "medium" ? "plugin_permission_risk_medium_tip"
                                            : "plugin_permission_risk_low_tip")
            var items = []
            var sourceItems = group.items || []
            for (var j = 0; j < sourceItems.length; j++) {
                var item = sourceItems[j] || ({})
                items.push({
                    "id": String(item.id || ""),
                    "name": map[String(item.label_key || "")] ?? String(item.label_key || ""),
                    "granted": item.granted === true
                })
            }
            out.push({
                "risk": risk,
                "label": map[String(group.label_key || "")] ?? String(group.label_key || ""),
                "tip": map[String(tipKey)] ?? String(tipKey),
                "color": color,
                "items": items
            })
        }
        return out
    }

    readonly property int totalItems: {
        var total = 0
        var source = dialog.displayGroups || []
        for (var i = 0; i < source.length; i++)
            total += (source[i].items || []).length
        return total
    }

    Rectangle {
        objectName: "pluginPermissionScrim"
        anchors.fill: parent
        visible: dialog.visible
        color: Theme?.scrim ?? "transparent"
    }

    MouseArea {
        objectName: "pluginPermissionBlocker"
        anchors.fill: parent
        enabled: dialog.visible
        acceptedButtons: Qt.AllButtons
    }

    Rectangle {
        id: card
        objectName: "pluginPermissionCard"
        anchors.centerIn: parent
        visible: dialog.visible
        width: Math.min(560, Math.max(320, parent.width - 2 * (Theme?.spacingXl ?? 0)))
        //: 高度跟随条目数（权限少时不要留一大片空白）；上限 560，超出后列表自己滚。
        height: Math.min(560, Math.max(300, 250 + dialog.totalItems * 44 + dialog.displayGroups.length * 30))
        radius: Theme?.radiusLg ?? 0
        color: Theme?.cardBg ?? "transparent"
        border.width: 1
        border.color: Theme?.cardBorder ?? "transparent"

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: Theme?.spacingLg ?? 0
            spacing: Theme?.spacingMd ?? 0

            Text {
                objectName: "pluginPermissionTitle"
                Layout.fillWidth: true
                text: dialog.titleValue
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeLarge ?? 14
                font.bold: true
                elide: Text.ElideRight
            }

            Text {
                objectName: "pluginPermissionSubtitle"
                Layout.fillWidth: true
                text: dialog.labels.subtitle
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeBase ?? 12
                wrapMode: Text.WordWrap
            }

            FmScrollView {
                objectName: "pluginPermissionScroll"
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentWidth: availableWidth
                verticalBarAlwaysVisible: true

                ColumnLayout {
                    width: dialog.totalItems > 0 ? parent.width : 0
                    spacing: Theme?.spacingSm ?? 8

                    Text {
                        objectName: "pluginPermissionEmpty"
                        Layout.fillWidth: true
                        visible: dialog.totalItems === 0
                        text: dialog.labels.empty
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        wrapMode: Text.WordWrap
                    }

                    Repeater {
                        model: dialog.displayGroups

                        ColumnLayout {
                            id: group
                            property var groupData: modelData
                            Layout.fillWidth: true
                            spacing: Theme?.spacingXs ?? 4

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: Theme?.spacingSm ?? 8

                                Text {
                                    objectName: "pluginPermissionRisk_" + String(group.groupData.risk)
                                    Layout.fillWidth: true
                                    text: String(group.groupData.label)
                                    color: group.groupData.color
                                    font.pixelSize: Theme?.fontSizeBase ?? 12
                                    font.bold: true
                                }

                                Text {
                                    objectName: "pluginPermissionRiskTip_" + String(group.groupData.risk)
                                    Layout.fillWidth: true
                                    horizontalAlignment: Text.AlignRight
                                    text: String(group.groupData.tip)
                                    color: Theme?.textSecondary ?? "transparent"
                                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                                    wrapMode: Text.WordWrap
                                }
                            }

                            Repeater {
                                model: group.groupData.items

                                Rectangle {
                                    id: permRow
                                    property var perm: modelData
                                    Layout.fillWidth: true
                                    implicitHeight: permLayout.implicitHeight + 2 * (Theme?.spacingXs ?? 0)
                                    radius: Theme?.radiusSm ?? 0
                                    color: Theme?.bgMedium ?? "transparent"

                                    RowLayout {
                                        id: permLayout
                                        anchors.fill: parent
                                        anchors.leftMargin: Theme?.spacingSm ?? 0
                                        anchors.rightMargin: Theme?.spacingSm ?? 0
                                        spacing: Theme?.spacingSm ?? 8

                                        Rectangle {
                                            Layout.preferredWidth: 3
                                            Layout.fillHeight: true
                                            radius: 2
                                            color: group.groupData.color
                                        }

                                        Text {
                                            objectName: "pluginPermissionName_" + String(permRow.perm.id)
                                            Layout.fillWidth: true
                                            text: String(permRow.perm.name)
                                            color: Theme?.textPrimary ?? "transparent"
                                            font.pixelSize: Theme?.fontSizeBase ?? 12
                                            elide: Text.ElideRight
                                        }

                                        Text {
                                            objectName: "pluginPermissionId_" + String(permRow.perm.id)
                                            text: String(permRow.perm.id)
                                            color: Theme?.textSecondary ?? "transparent"
                                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                                        }

                                        FmTag {
                                            visible: permRow.perm.granted === true
                                            text: dialog.labels.granted
                                            level: "success"
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            Text {
                objectName: "pluginPermissionFooter"
                Layout.fillWidth: true
                text: dialog.labels.footer
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                wrapMode: Text.WordWrap
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme?.spacingSm ?? 8

                Item {
                    Layout.fillWidth: true
                }

                FmButton {
                    objectName: "pluginPermissionDeny"
                    primary: false
                    text: dialog.labels.deny
                    enabled: !dialog.busy
                    onClicked: dialog.denied()
                }

                FmButton {
                    objectName: "pluginPermissionApprove"
                    primary: true
                    text: dialog.labels.approve
                    enabled: !dialog.busy
                    onClicked: dialog.approved()
                }
            }
        }
    }
}
