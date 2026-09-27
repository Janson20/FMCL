// 启动画面（阶段 2 任务 2.14；界面返工 A 组重做并修掉"加载完不消失"）。
//
// ## 时序语义**不在 QML 里**
//
// 由 Python 侧的 `StartupController`（app/startup.py）控制，它复刻了旧 main.py 的
// 4 条互相竞争的退出路径（≥1 秒显示 / 30 秒硬超时 / 初始化失败仍显示主窗口 /
// 关画面失败也继续）。QML 只负责"照 `Startup.startupActive` 显示"：
//
//   startupActive === true   → 本窗口显示（并且主窗口由 App.qml 藏起来）
//   startupActive === false  → 本窗口淡出后隐藏
//
// 为什么计时不放 QML：那属于"简化实现就会丢容错"的一类语义（风险 R-34），
// 放 Python 侧才能用注入的时钟快速确定地测出来。详见 app/startup.py 的模块文档。
//
// ## 返工 A 组修掉的缺陷（D-142）：加载完了窗口不消失
//
// 旧版本这里写的是 **`visible: true` 字面量**，而全工程**没有任何一行**把它关掉 ——
// Python 侧四条退出路径全都正常跑完（`dismissed` 已为真、主窗口也显示了），
// 那个顶层窗口却一直盖在所有东西上面。用户看到的就是"加载窗加载完后没消失"。
//
// 两个教训写进测试里，不再靠"记得写"：
//
//   * 可见性**必须是绑定**，而且绑定到 `Startup.startupActive`（`tests/qml_startup_probe.py`
//     真的跑一遍启动流程，断言收尾后本窗口不可见、主窗口可见）；
//   * `Startup` 完全缺席时**默认不显示**（`?? false`）—— 没有控制器就没有人关它，
//     宁可没有启动画面，也不能再留一个关不掉的窗口。
//
// 约束（闸门 R1/R3/R4/R8）：无渐变、无 emoji、无中文字面量、无颜色字面量。

import QtQuick
import QtQuick.Layouts
import "components"

