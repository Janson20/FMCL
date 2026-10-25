// ModReviewDialog.qml —— 待人工确认模组的选择框（阶段 3.12）。
//
// 为什么不是 ConfirmDialog：确认框只能回答“是/否”，而这里的单位是**每个文件**。
// 用户需要在同一个弹窗里勾选要禁用的模组、然后一次性应用，不必先跳到模组管理。
//
// 语义：
//   * 默认**全不勾**（保留）—— 未知模组保持可用是最保守的默认；
//   * 勾选 = 禁用该模组（追加 .disabled，由服务层改名）；
//   * 「禁用」按钮只在至少勾选一项时可用；「模组管理」保留手动处理的出口；
//   * 不新增 i18n 键：标题/提示/按钮文案全部由调用方传已翻译字符串。
//
// 它是页内浮层：调用方必须用 Loader.active 控制存在性（与 ConfirmDialog /
// ResourceUpdateDialog 同一纪律，留着的不可见实例会继续吃键盘焦点）。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Item {
    id: dialog
    objectName: "modReviewDialog"

    //: [{file_name, reason}, …]
    property var rows: []
    //: file_name -> true（勾选 = 禁用）；缺省 false
    property var checked: ({})

    property string titleText: ""
    property string hintText: ""
    property string disableText: ""
    property string manageText: ""
    property string closeText: ""
    //: 勾选表的变更计数器：QML 对 var 对象的原地修改不会自动通知绑定，
    //: 按钮的 (N) 与可用态必须显式依赖它（与 ResourceUpdateDialog 的同类坑）。
    property int revision: 0

    signal disableRequested(var names)
    signal manageRequested()
    signal cancelled()

    readonly property var source: (rows === undefined || rows === null) ? [] : rows
    readonly property int selectedCount: {
        dialog.revision
        return dialog.checkedNames().length
    }

    function isChecked(name) {
        return dialog.checked[String(name)] === true
    }

    function setChecked(name, value) {
        var next = dialog.checked
        var key = String(name)
        if (value)
            next[key] = true
        else
            delete next[key]
        dialog.checked = next
        dialog.revision += 1
    }

    function checkedNames() {
        var names = []
        for (var i = 0; i < dialog.source.length; ++i) {
            var name = String(dialog.source[i].file_name || "")
            if (name.length > 0 && dialog.isChecked(name))
                names.push(name)
        }
        return names
    }

    function syncChecked() {
        var next = {}
        for (var i = 0; i < dialog.source.length; ++i) {
            var name = String(dialog.source[i].file_name || "")
            if (name.length > 0 && dialog.checked[name] === true)
                next[name] = true
        }
        dialog.checked = next
        dialog.revision += 1
    }

    Component.onCompleted: dialog.syncChecked()
    onRowsChanged: dialog.syncChecked()

    focus: true
    Keys.onEscapePressed: dialog.cancelled()

    Rectangle {
        objectName: "modReviewScrim"
        anchors.fill: parent
        color: Theme?.scrim ?? "transparent"
    }

    Rectangle {
        id: card
        objectName: "modReviewCard"
        anchors.centerIn: parent
        width: Math.min(620, Math.max(420, parent.width - 2 * (Theme?.spacingXl ?? 24)))
        height: Math.min(480, Math.max(340, parent.height - 2 * (Theme?.spacingXl ?? 24)))
        radius: Theme?.radiusLg ?? 8
        color: Theme?.cardBg ?? "transparent"
        border.width: 1
        border.color: Theme?.cardBorder ?? "transparent"

        //: 挡住穿透点击
        MouseArea { anchors.fill: parent }

        ColumnLayout {
            objectName: "modReviewLayout"
            anchors.fill: parent
            anchors.margins: Theme?.spacingLg ?? 16
            spacing: Theme?.spacingSm ?? 8

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme?.spacingSm ?? 8

                Text {
                    objectName: "modReviewTitle"
                    Layout.fillWidth: true
                    text: dialog.titleText
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeTitle ?? 18
                    font.bold: true
                    elide: Text.ElideRight
                }

                FmToolButton {
                    objectName: "modReviewClose"
                    iconName: "close"
                    onClicked: dialog.cancelled()
                }
            }

            Text {
                objectName: "modReviewHint"
                Layout.fillWidth: true
                text: dialog.hintText
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                wrapMode: Text.WordWrap
            }

            ListView {
                id: reviewList
                objectName: "modReviewList"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: Theme?.spacingXs ?? 4
                model: dialog.source
                ScrollBar.vertical: FmScrollBar {}

                delegate: Rectangle {
                    id: rowItem
                    width: reviewList.width
                    height: rowLayout.implicitHeight + (Theme?.spacingSm ?? 8)
                    radius: Theme?.radiusSm ?? 6
                    color: Theme?.bgMedium ?? "transparent"

                    readonly property var row: (modelData === undefined) ? ({}) : modelData
                    readonly property string fileName: String(row.file_name || "")

                    ColumnLayout {
                        id: rowLayout
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.margins: Theme?.spacingSm ?? 8
                        spacing: 1

                        FmCheckBox {
                            objectName: "modReviewCheck-" + rowItem.fileName
                            Layout.fillWidth: true
                            text: rowItem.fileName
                            checked: dialog.isChecked(rowItem.fileName)
                            onToggled: dialog.setChecked(rowItem.fileName, checked)
                        }

                        Text {
                            objectName: "modReviewReason-" + rowItem.fileName
                            Layout.fillWidth: true
                            Layout.leftMargin: (Theme?.iconSize ?? 20) + (Theme?.spacingSm ?? 8)
                            visible: String(rowItem.row.reason || "").length > 0
                            text: String(rowItem.row.reason || "")
                            color: Theme?.textTertiary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                            elide: Text.ElideRight
                        }
                    }
                }
            }

            RowLayout {
                objectName: "modReviewActions"
                Layout.fillWidth: true
                spacing: Theme?.spacingSm ?? 8

                FmButton {
                    objectName: "modReviewDisable"
                    primary: true
                    text: dialog.selectedCount > 0
                          ? dialog.disableText + " (" + dialog.selectedCount + ")"
                          : dialog.disableText
                    enabled: dialog.selectedCount > 0
                    onClicked: dialog.disableRequested(dialog.checkedNames())
                }

                FmButton {
                    objectName: "modReviewManage"
                    visible: dialog.manageText.length > 0
                    primary: false
                    text: dialog.manageText
                    onClicked: dialog.manageRequested()
                }

                Item { Layout.fillWidth: true }

                FmButton {
                    objectName: "modReviewCancel"
                    primary: false
                    text: dialog.closeText.length > 0 ? dialog.closeText : (Tr?.map["close"] ?? "close")
                    onClicked: dialog.cancelled()
                }
            }
        }
    }
}
