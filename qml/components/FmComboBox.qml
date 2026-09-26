// FmComboBox.qml —— 下拉选择（阶段 2 任务 2.16）。
//
// 什么时候用它：一组互斥选项里**数量多或文案长**的场合 —— 镜像源、Java 路径、
//   下载线程数、语言、主题、模组加载器。取值与显示可以不同：`valueRole` 指向 model
//   里的取值字段（`currentValue` 读它），显示走 Qt 自带的 `textRole`。
//   **这两个属性由 QtQuick.Controls 提供，本件不重新声明**（重名会撞上
//   `Cannot override FINAL property`：ComboBox 的 `currentValue` 是 FINAL 的）。
// 什么时候不要用：只有 2~3 个短选项（用 FmRadio，一眼看得全，少一次点击）；
//   开关型设置（用 FmSwitch）；可输入的自定义值（本件不可编辑，需要就用 FmTextField）。
//
// 状态：正常 / 展开 / 禁用。model 支持字符串数组与 `[{...}]` 对象数组（Qt 原生语义）。
// 文案由调用方给：`model` 里的显示文本要么是 `Tr.map[…]` 的值，要么是数据本身
//   （版本号、路径），组件不做翻译。

import QtQuick
import QtQuick.Controls

ComboBox {
    id: control
    objectName: "fmComboBox"

    implicitWidth: 200
    implicitHeight: 32
    font.pixelSize: Theme?.fontSizeBase ?? 12

    background: Rectangle {
        radius: Theme?.radiusMd ?? 0
        color: control.enabled ? Theme?.bgMedium ?? "transparent" : Theme?.cardBg ?? "transparent"
        border.width: 1
        border.color: control.activeFocus ? Theme?.accent ?? "transparent" : Theme?.cardBorder ?? "transparent"
    }

    contentItem: Text {
        objectName: "fmComboBoxText"
        leftPadding: Theme?.spacingSm ?? 0
        rightPadding: control.indicator ? control.indicator.width + (Theme?.spacingSm ?? 0) : 0
        text: control.displayText
        color: control.enabled ? Theme?.textPrimary ?? "transparent" : Theme?.textSecondary ?? "transparent"
        font.pixelSize: control.font.pixelSize
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    indicator: FmIcon {
        objectName: "fmComboBoxIndicator"
        name: "chevron-down"
        color: control.enabled ? Theme?.textPrimary ?? "transparent" : Theme?.textSecondary ?? "transparent"
        size: Math.round((Theme?.fontSizeBase ?? 12) * 1.2)
        x: control.width - width - (Theme?.spacingSm ?? 0)
        y: (control.height - height) / 2
    }

    delegate: ItemDelegate {
        id: option
        objectName: "fmComboBoxItem"
        width: control.width
        height: Math.max(30, contentItem.implicitHeight + 2 * (Theme?.spacingXs ?? 0))
        highlighted: control.highlightedIndex === index

        contentItem: Text {
            text: control.textRole.length > 0 ? (modelData[control.textRole] ?? "")
                                             : (typeof modelData === "string" ? modelData : String(modelData))
            color: option.highlighted ? Theme?.accent ?? "transparent" : Theme?.textPrimary ?? "transparent"
            font.pixelSize: Theme?.fontSizeBase ?? 12
            verticalAlignment: Text.AlignVCenter
            leftPadding: Theme?.spacingSm ?? 0
            elide: Text.ElideRight
        }

        background: Rectangle {
            color: option.highlighted ? Theme?.bgLight ?? "transparent" : Theme?.cardBg ?? "transparent"
        }
    }

    popup: Popup {
        objectName: "fmComboBoxPopup"
        y: control.height
        width: control.width
        implicitHeight: contentItem.implicitHeight + 2
        padding: 1

        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: control.popup.visible ? control.delegateModel : null
            currentIndex: control.highlightedIndex
            boundsBehavior: Flickable.StopAtBounds
        }

        background: Rectangle {
            radius: Theme?.radiusMd ?? 0
            color: Theme?.cardBg ?? "transparent"
            border.width: 1
            border.color: Theme?.cardBorder ?? "transparent"
        }
    }
}
