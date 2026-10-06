// FMCL QML 根窗口 —— 阶段 2 任务 2.12 的窗口骨架（对应 02 第三节的骨架图）。
//
// 结构（自上而下三层，与骨架图一一对应；返工 B 组把原来那两条顶栏合并成了一条）：
//
//   FluWindow                     窗口本体 + **定制 appBar**（shell/AppBar.qml）
//     └ AppBar                    唯一一条顶栏：返回 / 面包屑 / 全局搜索 / 通知 / 账号
//     └ RowLayout                 窗口按钮由 FluAppBar 自带，占右侧 120px
//         ├ Navigation            12 个一级导航项（+ 插件页），切换即 Nav.push(routeId)
//         └ PageStack             页面栈（Nav 的镜像，见 PageStack.qml 的说明）
//     └ StatusBar                 状态文本 / 进度 / 后台任务数
//
// 为什么用 FluWindow：阶段 0 第 11.2 节定的分工 —— "需要标题栏按钮与窗口路由的主窗口
// 用 FluWindow，悬浮窗一律用原生 Window"。`effect: "normal"` 是**必须显式写**的
// （红线 5：禁渐变/亚克力；闸门 R1 只认字面量，写变量它看不见）。
//
// 本文件里没有一处硬编码颜色（一律 Theme.*）、没有一个中文字面量（一律 Tr?.map[...]）、
// 没有一个 emoji（图标一律 FmIcon / Runtime.iconUrl → qml/assets/icons/*.svg）。
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

    // 主窗口的可见性由**启动流程**决定（任务 2.14；返工 A 组修掉 D-142）。
    //
    // 判据要**两段都判**：
    //   * `typeof Startup !== "undefined"` —— 挡住"完全没声明"（抛 ReferenceError）；
    //   * `&& Startup` —— 挡住"已声明但为 null"（引擎析构时上下文属性先被清空，
    //     绑定还排在求值队列里，这时 `Startup.startupActive` 会抛 TypeError）。
    // 只写 `typeof` 的话守卫测试立刻会红（我第一版就是这么写的，被
    // `test_no_qml_binding_errors_at_process_exit` 当场抓到）。
    //
    // `startupActive` 的语义（含"启动流程没开跑就别藏窗口"这一档）写在
    // app/startup.py 的属主文档里 —— 测试、探针、只装配半个引擎的场景全都靠它
    // 拿到"看得见的主窗口"，而不是靠 FluWindow 自己的 show() 兜底。
    readonly property bool startupActive: (typeof Startup !== "undefined" && Startup) ? Startup.startupActive : false
    visible: !app.startupActive

    // `autoVisible: false` 是**必须**的：FluWindow 在自己的 `Component.onCompleted` 里
    // 会无条件 `show()`，那一步会把上面这条绑定覆盖掉，于是主窗口在启动画面期间
    // 就露出来了（用户截图里"两层窗口叠在一起"就是这么来的）。
    autoVisible: !app.startupActive

    // 启动画面收起后把主窗口激活一次：启动画面是 `WindowStaysOnTopHint` 的置顶窗，
    // 它自己藏掉之后焦点不一定回到主窗口。
    //
    // 只调 `requestActivate()`，**不调 `raise()`** —— offscreen 平台不支持 raise()，
    // 会打一条 `QtWarningMsg: This plugin does not support raise()`，而 [P0] 冒烟测试
    // 把"未登记的 Qt 警告"当作失败（返工 A 组第一版就这么把冒烟测红了）。
    // 置顶窗藏掉之后本来也不缺"提到前面"这一步。
    onVisibleChanged: {
        if (visible)
            app.requestActivate()
    }

    // 红线 5 + 闸门 R1：材质必须显式关掉（"normal" = 纯色，阶段 0 第 2.4 节实测）。
    effect: "normal"

    // 标题走 Runtime（只读运行时信息），与窗口内置 appBar 的同一份文案。
    // 判空是必须的：引擎析构时上下文属性会先被清掉，而求值还排在队列里（2.2 实测的日志污染）。
    title: Runtime ? (Runtime.appName + " " + Runtime.appVersion) : ""

    // ── 唯一一条顶栏（返工 B 组）──────────────────────────────────
    // `FluWindow.appBar` 是可替换属性；换掉的代价是必须自己保留
    // `buttonMinimize / buttonMaximize / buttonClose` 与 `layoutStandardbuttons` ——
    // `FluFrameless` 强依赖它们（见 shell/AppBar.qml 的文件头），
    // 所以这里给的是 `FluAppBar` 的**子类**，而不是随便一个 Item。
    appBar: AppBar {
        id: appBar
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            Navigation {
                Layout.fillHeight: true
                Layout.preferredWidth: Theme?.navWidth ?? 208
            }

            // 页面区：底色比壳层深一档（`Theme.windowBg` = bg_dark），把页面从导航栏里"沉"下去
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: Theme?.windowBg ?? "transparent"

                PageStack {
                    id: pageStack
                    anchors.fill: parent
                }
            }
        }

        StatusBar {
            Layout.fillWidth: true
            Layout.preferredHeight: Theme?.statusBarHeight ?? 28
        }
    }

    Component.onCompleted: {
        // 首页是栈底。入口（2.14）只负责建窗与启动流程，路由从这里开始 ——
        // 深链/命令行参数在入口调 Nav.openDeepLink() 即可，那时栈已经有底了。
        if (Nav && Nav.depth === 0)
            Nav.goHome()

        // 顶栏上的可交互项必须登记进 FluFrameless 的命中测试白名单，否则点击会被系统
        // 当成"拖窗口"吃掉 —— QML 侧一个事件都收不到，且**不报错**（见 AppBar 的文件头）。
        var items = (appBar && appBar.interactiveItems) ? appBar.interactiveItems : []
        for (var i = 0; i < items.length; ++i) {
            if (items[i])
                app.setHitTestVisible(items[i])
        }
    }

    Connections {
        target: Nav

        // 导航失败必须**看得见**（"非法链接不许静默"）：状态条显示原因，
        // 10 秒后由 Shell 自动清空（A-09 的语义）。
        function onNavFailed(reason) {
            if (Shell)
                Shell.setStatus(reason, "error")
        }

        // ── 离开守卫（阶段 3 任务 3.4；M-Q1 的 B6）──────────────────────
        // 设置域里有未保存改动时跨域导航会被挡下（`app/bridges/nav_bridge.py`
        // 的 `setLeaveGuard`），这里弹**一次**确认框：
        //   * 「确定」= 丢弃草稿并离开 —— 先让设置桥还原预览，再 `confirmLeave(true)`；
        //   * 「取消」= 留下，`confirmLeave(false)` 取消那次导航。
        // 顺序不能反：先压栈再丢弃的话，用户会看到设置页一闪而过。
        function onLeaveBlocked(domain, routeId) {
            var count = (typeof Settings !== "undefined" && Settings) ? Settings.changeCount : 0
            var title = Tr?.map["settings_title"] ?? "settings_title"
            var message = (typeof Tr !== "undefined" && Tr)
                    ? Tr.tf("settings_unsaved_confirm", {"count": count})
                    : "settings_unsaved_confirm"
            dialogHost.showPrompt({"title": title, "message": message, "default": false})
        }
    }

    //: 壳层提示的答案（上面那个确认框）：只有设置域的离开守卫会用到它。
    Connections {
        target: dialogHost

        function onPromptAnswered(ok) {
            if (!ok) {
                if (Nav)
                    Nav.confirmLeave(false)
                return
            }
            if (typeof Settings !== "undefined" && Settings)
                Settings.discardDraft()
            if (Nav)
                Nav.confirmLeave(true)
        }
    }

    // ── 启动流程 → 界面（返工 A 组补上的接线）────────────────────────
    // 这两个信号在阶段 2 就发出去了，但**一直没有人接**：`Startup.statusChanged`
    // 只被 Splash 读（那是画面内的一条文案），`coreFailed` 压根没有消费者 ——
    // 于是"启动器初始化失败"这条路在 QML 界面上**完全看不见**（主窗口照常显示、
    // 状态条空空如也、也没有任何提示）。旧实现（main.py 的 `_show_init_error`）
    // 是"状态栏报错 + 弹错误框"，这里按同样的语义补齐，只是弹窗走 Toast：
    // 启动失败发生在主线程的轮询里，用模态对话框会自己等自己。
    Connections {
        target: (typeof Startup !== "undefined" && Startup) ? Startup : null

        function onStatusChanged(key, level) {
            if (!Shell)
                return
            // Python 侧给的是 **i18n 键名**（它不持有界面文案），翻译在 QML 侧做
            Shell.setStatus(Tr ? (Tr?.map[key] ?? key) : key, level)
        }

        function onCoreFailed(titleKey, message) {
            var title = Tr ? (Tr?.map[titleKey] ?? titleKey) : titleKey
            // 状态条显示**具体原因**（旧实现 `_show_init_error` 就是先 `set_status(错误文本)`），
            // 标题与原因一起进 Toast。这里**不做字符串拼接**：拼接就需要一个分隔符字面量，
            // 而闸门 R3 连全角冒号都算硬编码文本（第一版就是被它拦下的）—— 文案属于语言文件。
            if (Shell)
                Shell.setStatus(message, "error")
            if (typeof Dialogs !== "undefined" && Dialogs && Dialogs.available)
                Dialogs.notify({"level": "error", "message": title, "subtitle": message, "icon": "error"})
        }
    }

    // ── 首页（阶段 3 任务 3.1）──────────────────────────────────────
    // 首页桥把"游戏进程那边发生的事"转成信号发过来，这里负责**落到壳层**：
    // 状态条文案、窗口最小化、崩溃提示、公告重看。
    //
    // 为什么不让 `Home` 直接调 `Shell` / 自己弹 Toast：桥里已经有一份"哪个键翻成哪句话"
    // 的表（`home_bridge._status_text`），但"这句话显示在状态条上还是弹成 Toast"是
    // **壳层的决定**（状态条 10 秒自动清空、Toast 要排队）。分开之后首页桥不用知道壳层长什么样。
    Connections {
        target: (typeof Home !== "undefined" && Home) ? Home : null

        function onStatusMessage(text, level) {
            if (Shell)
                Shell.setStatus(text, level)
        }

        function onMinimizeWindowRequested() {
            // B-03 的最小化开关：游戏窗口出现后才最小化（旧实现 `self.iconify()`）
            app.showMinimized()
        }

        function onCrashDetected(exitCode, message) {
            if (Shell)
                Shell.setStatus(message, "error")
            if (typeof Dialogs !== "undefined" && Dialogs && Dialogs.available)
                Dialogs.notify({"level": "error", "message": message, "icon": "error"})
        }

        function onNoticeRequested() {
            if (typeof Startup !== "undefined" && Startup)
                Startup.replayNotice()
        }
    }

    // 版本页桥（3.2）只发状态条文案：游戏状态、崩溃提示、最小化仍然由**首页桥**发
    // （它是常驻的那个，见 `app/bridges/version_bridge.py` 模块文档第 1 条）——
    // 这里再挂一次 `onCrashDetected` 就会出现两个 Toast。
    Connections {
        target: (typeof Versions !== "undefined" && Versions) ? Versions : null

        function onStatusMessage(text, level) {
            if (Shell)
                Shell.setStatus(text, level)
        }
    }

    // 壳层的"查看公告"请求（顶栏铃铛）→ 启动流程重看公告。
    // 没有公告时给一句话：按钮点下去什么都不发生是最难排查的那种"坏"。
    Connections {
        target: (typeof Shell !== "undefined" && Shell) ? Shell : null

        function onNoticeRequested() {
            if (typeof Startup !== "undefined" && Startup && Startup.hasNotice) {
                Startup.replayNotice()
                return
            }
            if (Shell)
                Shell.setStatus(Tr?.map["notice_none"] ?? "notice_none", "info")
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
        id: dialogHost
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
