// FmInfoBar.qml —— 信息条（阶段 2 任务 2.16）。
//
// 什么时候用它：一段**页面级**的说明/结果提示 —— "未检测到 Java"、"下载完成"、
//   "该版本与当前加载器不兼容"、"协议已更新"。带一个可选的动作按钮与关闭按钮。
// 什么时候不要用：Toast（右下角、会自动消失的短通知 —— 那是 2.13 的 ToastHost，
//   由 `Dialogs.notify()` 驱动）；表单字段级的校验提示（用 FmTextField 的 `errorText`）；
//   整页的加载/空/错误（用 FmLoadingState / FmEmptyState / FmErrorState）。
//
// 四档语义（`level`）：info / success / warning / error —— 图标与文字颜色跟着走
//   （图标一律来自 qml/assets/icons，禁止 emoji）。`text` 由调用方给（`Tr.map[…]`），
//   需要"详情"就在 text 里换行写，不要在这里堆子组件（那会变成卡片）。

import QtQuick
import QtQuick.Layouts

Rectangle {
    id: bar
    objectName: "fmInfoBar"

    property string text: ""
    property string level: "info"
    //: 可选动作按钮文案（空串 = 不显示按钮）
    property string actionText: ""
    property bool closable: false

    signal actionTriggered()
    signal closed()

    implicitWidth: 360
    implicitHeight: layout.implicitHeight + 2 * (Theme?.spacingSm ?? 0)
    radius: Theme?.radiusMd ?? 0
    color: Theme?.bgLight ?? "transparent"
    border.width: 1
    border.color: bar.levelColor()

    function levelColor() {
        if (level === "success")
            return Theme?.success ?? "transparent"
        if (level === "warning")
            return Theme?.warning ?? "transparent"
        if (level === "error")
            return Theme?.error ?? "transparent"
        return Theme?.accent ?? "transparent"
    }

    function levelIcon() {
        if (level === "success")
            return "success"
        if (level === "warning")
            return "warning"
        if (level === "error")
            return "error"
        return "info"
    }

    RowLayout {
        id: layout
        anchors.fill: parent
        anchors.margins: Theme?.spacingSm ?? 0
        spacing: Theme?.spacingSm ?? 0

        FmIcon {
            objectName: "fmInfoBarIcon"
            name: bar.levelIcon()
            color: bar.levelColor()
            size: Math.round((Theme?.fontSizeBase ?? 12) * 1.4)
            Layout.alignment: Qt.AlignTop
        }

        Text {
            objectName: "fmInfoBarText"
            Layout.fillWidth: true
            text: bar.text
            color: Theme?.textPrimary ?? "transparent"
            font.pixelSize: Theme?.fontSizeBase ?? 12
            wrapMode: Text.WordWrap
        }

        FmButton {
            objectName: "fmInfoBarAction"
            visible: bar.actionText.length > 0
            primary: false
            text: bar.actionText
            onClicked: bar.actionTriggered()
        }

        Item {
            objectName: "fmInfoBarClose"
            visible: bar.closable
            Layout.preferredWidth: Theme?.iconSize ?? 0
            Layout.preferredHeight: Theme?.iconSize ?? 0

            FmIcon {
                anchors.fill: parent
                name: "close"
                color: closeArea.containsMouse ? Theme?.textPrimary ?? "transparent" : Theme?.textSecondary ?? "transparent"
                size: Math.round((Theme?.fontSizeBase ?? 12) * 1.2)
            }

            MouseArea {
                id: closeArea
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: bar.closed()
            }
        }
    }
}
