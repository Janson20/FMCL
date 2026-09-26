// FmRadio.qml —— 单选（阶段 2 任务 2.16）。
//
// 什么时候用它：**一组互斥选项**且数量少（2~5 个）、选项文案短 —— 下载线程数、
//   主题模式、启动方式。QtQuick.Controls 的互斥语义：**同一个父项**下的多个 FmRadio
//   自动互斥（`autoExclusive` 默认 true），所以别把它们拆到不同的父项里；
//   需要跨父项互斥就自己写 `checked` + `autoExclusive: false`（设置项回显那种场景）。
// 什么时候不要用：需要点"保存"才生效的开关型设置（用 FmSwitch）；选项超过 5 个
//   或文案很长（用 FmComboBox，横排单选会挤成一团）；多选（同族的复选框不在本任务范围，
//   阶段 3 若需要再回 2.8 补件）。

import QtQuick
import QtQuick.Controls

RadioButton {
    id: control
    objectName: "fmRadio"

    // `indicator` / `contentItem` 是 Controls 样式后填的属性，建出来的那一瞬间可能是 null
    // （读 `.height` 会刷 TypeError —— 与 FmSlider 同一个坑，见那边的注释）。
    implicitHeight: Math.max(indicator ? indicator.height : 0,
                             (contentItem ? contentItem.implicitHeight : 0) + 2 * (Theme?.spacingXs ?? 0))
    implicitWidth: (indicator ? indicator.width : 0) + (Theme?.spacingSm ?? 0)
                   + (contentItem ? contentItem.implicitWidth : 0)

    indicator: Rectangle {
        objectName: "fmRadioRing"
        implicitWidth: 18
        implicitHeight: 18
        x: control.leftPadding
        y: control.topPadding + (control.availableHeight - height) / 2
        radius: width / 2
        color: Theme?.bgMedium ?? "transparent"
        border.width: control.checked ? 2 : 1
        border.color: !control.enabled
                      ? Theme?.cardBorder ?? "transparent"
                      : (control.checked ? Theme?.accent ?? "transparent" : Theme?.cardBorder ?? "transparent")

        Rectangle {
            objectName: "fmRadioDot"
            anchors.centerIn: parent
            visible: control.checked
            width: parent.width - 8
            height: width
            radius: width / 2
            color: control.enabled ? Theme?.accent ?? "transparent" : Theme?.textSecondary ?? "transparent"
        }
    }

    contentItem: Text {
        objectName: "fmRadioLabel"
        text: control.text
        color: control.enabled ? Theme?.textPrimary ?? "transparent" : Theme?.textSecondary ?? "transparent"
        font.pixelSize: Theme?.fontSizeBase ?? 12
        verticalAlignment: Text.AlignVCenter
        leftPadding: control.indicator.width + (Theme?.spacingSm ?? 0)
        elide: Text.ElideRight
    }
}
