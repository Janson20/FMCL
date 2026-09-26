// FmSwitch.qml —— 开关（阶段 2 任务 2.16）。
//
// 什么时候用它：**立即生效**的二值设置（"启动后最小化"、"自动检查更新"、"显示悬浮窗"）——
//   拨一下就生效，不需要"保存"按钮。
// 什么时候不要用：需要点"保存"才生效的表单项（用 FmRadio / 下拉更合适，开关会让人以为
//   已经生效了）；三态以上（用 FmComboBox）；一组互斥选项（用 FmRadio）。
//
// 状态：开 / 关 / 禁用（禁用时不变灰到手看不见 —— 文字用 textSecondary、底用 bgMedium，
//   仍然能读出"当前是开还是关"）。文案由调用方给（`text:` 写 `Tr.map[…]`）。
// 只暴露 QtQuick.Controls 的 `checked` / `toggled` / `text`，不自造一套属性名。

import QtQuick
import QtQuick.Controls

Switch {
    id: control
    objectName: "fmSwitch"

    // `indicator` / `contentItem` 是 Controls 样式后填的属性，建出来的那一瞬间可能是 null
    // （读 `.height` 会刷 TypeError —— 与 FmSlider 同一个坑，见那边的注释）。
    implicitHeight: Math.max(indicator ? indicator.height : 0,
                             (contentItem ? contentItem.implicitHeight : 0) + 2 * (Theme?.spacingXs ?? 0))
    implicitWidth: (indicator ? indicator.width : 0) + (Theme?.spacingSm ?? 0)
                   + (contentItem ? contentItem.implicitWidth : 0)

    indicator: Rectangle {
        id: track
        objectName: "fmSwitchTrack"
        implicitWidth: 40
        implicitHeight: 20
        x: control.leftPadding
        y: control.topPadding + (control.availableHeight - height) / 2
        radius: height / 2
        color: !control.enabled
               ? Theme?.bgMedium ?? "transparent"
               : (control.checked ? Theme?.accent ?? "transparent" : Theme?.cardBg ?? "transparent")
        border.width: 1
        border.color: control.checked && control.enabled
                      ? Theme?.accent ?? "transparent"
                      : Theme?.cardBorder ?? "transparent"

        Rectangle {
            id: knob
            objectName: "fmSwitchKnob"
            width: track.height - 6
            height: width
            radius: width / 2
            y: 3
            x: control.checked ? track.width - width - 3 : 3
            color: control.enabled ? Theme?.textPrimary ?? "transparent" : Theme?.textSecondary ?? "transparent"

            Behavior on x {
                NumberAnimation { duration: 90; easing.type: Easing.OutCubic }
            }
        }

        HoverHandler {
            id: hover
        }

        // 悬停时给一点反馈（只在开/关两态之外加描边，不改底色语义）
        Rectangle {
            anchors.fill: parent
            radius: parent.radius
            color: "transparent"
            border.width: hover.hovered && control.enabled ? 1 : 0
            border.color: Theme?.accentHover ?? "transparent"
        }
    }

    contentItem: Text {
        objectName: "fmSwitchLabel"
        text: control.text
        color: control.enabled ? Theme?.textPrimary ?? "transparent" : Theme?.textSecondary ?? "transparent"
        font.pixelSize: Theme?.fontSizeBase ?? 12
        verticalAlignment: Text.AlignVCenter
        leftPadding: control.indicator.width + (Theme?.spacingSm ?? 0)
        elide: Text.ElideRight
    }
}
