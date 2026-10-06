// FmButton.qml —— 通用按钮（阶段 2 任务 2.16）。
//
// 什么时候用它：界面/对话框里任何"点一下发生一件事"的地方 —— 提交、取消、保存、
//   删除、重试、导入导出。一个界面里**最多一个主按钮**。
// 什么时候不要用：一级导航与页面切换（那是 `shell/Navigation.qml` 的导航项，
//   02 的 P1：导航栏只放领域不放操作）；纯图标命中区（用 FmIcon 自己配命中区）；
//   整行点击的列表（用 FmListItem）；开关/单选（用 FmSwitch / FmRadio）。
//
// 三种语义、三种状态：
//   * 主按钮   `primary: true`（默认）—— 强调色底，主操作；
//   * 次按钮   `primary: false` —— 中性底 + 描边，取消/次要操作；
//   * 危险按钮 `primary: false; danger: true` —— 错误色，删除/强杀/清空这类不可逆操作；
//   * 状态     `enabled` / `!enabled`（禁用）/ `loading: true`（进行中，点击被吞掉）。
// 文案由调用方给（`text:` 写 `Tr.map[…]`，别在组件里判语义）。
//
// **文字与图标颜色按面板底色自适应**：底色随 5 个预设主题与用户自定义强调色变亮变暗，
//   写死白字在浅底上会糊（用户 2026-10-06 反馈「按钮文字看不清」：错误色底配白字 3.3:1、
//   配深色字 6.1:1）。算法是 WCAG 相对亮度 + 0.17905… 阈值（白/黑对比度相等的点），
//   浅底给深色文字、深底给浅色文字 —— 实现形态刻意与 committed 版的两个配色函数同构，
//   原因见缺陷 D-196。
//
// 为什么用两个布尔量而不是 `kind: "primary"` 字符串：2.13 的对话框临时件
//   （`dialogs/DialogButton.qml`）用的就是 `primary: bool`，而 `tests/test_dialogs_qml.py`
//   直接断言 `okButton.primary === true` —— 那条测试不在 2.16 的可改范围，所以
//   `primary` 保持主入口，危险态另加 `danger`；`kind` 做成只读派生值给 Gallery/测试读。

import QtQuick

