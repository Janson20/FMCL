// FmSpinBox.qml —— 数字步进输入框（阶段 2 任务 2.16）。
//
// 什么时候用它：一个**数值**既要精确输入、又要用两侧的步进按钮微调 —— 下载线程数、
//   最大内存(MB)、重试次数、超时秒数、缩放百分比、端口号。
// 什么时候不要用：连续区间里"看得见当前值"比精确更重要（用 FmSlider）；2~5 个短选项的
//   互斥选择（用 FmRadio）；选项多或文案长（用 FmComboBox）；非数字输入（用 FmTextField）。
//
// 为什么包一层：Basic 样式把 SpinBox 画成系统调色板的**浅色**（底色近白、步进按钮浅灰），
//   而本项目的界面锁深色（`FluTheme.darkMode = Dark`），原生件在深色卡片上就是一块白斑。
// 状态：正常 / 悬停 / 按下（两个按钮各自独立）/ 聚焦（描边转强调色）/ 禁用。
// 文案与单位由调用方给（`textFromValue` / `valueFromText` 都是原生属性，组件不猜单位）。
//
import QtQuick
import QtQuick.Controls

SpinBox {
    id: control
    objectName: "fmSpinBox"

    // 只重写**观感**（background / contentItem / up.indicator / down.indicator），
    // 不重新声明 `value` / `from` / `to` / `stepSize` / `editable` / `enabled` / `wrap`：
    // 重名会撞上 `Cannot override FINAL property`（FmComboBox 的 `currentValue` 就是这个坑），
    // 而且调用方写 `FmSpinBox { value: n }` 时就该拿到 Qt 原生语义。

    //: 数字文本的水平对齐（默认居中，与原生 SpinBox 一致；要跟 FmTextField 一样左对齐就传 Qt.AlignLeft）
    property int textAlignment: Qt.AlignHCenter
    //: 步进按钮那一列的宽度（左右内边距按它算；两个按钮同宽，所以数值区始终居中）
    readonly property int stepButtonWidth: 26

    // Basic 样式给左右各留 40（那是给浅色大按钮的），本件的按钮列是 26，
    // 所以内边距自己算 —— 不然数值文本会白白少掉十几像素的可视宽度（实测 6.7.3 的
    // Basic 样式里 `leftPadding` 恒为 40，**不**跟随指示器的实际宽度）。
    implicitWidth: 140
    implicitHeight: 32
    font.pixelSize: Theme?.fontSizeBase ?? 12
    // 必须显式打开：Qt 6.7 的 Basic 样式里 `up.hovered` / `down.hovered` **只在**
    // `hoverEnabled: true` 时才真的翻转（离屏实测：不开时鼠标压在上按钮上 `up.hovered`
    // 始终是 false，下面那两个悬停色就成了死代码）。
    hoverEnabled: true
    leftPadding: control.stepButtonWidth + (Theme?.spacingXs ?? 0)
    rightPadding: control.stepButtonWidth + (Theme?.spacingXs ?? 0)

    //: 步进按钮里加/减号的颜色：常态二级文字、悬停与按下一级文字、禁用三级文字
    function stepGlyphColor(hot) {
        if (!control.enabled)
            return Theme?.textTertiary ?? "transparent"
        return hot ? Theme?.textPrimary ?? "transparent" : Theme?.textSecondary ?? "transparent"
    }

    background: Rectangle {
        objectName: "fmSpinBoxBackground"
        implicitWidth: 140
        implicitHeight: 32
        radius: Theme?.radiusMd ?? 0
        color: control.enabled ? Theme?.bgMedium ?? "transparent" : Theme?.cardBg ?? "transparent"
        border.width: 1
        border.color: !control.enabled
                      ? Theme?.cardBorder ?? "transparent"
                      : (control.activeFocus ? Theme?.accent ?? "transparent" : Theme?.cardBorder ?? "transparent")
    }

    contentItem: TextInput {
        objectName: "fmSpinBoxInput"
        z: 2
        text: control.displayText
        font: control.font
        color: control.enabled ? Theme?.textPrimary ?? "transparent" : Theme?.textTertiary ?? "transparent"
        selectionColor: Theme?.accent ?? "transparent"
        selectedTextColor: Theme?.accentText ?? "transparent"
        horizontalAlignment: control.textAlignment
        verticalAlignment: Text.AlignVCenter
        // 与 Basic 样式一致：不可编辑时输入区只做显示（`editable` 的原生默认是 false）
        readOnly: !control.editable
        validator: control.validator
        inputMethodHints: control.inputMethodHints
        clip: width < implicitWidth
    }

    // 几何沿用 Basic 样式的排布：**减号在左、加号在右**（各占一整列，RTL 时自动镜像）。
    // 按钮的命中区由 Qt 按指示器的 `implicitWidth` 自己算，所以这里只改宽度与观感，
    // 不动 Basic 的 `x` / `height` 公式（`x` 的两个分支已验证与命中区对齐）。
    // 底色常态透明、悬停 `itemHover`、按下 `itemPress` —— 与 FmToolButton 同一套状态映射，
    // 这样一排步进按钮不会比同排的工具按钮更"重"。
    up.indicator: Rectangle {
        objectName: "fmSpinBoxUp"
        implicitWidth: control.stepButtonWidth
        implicitHeight: 20
        x: control.mirrored ? 1 : control.width - width - 1
        y: 1
        height: control.height - 2
        radius: Theme?.radiusSm ?? 0
        color: !control.enabled
               ? "transparent"
               : (control.up.pressed ? Theme?.itemPress ?? "transparent"
                                     : (control.up.hovered ? Theme?.itemHover ?? "transparent" : "transparent"))

        Behavior on color {
            ColorAnimation {
                duration: Theme?.durationFast ?? 110
            }
        }

        // 加号 = 一根横杠 + 一根竖杠。**不用 FmIcon**：图标集里只有 `add`（加号）
        // 没有减号（qml/assets/icons/ 下没有 minus/remove），而这一对符号必须同构 ——
        // 上面贴图标、下面自己画会让两根号粗细不一致。所以按 Basic 样式的做法自己拼，
        // 颜色仍然全部来自 Theme.*（闸门 R8 拦十六进制字面量）。
        Rectangle {
            objectName: "fmSpinBoxUpBar"
            anchors.centerIn: parent
            width: 9
            height: 2
            color: control.stepGlyphColor(control.up.hovered || control.up.pressed)
        }

        Rectangle {
            objectName: "fmSpinBoxUpStem"
            anchors.centerIn: parent
            width: 2
            height: 9
            color: control.stepGlyphColor(control.up.hovered || control.up.pressed)
        }
    }

    down.indicator: Rectangle {
        objectName: "fmSpinBoxDown"
        implicitWidth: control.stepButtonWidth
        implicitHeight: 20
        x: control.mirrored ? control.width - width - 1 : 1
        y: 1
        height: control.height - 2
        radius: Theme?.radiusSm ?? 0
        color: !control.enabled
               ? "transparent"
               : (control.down.pressed ? Theme?.itemPress ?? "transparent"
                                       : (control.down.hovered ? Theme?.itemHover ?? "transparent" : "transparent"))

        Behavior on color {
            ColorAnimation {
                duration: Theme?.durationFast ?? 110
            }
        }

        // 减号 = 只有横杠（与加号的横杠同宽同高，两个按钮的号才一样粗）
        Rectangle {
            objectName: "fmSpinBoxDownBar"
            anchors.centerIn: parent
            width: 9
            height: 2
            color: control.stepGlyphColor(control.down.hovered || control.down.pressed)
        }
    }
}
