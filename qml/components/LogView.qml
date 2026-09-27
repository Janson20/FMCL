// LogView.qml —— 通用日志面板 / 控制台组件（阶段 2 任务 2.18；对照表 A-13 的
// "实时捕获 + 清空"，以及 02 第 4.4 节服务器控制台 / 第 3.10 节控制台的同一条需求）。
//
// ## 为什么是 ListView + ListModel，而不是一个不断增长的 Text
//
// 旧实现用的是 `ctk.CTkTextbox`（一个 Tk 文本控件）+ 每次追加都整体裁剪，
// 阶段 1.22（D-86）踩到的正是"四个日志框只 append 从不删除"。
// QML 侧若写成 `Text { text: text + line + "\n" }`，问题会更严重：字符串每追加一行
// 都要整份重新分配，而且**文本框会重新排版全文** —— 安装/下载/服务器控制台这类
// 每秒上千行的场景下，界面会直接卡死。
// 这里用 `ListModel` + `ListView`：视图只实例化**可见的那十几行**（虚拟化），
// 追加一行是 O(1) 的模型插入；行数上限由本组件自己管（见 `maxLines` / `trimChunk`）。
//
// ## 行数上限：有界，但不是"每追加一行就整体搬一次"
//
// `ListModel.remove(0, n)` 的代价与**剩余行数**成正比（要把后面的元素整体前移）。
// 追加到上限之后再一行一行地删，等于每行都做一次 O(5000) 的整体搬移 —— 那是
// 把旧实现的坑换了个地方踩。所以裁剪分两级：
//
// 1. **按块裁**：行数达到 `maxLines + trimChunk` 时**一次性**丢掉 `trimChunk` 行，
//    行数正好回到 `maxLines`。摊薄之后每行的裁剪成本是 O(1) 量级；
// 2. **收尾对齐**：一轮爆发结束后（事件循环空闲时，`Qt.callLater`）把多出来的
//    那点尾巴补裁到**恰好** `maxLines` 行。
//
// 于是内存的硬上界是 `maxLines + trimChunk` 行，而"安静下来之后"的
// `lineCount` 恰好是 `maxLines`。
//
// ## 实测（数据来自任务 2.19 的冒烟测试；`tests/_smoke_driver.py` 每次跑都会重测一遍，
//    并断言"瞬态峰值 ≤ maxLines + trimChunk - 1"与"安静后恰好 maxLines"）
//
// 环境：Windows 10 / offscreen / Qt 6.7.3 / Python 3.11.14（多次运行的实测范围）：
//
// | 场景 | 实测 |
// |------|------|
// | 逐行 `append`（同步循环，不含渲染与事件循环） | 4000 行 79~99 ms ≈ **4~6 万行/秒** |
// | 每 100 行让出一次事件循环（带渲染 + 自动滚到底） | 2000 行 253~484 ms ≈ **4000~7900 行/秒** |
// | 一次 `appendLines(5000 行)` | 约 56 ms |
// | `copyAll()`（200 行 → 剪贴板） | 约 63 ms |
// | 上限（`maxLines=200` / `trimChunk=32`，连追 1000 行） | 瞬态峰值 **231 = 200+32-1**，安静后**恰好 200** |
//
// 任务书点名的"每秒 2000 行"落在第二行那种场景里：此时事件循环最长一次迟滞只有
// 5~18 ms（界面仍然可交互），行数上界在单行与批量两条路径上都成立。
//
// ## 自动滚到底：用户手动上滚之后不许抢
//
// `autoScroll` 为真时，新行到达会滚到底；用户一旦往上滚（`movementEnded` 时不在
// 底部），组件自动把 `autoScroll` 置假并**不再抢滚动位置** —— 正在翻旧日志的人
// 最烦的就是每来一行就被拽回底部。点工具栏最右边那个"到底部"按钮可以立刻
// 回到末尾并重新打开自动滚动。
//
// ## 与 Python 侧的关系（契约第三节红线）
//
// 本组件**不读文件、不碰服务**：日志内容由调用方喂进来。
// `append(line, level)` / `appendLines(list)` / `clear()` / `copyAll()` 都是
// QML 函数（在元对象里就是可调用方法），Python 侧可以：
//   * 经桥发信号 → QML 侧 `Connections` 里调用（契约决策 10 的推荐路径）；
//   * 或在拿得到 `QQuickItem` 时用 `QMetaObject.invokeMethod(item, "append", …)`。
// 两条路都在 `tests/_smoke_driver.py` 里实测过（见该文件里的 LogView 小结）。
//
// ## 没有硬编码
//
// 颜色一律 `Theme.*`（闸门 R8）、文案一律 `Tr.map[…]`（R3/R4 与契约 §5.2）、
// 图标一律 `Runtime.iconUrl(…)`（R2：不许 emoji）。

