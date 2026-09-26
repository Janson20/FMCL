// FmTextField.qml —— 单行输入框（阶段 2 任务 2.16）。
//
// 什么时候用它：表单里的单行文本输入（版本名、玩家名、路径、关键词之类的**非搜索**输入）。
// 什么时候不要用：搜索框（用 FmSearchField，它带放大镜与一键清空）；多行文本
//   （用 FmTextArea）；只读信息展示（用 FmListItem 或 Text，输入框会让人以为能编辑）。
//
// 四个状态：正常 / 聚焦（强调色描边）/ 出错（`errorText` 非空 → 错误色描边 + 提示行）/
//   禁用（`enabled: false`）。**校验规则不在这里**：`errorText` 由页面按业务给
//   （红线 2：业务只存在于 services/，组件只负责把错误**显示出来**）。
//
// `text` 是到内部 TextField 的别名，所以 `field.text` 读/写都行；输入过程中的变化走
// `edited(value)`，回车走 `accepted()`。文案由调用方给（`Tr.map[…]`），组件不判语义。

import QtQuick
import QtQuick.Controls

Item {
    id: field
    objectName: "fmTextField"

    property alias text: input.text
    property string label: ""
    property string placeholder: ""
    //: 非空即进入出错态（页面把服务的校验结果写进来）
    property string errorText: ""
    property bool password: false
    property alias readOnly: input.readOnly
    property alias echoMode: input.echoMode

    readonly property bool hasError: errorText.length > 0

    signal accepted()
    signal edited(string value)

    implicitWidth: 240
    implicitHeight: layout.implicitHeight

    Column {
        id: layout
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        spacing: Theme?.spacingXs ?? 0

        Text {
            objectName: "fmTextFieldLabel"
            width: parent.width
            visible: field.label.length > 0
            text: field.label
            color: Theme?.textSecondary ?? "transparent"
            font.pixelSize: Theme?.fontSizeSmall ?? 10
            elide: Text.ElideRight
        }

        TextField {
            id: input
            objectName: "fmTextFieldInput"
            width: parent.width
            enabled: field.enabled
            placeholderText: field.placeholder
            echoMode: field.password ? TextInput.Password : TextInput.Normal
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
                border.color: field.hasError
                              ? Theme?.error ?? "transparent"
                              : (input.activeFocus ? Theme?.accent ?? "transparent" : Theme?.cardBorder ?? "transparent")
            }

            onAccepted: field.accepted()
            onTextEdited: field.edited(text)
        }

        Text {
            objectName: "fmTextFieldError"
            width: parent.width
            visible: field.hasError
            text: field.errorText
            color: Theme?.error ?? "transparent"
            font.pixelSize: Theme?.fontSizeSmall ?? 10
            wrapMode: Text.WordWrap
        }
    }
}
