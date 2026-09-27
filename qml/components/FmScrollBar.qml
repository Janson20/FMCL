// FmScrollBar.qml —— 主题化滚动条（阶段 2 任务 2.16 组件库／界面返工 C 组新增）。
//
// 什么时候用它：给**自己就会滚的容器**贴一条跟随主题的滚动条 —— `ListView` / `GridView` /
//   `Flickable` 的 `ScrollBar.vertical` / `ScrollBar.horizontal`（日志面板的 `LogView`
//   就是这么用的）。`FmScrollView`（整块内容滚动）内部用的也是它。
// 什么时候不要用：整块内容比可视区高（那用 `FmScrollView`，它自带两条）；不要给一个
//   不滚动的容器贴条 —— 永远不出现的滚动条只会让人以为"这里还能滚"。
//
// 为什么需要它（闸门 R9 的由来）：本项目的 Qt Quick Controls 样式是 **Basic**
//   （`main_qml.py: create_application()` 里设的 `QT_QUICK_CONTROLS_STYLE`），滚动条跟着
//   系统调色板走 —— 在锁定深色的界面里是一条浅色亮条，而且完全不跟随 `Theme.*`
//   （与缺陷 D-102「主题外的硬编码颜色」同一类）。所以手柄由本件用令牌重画：
//   常态 `divider`、悬停 `textSecondary`、按下 `textTertiary`。
//
// 用法（附着属性的**名字**来自 Qt，改不了；换的只是"值"这个实例）::
//
//     ListView {
//         ScrollBar.vertical: FmScrollBar { policy: ScrollBar.AsNeeded }
//     }
//
// 闸门 R9 判的是"文件里有没有出现原生控件的**实例**"（`ScrollBar { }`），
//   `ScrollBar.vertical:` 这种附着属性名不算 —— 这一条写在 R9 的判据里。

import QtQuick
import QtQuick.Controls

ScrollBar {
    id: bar
    objectName: "fmScrollBar"

    //: 滚动条粗细（默认 6；FluentUI 的观感也是细条）
    property int thickness: 6
    //: true = 常显（`AlwaysOn`）；false = 按需出现（`AsNeeded`，默认）
    property bool alwaysVisible: false

    //: 手柄最短占轨道的 8%：内容再长也留得住一条抓得住的柄
    minimumSize: 0.08
    // 调用方可以覆盖 `policy`（`FmScrollView` 就用它表达"整条关掉"= AlwaysOff）
    policy: bar.alwaysVisible ? ScrollBar.AlwaysOn : ScrollBar.AsNeeded

    //: 手柄是否处于"被摸到"的状态（悬停或按下）—— 外露给测试与调用方
    readonly property bool engaged: bar.pressed || bar.hovered

    contentItem: Rectangle {
        objectName: "fmScrollBarHandle"
        implicitWidth: bar.thickness
        implicitHeight: bar.thickness
        radius: Math.round(Math.min(width, height) / 2)
        color: bar.pressed ? (Theme?.textTertiary ?? "transparent")
             : (bar.hovered ? (Theme?.textSecondary ?? "transparent")
                            : (Theme?.divider ?? "transparent"))
        Behavior on color {
            ColorAnimation { duration: Theme?.durationFast ?? 0 }
        }
    }

    background: Rectangle {
        objectName: "fmScrollBarTrack"
        implicitWidth: bar.thickness
        implicitHeight: bar.thickness
        color: "transparent"
    }
}