Item {
    id: control
    objectName: "fmButton"

    property string text: ""
    property bool primary: true
    property bool danger: false
    //: 进行中：显示 loading 图标、吞掉点击，但**不**改文案（"取消" → "取消中" 由调用方写）
    property bool loading: false
    //: 可选图标名（不带扩展名）；空串 = 不显示图标
    property string iconName: ""
    //: 只要图标不要文字（分页的上一页/下一页、清空、展开这类）。配 `text: ""` 用。
    property bool iconOnly: false

    readonly property string kind: danger ? "danger" : (primary ? "primary" : "secondary")
    readonly property bool interactive: enabled && !loading
    //: 悬停 / 按下（外露给调用方：对话框组件的 ToolTip 就绑在 `hovered` 上）
    readonly property alias hovered: area.containsMouse
    readonly property alias pressed: area.pressed

    signal clicked()

    implicitWidth: iconOnly
                   ? (Theme?.iconSize ?? 0) + 2 * (Theme?.spacingSm ?? 0)
                   : Math.max(88, content.implicitWidth + 2 * (Theme?.spacingLg ?? 0))
    implicitHeight: 34

    //: WCAG 相对亮度（`#rgb` / `#rrggbb` / `#aarrggbb`；解析不了返回 0）。
    //: **刻意做成普通函数、也刻意不放进单独的 JS 模块**：阶段 3.5 反馈轮实测，
    //: 把它组合成"只读属性 + 组件自己的函数"会让真 `App.qml` 里三态演示按钮刷
    //: `QML FmButton: Cannot find member data`（缺陷 D-196，二分记录见 `16` §二十一）。
    //: 这里的形态与 committed 版里那两个配色函数**同构**，是实测 0 命中的写法。
    function faceLuminance(color) {
        var text = String(color)
        if (text.charAt(0) !== "#")
            return 0
        var hex = text.substring(1)
        if (hex.length === 8)
            hex = hex.substring(2)
        var r = 0
        var g = 0
        var b = 0
        if (hex.length === 3) {
            r = parseInt(hex.charAt(0) + hex.charAt(0), 16)
            g = parseInt(hex.charAt(1) + hex.charAt(1), 16)
            b = parseInt(hex.charAt(2) + hex.charAt(2), 16)
        } else if (hex.length === 6) {
            r = parseInt(hex.substring(0, 2), 16)
            g = parseInt(hex.substring(2, 4), 16)
            b = parseInt(hex.substring(4, 6), 16)
        } else {
            return 0
        }
        if (isNaN(r) || isNaN(g) || isNaN(b))
            return 0
        return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
    }

    function channel(value) {
        var v = Number(value) / 255.0
        return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)
    }

    //: 文字/图标颜色：**按面板底色自适应**（浅底深字、深底浅字）。
    //: 形态说明见 D-196：这种"读 `control.hovered` 的多数值三元"是实测 0 命中的写法。
    readonly property color labelColor: control.enabled
                   ? (((control.danger && !control.hovered)
                       ? (control.faceLuminance(Theme?.error) > 0.17905004914115914
                          ? control.faceLuminance(Theme?.bgMedium) : 0)
                       : control.faceLuminance(control.faceColor())) > 0.17905004914115914
                      ? (Theme?.bgDark ?? "transparent")
                      : (Theme?.textPrimary ?? "transparent"))
                   : (Theme?.textSecondary ?? "transparent")

    function faceColor() {
        if (!control.enabled)
            return Theme?.bgMedium ?? "transparent"
        if (control.danger)
            return area.containsMouse ? Theme?.error ?? "transparent" : Theme?.bgMedium ?? "transparent"
        if (control.primary)
            return area.containsMouse ? Theme?.accentHover ?? "transparent" : Theme?.accent ?? "transparent"
        return area.containsMouse ? Theme?.cardBorder ?? "transparent" : Theme?.bgMedium ?? "transparent"
    }

    function borderColor() {
        if (!control.enabled)
            return Theme?.cardBorder ?? "transparent"
        if (control.danger)
            return Theme?.error ?? "transparent"
        return control.primary ? Theme?.accent ?? "transparent" : Theme?.cardBorder ?? "transparent"
    }

    Rectangle {
        id: background
        objectName: "fmButtonFace"
        anchors.fill: parent
        radius: Theme?.radiusMd ?? 0
        border.width: 1
        // 为什么要判 `control.borderColor` 这个函数**在不在**（阶段 3 任务 3.1 的缺陷 D-163）：
        // 对象被销毁（`StackView` 弹页、进程退出）时，QML 会先把元对象拆掉、绑定却还排在
        // 求值队列里 —— 那一刻 `control` 还在、`control.borderColor` 已经不是函数了，
        // 绑定一求值就是 `TypeError: Property 'borderColor' … is not a function`。
        // 这与全局那条"上下文属性析构时先被清空，所以一律 `Theme?.x ?? 兜底`"是同一类问题，
        // 兜底写法也一致；不兜的话视觉回归的"整轮零 TypeError"判据会偶发变红（实测稳定复现）。
        border.color: control.borderColor ? control.borderColor() : (Theme?.cardBorder ?? "transparent")
        color: control.faceColor ? control.faceColor() : (Theme?.bgMedium ?? "transparent")
    }

    Row {
        id: content
        anchors.centerIn: parent
        spacing: Theme?.spacingSm ?? 0

        FmIcon {
            objectName: "fmButtonIcon"
            visible: control.loading || control.iconName.length > 0
            name: control.loading ? "loading" : control.iconName
            color: control.labelColor
            size: Math.round((Theme?.fontSizeBase ?? 12) * 1.2)
            anchors.verticalCenter: parent.verticalCenter
        }

        Text {
            objectName: "fmButtonLabel"
            anchors.verticalCenter: parent.verticalCenter
            text: control.text
            color: control.labelColor
            font.pixelSize: Theme?.fontSizeBase ?? 12
            elide: Text.ElideRight
        }
    }

    MouseArea {
        id: area
        anchors.fill: parent
        hoverEnabled: true
        enabled: control.interactive
        cursorShape: Qt.PointingHandCursor
        onClicked: control.clicked()
    }

    // 键盘激活：焦点在本按钮上时回车/空格 = 点击。对话框根节点上的回车处理器
    // 只在按钮**没有**焦点时才拿到事件（Keys 处理器会消费事件），两者不冲突。
    Keys.onReturnPressed: if (control.interactive) control.clicked()
    Keys.onEnterPressed: if (control.interactive) control.clicked()
    Keys.onSpacePressed: if (control.interactive) control.clicked()
}
