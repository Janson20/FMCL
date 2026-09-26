// DialogButton.qml —— 对话框里的按钮（阶段 2 任务 2.13 的临时件）。
//
// 为什么不直接用 FluentUI 的 FluButton：对话框组件要能在**没有 FluentUI 导入路径**的
// 进程里单独加载（`tests/test_dialogs_qml.py` 的负例会把这一小片目录复制到 tmp 再加载，
// 那里没有第三方模块）。2.16 的组件库落地后换掉这一处即可 —— 调用点只有本目录内的几处。
//
// 颜色只来自 Theme.*（闸门 R8），文案由调用方给（闸门 R3）。

import QtQuick
import QtQuick.Controls

Button {
    id: control

    //: 主按钮（强调色）还是次按钮（中性底色）
    property bool primary: false

    implicitWidth: Math.max(88, labelText.implicitWidth + 2 * Theme?.spacingLg ?? 0)
    implicitHeight: 34
    hoverEnabled: true

    background: Rectangle {
        radius: Theme?.radiusMd ?? 0
        border.width: 1
        border.color: control.primary ? Theme?.accent ?? "transparent" : Theme?.cardBorder ?? "transparent"
        color: !control.enabled
               ? Theme?.bgMedium ?? "transparent"
               : (control.primary
                  ? (control.hovered ? Theme?.accentHover ?? "transparent" : Theme?.accent ?? "transparent")
                  : (control.hovered ? Theme?.cardBorder ?? "transparent" : Theme?.bgMedium ?? "transparent"))
    }

    contentItem: Text {
        id: labelText
        text: control.text
        color: control.enabled ? Theme?.textPrimary ?? "transparent" : Theme?.textSecondary ?? "transparent"
        font.pixelSize: Theme?.fontSizeBase ?? 12
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
}