Window {
    id: splash
    objectName: "splashWindow"

    //: 启动流程是否占屏。`typeof` 挡"完全没声明"（抛 ReferenceError），
    //: 后面的 `&& Startup` 挡"已声明但为 null"（引擎析构时上下文属性先被清空、
    //: 绑定还排在求值队列里，这时 `Startup.startupActive` 会抛 TypeError —— 2.2 实测）。
    readonly property bool active: (typeof Startup !== "undefined" && Startup) ? Startup.startupActive : false
    //: 淡出进行中：这段时间里窗口还得可见，否则动画会被自己掐掉
    property bool fading: false

    width: 420
    height: 236
    flags: Qt.SplashScreen | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
    // 圆角要能透出桌面：窗口本身全透明，视觉全在 card 上。
    // （R8 只管 `#hex` 与 `Qt.rgba`，命名色 `transparent` 是允许的。）
    color: "transparent"

    // **必须显式切断 transientParent**（返工 A 组的实测结论，`poc/_probe_splash_window_visibility.py`）：
    //
    // 声明在另一个 Window 内部的 Window 会自动把外层窗口设成 `transientParent`，
    // 而 Qt **不允许 transient 子窗口在父窗口不可见时显示** —— 那一步会把 QML 侧的
    // `visible` 直接压成 false（实测：父窗口藏着时子窗口 `visible=false` / `visibility=0`，
    // 把父窗口 show 出来之后子窗口自己就变可见了）。
    //
    // 也就是说：既然返工 A 组让"启动画面占屏期间主窗口藏起来"（缺陷 D-142），
    // 这个子窗口就会被连带藏掉 —— 用户看到的会是"启动时什么都没有"，
    // 比原来那个关不掉的加载窗更糟。显式置 null 之后实测：
    // 父窗口不可见时本窗口照样 `isVisible=true`（visibility=2），父窗口后来显示也不影响它。
    transientParent: null
    visible: active || fading

    // 居中到主屏（阶段 0 第 12.5 节的教训：多屏时要用"窗口所在的 QScreen"，
    // 但启动画面此刻还没有归属屏幕，取主屏是旧实现的等价行为）
    //
    // 判空是必须的：**offscreen 平台上 `screen.geometry` 可能是 undefined**
    // （实测报 `Cannot read property 'x' of undefined`），而那是加载期错误、
    // 会污染"日志里有没有 QML 报错"这条判据。
    function centerOnPrimary() {
        var screen = splash.screen
        if (!screen || !screen.geometry)
            return
        splash.x = Math.round(screen.geometry.x + (screen.geometry.width - splash.width) / 2)
        splash.y = Math.round(screen.geometry.y + (screen.geometry.height - splash.height) / 2)
    }

    onActiveChanged: {
        if (active) {
            fading = false
            return
        }
        // 收尾：先淡出，再把窗口藏掉。`Theme` 缺席时退回一个固定时长，
        // 但**一定要**有定时器兜底 —— 卡在"动画没跑完所以窗口不关"就又回到老 bug 了。
        fading = true
        hideTimer.restart()
    }

    Timer {
        id: hideTimer
        // 比淡出动画多留一点余量；`Theme` 缺席时用 300ms 的等价默认值
        interval: (Theme ? Theme.durationSlow : 300) + 80
        onTriggered: splash.fading = false
    }

    Rectangle {
        id: card
        objectName: "splashCard"
        anchors.fill: parent
        radius: (Theme?.radiusLg ?? 12) + 4
        color: Theme?.overlayBg ?? "transparent"
        border.width: 1
        border.color: Theme?.divider ?? "transparent"
        // 淡出只改内容的不透明度：窗口自身的 opacity 在部分平台上不生效，
        // 而窗口底色本来就是透明的，所以效果等价且可移植。
        opacity: splash.active ? 1 : 0
        Behavior on opacity {
            NumberAnimation {
                duration: Theme?.durationSlow ?? 300
                easing.type: Easing.OutCubic
            }
        }

        ColumnLayout {
            anchors.centerIn: parent
            spacing: Theme?.spacingMd ?? 0

            // 品牌块：强调色底 + 一个图标（图标名来自 qml/assets/icons，闸门 R2：不许 emoji）
            Rectangle {
                Layout.alignment: Qt.AlignHCenter
                width: 44
                height: 44
                radius: Theme?.radiusLg ?? 0
                color: Theme?.accentSoft ?? "transparent"

                FmIcon {
                    objectName: "splashIcon"
                    anchors.centerIn: parent
                    name: "package"
                    color: Theme?.accent ?? "transparent"
                    size: 24
                }
            }

            Text {
                objectName: "splashTitle"
                Layout.alignment: Qt.AlignHCenter
                text: (typeof Runtime !== "undefined" && Runtime)
                      ? (Runtime.appName + " " + Runtime.appVersion) : ""
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeTitle ?? 18
                font.bold: true
            }

            FmProgressRing {
                objectName: "splashRing"
                Layout.alignment: Qt.AlignHCenter
                Layout.preferredWidth: 26
                Layout.preferredHeight: 26
                indeterminate: true
                lineWidth: 3
                visible: splash.active
            }

            // 状态文本：`Startup.statusText` 给的是 **i18n 键名**（不是已翻译的中文）——
            // 文案永远在语言文件里，Python 侧不持有界面文案。
            // 缺键时回退显示键名（与旧 `_()` 行为一致）。
            Text {
                objectName: "splashStatusText"
                Layout.alignment: Qt.AlignHCenter
                text: {
                    // 判据用 `typeof`：`Startup` 完全没声明时 `Startup ? …` 会抛
                    // `ReferenceError`（只有"已声明但为 null"才适用 `?.` / `??`）。
                    var key = (typeof Startup !== "undefined" && Startup) ? Startup.statusText : ""
                    if (!key)
                        return ""
                    return Tr ? (Tr?.map[key] ?? key) : key
                }
                color: Theme?.textTertiary ?? "transparent"
                font.pixelSize: Theme?.fontSizeBase ?? 12
            }
        }
    }

    Component.onCompleted: centerOnPrimary()
}
