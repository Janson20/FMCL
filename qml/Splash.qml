// 启动画面（阶段 2 任务 2.14）。
//
// 时序语义**不在 QML 里**：由 Python 侧的 `StartupController`（app/startup.py）控制，
// 它复刻了旧 main.py 的 4 条互相竞争的退出路径（≥1 秒显示 / 30 秒硬超时 /
// 初始化失败仍显示主窗口 / 关画面失败也继续）。QML 只负责"照 phase 显示"：
//
//   Startup.phase === "splash"  → 显示本窗口、隐藏主窗口
//   其它                        → 反过来
//
// 为什么计时不放 QML：那属于"简化实现就会丢容错"的一类语义（风险 R-34），
// 放 Python 侧才能用注入的时钟快速确定地测出来。详见 app/startup.py 的模块文档。
//
// 约束（闸门 R1/R3/R4/R8）：无渐变、无 emoji、无中文字面量、无颜色字面量。

import QtQuick
import QtQuick.Controls

Window {
    id: splash
    objectName: "splashWindow"

    width: 420
    height: 260
    visible: true
    flags: Qt.SplashScreen | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
    color: Theme?.bgDark ?? "transparent"

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

    Column {
        anchors.centerIn: parent
        spacing: Theme?.spacingLg ?? 20

        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: Runtime ? (Runtime.appName + " " + Runtime.appVersion) : ""
            color: Theme?.textPrimary ?? "transparent"
            font.pixelSize: Theme?.fontSizeTitle ?? 18
            font.bold: true
        }

        BusyIndicator {
            anchors.horizontalCenter: parent.horizontalCenter
            running: true
            width: 42
            height: 42
        }

        // 状态文本：`Startup.statusText` 给的是 **i18n 键名**（不是已翻译的中文）——
        // 文案永远在语言文件里，Python 侧不持有界面文案。
        // 缺键时回退显示键名（与旧 `_()` 行为一致）。
        Text {
            objectName: "splashStatusText"
            anchors.horizontalCenter: parent.horizontalCenter
            text: {
                // 判据用 `typeof`：`Startup` 完全没声明时 `Startup ? …` 会抛
                // `ReferenceError`（只有"已声明但为 null"才适用 `?.` / `??`）。
                var key = (typeof Startup !== "undefined" && Startup) ? Startup.statusText : ""
                if (!key)
                    return ""
                return Tr ? (Tr?.map[key] ?? key) : key
            }
            color: Theme?.textSecondary ?? "transparent"
            font.pixelSize: Theme?.fontSizeBase ?? 12
        }
    }

    Component.onCompleted: centerOnPrimary()
}
