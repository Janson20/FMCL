// 页面栈 —— `Nav` 的**镜像**（二级/三级页面栈，02 第三节骨架图的右侧内容区）。
//
// ## 为什么是"镜像"而不是自己维护历史
//
// 栈的真相只有一份，在 Python 侧的 `NavBridge`。这里不接收任何"用户手势导致的
// pop"，只做一件事：`Nav` 一变就把 `StackView` 调整成同样的形状。
// 好处是深链一次跳两级、Agent 直接跳转、插件注册的新页面都不需要在 QML 里
// 再走一遍同样的栈逻辑（两份实现必然不同步）。
//
// ## 退多层怎么写（实测过的形式）
//
// `poc/_probe_stackview.py` 在 PySide6 6.7.3 上实测：
//   * `pop()`                       → 退一层（带过渡动画）
//   * `pop(item, StackView.Immediate)` → 一直退到 item 成为前台（**保留** item）
//   * `pop(null, StackView.Immediate)` → 退到**栈底第一项**（不是退一层！容易误用）
// 所以多退用 `pop(view.get(keep - 1), Immediate)`，单退用 `pop()`（保留动画）。
//
// ## push 时给页面传的属性
//
// `StackView.push(url, properties)` 会把这份属性表设到页面的根对象上。传的是
// **这一帧的快照**（路由 id / 标题键 / 描述键 / 图标 / 参数）而不是让页面去读
// `Nav.current*`：过渡动画期间会有两页同时在屏上，读全局状态的那一页会在动画
// 中途改文案。代价是页面必须声明这五个属性（内置 12 页都声明了；插件页也要声明，
// 这条要求写在 2.11 的报告里，已作为 UI API v2 的注意事项登记）。

import QtQuick
import QtQuick.Controls

StackView {
    id: view
    objectName: "pageStack"

    //: 同步中（防止 Nav 信号与 StackView 自身变化互相触发）
    property bool syncing: false
    //: 最近一次同步的异常/不一致（给冒烟测试与诊断看，界面上的表现就是缺页）
    property string lastError: ""

    function syncPages() {
        if (!Nav || view.syncing)
            return
        view.syncing = true
        var target = Nav.depth
        var frames = Nav.breadcrumb
        try {
            // 1) 比 Nav 深 → 退到目标深度
            if (view.depth > target) {
                var keep = Math.max(1, target)      // StackView 至少保留一项
                var anchor = view.get(keep - 1)
                if (anchor)
                    view.pop(anchor, StackView.Immediate)
                else
                    view.pop()
            }
            // 2) 深度一致但前台不是当前路由（reset / goHome 换页）→ 换掉最上面那页
            if (view.depth === target && target > 0 && view.currentItem
                    && view.currentItem.routeId !== undefined
                    && view.currentItem.routeId !== Nav.currentRoute) {
                view.pop()
            }
            // 3) 比 Nav 浅 → 补齐缺的帧
            while (view.depth < target) {
                var frame = frames[view.depth]
                if (!frame) {
                    view.lastError = "breadcrumb missing frame at depth " + view.depth
                    break
                }
                if (!view.pushFrame(frame))
                    break
            }
            if (view.depth === target)
                view.lastError = ""
        } catch (e) {
            view.lastError = String(e)
            console.log("PageStack sync failed: " + e)
        } finally {
            view.syncing = false
        }
    }

    //: 推入一帧。先自己建对象、只传页面**声明过**的属性，再 push 这个对象。
    //:
    //: 为什么不直接 `push(url, {...})`：未声明的属性会各刷一条
    //: `Error: Cannot assign to non-existent property "routeId"`（插件页很可能不声明它们，
    //: 探针 `poc/_probe_plugin_page.py` 实测：页面能起，但日志里 5 条 Error）。
    //: 自己建对象还顺带把"组件坏了"变成一条明确的 `lastError` + 日志，
    //: 而不是一页白屏（navFailed 那条路留给 Nav 的入参校验）。
    function pushFrame(frame) {
        var component = Qt.createComponent(frame.qml_url)
        if (component.status !== Component.Ready) {
            view.lastError = "component not ready: " + frame.qml_url + " (" + component.errorString() + ")"
            console.log("PageStack: " + view.lastError)
            return false
        }
        var item = component.createObject(null)
        if (item === null) {
            view.lastError = "createObject returned null: " + frame.qml_url
            console.log("PageStack: " + view.lastError)
            return false
        }
        var props = {}
        if (item.routeId !== undefined)
            props.routeId = frame.id
        if (item.routeTitleKey !== undefined)
            props.routeTitleKey = frame.title_key
        if (item.routeDescriptionKey !== undefined)
            props.routeDescriptionKey = frame.description_key
        if (item.routeIcon !== undefined)
            props.routeIcon = frame.icon
        if (item.routeParams !== undefined)
            props.routeParams = frame.params
        view.push(item, props)
        return true
    }

    // 栈空时不显示任何东西（正常路径下不会出现：Nav.reset() 会回到首页）
    visible: Nav ? Nav.depth > 0 : true

    Component.onCompleted: syncPages()

    Connections {
        target: Nav

        function onRouteChanged(route) {
            view.syncPages()
        }

        function onBreadcrumbChanged() {
            view.syncPages()
        }
    }
}
