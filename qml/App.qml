// FMCL QML 根窗口 —— 阶段 2 任务 2.12 的窗口骨架（对应 02 第三节的骨架图）。
//
// 结构（自上而下四层，与骨架图一一对应）：
//
//   FluWindow                     窗口本体 + 内置 appBar（标题、最小化/最大化/关闭）
//     └ TitleBar                  返回 / 面包屑 / 全局搜索 / 通知 / 账号
//     └ RowLayout
//         ├ Navigation            12 个一级导航项（+ 插件页），切换即 Nav.push(routeId)
//         └ PageStack             页面栈（Nav 的镜像，见 PageStack.qml 的说明）
//     └ StatusBar                 状态文本 / 进度 / 后台任务数
//
// 为什么用 FluWindow：阶段 0 第 11.2 节定的分工 —— "需要标题栏按钮与窗口路由的主窗口
// 用 FluWindow，悬浮窗一律用原生 Window"。`effect: "normal"` 是**必须显式写**的
// （红线 5：禁渐变/亚克力；闸门 R1 只认字面量，写变量它看不见）。
//
// 本文件里没有一处硬编码颜色（一律 Theme.*）、没有一个中文字面量（一律 Tr?.map[...]）、
// 没有一个 emoji（图标一律 (Runtime?.iconUrl("name") ?? "") → qml/assets/icons/*.svg）。
//
// 关于 `Theme?.x ?? 兜底` 的写法：上下文属性在引擎析构时**先**被清成 null，而绑定还排在
// 求值队列里 —— 不判空的话退出阶段每个绑定刷一条 `TypeError: … of null`，日志判据
// （"有没有 QML 报错"）就永远为真了（2.2 实测的缺陷，`test_main_qml_entry.py` 钉住它）。
// Qt 6.7 的 V4 支持可选链与 `??`（`poc/_probe_js_optional_chaining.py` 实测），
// 兜底值只在析构那一瞬间用到，屏幕上永远看不到。同样的判空写在 shell/ 与 pages/ 里。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import FluentUI
import "shell"
import "components"
import "components/dialogs"
import "overlays"

FluWindow {
    id: app
    objectName: "appWindow"

    width: 1280
    height: 800
    minimumWidth: 960
    minimumHeight: 640

    // 主窗口的可见性由**启动流程**决定（任务 2.14）：启动画面期间隐藏，
    // 关画面之后才显示。
    //
    // 判据要**两段都判**：
    //   * `typeof Startup !== "undefined"` —— 挡住"完全没声明"（抛 ReferenceError）；
    //   * `&& Startup` —— 挡住"已声明但为 null"（引擎析构时上下文属性先被清空，
    //     绑定还排在求值队列里，这时 `Startup.dismissed` 会抛 TypeError）。
    // 只写 `typeof` 的话守卫测试立刻会红（我第一版就是这么写的，被
    // `test_no_qml_binding_errors_at_process_exit` 当场抓到）。
    visible: (typeof Startup !== "undefined" && Startup) ? Startup.dismissed : true

    // 红线 5 + 闸门 R1：材质必须显式关掉（"normal" = 纯色，阶段 0 第 2.4 节实测）。
    effect: "normal"

    // 标题走 Runtime（只读运行时信息），与窗口内置 appBar 的同一份文案。
    // 判空是必须的：引擎析构时上下文属性会先被清掉，而求值还排在队列里（2.2 实测的日志污染）。
    title: Runtime ? (Runtime.appName + " " + Runtime.appVersion) : ""

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        TitleBar {
            id: titleBar
            Layout.fillWidth: true
            Layout.preferredHeight: 48
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            Navigation {
                Layout.fillHeight: true
                Layout.preferredWidth: 208
            }

            // 页面区：底色用 Theme?.bgDark ?? "transparent"（比壳层深一档，把页面从导航栏里"沉"下去）
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: Theme?.bgDark ?? "transparent"

                PageStack {
                    id: pageStack
                    anchors.fill: parent
                }
            }
        }

        StatusBar {
            Layout.fillWidth: true
            Layout.preferredHeight: 28
        }
    }

    Component.onCompleted: {
        // 首页是栈底。入口（2.14）只负责建窗与启动流程，路由从这里开始 ——
        // 深链/命令行参数在入口调 Nav.openDeepLink() 即可，那时栈已经有底了。
        if (Nav && Nav.depth === 0)
            Nav.goHome()
    }

    Connections {
        target: Nav

        // 导航失败必须**看得见**（"非法链接不许静默"）：状态条显示原因，
        // 10 秒后由 Shell 自动清空（A-09 的语义）。
        function onNavFailed(reason) {
            if (Shell)
                Shell.setStatus(reason, "error")
        }
    }

    // ── 启动画面（任务 2.14）────────────────────────────────────────
    // 单独的顶层窗口，显示/隐藏在 Python 侧决定（app/startup.py 的 4 条竞争退出路径）。
    Splash {}

    // ── 启动链条：协议 → 公告 → 预下载（A-21 / A-22 / A-23）──────────
    // 这两个弹窗由**启动流程**驱动（不是 2.13 的对话框宿主）：它们必须在主窗口还没
    // 显示、宿主还没握手之前就能弹出来。视图在 StartupDialogs.qml。
    StartupDialogs {
        anchors.fill: parent
        z: 200
    }

    // ── 对话框宿主与 Toast（任务 2.13）──────────────────────────────
    // 两者在 Component.onCompleted 里调 Dialogs.markReady() —— 这是 QtUIPort 从
    // "整体退化为 NullUIPort" 切到"真的把请求送到 QML"的那一步（见 dialogs/README.md）。
    DialogHost {
        anchors.fill: parent
        z: 100
    }

    ToastHost {
        anchors.fill: parent
        z: 90
    }

    // ── 三悬浮窗（任务 2.15）──────────────────────────────────────
    // 它们自己就是顶层原生 Window（不是本窗口的子项），在这里实例化只是为了随根组件
    // 一起创建/销毁；可见性由 Overlay 桥的语义位控制。
    MonitorOverlay {}

    DesktopLyricOverlay {}
}
