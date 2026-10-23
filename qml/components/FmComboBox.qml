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
//
// 长列表（阶段 3 任务 3.7 补）：`popupMaxHeight` 限制弹出层高度，超出的部分**滚动**。
//   游戏版本候选有一百多个，不限制时 Popup 会撑到比窗口还高 —— 既放不下、也滚不动
//   （用户 2026-10-23 反馈「版本的下拉列表太长还不能滚动」）。默认 320，调用方可以改。

import QtQuick
import QtQuick.Controls

ComboBox {
    id: control
    objectName: "fmComboBox"

    implicitWidth: 200
    implicitHeight: 32
    font.pixelSize: Theme?.fontSizeBase ?? 12

    //: 弹出层最大高度（像素）。超过就出现滚动条；列表短时按内容高度收缩。
    property int popupMaxHeight: 320

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
        id: popup
        objectName: "fmComboBoxPopup"
        y: control.height
        width: control.width
        //: 内容高度与最大高度取小 —— 这就是"长列表可滚动"的实现点
        implicitHeight: Math.min(contentItem.implicitHeight + 2, control.popupMaxHeight)
        padding: 1

        contentItem: ListView {
            objectName: "fmComboBoxPopupList"
            clip: true
            implicitHeight: Math.min(contentHeight, control.popupMaxHeight - 2)
            model: popup.visible ? control.delegateModel : null
            currentIndex: control.highlightedIndex
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: FmScrollBar {}
        }

        background: Rectangle {
            radius: Theme?.radiusMd ?? 0
            color: Theme?.cardBg ?? "transparent"
            border.width: 1
            border.color: Theme?.cardBorder ?? "transparent"
        }
    }
}
