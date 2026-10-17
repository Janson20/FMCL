// ResourceUpdateDialog.qml —— 模组更新选择对话框（阶段 3 任务 3.6，对照表 L-55）。
//
// ## 与旧实现的关系
//
// 旧界面在"检查更新发现有可更新项"之后弹一个 580x480 的 `CTkToplevel`
// （`resource_manager.py:1406-1610`）：一行一个模组（复选框 + 名字 + `v旧 → v新`）、
// 每页 10 条的分页、底部三颗按钮「取消 / 全部更新 / 更新选中」。
//
// 本件的**语义逐条照抄**，两处结构差异：
//
// 1. **不再分页**：旧实现分页是 Tk 一次性建控件的性能妥协；QML 的 `ListView` 会虚拟化，
//    直接把全部候选铺进一个列表（本项目在 `LogView` 里已经用同一手法）。
//    分页控件因此消失，其余（勾选、全选默认、两颗更新按钮的语义）一字不变。
// 2. **`v旧 → v新` 用 ASCII 箭头 `->`**：`mod_update_selected` 那个键的值里带着
//    `⬆`，本轮不新增 emoji（用户裁决）；箭头用 ASCII 不受闸门 R2 影响。
//
// ## 契约
//
// * 本件**不碰服务**：勾选状态留在自己这儿，点按钮只发 `selected(list<modid>)`
//   或 `allRequested()`；"更新哪些、怎么更新"在桥与服务层（红线 2）。
// * 文案键由调用方给（与 3.5 的三个页内浮层同一规矩）：本件只做显示与勾选。
// * 它是**页内浮层**，所以调用方必须用 `Loader.active` 控制存在性（不是 `visible`）——
//   留着的不可见实例会继续吃键盘焦点。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Item {
    id: dialog
    objectName: "resourceUpdateDialog"

    //: `[{modid, mod_name, current_version, latest_version}, …]`
    property var rows: []
    //: 勾选状态：`modid -> bool`（缺省视为选中，与旧实现 `BooleanVar(value=True)` 一致）
    property var checked: ({})

    //: 文案（由调用方传已翻译好的字符串）
    property string titleText: ""
    property string headerText: ""
    property string hintText: ""
    property string cancelText: ""
    property string allText: ""
    property string selectedText: ""

    //: 取消（Esc / 右上角关闭 / 「取消」按钮）
    signal cancelled()
    //: 更新选中的那批（参数 = modid 列表）
    signal selected(var modids)
    //: 全部更新
    signal allRequested()

    readonly property var source: (rows === undefined || rows === null) ? [] : rows
    readonly property int selectedCount: dialog.checkedIds().length

    //: 勾选状态与列表对齐（默认全选；缺失的键补 true，多出来的键丢掉）
    function syncChecked() {
        var next = {};
        for (var i = 0; i < dialog.source.length; ++i) {
            var id = dialog.idOf(dialog.source[i]);
            next[id] = (dialog.checked[id] === undefined) ? true : !!dialog.checked[id];
        }
        dialog.checked = next;
    }

    function idOf(entry) {
        if (entry === undefined || entry === null)
            return "";
        return String(entry.modid === undefined ? "" : entry.modid);
    }

    function isChecked(id) {
        return dialog.checked[String(id)] !== false;
    }

    function setChecked(id, value) {
        var next = dialog.checked;
        next[String(id)] = !!value;
        dialog.checked = next;
    }

    function checkedIds() {
        var ids = [];
        for (var i = 0; i < dialog.source.length; ++i) {
            var id = dialog.idOf(dialog.source[i]);
            if (dialog.isChecked(id))
                ids.push(id);
        }
        return ids;
    }

    function allIds() {
        var ids = [];
        for (var i = 0; i < dialog.source.length; ++i)
            ids.push(dialog.idOf(dialog.source[i]));
        return ids;
    }

    focus: true
    Keys.onEscapePressed: dialog.cancelled()

    Component.onCompleted: dialog.syncChecked()

    Rectangle {
        objectName: "resourceUpdateScrim"
        anchors.fill: parent
        color: Theme?.bgDark ?? "transparent"
        opacity: 0.55
    }

    Rectangle {
        id: card
        objectName: "resourceUpdateCard"
        anchors.centerIn: parent
        width: Math.min(560, Math.max(360, parent.width - 2 * (Theme?.spacingXl ?? 24)))
        height: Math.min(520, Math.max(320, parent.height - 2 * (Theme?.spacingXl ?? 24)))
        radius: Theme?.radiusLg ?? 8
        color: Theme?.cardBg ?? "transparent"
        border.width: 1
        border.color: Theme?.cardBorder ?? "transparent"

        //: 挡住穿透点击（浮层不关，但点到底下的列表会让人莫名其妙）
        MouseArea { anchors.fill: parent }

        ColumnLayout {
            id: layout
            anchors.fill: parent
            anchors.margins: Theme?.spacingLg ?? 16
            spacing: Theme?.spacingSm ?? 8

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme?.spacingSm ?? 8

                Text {
                    objectName: "resourceUpdateTitle"
                    Layout.fillWidth: true
                    text: dialog.titleText
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeTitle ?? 18
                    font.bold: true
                    elide: Text.ElideRight
                }

                FmToolButton {
                    objectName: "resourceUpdateClose"
                    iconName: "close"
                    onClicked: dialog.cancelled()
                }
            }

            Text {
                objectName: "resourceUpdateHeader"
                Layout.fillWidth: true
                text: dialog.headerText
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeLarge ?? 14
                font.bold: true
                elide: Text.ElideRight
            }

            Text {
                objectName: "resourceUpdateHint"
                Layout.fillWidth: true
                text: dialog.hintText
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                wrapMode: Text.WordWrap
            }

            ListView {
                id: updateList
                objectName: "resourceUpdateListView"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: Theme?.spacingXs ?? 4
                model: dialog.source
                ScrollBar.vertical: FmScrollBar {}

                delegate: Rectangle {
                    id: row
                    objectName: "resourceUpdateRow"
                    width: updateList.width
                    height: 36
                    radius: Theme?.radiusSm ?? 6
                    color: Theme?.bgMedium ?? "transparent"

                    readonly property string modId: dialog.idOf(modelData)

                    RowLayout {
                        objectName: "resourceUpdateRowLayout"
                        anchors.fill: parent
                        anchors.leftMargin: Theme?.spacingSm ?? 8
                        anchors.rightMargin: Theme?.spacingSm ?? 8
                        spacing: Theme?.spacingSm ?? 8

                        FmCheckBox {
                            objectName: "resourceUpdateCheck-" + row.modId
                            checked: dialog.isChecked(row.modId)
                            onToggled: dialog.setChecked(row.modId, checked)
                        }

                        Text {
                            objectName: "resourceUpdateName-" + row.modId
                            Layout.fillWidth: true
                            text: String(modelData.mod_name || row.modId)
                            color: Theme?.textPrimary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeBase ?? 12
                            elide: Text.ElideRight
                        }

                        Text {
                            objectName: "resourceUpdateVersion-" + row.modId
                            text: "v" + String(modelData.current_version || "?")
                                  + " -> v" + String(modelData.latest_version || "?")
                            color: Theme?.success ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                        }
                    }
                }
            }

            RowLayout {
                objectName: "resourceUpdateActions"
                Layout.fillWidth: true
                spacing: Theme?.spacingSm ?? 8

                Item { Layout.fillWidth: true }

                FmButton {
                    objectName: "resourceUpdateCancelButton"
                    primary: false
                    text: dialog.cancelText
                    onClicked: dialog.cancelled()
                }

                FmButton {
                    objectName: "resourceUpdateAllButton"
                    primary: false
                    text: dialog.allText
                    onClicked: dialog.allRequested()
                }

                FmButton {
                    objectName: "resourceUpdateSelectedButton"
                    primary: true
                    text: dialog.selectedText
                    enabled: dialog.selectedCount > 0
                    onClicked: dialog.selected(dialog.checkedIds())
                }
            }
        }
    }
}
