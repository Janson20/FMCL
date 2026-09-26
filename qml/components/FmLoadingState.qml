// FmLoadingState.qml —— 加载态（阶段 2 任务 2.16；`03` 的 SOP 第 5 条要求每页覆盖三态）。
//
// 什么时候用它：页面的**整块内容区**正在等数据（列表还在拉、详情还没到）。
//   与"按钮自己的 loading"是两件事：那是 FmButton 的 `loading`，这是区域级占位。
// 什么时候不要用：进度**可量化**的等待（用 FmProgressBar，让用户知道还差多少）；
//   局部的一小块（用 FmProgressRing）；空数据（用 FmEmptyState）；出错（用 FmErrorState）。
//
// 文案由调用方给：`title` 写 `Tr.map[…]`（例如 plugin_state_loading 那个键），组件不带默认文案 ——
//   默认文案会变成第二份 i18n（红线 3/4 的精神）。

import QtQuick

Item {
    id: block
    objectName: "fmLoadingState"

    property string title: ""
    property string description: ""
    //: 图标名（默认 loading；换成领域图标也可以）
    property string iconName: "loading"
    //: 图标是否旋转（默认转，"正在发生"这个语义靠它）
    property bool spinning: true

    implicitWidth: 260
    implicitHeight: column.implicitHeight

    Column {
        id: column
        anchors.centerIn: parent
        width: Math.min(parent.width, 360)
        spacing: Theme?.spacingSm ?? 0

        FmIcon {
            id: icon
            objectName: "fmLoadingStateIcon"
            anchors.horizontalCenter: parent.horizontalCenter
            name: block.iconName
            color: Theme?.accent ?? "transparent"
            size: Math.round((Theme?.iconSize ?? 0) * 1.6)
            transformOrigin: Item.Center
        }

        RotationAnimator {
            target: icon
            running: block.spinning && block.visible
            from: 0
            to: 360
            duration: 1200
            loops: Animation.Infinite
        }

        Text {
            objectName: "fmLoadingStateTitle"
            width: parent.width
            visible: block.title.length > 0
            text: block.title
            color: Theme?.textPrimary ?? "transparent"
            font.pixelSize: Theme?.fontSizeLarge ?? 14
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
        }

        Text {
            objectName: "fmLoadingStateDescription"
            width: parent.width
            visible: block.description.length > 0
            text: block.description
            color: Theme?.textSecondary ?? "transparent"
            font.pixelSize: Theme?.fontSizeSmall ?? 10
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
        }
    }
}
