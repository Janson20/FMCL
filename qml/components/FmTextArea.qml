// FmTextArea.qml —— 多行输入框（阶段 2 任务 2.16）。
//
// 什么时候用它：需要换行的文本输入 —— 公告内容、备注、启动参数（一行一个）、
//   自定义 JSON、AI 提示词。
// 什么时候不要用：单行输入（用 FmTextField）；只读的多行文本展示（用 Text +
//   FmCard；输入框会让人以为能编辑）；日志查看（那是 2.18 的日志组件）。
//
// 状态与 FmTextField 一致：正常 / 聚焦 / 出错（`errorText` 非空）/ 禁用。
// `text` 是内部 TextArea 的别名。**没有**字数上限与字数统计：那是页面按业务决定的，
// 需要时读 `text.length` 自己显示（组件不猜业务）。

import QtQuick
import QtQuick.Controls

Item {
    id: area
    objectName: "fmTextArea"

    property alias text: input.text
    property string label: ""
    property string placeholder: ""
    property string errorText: ""
    property alias readOnly: input.readOnly
    //: 可视行数（控件的初始高度按它算，用户可以自己给 height）
    property int lines: 4

    readonly property bool hasError: errorText.length > 0

    signal edited(string value)

    implicitWidth: 320
    implicitHeight: layout.implicitHeight

    Column {
        id: layout
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        spacing: Theme?.spacingXs ?? 0

        Text {
            objectName: "fmTextAreaLabel"
            width: parent.width
            visible: area.label.length > 0
            text: area.label
            color: Theme?.textSecondary ?? "transparent"
            font.pixelSize: Theme?.fontSizeSmall ?? 10
            elide: Text.ElideRight
        }

        TextArea {
            id: input
            objectName: "fmTextAreaInput"
            width: parent.width
            height: Math.max(
                        (Theme?.fontSizeBase ?? 12) * 1.4 * Math.max(1, area.lines) + 2 * (Theme?.spacingSm ?? 0),
                        implicitHeight)
            enabled: area.enabled
            placeholderText: area.placeholder
            wrapMode: TextArea.Wrap
            selectByMouse: true
            color: Theme?.textPrimary ?? "transparent"
            placeholderTextColor: Theme?.textSecondary ?? "transparent"
            font.pixelSize: Theme?.fontSizeBase ?? 12
            leftPadding: Theme?.spacingSm ?? 0
            rightPadding: Theme?.spacingSm ?? 0

            background: Rectangle {
                radius: Theme?.radiusMd ?? 0
                color: Theme?.bgMedium ?? "transparent"
                border.width: 1
                border.color: area.hasError
                              ? Theme?.error ?? "transparent"
                              : (input.activeFocus ? Theme?.accent ?? "transparent" : Theme?.cardBorder ?? "transparent")
            }

            onTextChanged: area.edited(text)
        }

        Text {
            objectName: "fmTextAreaError"
            width: parent.width
            visible: area.hasError
            text: area.errorText
            color: Theme?.error ?? "transparent"
            font.pixelSize: Theme?.fontSizeSmall ?? 10
            wrapMode: Text.WordWrap
        }
    }
}
