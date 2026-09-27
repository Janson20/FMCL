// FmScrollView.qml —— 主题化滚动容器（阶段 2 任务 2.16 组件库／界面返工 C 组新增）。
//
// 什么时候用它：**整块内容比可视区高**的地方 —— 组件画廊、启动期的协议/公告长文、
//   阶段 3 的长表单与详情面板。内容直接写在里面即可（默认属性就是内容），要竖着堆
//   就在里面放一个 `ColumnLayout`。
// 什么时候不要用：列表数据（用 `ListView`：它自己就是滚动容器，外面再套一层会出现
//   两条滚动条打架）；多行输入框内部滚动（`FmTextArea` 自己滚）；内容本来就装得下的
//   地方（那说明布局该收紧，不是加滚动条）。
//
// 为什么需要它（闸门 R9 的由来）：本项目的 Qt Quick Controls 样式是 **Basic**
//   （`main_qml.py: create_application()` 里设的 `QT_QUICK_CONTROLS_STYLE`），它的
//   滚动条是浅色的、跟着系统调色板走 —— 在锁定深色的界面里是一条突兀的亮条，
//   而且完全不跟随 `Theme.*`（切主题不动，缺陷 D-102 的同一类问题）。
//   所以滚动条由本件用令牌重画：常态 `divider`、悬停 `textSecondary`、按下 `textTertiary`。
//
// 三个 API（够用且不给页面漏出原生枚举 —— 页面里出现 `ScrollBar.AlwaysOff` 这种写法
//   就等于绕过闸门 R9 的语义，所以策略只暴露成布尔量）：
//   * `verticalBarEnabled`（默认 true）  = false 时纵向滚动条彻底关掉；
//   * `horizontalBarEnabled`（默认 false）= 本项目的页面内容一律按可用宽度布局，默认不出横条；
//   * `verticalBarAlwaysVisible` / `horizontalBarAlwaysVisible`（默认 false）= 常显。

import QtQuick
import QtQuick.Controls

ScrollView {
    id: view
    objectName: "fmScrollView"

    //: 纵向滚动条是否可用（false = AlwaysOff）
    property bool verticalBarEnabled: true
    //: 横向滚动条是否可用（默认关：内容宽度一律跟随可用宽度）
    property bool horizontalBarEnabled: false
    //: 纵向滚动条是否常显（默认按需：滚动或悬停时才出现）
    property bool verticalBarAlwaysVisible: false
    //: 横向滚动条是否常显
    property bool horizontalBarAlwaysVisible: false

    //: 滚动条手柄粗细（默认 6；FluentUI 的观感也是细条）
    property int barThickness: 6

    function barPolicy(enabled, alwaysVisible) {
        if (!enabled)
            return ScrollBar.AlwaysOff
        return alwaysVisible ? ScrollBar.AlwaysOn : ScrollBar.AsNeeded
    }

    clip: true

    // 两条滚动条都是自研件（样式只有一份，画法在 `FmScrollBar.qml` 里）。
    // `policy` 由这里按上面三个布尔量算（`FmScrollBar` 自己那份 `alwaysVisible` 绑定被覆盖）。
    ScrollBar.vertical: FmScrollBar {
        id: verticalBar
        objectName: "fmScrollViewVerticalBar"
        policy: view.barPolicy(view.verticalBarEnabled, view.verticalBarAlwaysVisible)
        thickness: view.barThickness
    }

    ScrollBar.horizontal: FmScrollBar {
        id: horizontalBar
        objectName: "fmScrollViewHorizontalBar"
        policy: view.barPolicy(view.horizontalBarEnabled, view.horizontalBarAlwaysVisible)
        thickness: view.barThickness
    }
}
