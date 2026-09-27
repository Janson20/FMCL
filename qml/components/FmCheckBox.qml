// FmCheckBox.qml —— 复选框（阶段 2 任务 2.16）。
//
// 什么时候用它：**要跟"确定"按钮一起生效**的勾选项 —— 启动期对话框的"以后不再询问"、
//   风险确认里的"我已了解"、批量操作前的一次性同意。勾选只是攒状态，点了确定才落库；
//   另外"这一组里选哪几个"的多选、以及"全选/半选/全不选"的父节点也归它（半选态）。
// 什么时候不要用：**立即生效**的二值设置（拨一下就该生效 —— 用 FmSwitch，界面上长成开关
//   的样子才是对的）；一组里只能选一个（用 FmRadio / FmComboBox）；二值而且不需要"提交"
//   语义（还是 FmSwitch）。FmCheckBox 与 FmSwitch 的区别是**语义**，不是长相，别互换。
//
// 与 FmSwitch 的关系：高度与文字排版刻意对齐（implicitHeight 用同一套算法、
//   font.pixelSize 走 fontSizeBase、文字 textPrimary / 禁用态 textTertiary），
//   所以两者并排放在同一行里不会参差。
//
// 三态观感（颜色一律来自 Theme.*，本文件不出现任何颜色字面量，也没有浅色分支 —— 界面锁深色）：
//   * 勾选   —— `accent` 填充 ＋ `accentText` 的对勾（走 FmIcon { name: "check" }）
//   * 半选   —— `accent` 填充 ＋ `accentText` 的横线（方块正中一条，与 Qt Basic 样式同观感）
//   * 未勾选 —— 透明底 ＋ `cardBorder` 描边
//   * 悬停 / 按下 —— 未勾选走 `itemHover` / `itemPress`，已勾选（含半选）走
//     `accentHover` / `accentPressed`；过渡时长一律 `durationFast`
//   * 禁用   —— 整块降级：已勾选的底走 `cardHover`、未勾选保持透明＋ `cardBorder`，
//     对勾/横线走 `textTertiary`，标签也走 `textTertiary`
//
// 半选怎么进来：本 Qt 版本（6.7.3）**没有** `partiallyCheckedEnabled` 属性（实测：CheckBox 的
//   metaObject 里只有 `tristate` / `checkState`），所以半选只能由调用方写
//   `checkState: Qt.PartiallyChecked`，或者 `tristate: true` 让点击自己轮转三态。
//   注意 `tristate: false`（默认）时**点击**不会走到半选，但程序写进来的半选态照常渲染。
//
// `hoverEnabled: true` 是必须显式打开的：Basic 样式的 CheckBox 默认 `hoverEnabled` 是 false，
//   不打开的话 `hovered` 永远是 false，悬停反馈就是一段死代码（实测值，见本文件对应的自查记录）。
//
// 只暴露 QtQuick.Controls 的 `checkState` / `checked` / `tristate` / `text` / `toggled()`
// 与 `clicked()`，不自造一套属性名。文案由调用方给（`text:` 写 `Tr.map[…]`）。

import QtQuick
import QtQuick.Controls

CheckBox {
    id: control
    objectName: "fmCheckBox"

    // 悬停反馈要自己打开（Basic 样式下默认是 false，见文件头）
    hoverEnabled: true

    // `indicator` / `contentItem` 是 Controls 样式后填的属性，建出来的那一瞬间可能是 null
    // （读 `.height` 会刷 TypeError —— 与 FmSwitch / FmSlider 同一个坑，见那边的注释）。
    implicitHeight: Math.max(indicator ? indicator.height : 0,
                             (contentItem ? contentItem.implicitHeight : 0) + 2 * (Theme?.spacingXs ?? 0))
    implicitWidth: (indicator ? indicator.width : 0) + (Theme?.spacingSm ?? 0)
                   + (contentItem ? contentItem.implicitWidth : 0)

    indicator: Rectangle {
        id: box
        objectName: "fmCheckBoxBox"
        implicitWidth: 18
        implicitHeight: 18
        x: control.leftPadding
        y: control.topPadding + (control.availableHeight - height) / 2
        radius: Theme?.radiusSm ?? 6

        // 三态判据（Qt.CheckState 的三个值：Unchecked / PartiallyChecked / Checked）
        readonly property bool isChecked: control.checkState === Qt.Checked
        readonly property bool isPartial: control.checkState === Qt.PartiallyChecked

        // 底色：已勾选/半选走强调色系，未勾选是透明底 + 悬停/按下叠加
        color: {
            if (!control.enabled)
                return (isChecked || isPartial) ? (Theme?.cardHover ?? "transparent") : "transparent"
            if (isChecked || isPartial)
                return control.down ? (Theme?.accentPressed ?? "transparent")
                                    : (control.hovered ? (Theme?.accentHover ?? "transparent")
                                                       : (Theme?.accent ?? "transparent"))
            return control.down ? (Theme?.itemPress ?? "transparent")
                                : (control.hovered ? (Theme?.itemHover ?? "transparent") : "transparent")
        }

        // 描边：已勾选/半选跟底色同色（描边与填充连成一整块），未勾选用 cardBorder，
        // 悬停时提到 accentHover 表示"点得到"
        border.width: 1
        border.color: (isChecked || isPartial)
                      ? color
                      : (control.hovered && control.enabled ? (Theme?.accentHover ?? "transparent")
                                                            : (Theme?.cardBorder ?? "transparent"))

        Behavior on color {
            ColorAnimation {
                duration: Theme?.durationFast ?? 110
            }
        }

        // 勾选态：对勾（图标唯一入口是 FmIcon；不用 emoji、也不自己画字形）
        FmIcon {
            objectName: "fmCheckBoxMark"
            anchors.centerIn: parent
            visible: box.isChecked
            name: "check"
            color: control.enabled ? (Theme?.accentText ?? "transparent") : (Theme?.textTertiary ?? "transparent")
            size: parent.width - 4
        }

        // 半选态：正中一条横线
        Rectangle {
            objectName: "fmCheckBoxDash"
            anchors.centerIn: parent
            visible: box.isPartial
            width: parent.width - 4
            height: 2
            radius: height / 2
            color: control.enabled ? (Theme?.accentText ?? "transparent") : (Theme?.textTertiary ?? "transparent")
        }
    }

    contentItem: Text {
        objectName: "fmCheckBoxLabel"
        text: control.text
        // 禁用态的标签色跟 `FmSwitch` / `FmRadio` 对齐（都是 `textSecondary`）——
        // 同一个表单里两个二值控件的禁用态不该一深一浅；方块里的勾/横线仍走 `textTertiary`
        color: control.enabled ? (Theme?.textPrimary ?? "transparent") : (Theme?.textSecondary ?? "transparent")
        font.pixelSize: Theme?.fontSizeBase ?? 12
        verticalAlignment: Text.AlignVCenter
        leftPadding: (control.indicator ? control.indicator.width : 0) + (Theme?.spacingSm ?? 0)
        elide: Text.ElideRight
    }
}
