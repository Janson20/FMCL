// FmDropZone.qml —— 文件拖放区（阶段 2 任务 2.16 的组件库，阶段 3 任务 3.6 落地）。
//
// 为什么现在才出现：2.16 锁定的清单里没有它，因为当时没有页面需要"收文件"；
// 3.6 的资源管理页要拖拽安装，按 2.16 的四步补件流程（加文件 → 守纪律 → 进白名单 → 跑闸门）
// 补进来，而不是在页面里就地写一个一次性控件。
//
// 什么时候用它：**接受从文件管理器拖进来的文件**、且"拖进来"是这一块的主要交互
//   —— 资源管理页的「把 .jar / .zip 拖到这里」，它同时也是空态。
// 什么时候不要用：
//   * 只是想让某个列表"顺便"支持拖放（用裸 `DropArea` 铺在列表上，不要为了一个
//     隐形的命中区画一圈虚线边框）；
//   * 拖进来的是**界面内部**的东西（列表项排序之类，那不是文件拖放）。
//
// 契约：本件**不碰服务、不写文件**，只把 `urls`（`list<url>`）原样发出去 ——
//   路径解析（`file://` → 本地路径）与"认不认这个类型"都在桥/服务层（红线 2）。
//   `dropped` 发出的就是 `DropArea.onDropped` 的 `drop.urls`，调用方直接转发。
//
// 视觉：虚线边框 + 图标 + 提示文案。**不画渐变**（闸门 R1），颜色只来自 `Theme.*`
//   （闸门 R8），图标走 `FmIcon`（闸门 R2）。
//   虚线用 `QtQuick.Shapes`（`ShapePath.strokeStyle: DashLine`）—— 这与
//   `FmProgressRing` 用的是同一个模块，**没有新增依赖**；比用几十个小矩形拼
//   虚线省事得多，也不会在缩放时露馅。

import QtQuick
import QtQuick.Layouts
import QtQuick.Shapes

Rectangle {
    id: zone
    objectName: "fmDropZone"

    //: 提示文案（调用方给，通常是 `Tr.map[...]` 的结果）
    property string text: ""
    //: 图标名（`qml/assets/icons/*.svg` 的名字）
    property string iconName: "download"
    //: 当前有没有东西悬在上面（`DropArea` 的 entered/exited 维护）
    property bool active: false

    //: `drop.urls` 原样转发（`list<url>`）
    signal dropped(var urls)

    readonly property color borderColor: zone.active ? (Theme?.accent ?? "transparent")
                                                     : (Theme?.cardBorder ?? "transparent")
    readonly property color fillColor: zone.active ? (Theme?.accentSoft ?? "transparent")
                                                   : (Theme?.cardBg ?? "transparent")

    radius: Theme?.radiusSm ?? 6
    color: zone.fillColor
    border.width: 0  // 边框由下面的 Shape 画（虚线的 width 语义与 Rectangle 不同）

    // ── 虚线边框 ──
    Shape {
        objectName: "fmDropZoneBorder"
        anchors.fill: parent
        //: 抗锯齿在这里是**必须**的：虚线斜边与圆角不抗锯齿会呈锯齿状
        layer.enabled: false
        antialiasing: true
        preferredRendererType: Shape.CurveRenderer

        ShapePath {
            objectName: "fmDropZoneBorderPath"
            strokeColor: zone.borderColor
            strokeWidth: 1
            fillColor: "transparent"
            capStyle: ShapePath.FlatCap
            //: 虚线。**必须用具名枚举**：实测 `strokeStyle: 4` 报
            //: `Invalid property assignment: unknown enumeration`，整个组件变成
            //: `Type FmDropZone unavailable`，用它的页面直接加载不了
            //: （阶段 3.6 探针实测，见 `docs/refactor/16-phase3-execution-log.md` §二十二）。
            //: `dashPattern` 的单位是 `strokeWidth` 的倍数。
            strokeStyle: ShapePath.DashLine
            dashPattern: [4, 3]
            startX: zone.radius
            startY: 0.5
            PathLine { x: zone.width - zone.radius; y: 0.5 }
            PathArc {
                x: zone.width - 0.5
                y: zone.radius
                radiusX: zone.radius
                radiusY: zone.radius
            }
            PathLine { x: zone.width - 0.5; y: zone.height - zone.radius }
            PathArc {
                x: zone.width - zone.radius
                y: zone.height - 0.5
                radiusX: zone.radius
                radiusY: zone.radius
            }
            PathLine { x: zone.radius; y: zone.height - 0.5 }
            PathArc {
                x: 0.5
                y: zone.height - zone.radius
                radiusX: zone.radius
                radiusY: zone.radius
            }
            PathLine { x: 0.5; y: zone.radius }
            PathArc {
                x: zone.radius
                y: 0.5
                radiusX: zone.radius
                radiusY: zone.radius
            }
        }
    }

    ColumnLayout {
        objectName: "fmDropZoneBody"
        anchors.centerIn: parent
        width: Math.min(parent.width - 2 * (Theme?.spacingLg ?? 20), implicitWidth)
        spacing: Theme?.spacingSm ?? 8

        FmIcon {
            objectName: "fmDropZoneIcon"
            Layout.alignment: Qt.AlignHCenter
            name: zone.iconName
            color: zone.active ? (Theme?.accent ?? "transparent") : (Theme?.textSecondary ?? "transparent")
            size: Theme?.iconSize ?? 20
        }

        Text {
            objectName: "fmDropZoneText"
            Layout.fillWidth: true
            text: zone.text
            color: zone.active ? (Theme?.textPrimary ?? "transparent") : (Theme?.textSecondary ?? "transparent")
            font.pixelSize: Theme?.fontSizeBase ?? 12
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
        }
    }

    DropArea {
        id: dropArea
        objectName: "fmDropZoneArea"
        anchors.fill: parent
        onEntered: zone.active = true
        onExited: zone.active = false
        onDropped: function (drop) {
            //: 放下之后不再高亮（Qt 不保证 exited 一定会来 —— 从区域外丢下时就没有）
            zone.active = false;
            zone.dropped(drop.urls);
        }
    }
}
