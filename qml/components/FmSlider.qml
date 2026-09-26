// FmSlider.qml —— 滑块（阶段 2 任务 2.16）。
//
// 什么时候用它：在**一个区间**里连续取值，而且"能立刻看到当前值"有意义 ——
//   音量、界面缩放、下载线程数、歌词偏移、透明度。
// 什么时候不要用：精确数值输入（用 FmTextField，滑块拖不准）；二值（用 FmSwitch）；
//   一组离散且语义独立的选项（用 FmRadio / FmComboBox）。
//
// 只暴露 QtQuick.Controls 的 `value` / `from` / `to` / `stepSize` / `snapMode`，
// 不自造属性名。`showValue: true` 时右侧显示 `valueText`（由调用方拼单位：
//   `valueText: Math.round(value) + "%"`，组件不猜单位）。
// 状态：正常 / 拖动中 / 禁用。拖动中的反馈只是把手变大，不改语义。

import QtQuick
import QtQuick.Controls

Slider {
    id: control
    objectName: "fmSlider"

    //: 右侧是否显示数值
    property bool showValue: true
    //: 数值文本（默认取整；带单位由调用方写绑定）
    property string valueText: String(Math.round(control.value))
    //: 控件里是否显示 from/to 两端的刻度文字
    property bool showRange: false

    implicitWidth: 220
    // `handle` / `contentItem` 是 Controls 样式**后填**的两个属性：组件刚建出来的那一瞬间
    // 它们还是 null，直接读 `.height` 会刷一条 `Cannot read property … of null`
    // （实测踩到：Gallery 加载期就报了这一条，被 `tests/test_components_qml.py` 抓住）。
    implicitHeight: Math.max(handle ? handle.height : 0, contentItem ? contentItem.implicitHeight : 0)
                    + (showRange ? 16 : 0)

    background: Rectangle {
        x: control.leftPadding
        y: control.topPadding + (control.availableHeight - height) / 2
        width: control.availableWidth
        height: 4
        radius: height / 2
        color: Theme?.cardBorder ?? "transparent"

        Rectangle {
            objectName: "fmSliderFill"
            width: control.visualPosition * parent.width
            height: parent.height
            radius: parent.radius
            color: control.enabled ? Theme?.accent ?? "transparent" : Theme?.textSecondary ?? "transparent"
        }
    }

    handle: Rectangle {
        objectName: "fmSliderHandle"
        x: control.leftPadding + control.visualPosition * (control.availableWidth - width)
        y: control.topPadding + (control.availableHeight - height) / 2
        width: control.pressed ? 18 : 14
        height: width
        radius: width / 2
        color: control.enabled ? Theme?.textPrimary ?? "transparent" : Theme?.textSecondary ?? "transparent"
        border.width: 2
        border.color: control.enabled ? Theme?.accent ?? "transparent" : Theme?.cardBorder ?? "transparent"
    }

    Text {
        objectName: "fmSliderValue"
        visible: control.showValue
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        text: control.valueText
        color: control.enabled ? Theme?.textSecondary ?? "transparent" : Theme?.cardBorder ?? "transparent"
        font.pixelSize: Theme?.fontSizeSmall ?? 10

        // 数值文本占位：滑块本体让出右侧空间，避免把手压到文字上
        onVisibleChanged: control.rightPadding = visible ? width + (Theme?.spacingSm ?? 0) : 0
        Component.onCompleted: control.rightPadding = visible ? width + (Theme?.spacingSm ?? 0) : 0
    }

    Text {
        objectName: "fmSliderFrom"
        visible: control.showRange
        anchors.left: parent.left
        anchors.bottom: parent.bottom
        text: String(control.from)
        color: Theme?.textSecondary ?? "transparent"
        font.pixelSize: Theme?.fontSizeSmall ?? 10
    }

    Text {
        objectName: "fmSliderTo"
        visible: control.showRange
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        text: String(control.to)
        color: Theme?.textSecondary ?? "transparent"
        font.pixelSize: Theme?.fontSizeSmall ?? 10
    }
}