import QtQuick
import QtQuick.Controls

Item {
    id: root
    objectName: "logView"

    // ─── 对外属性 ───────────────────────────────────────────────

    //: 保留的最大行数（默认与旧实现 `ui/log_widget.py:LOG_VIEW_MAX_LINES` 对齐）。
    //: 传 <= 0 会被夹到 1：**本组件永不无上限** —— "无上限日志缓冲"正是 D-86 那个坑。
    property int maxLines: 5000

    //: 按块裁剪的块大小（见文件头的说明）。内存硬上界 = maxLines + trimChunk 行。
    property int trimChunk: 512

    //: 已写入的行数（只读语义；写入请走 append / appendLines / clear）。
    property int lineCount: 0

    //: 是否跟随最新一行自动滚到底（用户手动上滚会把它置假，见文件头）。
    property bool autoScroll: true

    //: 搜索关键字：非空时把命中处高亮（**不做正则**，只做大小写不敏感的子串匹配）。
    property string searchText: ""

    //: 面板标题（空则不显示）。文案由调用方给，组件不内置中文。
    property string title: ""

    //: 一行都没有时的提示文案（空则不显示文案，只显示图标）。
    property string emptyText: ""

    //: 是否显示工具栏（详情页嵌入式的小控制台可以关掉它）。
    property bool showToolbar: true

    //: 命中位置离行首超过这么多字符时，前缀从左截断，保证高亮留在可见区域内。
    property int maxPrefixChars: 120

    //: 行号列（诊断用；日志行数不多时可读性更好）
    property bool showLineNumbers: false

    //: 清空完成 / 复制完成。`copied` 带的是被复制的行数。
    signal cleared()
    signal copied(int lineCount)

    // ─── 设计令牌派生（Theme 缺席时全部退化为中性值，不留硬编码颜色） ──

    readonly property string monoFamily: Qt.platform.os === "windows"
                                         ? "Consolas"
                                         : (Qt.platform.os === "macos" ? "Menlo" : "DejaVu Sans Mono")
    readonly property int lineFontSize: Theme?.fontSizeSmall ?? 11
    readonly property int rowHeight: Math.ceil(rowMetrics.height) + 2
    readonly property int effectiveMaxLines: Math.max(1, maxLines)

    function levelColor(level) {
        if (level === "success")
            return Theme?.success ?? "transparent"
        if (level === "warning")
            return Theme?.warning ?? "transparent"
        if (level === "error")
            return Theme?.error ?? "transparent"
        return Theme?.textSecondary ?? "transparent"
    }

    //: 级别归一到 info / success / warning / error（未知级别降级为 info，不报错）。
    function normalizeLevel(level) {
        var text = (level === undefined || level === null) ? "info" : String(level).toLowerCase()
        if (text === "success" || text === "warning" || text === "error" || text === "info")
            return text
        return "info"
    }

    // ─── 追加 / 裁剪 ─────────────────────────────────────────────

    function pushLine(line, level) {
        var text = (line === undefined || line === null) ? "" : String(line)
        logModel.append({ "lineText": text, "lineLevel": root.normalizeLevel(level) })
    }

    function enforceCap() {
        var cap = root.effectiveMaxLines
        var chunk = Math.min(Math.max(1, root.trimChunk), cap)
        if (logModel.count >= cap + chunk)
            logModel.remove(0, chunk)          // 整块丢掉：摊薄之后每行 O(1)
        if (logModel.count > cap)
            scheduleTrim()                     // 收尾：空闲时对齐到恰好 cap 行
    }

    function scheduleTrim() {
        if (trimPending)
            return
        trimPending = true
        Qt.callLater(applyTrim)
    }

    function applyTrim() {
        trimPending = false
        var over = logModel.count - root.effectiveMaxLines
        if (over > 0)
            logModel.remove(0, over)
    }

    function append(line, level) {
        pushLine(line, level)
        enforceCap()
        if (autoScroll)
            scheduleScrollToEnd()
    }

    function appendLines(lines) {
        if (!lines || lines.length === undefined)
            return
        // 批量路径也要**边追加边看上限**：整批追加完再裁的话，瞬态行数会多出一个
        // "批量长度"（实测：一次 5000 行的批量让 5000 行的面板冲到 6489 行）。
        // 这里每行只多一次比较，代价可忽略，而"内存硬上界 = maxLines + trimChunk"
        // 在单行与批量两条路径上都成立。
        var cap = root.effectiveMaxLines
        var chunk = Math.min(Math.max(1, root.trimChunk), cap)
        for (var i = 0; i < lines.length; i++) {
            var entry = lines[i]
            if (entry !== null && typeof entry === "object")
                pushLine(entry.text, entry.level)
            else
                pushLine(entry, "info")
            if (logModel.count >= cap + chunk)
                enforceCap()
        }
        enforceCap()
        if (autoScroll)
            scheduleScrollToEnd()
    }

    function clear() {
        logModel.clear()
        if (autoScroll)
            scheduleScrollToEnd()
        root.cleared()
    }

    // ─── 读取 / 复制 ─────────────────────────────────────────────

    //: 第 index 行的 `{text, level}`（越界返回空表）—— 给测试与命令式调用用。
    function lineAt(index) {
        if (index < 0 || index >= logModel.count)
            return {}
        var entry = logModel.get(index)
        return { "text": String(entry.lineText), "level": String(entry.lineLevel) }
    }

    //: 全部行的纯文本（换行拼接）。行数受 `maxLines` 限制，所以它是**有界**的。
    function plainText() {
        var parts = []
        for (var i = 0; i < logModel.count; i++)
            parts.push(String(logModel.get(i).lineText))
        return parts.join("\n")
    }

    //: 复制全部到剪贴板（剪贴板接口由 Qt 提供，不需要 Python 侧参与）。
    function copyAll() {
        clip.text = plainText()
        clip.selectAll()
        clip.copy()
        root.copied(logModel.count)
    }

    // ─── 滚动 ───────────────────────────────────────────────────

    function scheduleScrollToEnd() {
        if (scrollPending)
            return
        scrollPending = true
        Qt.callLater(doScrollToEnd)
    }

    function doScrollToEnd() {
        scrollPending = false
        if (autoScroll)
            view.positionViewAtEnd()
    }

    //: 立刻回到末尾并重新打开自动滚动（工具栏按钮与 Python 侧都可用）。
    function scrollToEnd() {
        autoScroll = true
        view.positionViewAtEnd()
    }

    //: 命中位置（大小写不敏感的子串查找）；没有关键字或没命中返回 -1。
    function hitIndex(text) {
        var needle = root.searchText
        if (!needle || needle.length === 0)
            return -1
        return String(text).toLowerCase().indexOf(needle.toLowerCase())
    }

    // 只在本组件内部用的两个"待办"标志。写成 QML 属性而不是普通 JS 变量：
    // 根对象体里没有"可变局部变量"这种东西，`var` 声明出来的名字不能被后面的函数改。
    property bool trimPending: false
    property bool scrollPending: false

    // 隐藏的剪贴板载体（QML 里复制文本的标准做法：选中 + copy()）
    TextEdit {
        id: clip
        visible: false
        width: 1
        height: 1
    }

    //: 行高/字宽的度量（行高必须跟着字号走，写死会在中文字体下裁掉字的下缘）
    TextMetrics {
        id: rowMetrics
        font.family: root.monoFamily
        font.pixelSize: root.lineFontSize
        text: "Ag"
    }

    ListModel {
        id: logModel
        onCountChanged: root.lineCount = count
    }

    Column {
        anchors.fill: parent
        spacing: 0

        // ─── 工具栏 ─────────────────────────────────────────────
        Rectangle {
            id: toolbar
            objectName: "logToolbar"
            visible: root.showToolbar
            width: parent.width
            height: root.showToolbar ? Math.max(28, (Theme?.iconSize ?? 16) + (Theme?.spacingSm ?? 4) * 2) : 0
            color: Theme?.bgMedium ?? "transparent"
            border.width: 0

            Row {
                anchors.fill: parent
                anchors.leftMargin: Theme?.spacingSm ?? 4
                anchors.rightMargin: Theme?.spacingSm ?? 4
                spacing: Theme?.spacingSm ?? 4

                Text {
                    objectName: "logTitleText"
                    anchors.verticalCenter: parent.verticalCenter
                    visible: root.title.length > 0
                    text: root.title
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                    font.bold: true
                }

                // 搜索框：返工 C 组换成自研件（闸门 R9 禁原生控件 —— 原生输入框在 Basic
                // 样式下是浅色的，锁深色的界面里必然突兀）。`search` 是既有 i18n 键，
                // 不新造前缀；`onTextChanged` 能直接用，因为 FmSearchField 的根节点就是
                // TextField（`text` / `placeholderText` / 自带信号都转发得到）。
                FmSearchField {
                    id: searchField
                    objectName: "logSearchField"
                    anchors.verticalCenter: parent.verticalCenter
                    width: Math.max(120, Math.min(240, root.width / 4))
                    height: Math.max(24, (Theme?.iconSize ?? 16) + (Theme?.spacingSm ?? 4))
                    placeholderText: (typeof Tr !== "undefined" && Tr)
                                     ? (Tr.map["search"] ?? "search") : "search"
                    text: root.searchText
                    onTextChanged: root.searchText = text
                }

                // 行数：技术串（不是用户文案），与占位页的 "route: …" 同一性质，不走 i18n
                Text {
                    objectName: "logCountText"
                    anchors.verticalCenter: parent.verticalCenter
                    text: root.lineCount + " / " + root.effectiveMaxLines
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 11
                }

                // 清空：图标 + 文字 → FmButton（自研件里"有文字的按钮"就是它）
                FmButton {
                    objectName: "logClearButton"
                    anchors.verticalCenter: parent.verticalCenter
                    primary: false
                    iconName: "trash"
                    text: (typeof Tr !== "undefined" && Tr)
                          ? (Tr.map["clear_log"] ?? "clear_log") : "clear_log"
                    onClicked: root.clear()
                }

                // 复制全部：**只给图标**。既有 1529 个键里没有"复制全部"这一条
                // （copy_link 的语义是复制链接），而本任务不允许改 ui/locales/*.json，
                // 所以这里不放可能误导的文案，语义交给 copy.svg 图标表达。
                // 键落地之后补 ToolTip 即可（登记在 COMPONENTS.md 的待办里）。
                FmToolButton {
                    objectName: "logCopyButton"
                    anchors.verticalCenter: parent.verticalCenter
                    iconName: "copy"
                    onClicked: root.copyAll()
                }

                // 到底部（同时重新打开自动滚动）；图标颜色即 autoScroll 的状态指示
                FmToolButton {
                    objectName: "logBottomButton"
                    anchors.verticalCenter: parent.verticalCenter
                    iconName: "chevron-down"
                    // 原来用 `opacity: 0.45` 表示"自动滚动已关" —— 换成令牌色表达同一件事，
                    // 顺带不再让图标因为透明度而看不清（且颜色只来自 Theme.*，闸门 R8）
                    iconColor: root.autoScroll ? Theme?.textPrimary ?? "transparent"
                                               : Theme?.textTertiary ?? "transparent"
                    onClicked: root.scrollToEnd()
                }
            }
        }

        // ─── 日志本体 ───────────────────────────────────────────
        Rectangle {
            id: body
            width: parent.width
            height: parent.height - toolbar.height
            color: Theme?.bgDark ?? "transparent"
            border.width: 1
            border.color: Theme?.cardBorder ?? "transparent"

            ListView {
                id: view
                objectName: "logList"
                anchors.fill: parent
                anchors.margins: 1
                clip: true
                model: logModel
                // 等宽字体 + 定高行：追加时不重新排版（这是"每秒上千行"能跑住的关键）
                spacing: 0
                boundsBehavior: Flickable.StopAtBounds
                // 返工 C 组：原来的 `ScrollBar {}`（原生 Basic 样式 = 浅色亮条）换成自研件。
                // 附着属性名 `ScrollBar.vertical` 来自 Qt，改不了；换的是"值"这个实例。
                ScrollBar.vertical: FmScrollBar {}

                //: 用户自己滚动了：在底部就继续跟随，不在底部就**不抢**
                onMovementEnded: root.autoScroll = view.atYEnd

                delegate: Item {
                    id: rowItem
                    objectName: "logLine"
                    required property int index
                    required property string lineText
                    required property string lineLevel

                    width: view.width
                    height: root.rowHeight

                    readonly property int hitStart: root.hitIndex(lineText)
                    readonly property bool hasHit: hitStart >= 0
                    readonly property int prefixCut: (hasHit && hitStart > root.maxPrefixChars)
                                                      ? hitStart - root.maxPrefixChars : 0
                    readonly property string prefixText: hasHit
                        ? (prefixCut > 0 ? "..." + lineText.substring(prefixCut, hitStart)
                                         : lineText.substring(0, hitStart))
                        : lineText
                    readonly property string hitText: hasHit ? lineText.substr(hitStart, root.searchText.length) : ""
                    readonly property string suffixText: hasHit
                        ? lineText.substring(hitStart + root.searchText.length) : ""

                    // 行号列（可选）
                    Text {
                        id: numberText
                        visible: root.showLineNumbers
                        width: visible ? Math.ceil(rowMetrics.width * 5) : 0
                        text: visible ? String(rowItem.index + 1) : ""
                        color: Theme?.textSecondary ?? "transparent"
                        font.family: root.monoFamily
                        font.pixelSize: root.lineFontSize
                        elide: Text.ElideRight
                    }

                    // 没命中：整行一个 Text（绝大多数行都是这条路径，最省）
                    Text {
                        objectName: "logLineText"
                        x: numberText.width
                        visible: !rowItem.hasHit
                        width: Math.max(0, rowItem.width - numberText.width)
                        text: rowItem.lineText
                        color: root.levelColor(rowItem.lineLevel)
                        font.family: root.monoFamily
                        font.pixelSize: root.lineFontSize
                        elide: Text.ElideRight
                    }

                    // 命中：前缀 + 高亮块 + 后缀。**不用 StyledText/HTML** ——
                    // 日志行里满是 < & " 这类字符，拼 HTML 迟早出错（契约决策 6 的教训）。
                    Text {
                        id: preText
                        x: numberText.width
                        visible: rowItem.hasHit
                        text: rowItem.prefixText
                        color: root.levelColor(rowItem.lineLevel)
                        font.family: root.monoFamily
                        font.pixelSize: root.lineFontSize
                    }

                    Rectangle {
                        id: hitBox
                        objectName: "logHitBox"
                        visible: rowItem.hasHit
                        x: preText.x + preText.implicitWidth
                        width: hitText.implicitWidth
                        height: rowItem.height
                        color: Theme?.accent ?? "transparent"

                        Text {
                            id: hitText
                            anchors.fill: parent
                            text: rowItem.hitText
                            color: Theme?.bgDark ?? "transparent"
                            font.family: root.monoFamily
                            font.pixelSize: root.lineFontSize
                        }
                    }

                    Text {
                        objectName: "logLineSuffix"
                        visible: rowItem.hasHit
                        x: hitBox.x + hitBox.width
                        width: Math.max(0, rowItem.width - x)
                        text: rowItem.suffixText
                        color: root.levelColor(rowItem.lineLevel)
                        font.family: root.monoFamily
                        font.pixelSize: root.lineFontSize
                        elide: Text.ElideRight
                    }
                }
            }

            // 空状态：一行都没有时给个图标（文案由调用方给，组件不内置中文）
            Column {
                objectName: "logEmptyState"
                anchors.centerIn: parent
                spacing: Theme?.spacingXs ?? 2
                visible: logModel.count === 0

                Image {
                    objectName: "logEmptyIcon"
                    anchors.horizontalCenter: parent.horizontalCenter
                    source: (typeof Runtime !== "undefined" && Runtime)
                            ? (Runtime?.iconUrl("note") ?? "") : ""
                    sourceSize.width: (Theme?.iconSize ?? 16) * 2
                    sourceSize.height: (Theme?.iconSize ?? 16) * 2
                    width: (Theme?.iconSize ?? 16) * 2
                    height: (Theme?.iconSize ?? 16) * 2
                }

                Text {
                    objectName: "logEmptyText"
                    anchors.horizontalCenter: parent.horizontalCenter
                    visible: root.emptyText.length > 0
                    text: root.emptyText
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                }
            }
        }
    }
}
