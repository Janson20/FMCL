// FmSearchField.qml —— 搜索框（阶段 2 任务 2.16）。
//
// 什么时候用它：页面上"输入关键词 → 过滤/搜索"的那一个输入框（模组浏览、版本列表、
//   音乐搜索、设置里的搜索）。
// 什么时候不要用：普通表单输入（用 FmTextField —— 搜索框带放大镜与一键清空，
//   用在表单上会让人以为输入会立即过滤）。
//
// 与 FmTextField 的区别只有三处：左边固定一个 search 图标、右边有清空按钮（有内容时才出现）、
//   回车走 TextField **自带的** `accepted()` 信号（本件**不**再声明一个同名信号：
//   QtQuick.Controls 的 TextField 本来就有 `accepted`，重名会让两个同名信号互相遮蔽 ——
//   实测踩到，Gallery 的 `onAccepted:` 收到了空文本）。
// 防抖不在组件里：要不要防抖、防多久是页面对桥的调用策略（红线 2）。

import QtQuick
import QtQuick.Controls

TextField {
    id: control
    objectName: "fmSearchField"

    //: 清空按钮被点击
    signal cleared()

    placeholderText: ""
    selectByMouse: true
    color: Theme?.textPrimary ?? "transparent"
    placeholderTextColor: Theme?.textSecondary ?? "transparent"
    font.pixelSize: Theme?.fontSizeBase ?? 12
    implicitWidth: 240
    implicitHeight: 32
    leftPadding: (Theme?.iconSize ?? 0) + 2 * (Theme?.spacingSm ?? 0)
    rightPadding: (Theme?.iconSize ?? 0) + Theme?.spacingSm ?? 0

    background: Rectangle {
        radius: Theme?.radiusMd ?? 0
        color: Theme?.bgMedium ?? "transparent"
        border.width: 1
        border.color: control.activeFocus ? Theme?.accent ?? "transparent" : Theme?.cardBorder ?? "transparent"
    }

    // 回车直接走 TextField 自带的 accepted 信号（不需要转发，也不该转发：
    // `onAccepted: control.accepted()` 会自己调自己，实测就是无限递归）

    FmIcon {
        objectName: "fmSearchFieldIcon"
        name: "search"
        color: Theme?.textSecondary ?? "transparent"
        size: Math.round((Theme?.fontSizeBase ?? 12) * 1.2)
        anchors.left: parent.left
        anchors.leftMargin: Theme?.spacingSm ?? 0
        anchors.verticalCenter: parent.verticalCenter
    }

    Item {
        objectName: "fmSearchFieldClear"
        visible: control.text.length > 0
        width: Theme?.iconSize ?? 0
        height: Theme?.iconSize ?? 0
        anchors.right: parent.right
        anchors.rightMargin: Theme?.spacingXs ?? 0
        anchors.verticalCenter: parent.verticalCenter

        FmIcon {
            anchors.fill: parent
            name: "close"
            color: clearArea.containsMouse ? Theme?.textPrimary ?? "transparent" : Theme?.textSecondary ?? "transparent"
            size: Math.round((Theme?.fontSizeBase ?? 12) * 1.2)
        }

        MouseArea {
            id: clearArea
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            // 只清空、不自动搜索：搜不搜由调用方决定（有的页面要立刻过滤，有的要按回车）
            onClicked: {
                control.text = ""
                control.cleared()
            }
        }
    }
}
