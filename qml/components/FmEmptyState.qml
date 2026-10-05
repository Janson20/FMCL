// FmEmptyState.qml —— 空数据态（阶段 2 任务 2.16；`03` 的 SOP 第 5 条要求每页覆盖三态）。
//
// 什么时候用它：数据**成功取到了，但一条都没有** —— 没装版本、没有存档、搜索无结果、
//   插件列表为空。必须与"出错"分开：空不是错误，别让用户以为坏了。
// 什么时候不要用：还在加载（用 FmLoadingState）；出错（用 FmErrorState）；
//   一个字段为空（那用 `visible: text.length > 0`，不要摆一整个空状态块）。
//
// `actionText` 非空时右下出现一个按钮并 `actionTriggered()`（"去安装一个"、
//   "清空筛选条件"、"打开目录"）。文案与按钮文案都由调用方给（`Tr.map[…]`）。
//
// `actionPrimary`（阶段 3 任务 3.1 新增）：把那个动作按钮变成**主按钮**（强调色）。
//   默认是次按钮 —— 空态里的动作多数是"换条路走"，不该抢眼。但首页那种
//   "首启什么都没有、唯一的下一步就是去装一个版本"的场景，动作就是页面的**主操作**，
//   次按钮会让整页看不到一处强调色（视觉回归 `test_visual_regression.py` 正是按
//   "每页至少有一处强调色"判的）。调用方按语义选，不按好看选。

import QtQuick

Item {
    id: block
    objectName: "fmEmptyState"

    property string title: ""
    property string description: ""
    property string iconName: "folder-open"
    property string actionText: ""
    //: 动作按钮是否用主按钮样式（默认次按钮，见文件头）
    property bool actionPrimary: false

    signal actionTriggered()

    implicitWidth: 260
    implicitHeight: column.implicitHeight

    Column {
        id: column
        anchors.centerIn: parent
        width: Math.min(parent.width, 360)
        spacing: Theme?.spacingSm ?? 0

        FmIcon {
            objectName: "fmEmptyStateIcon"
            anchors.horizontalCenter: parent.horizontalCenter
            name: block.iconName
            color: Theme?.textSecondary ?? "transparent"
            size: Math.round((Theme?.iconSize ?? 0) * 1.6)
        }

        Text {
            objectName: "fmEmptyStateTitle"
            width: parent.width
            visible: block.title.length > 0
            text: block.title
            color: Theme?.textPrimary ?? "transparent"
            font.pixelSize: Theme?.fontSizeLarge ?? 14
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
        }

        Text {
            objectName: "fmEmptyStateDescription"
            width: parent.width
            visible: block.description.length > 0
            text: block.description
            color: Theme?.textSecondary ?? "transparent"
            font.pixelSize: Theme?.fontSizeSmall ?? 10
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
        }

        FmButton {
            objectName: "fmEmptyStateAction"
            anchors.horizontalCenter: parent.horizontalCenter
            visible: block.actionText.length > 0
            primary: block.actionPrimary
            text: block.actionText
            onClicked: block.actionTriggered()
        }
    }
}
