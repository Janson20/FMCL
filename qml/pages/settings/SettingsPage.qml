// SettingsPage.qml —— 设置域。
//
// ## 一、一页承载 10 条二级路由
//
// 路由表（`app/bridges/nav_bridge.py`）把设置拆成 10 条二级路由，全部指向本文件
//（另有 `settings` 根路由，等同 `settings/launcher`）：`settings/launcher`、`settings/dirs`、
// `settings/java`、`settings/theme`、`settings/music`、`settings/log`、`settings/about`、
// `settings/account`、`settings/ai`、`settings/plugin`。
// 本文件按 `routeId` 切左侧分区导航 + 右侧内容（`Loader`），**一屏一个分区**。
// 分区清单**从 `Nav.routes()` 现取**（`parent === "settings"`），不写死在 QML 里 ——
// 路由表加一条，这里就多一个分区，不会出现"路由进得去但导航里看不见"。
//
// 分区：启动器 / 游戏目录 / Java / 主题 / 音乐 / 日志 / 关于 / 账户 / AI / 插件。
//
// ## 二、草稿范式
//
// 控件改动**只进草稿**（`Settings.setField`）；底部动作条上的「保存」才写盘、
// 「取消」丢弃并把预览还原成已保存值。主题/强调色/语言三项在改动瞬间**只改内存**
// 预览（服务层做，桥负责刷新 `Theme` / `Tr` 的只读快照）—— 用户点保存之前就看得见
// 效果，取消之后效果又退回去。带着未保存改动离开设置域时，由壳层的"离开守卫"
// 提示一次（`qml/App.qml` 的 `onLeaveBlocked`；守卫登记在 `SettingsBridge` 里）。
//
// ## 三、写法纪律（踩过的坑，这里写死）
//
// 1. **绑定里不出现 `page.*`**：页面被销毁时（切走路由 → `StackView` 弹栈）绑定会再
//    求值一次，而 id 作用域已经变成 null —— 报的是 `page.t()` / `page.bridgeAvailable`，
//    表现为"TypeError 刷满日志"。所以：
//    * **静态文案**用 `Tr?.map[…] ?? "键"` 直接绑（语言切换自动重算）；
//    * **动态状态**（开关、下拉、滑块、列表、按钮可用性、计数）一律在函数里赋值，
//      函数体第一行判活（`if (!page) return`）。
// 2. **分区内容各管各的状态**：分区是 `Loader` + 内联 `Component`，组件内部的 id
//    在外层作用域**看不见**（QML 的 id 作用域规则），所以每个分区自己订阅桥的信号
//    并在 `refresh()` 里把值推给控件 —— 页面只负责"哪个分区 + 动作条"。
// 3. **带占位符的句子走 `format()`**（QML 没有 i18n 的占位符替换），只在函数里调。
//
// 纪律：颜色只来自 `Theme.*`（R8）、图标走 `FmIcon`/`FmToolButton`（R2）、
// 文案走 `Tr.map[…]`（R3/R4）、只用 `qml/components` 里的 `Fm*`（R9）。

import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import "../../components"
import "../../components/agent"
import "../../components/dialogs"
import "../../components/settings"

FmPage {
    id: page
    objectName: "settingsPage"

    //: 桥缺席（注册失败）时整页走错误态，而不是绑一堆 `undefined` 报错
    readonly property bool live: (typeof Settings !== "undefined" && Settings) ? true : false

    //: 当前分区（`settings/theme` → `theme`；无子路径的 `settings` → `launcher`，
    //: 即把启动器设置排在第一个）。
    //:
    //: **刻意不做成绑定**：`PageStack.pushFrame()` 是
    //: `createObject()` 之后再用 `StackView.push(item, {routeId: …})` 写属性的，
    //: 那一刻 `routeIdChanged` 处理器**看得到新值**，
    //: 而依赖它的绑定却还是旧值 —— 于是页面停在启动器分区、主题分区永远不加载。
    //: 所以这里由 `refreshUi()` 现算现赋（与"动态状态一律在函数里赋值"同一条纪律）。
    property string section: "launcher"

    //: 显示顺序：按常用程度排列（启动器与游戏目录在前），清单之外的 id 追加在末尾 ——
    //: 路由表加新分区时不会漏，只是排在后面。
    readonly property var sectionOrder: ["launcher", "dirs", "java", "theme", "music", "log",
                                          "about", "account", "ai", "plugin"]

    // ── 页面状态（全部在 `refreshUi()` 里赋值）────────────────
    property var sections: []
    property bool dirty: false
    property int changeCount: 0

    contentState: page.live ? "ready" : "error"
    errorText: Tr?.map["plugin_state_error"] ?? "plugin_state_error"
    errorDetail: Tr?.map["settings_service_missing"] ?? "settings_service_missing"
    retryText: Tr?.map["version_refresh"] ?? "version_refresh"

    onRetried: page.refreshUi()

    // ── 文案工具 ──────────────────────────────────────────────

    function t(key) {
        return Tr ? (Tr.map[key] ?? key) : key
    }

    //: 取文案 + 填占位符（`{count}` / `{path}` 这种）。只在函数里调，不在绑定里调。
    function format(key, params) {
        if (!key)
            return ""
        var text = page.t(key)
        if (!params)
            return text
        for (var name in params)
            text = text.replace("{" + name + "}", String(params[name]))
        return text
    }

    function indexOfValue(list, key, value) {
        if (!list)
            return -1
        for (var i = 0; i < list.length; i++) {
            if (String(list[i][key]) === String(value))
                return i
        }
        return -1
    }

    // ── 分区清单（从路由表现取）──────────────────────────────

    function buildSections() {
        var out = []
        if (typeof Nav === "undefined" || !Nav)
            return out
        var routes = Nav.routes()
        var byId = ({})
        for (var i = 0; i < routes.length; i++) {
            var route = routes[i]
            if (String(route.parent) !== "settings")
                continue
            //: `dev/gallery` 也挂在 `settings` 下（路由表注释写了：那是"进得去但不占
            //: 一级导航位"的开发自查页）。它**不是**设置分区，这里必须排除，
            //: 否则设置页左侧会多出一个"组件画廊"。
            if (String(route.id).indexOf("dev/") === 0)
                continue
            byId[String(route.id)] = route
        }
        for (var j = 0; j < page.sectionOrder.length; j++) {
            var wanted = "settings/" + page.sectionOrder[j]
            var found = byId[wanted]
            if (found !== undefined) {
                out.push({"id": String(found.id), "title_key": String(found.title_key),
                          "icon": String(found.icon || ""), "active": false})
                delete byId[wanted]
            }
        }
        for (var key in byId)
            out.push({"id": key, "title_key": String(byId[key].title_key),
                      "icon": String(byId[key].icon || ""), "active": false})
        return out
    }

    function componentFor(name) {
        if (name === "launcher")
            return launcherSection
        if (name === "dirs")
            return gameDirSection
        if (name === "java")
            return javaSection
        if (name === "theme")
            return themeSection
        if (name === "music")
            return musicAccountSection
        if (name === "log")
            return logSection
        if (name === "about")
            return aboutSection
        //: AI 分区目前只有净读登录，模型 / 工具权限等内容以后再加；插件分区仍是占位。
        if (name === "account")
            return accountSection
        if (name === "ai")
            return jingduSection
        //: 插件分区：概况 + 打开管理器 / 市场。
        if (name === "plugin")
            return pluginSection
        return placeholderSection
    }

    //: 分区 id → 对应的组件（`refreshUi` 与 `componentFor` 共用一张表）
    function sectionName(routeId) {
        var text = String(routeId || "")
        return text.indexOf("settings/") === 0 ? text.substring(9) : "launcher"
    }

    // ── 刷新整页（页面级状态；分区内部状态各自刷新）───────────

    function refreshUi() {
        if (!page) {
            return
        }
        page.section = page.sectionName(page.routeId)
        var list = page.buildSections()
        for (var i = 0; i < list.length; i++) {
            list[i].active = (page.sectionName(list[i].id) === page.section)
        }
        page.sections = list
        sectionLoader.sourceComponent = page.componentFor(page.section)

        page.dirty = page.live ? Settings.dirty : false
        page.changeCount = page.live ? Settings.changeCount : 0
        unsavedTag.text = page.changeCount > 0
                ? page.format("settings_unsaved_count", {"count": page.changeCount}) : ""
        unsavedTag.visible = page.changeCount > 0
        saveButton.enabled = page.dirty
        cancelButton.enabled = page.dirty
    }

    Component.onCompleted: {
        if (page.live)
            Settings.beginDraft()
        page.refreshUi()
    }

    onRouteIdChanged: page.refreshUi()

    // ── 桥的信号（页面级状态从这里进 refreshUi）──────────────

    Connections {
        target: (typeof Settings !== "undefined" && Settings) ? Settings : null

        function onDraftChanged() {
            if (page)
                page.refreshUi()
        }

        function onStatusMessage(text, level) {
            if (typeof Shell !== "undefined" && Shell)
                Shell.setStatus(text, level)
        }

        //: 「保存并重启」：服务已经拉起新进程，这里让当前进程退出
        //: （`Qt.quit()` 会走 `main_qml` 的 `aboutToQuit` 清理链）。
        function onRestartRequested() {
            Qt.quit()
        }
    }

    // ── 布局：左分区导航 + 右内容 + 底部动作条 ────────────────

    RowLayout {
        objectName: "settingsLayout"
        anchors.fill: parent
        spacing: Theme?.spacingLg ?? 16

        FmCard {
            objectName: "settingsSectionNav"
            Layout.preferredWidth: 200
            Layout.fillHeight: true

            //: 卡片的默认内容落进卡片内部的 `ColumnLayout`，所以这里**不能用 anchors.fill**
            //: （布局托管的项带锚点会被 Qt 判定为未定义行为，会刷 8 条警告）。
            ColumnLayout {
                objectName: "settingsSectionList"
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: Theme?.spacingXs ?? 4

                FmListItem {
                    objectName: "settingsNavTitle"
                    Layout.fillWidth: true
                    clickable: false
                    iconName: "settings-gear"
                    title: Tr?.map["settings_title"] ?? "settings_title"
                }

                Repeater {
                    objectName: "settingsSectionRepeater"
                    model: page.sections

                    //: 每个分区 = 「强调色指示条 + 列表项」。指示条标出**当前分区**
                    //: （选中态本身只有一层很淡的底色，在深色主题里不够醒目）；
                    //: 顺带让这一页在任何主题下都至少有一处强调色 —— 视觉回归
                    //: （`tests/test_visual_regression.py`）对每个页面都有这条判据，
                    //: 而设置页在静止状态下没有主按钮（未改动时「保存」是禁用的）。
                    delegate: RowLayout {
                        id: sectionRow
                        Layout.fillWidth: true
                        //: **必须卡住高度**：只给 `preferredHeight` 不够 —— 否则
                        //: `ColumnLayout` 会把多余高度分给第一行（选中项被拉到 215px，
                        //: 指示条跟着变成一根长杠）。`maximumHeight` 才是硬约束，
                        //: 高度取列表项自己的隐式高度，不写死数字。
                        Layout.preferredHeight: sectionItem.implicitHeight
                        Layout.maximumHeight: sectionItem.implicitHeight
                        spacing: 0
                        Rectangle {
                            objectName: "settingsSectionIndicator"
                            Layout.preferredWidth: 3
                            Layout.fillHeight: true
                            Layout.topMargin: Theme?.spacingXs ?? 0
                            Layout.bottomMargin: Theme?.spacingXs ?? 0
                            radius: width / 2
                            visible: modelData.active === true
                            color: Theme?.accent ?? "transparent"
                        }

                        FmListItem {
                            id: sectionItem
                            objectName: "settingsSectionItem"
                            Layout.fillWidth: true
                            title: Tr?.map[modelData.title_key] ?? modelData.title_key
                            iconName: String(modelData.icon || "")
                            selected: modelData.active === true
                            onClicked: {
                                if (!page) {
                                    return
                                }
                                if (typeof Nav !== "undefined" && Nav)
                                    Nav.push(String(modelData.id))
                            }
                        }
                    }
                }

                Item { Layout.fillHeight: true }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Theme?.spacingMd ?? 12

            Loader {
                id: sectionLoader
                objectName: "settingsSectionLoader"
                Layout.fillWidth: true
                Layout.fillHeight: true
            }

            // ── 底部动作条（B1：整域一份草稿，所以每个分区都看得见它）──
            // 例外：游戏目录是**即时动作**（添加/切换/删除立刻生效），不进入草稿。
            // 在那一分区继续显示「保存/取消」会让用户以为可以撤销切换，因此隐藏。
            RowLayout {
                objectName: "settingsActionBar"
                Layout.fillWidth: true
                visible: page.section !== "dirs"
                spacing: Theme?.spacingSm ?? 8

                FmTag {
                    id: unsavedTag
                    objectName: "settingsUnsavedTag"
                    visible: false
                    level: "warning"
                    iconName: "warning"
                    text: ""
                }

                Item { Layout.fillWidth: true }

                FmButton {
                    id: cancelButton
                    objectName: "settingsCancelButton"
                    primary: false
                    text: Tr?.map["cancel"] ?? "cancel"
                    onClicked: {
                        if (page && page.live)
                            Settings.discardDraft()
                    }
                }

                FmButton {
                    id: saveButton
                    objectName: "settingsSaveButton"
                    text: Tr?.map["settings_save"] ?? "settings_save"
                    onClicked: {
                        if (page && page.live)
                            Settings.commitDraft()
                    }
                }

                //: 底部按钮「保存并重启」—— 它其实是**重启启动器进程**，顺序是先落盘再重启。
                FmButton {
                    id: saveRestartButton
                    objectName: "settingsSaveRestartButton"
                    primary: false
                    iconName: "refresh"
                    text: Tr?.map["settings_save_restart"] ?? "settings_save_restart"
                    onClicked: {
                        if (page && page.live)
                            Settings.saveAndRestart()
                    }
                }
            }
        }
    }

    // ══ 分区一：启动器功能 ══════════════════════════════════════
    //
    // 语言在本分区而不是独立分区：路由表没有 `settings/language` 这一条。

    Component {
        id: launcherSection

        Item {
            id: launcherPane
            objectName: "settingsLauncherSection"

            function refresh() {
                if (typeof Settings === "undefined" || !Settings)
                    return
                var fields = Settings.fields
                minimizeSwitch.checked = fields.minimize_on_game_launch === true
                mirrorSwitch.checked = fields.mirror_enabled === true
                threadsSlider.value = Number(fields.download_threads)
                threadsValue.text = String(fields.download_threads)
                var choices = Settings.languages
                languageCombo.model = choices
                languageCombo.currentIndex = page.indexOfValue(choices, "code", fields.language)
            }

            FmScrollView {
                objectName: "settingsLauncherScroll"
                anchors.fill: parent
                clip: true

                ColumnLayout {
                    width: launcherPane.width - (Theme?.spacingLg ?? 20)
                    spacing: Theme?.spacingLg ?? 16

                    // ── 最小化开关（M-01）──
                    RowLayout {
                        objectName: "settingsMinimizeRow"
                        Layout.fillWidth: true
                        spacing: Theme?.spacingSm ?? 8

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: Theme?.spacingXs ?? 4

                            Text {
                                Layout.fillWidth: true
                                text: Tr?.map["settings_minimize"] ?? "settings_minimize"
                                color: Theme?.textPrimary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeBase ?? 12
                                elide: Text.ElideRight
                            }

                            Text {
                                Layout.fillWidth: true
                                text: Tr?.map["settings_minimize_hint"] ?? "settings_minimize_hint"
                                color: Theme?.textSecondary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 11
                                wrapMode: Text.WordWrap
                            }
                        }

                        FmSwitch {
                            id: minimizeSwitch
                            objectName: "settingsMinimizeSwitch"
                            onToggled: {
                                if (page && page.live)
                                    Settings.setField("minimize_on_game_launch", checked)
                            }
                        }
                    }

                    // ── 国内镜像源（M-02）──
                    RowLayout {
                        objectName: "settingsMirrorRow"
                        Layout.fillWidth: true
                        spacing: Theme?.spacingSm ?? 8

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: Theme?.spacingXs ?? 4

                            Text {
                                Layout.fillWidth: true
                                text: Tr?.map["settings_mirror"] ?? "settings_mirror"
                                color: Theme?.textPrimary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeBase ?? 12
                                elide: Text.ElideRight
                            }

                            Text {
                                Layout.fillWidth: true
                                text: Tr?.map["settings_mirror_hint"] ?? "settings_mirror_hint"
                                color: Theme?.textSecondary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 11
                                wrapMode: Text.WordWrap
                            }
                        }

                        FmSwitch {
                            id: mirrorSwitch
                            objectName: "settingsMirrorSwitch"
                            onToggled: {
                                if (page && page.live)
                                    Settings.setField("mirror_enabled", checked)
                            }
                        }
                    }

                    // ── 下载线程数（M-12）──
                    ColumnLayout {
                        objectName: "settingsThreadsRow"
                        Layout.fillWidth: true
                        spacing: Theme?.spacingXs ?? 4

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme?.spacingSm ?? 8

                            Text {
                                Layout.fillWidth: true
                                text: Tr?.map["settings_download_threads"] ?? "settings_download_threads"
                                color: Theme?.textPrimary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeBase ?? 12
                                elide: Text.ElideRight
                            }

                            Text {
                                id: threadsValue
                                objectName: "settingsThreadsValue"
                                text: ""
                                color: Theme?.accent ?? "transparent"
                                font.pixelSize: Theme?.fontSizeBase ?? 12
                                font.bold: true
                            }
                        }

                        FmSlider {
                            id: threadsSlider
                            objectName: "settingsThreadsSlider"
                            Layout.fillWidth: true
                            from: 1
                            to: 255
                            stepSize: 1
                            showValue: false
                            onMoved: {
                                if (page && page.live)
                                    Settings.setField("download_threads", Math.round(value))
                            }
                        }

                        Text {
                            Layout.fillWidth: true
                            text: Tr?.map["ach_advanced_multithread_desc"] ?? "ach_advanced_multithread_desc"
                            color: Theme?.textSecondary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 11
                            wrapMode: Text.WordWrap
                        }
                    }

                    // ── 界面语言（M-06）──
                    ColumnLayout {
                        objectName: "settingsLanguageRow"
                        Layout.fillWidth: true
                        spacing: Theme?.spacingXs ?? 4

                        Text {
                            Layout.fillWidth: true
                            text: Tr?.map["settings_language"] ?? "settings_language"
                            color: Theme?.textPrimary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeBase ?? 12
                            elide: Text.ElideRight
                        }

                        FmComboBox {
                            id: languageCombo
                            objectName: "settingsLanguageCombo"
                            Layout.preferredWidth: 260
                            textRole: "name"
                            valueRole: "code"
                            onActivated: {
                                if (page && page.live)
                                    Settings.setField("language", String(currentValue))
                            }
                        }

                        Text {
                            Layout.fillWidth: true
                            //: 语言切换是**热切换**，不要写「重启启动器后生效」这类提示。
                            text: Tr?.map["settings_language_hint"] ?? "settings_language_hint"
                            color: Theme?.textSecondary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 11
                            wrapMode: Text.WordWrap
                        }
                    }
                }
            }

            Component.onCompleted: launcherPane.refresh()

            Connections {
                target: (typeof Settings !== "undefined" && Settings) ? Settings : null

                function onDraftChanged() {
                    if (launcherPane)
                        launcherPane.refresh()
                }
            }
        }
    }

    // ══ 分区二：游戏目录（多 .minecraft）══════════════════════
    //
    // 目录的增删改与切换都是**立即生效**的动作，不进入设置草稿：
    // 草稿的「取消」语义是"丢弃未保存的改动"，而目录切换一旦改了内存里的当前目录，
    // 再取消也回不到旧状态（各页面已按新目录刷新）。所以这一分区独立于动作条。

    Component {
        id: gameDirSection
        FmGameDirSection {}
    }

    // ══ 分区三：Java 运行时（M-03 / M-04 / M-05）═══════════════

    Component {
        id: javaSection

        Item {
            id: javaPane
            objectName: "settingsJavaSection"

            function refresh() {
                if (typeof Settings === "undefined" || !Settings)
                    return
                var fields = Settings.fields
                var modes = Settings.javaModes
                javaModeCombo.model = modes
                javaModeCombo.currentIndex = page.indexOfValue(modes, "value", fields.java_mode)
                var path = (fields.java_custom_path === null || fields.java_custom_path === undefined)
                         ? "" : String(fields.java_custom_path)
                javaCustomField.text = path
                javaCustomRow.visible = String(fields.java_mode) === "custom"
                javaScanRow.visible = String(fields.java_mode) === "scan"
                javaPane.refreshScan()
            }

            function refreshScan() {
                if (typeof Settings === "undefined" || !Settings)
                    return
                var rows = Settings.javaRuntimes
                javaScanList.model = rows
                var scanning = String(Settings.javaScanState) === "scanning"
                javaScanState.text = scanning
                        ? page.t("settings_java_scanning")
                        : (rows.length > 0 ? "" : page.t("settings_java_scan_none"))
                javaScanState.visible = javaScanState.text.length > 0
            }

            FmScrollView {
                objectName: "settingsJavaScroll"
                anchors.fill: parent
                clip: true

                ColumnLayout {
                    width: javaPane.width - (Theme?.spacingLg ?? 20)
                    spacing: Theme?.spacingLg ?? 16

                    // ── 模式下拉（M-03）──
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: Theme?.spacingXs ?? 4

                        Text {
                            Layout.fillWidth: true
                            text: Tr?.map["settings_java_mode"] ?? "settings_java_mode"
                            color: Theme?.textPrimary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeBase ?? 12
                        }

                        FmComboBox {
                            id: javaModeCombo
                            objectName: "settingsJavaModeCombo"
                            Layout.preferredWidth: 260
                            textRole: "label"
                            valueRole: "value"
                            onActivated: {
                                if (page && page.live)
                                    Settings.setField("java_mode", String(currentValue))
                            }
                        }
                    }

                    // ── 自定义路径（M-04）──
                    RowLayout {
                        id: javaCustomRow
                        objectName: "settingsJavaCustomRow"
                        Layout.fillWidth: true
                        spacing: Theme?.spacingSm ?? 8

                        FmTextField {
                            id: javaCustomField
                            objectName: "settingsJavaCustomField"
                            Layout.fillWidth: true
                            label: Tr?.map["settings_java_custom_path"] ?? "settings_java_custom_path"
                            placeholder: Tr?.map["settings_java_custom_placeholder"] ?? "settings_java_custom_placeholder"
                            onEdited: {
                                if (page && page.live)
                                    Settings.setField("java_custom_path", value)
                            }
                        }

                        FmButton {
                            objectName: "settingsJavaBrowseButton"
                            primary: false
                            Layout.alignment: Qt.AlignBottom
                            text: Tr?.map["settings_java_custom_browse"] ?? "settings_java_custom_browse"
                            onClicked: javaDialog.open()
                        }
                    }

                    // ── 扫描结果（M-05）──
                    ColumnLayout {
                        id: javaScanRow
                        objectName: "settingsJavaScanRow"
                        Layout.fillWidth: true
                        spacing: Theme?.spacingSm ?? 8

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme?.spacingSm ?? 8

                            Text {
                                Layout.fillWidth: true
                                text: Tr?.map["settings_java_scan_title"] ?? "settings_java_scan_title"
                                color: Theme?.textPrimary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeBase ?? 12
                            }

                            FmButton {
                                objectName: "settingsJavaScanRefresh"
                                primary: false
                                text: Tr?.map["settings_java_scan_refresh"] ?? "settings_java_scan_refresh"
                                onClicked: {
                                    if (!page || !page.live)
                                        return
                                    Settings.refreshJavaStatus()
                                    Settings.scanJava()
                                }
                            }
                        }

                        Text {
                            id: javaScanState
                            objectName: "settingsJavaScanState"
                            Layout.fillWidth: true
                            color: Theme?.textSecondary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 11
                            wrapMode: Text.WordWrap
                        }

                        ColumnLayout {
                            objectName: "settingsJavaScanList"
                            Layout.fillWidth: true
                            spacing: Theme?.spacingXs ?? 4

                            Repeater {
                                id: javaScanList
                                model: []

                                delegate: ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 0

                                    //: 单选应用（扫描到的 Java 列表）。
                                    //: 刻意关掉自动互斥：勾选状态由草稿里的 `java_custom_path` 决定
                                    //: （草稿是唯一真值来源），让 Qt 自己管会出现"点了 A 但草稿没变"。
                                    //: 绑定只读桥的属性（带 typeof 判空），不引用 `page.*`。
                                    FmRadio {
                                        objectName: "settingsJavaScanItem"
                                        Layout.fillWidth: true
                                        autoExclusive: false
                                        text: String(modelData.title || "")
                                        checked: (typeof Settings !== "undefined" && Settings)
                                                 ? String((Settings.fields.java_custom_path === null
                                                           || Settings.fields.java_custom_path === undefined)
                                                          ? "" : Settings.fields.java_custom_path)
                                                   === String(modelData.value || "")
                                                 : false
                                        onClicked: {
                                            if (page && page.live)
                                                Settings.setField("java_custom_path", String(modelData.value || ""))
                                        }
                                    }

                                    Text {
                                        Layout.fillWidth: true
                                        Layout.leftMargin: Theme?.spacingLg ?? 20
                                        text: String(modelData.subtitle || "")
                                        color: Theme?.textSecondary ?? "transparent"
                                        font.pixelSize: Theme?.fontSizeSmall ?? 11
                                        elide: Text.ElideMiddle
                                    }
                                }
                            }
                        }
                    }
                }
            }

            Component.onCompleted: javaPane.refresh()

            Connections {
                target: (typeof Settings !== "undefined" && Settings) ? Settings : null

                function onDraftChanged() {
                    if (javaPane)
                        javaPane.refresh()
                }

                function onJavaChanged() {
                    if (javaPane)
                        javaPane.refreshScan()
                }
            }
        }
    }

    // ══ 分区四：主题（M-07 ~ M-11）══════════════════════════════

    Component {
        id: themeSection

        Item {
            id: themePane
            objectName: "settingsThemeSection"

            function refresh() {
                if (typeof Settings === "undefined" || !Settings)
                    return
                var fields = Settings.fields
                var themes = Settings.themes
                themeCombo.model = themes
                themeCombo.currentIndex = page.indexOfValue(themes, "name", fields.theme_name)
                accentField.text = (fields.accent_color === null || fields.accent_color === undefined)
                                 ? "" : String(fields.accent_color)
                accentField.errorText = ""
                dynamicSwitch.checked = fields.dynamic_version_theme === true
            }

            //: 应用自定义强调色。空输入 = 清除（空串走 `settings_accent_cleared`，
            //: 非法格式走 `settings_accent_invalid`）。
            function applyAccent() {
                if (!page || !page.live)
                    return
                var invalid = Settings.validateAccent(accentField.text)
                accentField.errorText = invalid
                if (invalid.length > 0)
                    return
                Settings.setField("accent_color", accentField.text)
            }

            FmScrollView {
                objectName: "settingsThemeScroll"
                anchors.fill: parent
                clip: true

                ColumnLayout {
                    width: themePane.width - (Theme?.spacingLg ?? 20)
                    spacing: Theme?.spacingLg ?? 16

                    // ── 主题下拉 + 导入（M-07 / M-08）──
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: Theme?.spacingXs ?? 4

                        Text {
                            Layout.fillWidth: true
                            text: Tr?.map["settings_theme_select"] ?? "settings_theme_select"
                            color: Theme?.textPrimary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeBase ?? 12
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme?.spacingSm ?? 8

                            FmComboBox {
                                id: themeCombo
                                objectName: "settingsThemeCombo"
                                Layout.preferredWidth: 260
                                textRole: "label"
                                valueRole: "name"
                                onActivated: {
                                    if (page && page.live)
                                        Settings.setField("theme_name", String(currentValue))
                                }
                            }

                            FmButton {
                                objectName: "settingsThemeImportButton"
                                primary: false
                                text: Tr?.map["settings_theme_import"] ?? "settings_theme_import"
                                onClicked: themeDialog.open()
                            }
                        }
                    }

                    // ── 自定义强调色（M-09 / M-10）──
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: Theme?.spacingXs ?? 4

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme?.spacingSm ?? 8

                            FmTextField {
                                id: accentField
                                objectName: "settingsAccentField"
                                Layout.fillWidth: true
                                label: Tr?.map["settings_accent_color"] ?? "settings_accent_color"
                                placeholder: "#RRGGBB"
                                onEdited: {
                                    if (!page)
                                        return
                                    errorText = page.live ? Settings.validateAccent(value) : ""
                                }
                                onAccepted: themePane.applyAccent()
                            }

                            FmButton {
                                objectName: "settingsAccentApplyButton"
                                Layout.alignment: Qt.AlignBottom
                                text: Tr?.map["settings_accent_apply"] ?? "settings_accent_apply"
                                onClicked: themePane.applyAccent()
                            }

                            FmButton {
                                objectName: "settingsAccentRandomButton"
                                primary: false
                                Layout.alignment: Qt.AlignBottom
                                text: Tr?.map["settings_accent_random"] ?? "settings_accent_random"
                                onClicked: {
                                    if (page && page.live)
                                        Settings.randomAccent()
                                }
                            }
                        }
                    }

                    // ── 版本动态主题（M-11）──
                    RowLayout {
                        objectName: "settingsDynamicRow"
                        Layout.fillWidth: true
                        spacing: Theme?.spacingSm ?? 8

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: Theme?.spacingXs ?? 4

                            Text {
                                Layout.fillWidth: true
                                text: Tr?.map["settings_dynamic_theme"] ?? "settings_dynamic_theme"
                                color: Theme?.textPrimary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeBase ?? 12
                            }

                            Text {
                                Layout.fillWidth: true
                                text: Tr?.map["settings_dynamic_theme_hint"] ?? "settings_dynamic_theme_hint"
                                color: Theme?.textSecondary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 11
                                wrapMode: Text.WordWrap
                            }
                        }

                        FmSwitch {
                            id: dynamicSwitch
                            objectName: "settingsDynamicSwitch"
                            onToggled: {
                                if (page && page.live)
                                    Settings.setField("dynamic_version_theme", checked)
                            }
                        }
                    }
                }
            }

            Component.onCompleted: themePane.refresh()

            Connections {
                target: (typeof Settings !== "undefined" && Settings) ? Settings : null

                function onDraftChanged() {
                    if (themePane)
                        themePane.refresh()
                }

                function onThemesChanged() {
                    if (themePane)
                        themePane.refresh()
                }
            }
        }
    }

    // ══ 分区五：日志（A-13 / A-18；03 章的 3.4 摘要要求"查看与导出"）══

    Component {
        id: logSection

        Item {
            id: logPane
            objectName: "settingsLogSection"

            function refreshStats() {
                if (!page || typeof Logs === "undefined" || !Logs)
                    return
                logStats.text = page.format("settings_log_lines", {"count": Logs.lineCount, "max": Logs.capacity})
                logPath.text = Logs.logFile
            }

            ColumnLayout {
                objectName: "settingsLogLayout"
                anchors.fill: parent
                spacing: Theme?.spacingSm ?? 8

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 8

                    Text {
                        id: logStats
                        objectName: "settingsLogStats"
                        Layout.fillWidth: true
                        text: ""
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 11
                        elide: Text.ElideRight
                    }

                    FmButton {
                        objectName: "settingsLogClearButton"
                        primary: false
                        text: Tr?.map["clear_log"] ?? "clear_log"
                        onClicked: {
                            if (typeof Logs !== "undefined" && Logs)
                                Logs.clearLog()
                        }
                    }

                    FmButton {
                        objectName: "settingsLogExportButton"
                        primary: false
                        text: Tr?.map["settings_log_export"] ?? "settings_log_export"
                        onClicked: logDialog.open()
                    }

                    FmButton {
                        objectName: "settingsLogFolderButton"
                        primary: false
                        text: Tr?.map["version_open_folder"] ?? "version_open_folder"
                        onClicked: {
                            if (typeof Logs !== "undefined" && Logs)
                                Logs.openLogDir()
                        }
                    }
                }

                Text {
                    Layout.fillWidth: true
                    text: Tr?.map["settings_log_path_label"] ?? "settings_log_path_label"
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 11
                }

                Text {
                    id: logPath
                    objectName: "settingsLogPath"
                    Layout.fillWidth: true
                    text: ""
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 11
                    elide: Text.ElideMiddle
                }

                //: 日志面板（通用件）。
                //: 行是靠 `Logs.linesAppended` 推过来的 —— 组件本身不读文件、不碰服务。
                LogView {
                    id: logView
                    objectName: "settingsLogView"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    title: Tr?.map["launcher_log"] ?? "launcher_log"
                    emptyText: Tr?.map["settings_log_empty"] ?? "settings_log_empty"
                    showLineNumbers: false
                }
            }

            Component.onCompleted: {
                //: 打开页面时把已有的行一次性灌进去（启动日志在设置页之前就产生了），
                //: 之后只走增量 —— 桥里那条游标保证不会重复投递。
                if (typeof Logs !== "undefined" && Logs)
                    logView.appendLines(Logs.snapshot())
                logPane.refreshStats()
            }

            Connections {
                target: (typeof Logs !== "undefined" && Logs) ? Logs : null

                function onLinesAppended(lines) {
                    if (logView)
                        logView.appendLines(lines)
                    if (logPane)
                        logPane.refreshStats()
                }

                function onLinesCleared() {
                    if (logView)
                        logView.clear()
                    if (logPane)
                        logPane.refreshStats()
                }

                function onStatsChanged() {
                    if (logPane)
                        logPane.refreshStats()
                }
            }

            Connections {
                target: (typeof Tr !== "undefined" && Tr) ? Tr : null

                function onLanguageChanged() {
                    if (logPane)
                        logPane.refreshStats()
                }
            }
        }
    }

    // ══ 分区六：关于 ════════════════════════════════════════════
    //
    // 内容：图标 + 名称 + 系统信息 + 鸣谢项目（各带"地址 / 许可证"两个外链）
    // + 许可证一行 + 赞赏按钮。它是设置域里的一条路由（L2「关于」）；
    // 协议全文复用 `legal_service` 的渲染方式。

    Component {
        id: aboutSection

        Item {
            id: aboutPane
            objectName: "settingsAboutSection"

            function openUrl(url) {
                if (typeof About === "undefined" || !About)
                    return
                About.openUrl(String(url))
            }

            FmScrollView {
                objectName: "settingsAboutScroll"
                anchors.fill: parent
                clip: true

                ColumnLayout {
                    width: aboutPane.width - (Theme?.spacingLg ?? 20)
                    spacing: Theme?.spacingMd ?? 12

                    // ── 顶部：图标 + 名称 + 副标题 ──
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme?.spacingMd ?? 12

                        FmIcon {
                            objectName: "settingsAboutIcon"
                            name: "home"
                            color: Theme?.accent ?? "transparent"
                            size: 40
                            Layout.alignment: Qt.AlignVCenter
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 0

                            Text {
                                Layout.fillWidth: true
                                text: (typeof About !== "undefined" && About) ? About.appName : ""
                                color: Theme?.textPrimary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeTitle ?? 18
                                font.bold: true
                            }

                            Text {
                                Layout.fillWidth: true
                                text: (typeof About !== "undefined" && About) ? About.appSubtitle : ""
                                color: Theme?.textSecondary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 11
                            }
                        }
                    }

                    // ── 系统信息（4 行：版本 / Python / 系统 / 架构）──
                    ColumnLayout {
                        objectName: "settingsAboutInfo"
                        Layout.fillWidth: true
                        spacing: Theme?.spacingXs ?? 4

                        Repeater {
                            model: (typeof About !== "undefined" && About) ? About.info : []

                            delegate: RowLayout {
                                objectName: "settingsAboutInfoRow"
                                Layout.fillWidth: true
                                spacing: Theme?.spacingSm ?? 8

                                Text {
                                    Layout.preferredWidth: 80
                                    horizontalAlignment: Text.AlignRight
                                    text: Tr?.map[modelData.key] ?? modelData.key
                                    color: Theme?.textSecondary ?? "transparent"
                                    font.pixelSize: Theme?.fontSizeSmall ?? 11
                                }

                                Text {
                                    Layout.fillWidth: true
                                    text: String(modelData.value || "")
                                    color: Theme?.textPrimary ?? "transparent"
                                    font.pixelSize: Theme?.fontSizeSmall ?? 11
                                    elide: Text.ElideRight
                                }
                            }
                        }
                    }

                    // ── 启动器更新（3.28）──
                    FmUpdateSection {
                        Layout.fillWidth: true
                    }

                    // ── 鸣谢（9 个项目，各带"地址 / 许可证"）──
                    Text {
                        Layout.fillWidth: true
                        text: Tr?.map["about_acknowledgments"] ?? "about_acknowledgments"
                        color: Theme?.textPrimary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        font.bold: true
                    }

                    Repeater {
                        model: (typeof About !== "undefined" && About) ? About.acknowledgments : []

                        delegate: RowLayout {
                            objectName: "settingsAboutAck"
                            Layout.fillWidth: true
                            spacing: Theme?.spacingSm ?? 8

                            Text {
                                Layout.fillWidth: true
                                text: String(modelData.name || "")
                                color: Theme?.textPrimary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 11
                                elide: Text.ElideRight
                            }

                            FmButton {
                                primary: false
                                text: Tr?.map["about_address"] ?? "about_address"
                                onClicked: aboutPane.openUrl(modelData.url)
                            }

                            FmButton {
                                primary: false
                                text: Tr?.map["about_license_btn"] ?? "about_license_btn"
                                onClicked: aboutPane.openUrl(modelData.license_url)
                            }
                        }
                    }

                    // ── 许可证 + 赞赏 ──
                    Text {
                        Layout.fillWidth: true
                        text: Tr?.map["about_license"] ?? "about_license"
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 11
                    }

                    FmButton {
                        objectName: "settingsAboutDonate"
                        text: Tr?.map["about_donate"] ?? "about_donate"
                        onClicked: {
                            if (typeof About !== "undefined" && About)
                                About.openDonate()
                        }
                    }

                    // ── 用户协议全文（J-01 / J-02）──
                    Text {
                        Layout.fillWidth: true
                        Layout.topMargin: Theme?.spacingMd ?? 12
                        text: Tr?.map["terms_title"] ?? "terms_title"
                        color: Theme?.textPrimary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        font.bold: true
                    }

                    Text {
                        objectName: "settingsAboutTerms"
                        Layout.fillWidth: true
                        //: 协议**全文**（`TERMS_OF_USE.md` 的 Markdown）；读不到时用语言
                        //: 文件里的摘要兜底 —— 与 `qml/StartupDialogs.qml` 的处理一致。
                        text: {
                            var full = (typeof About !== "undefined" && About) ? String(About.termsText ?? "") : ""
                            return full.length > 0 ? full : (Tr?.map["terms_content"] ?? "terms_content")
                        }
                        textFormat: ((typeof About !== "undefined" && About) && About.termsAvailable)
                                    ? Text.MarkdownText : Text.PlainText
                        color: Theme?.textPrimary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        wrapMode: Text.WordWrap
                    }
                }
            }
        }
    }

    // ══ 分区七：音乐账号 ════════════════════════════════════════
    //
    // 设置域的一条独立分区 `settings/music`，路由标题复用 `settings_wy_login_title`。
    // 只负责登录态、扫码入口、重新同步与退出；VIP 等级、续期、账号详情不在这里。
    // 账号与二维码逻辑全在 `Music.account`（AccountManager），本分区不碰凭据与网络。

    Component {
        id: musicAccountSection

        Item {
            id: musicAccountPane
            objectName: "musicAccountSection"

            readonly property bool live: (typeof Music !== "undefined" && Music) ? true : false
            readonly property var account: (musicAccountPane.live && Music.ready) ? Music.account : null
            readonly property bool loggedIn: !!(musicAccountPane.account && musicAccountPane.account.loggedIn)
            readonly property bool busy: !!(musicAccountPane.account && musicAccountPane.account.busy)
            readonly property string credentialExpiry: (musicAccountPane.account
                                                        ? String(musicAccountPane.account.credentialExpiryDate || "") : "")
            readonly property string vipLabel: (musicAccountPane.account
                                                ? String(musicAccountPane.account.vipLabel || "") : "")
            readonly property string vipQualityKey: (musicAccountPane.account
                                                      ? String(musicAccountPane.account.vipQualityKey || "") : "")
            property bool dialogOpen: false
            property string errorText: ""
            property string statusText: ""

            function t(key) {
                return Tr ? (Tr.map[key] ?? key) : key
            }

            function format(key, params) {
                var text = musicAccountPane.t(key)
                if (!params)
                    return text
                for (var name in params)
                    text = text.replace("{" + name + "}", String(params[name]))
                return text
            }

            function displayStatus() {
                if (!musicAccountPane.loggedIn)
                    return musicAccountPane.t("settings_wy_not_logged_in")
                var name = musicAccountPane.account
                        ? String(musicAccountPane.account.nickname || "") : ""
                return name.length > 0
                        ? musicAccountPane.format("settings_wy_logged_in_as", {"name": name})
                        : musicAccountPane.t("settings_wy_logged_in")
            }

            function refresh() {
                if (!musicAccountPane.live) {
                    musicAccountPane.errorText = musicAccountPane.t("music_backend_failed")
                    return
                }
                musicAccountPane.statusText = musicAccountPane.displayStatus()
                if (!musicAccountPane.account) {
                    musicAccountPane.errorText = Music.ready ? musicAccountPane.t("music_backend_failed") : ""
                    return
                }
                musicAccountPane.errorText = ""
            }

            function openLogin() {
                if (!musicAccountPane.live)
                    return
                if (!Music.ready)
                    Music.ensureReady()
                musicAccountPane.refresh()
                if (!Music.ready || !Music.account) {
                    musicAccountPane.errorText = musicAccountPane.t("music_backend_failed")
                    return
                }
                musicAccountPane.errorText = ""
                musicAccountPane.dialogOpen = true
            }

            function logout() {
                if (musicAccountPane.account)
                    musicAccountPane.account.logout()
            }

            function renewLogin() {
                if (musicAccountPane.account && !musicAccountPane.busy)
                    musicAccountPane.account.renewCredentials()
            }

            function syncNow() {
                if (typeof Music !== "undefined" && Music)
                    Music.syncPlaylists()
            }

            onAccountChanged: musicAccountPane.refresh()

            Component.onCompleted: {
                if (musicAccountPane.live && !Music.ready)
                    Music.ensureReady()
                musicAccountPane.refresh()
            }

            Connections {
                target: (typeof Music !== "undefined" && Music) ? Music : null
                function onReadyChanged() { musicAccountPane.refresh() }
            }

            Connections {
                target: musicAccountPane.account
                function onLoggedInChanged() { musicAccountPane.refresh() }
                function onBusyChanged() { musicAccountPane.refresh() }
                function onProfileChanged() { musicAccountPane.refresh() }
                function onCredentialsChanged() { musicAccountPane.refresh() }
                function onPlaylistsFailedChanged() { musicAccountPane.refresh() }
            }

            Connections {
                target: (typeof Tr !== "undefined" && Tr) ? Tr : null
                function onLanguageChanged() { musicAccountPane.refresh() }
            }

            FmScrollView {
                id: musicAccountScroll
                anchors.fill: parent
                contentWidth: availableWidth

                ColumnLayout {
                    width: musicAccountScroll.availableWidth
                    spacing: Theme?.spacingMd ?? 12

                    FmCard {
                        Layout.fillWidth: true
                        title: Tr?.map["settings_wy_login_title"] ?? ""
                        subtitle: Tr?.map["settings_wy_login_desc"] ?? ""

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme?.spacingSm ?? 8

                            FmIcon {
                                name: "account"
                                size: 22
                                color: musicAccountPane.loggedIn
                                       ? (Theme?.success ?? "transparent")
                                       : (Theme?.textTertiary ?? "transparent")
                            }

                            Text {
                                Layout.fillWidth: true
                                text: musicAccountPane.statusText
                                color: Theme?.textPrimary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeBase ?? 12
                                elide: Text.ElideRight
                            }

                            FmProgressRing {
                                visible: musicAccountPane.busy
                                Layout.preferredWidth: 16
                                Layout.preferredHeight: 16
                                lineWidth: 2
                                indeterminate: true
                            }

                            FmTag {
                                visible: musicAccountPane.loggedIn
                                text: Tr?.map["settings_wy_logged_in"] ?? ""
                                level: "success"
                                iconName: "check"
                            }

                            FmTag {
                                visible: musicAccountPane.loggedIn && musicAccountPane.account
                                         && musicAccountPane.account.playlistsFailed
                                text: Tr?.map["music_wy_sync_failed"] ?? ""
                                level: "error"
                            }
                        }

                        FmInfoBar {
                            visible: musicAccountPane.errorText.length > 0
                            level: "error"
                            text: musicAccountPane.errorText
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            visible: musicAccountPane.loggedIn
                            spacing: Theme?.spacingSm ?? 8

                            FmTag {
                                visible: musicAccountPane.vipLabel.length > 0
                                text: musicAccountPane.vipLabel
                                level: "accent"
                                iconName: "crown"
                            }

                            FmTag {
                                visible: musicAccountPane.vipQualityKey.length > 0
                                text: Tr?.map[musicAccountPane.vipQualityKey] ?? ""
                                level: "neutral"
                            }

                            Text {
                                Layout.fillWidth: true
                                visible: musicAccountPane.loggedIn
                                         && musicAccountPane.credentialExpiry.length > 0
                                text: musicAccountPane.format("settings_wy_credential_expiry",
                                                             {"date": musicAccountPane.credentialExpiry})
                                color: Theme?.textTertiary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 10
                                elide: Text.ElideRight
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme?.spacingSm ?? 8

                            FmButton {
                                objectName: "settingsMusicLoginButton"
                                visible: !musicAccountPane.loggedIn
                                primary: true
                                iconName: "account"
                                text: Tr?.map["settings_wy_login_btn"] ?? ""
                                onClicked: musicAccountPane.openLogin()
                            }

                            FmButton {
                                objectName: "settingsMusicSyncButton"
                                visible: musicAccountPane.loggedIn
                                primary: false
                                iconName: "refresh"
                                text: Tr?.map["music_wy_sync_now"] ?? ""
                                enabled: !musicAccountPane.busy
                                onClicked: musicAccountPane.syncNow()
                            }

                            FmButton {
                                objectName: "settingsMusicRenewButton"
                                visible: musicAccountPane.loggedIn
                                primary: false
                                iconName: "timer"
                                text: Tr?.map["settings_wy_renew_btn"] ?? ""
                                enabled: !musicAccountPane.busy
                                onClicked: musicAccountPane.renewLogin()
                            }

                            FmButton {
                                objectName: "settingsMusicLogoutButton"
                                visible: musicAccountPane.loggedIn
                                primary: false
                                danger: true
                                text: Tr?.map["settings_wy_logout_btn"] ?? ""
                                onClicked: musicAccountPane.logout()
                            }

                            Item { Layout.fillWidth: true }
                        }
                    }

                    FmCard {
                        Layout.fillWidth: true

                        FmSwitch {
                            objectName: "settingsMusicBaikeSwitch"
                            Layout.fillWidth: true
                            enabled: (typeof Music !== "undefined" && Music) ? Music.ready : false
                            text: Tr?.map["settings_music_baike_original"] ?? ""
                            checked: (typeof Music !== "undefined" && Music) ? Music.baikeOriginal : true
                            onToggled: {
                                if (typeof Music !== "undefined" && Music)
                                    Music.setBaikeOriginal(checked);
                            }
                        }

                        Text {
                            Layout.fillWidth: true
                            text: Tr?.map["settings_music_baike_original_desc"] ?? ""
                            color: Theme?.textTertiary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                            wrapMode: Text.WordWrap
                        }
                    }
                }
            }

            Loader {
                id: musicAccountDialogLoader
                anchors.fill: parent
                active: musicAccountPane.dialogOpen
                source: "../../components/music/FmMusicAccountDialog.qml"
                onLoaded: {
                    item.account = musicAccountPane.account
                    item.opened = true
                }
            }

            Connections {
                target: musicAccountDialogLoader.item
                function onClosed() { musicAccountPane.dialogOpen = false }
            }
        }
    }

    // ══ 分区八：账户 ════════════════════════════════════════════
    //
    // 它是 `settings/account` 这条路由的内容，所以本分区**没有关闭按钮**
    // （离开 = 点别的分区 / 面包屑返回）。
    //
    // ## 三块内容
    //
    // 1. **标题 + 说明 + 动作条**：「全部刷新 Token」在启动时刷一遍之外随时可手动触发；
    // 2. **进度**：状态一句话 + 进度 + 「取消」（取消口在 `launcher/account.py`）；
    // 3. **账号列表**：`ListView` + 每行一张卡片（类型徽标 / 名字 / 当前标记 / UUID / 三个按钮），
    //    空列表走 `FmEmptyState`。
    //
    // ## 状态从哪来
    //
    // 全部来自 `Accounts` 桥（`app/bridges/accounts_bridge.py`）—— 本分区**没有业务规则**：
    // 参数校验、重名判断、删除后的落盘、导入导出、取消，都在服务层。
    //
    // ## 写法纪律（踩过的坑）
    //
    // * 绑定里只读 `Tr?.map[…]` 与本分区属性；动态状态（行、进度文案、值）一律在
    //   `refreshUi()` 里算好，函数第一行判活（`if (!section) return`）。
    // * 不在绑定里调组件自己的 JS 函数（FmTag 的教训）—— 类型徽标档位在
    //   `refreshUi()` 里算成 `type_level`。
    // * 确认框不传 JS 回调：桥只发 `deleteRequested(id, name)` 这样的信号，
    //   本分区据此填 `confirmRequest` 属性。
    // * 对话框是分区 `Item` 的子节点，靠 `visible` 开关；分区各有自己的 Item 作用域，
    //   id 不会与别的分区撞名，所以这里不需要 `Loader` 那层间接。
    //
    // 纪律：颜色只来自 `Theme.*`（R8）、图标走 `FmIcon`（R2）、文案走 `Tr.map[…]`（R3/R4）、
    // 只用 `qml/components` 里的件（R9）。

    Component {
        id: accountSection

        Item {
            id: section
            objectName: "accountSection"

            //: 桥缺席（注册失败）时整块走错误态，而不是绑一堆 `undefined` 报错
            readonly property bool live: (typeof Accounts !== "undefined" && Accounts) ? true : false

            //: 列表行（含 `refreshUi()` 算好的派生字段：`type_label` / `type_level` / `uuid_short`…）
            property var rows: []
            //: 进度（在函数里算，绑定只读属性）
            property bool progressActive: false
            property string progressText: ""
            property bool progressCancellable: false
            property real progressValue: 0

            //: 添加账号对话框：非空 = 显示（值就是 microsoft / offline / yggdrasil）
            property string addKind: ""
            property string addError: ""

            //: 确认框请求（`kind` = delete / refresh_token；空对象 = 不显示）
            property var confirmRequest: ({})
            //: 导出结果对话框要不要显示 + 导出到哪了
            property bool exportResultVisible: false
            property string exportPath: ""
            //: 导出密码的**第 2 步**（再输一遍核对）标记 + 不一致时的错误文案
            property bool exportConfirmStep: false
            property string exportPasswordError: ""
            //: **登录失败**的常驻提示（标题 + 原因）；标题非空就显示那条提示条。
            //: **登录失败不要静默**：Toast 会自己消失、状态栏 10 秒就清，
            //: 而登录失败要等一两分钟才出结果 —— 回来时两条都没了，用户只看到"什么都没发生"。
            property string failureTitle: ""
            property string failureDetail: ""

            // ── 文案工具：只在函数里调 ────────────────────────────────

            function t(key) {
                return Tr ? (Tr.map[key] ?? key) : key
            }

            function format(key, params) {
                if (!key)
                    return ""
                var text = section.t(key)
                if (!params)
                    return text
                for (var name in params)
                    text = text.replace("{" + name + "}", String(params[name]))
                return text
            }

            //: `file:///D:/a b/c.fmcl_accounts` → `D:/a b/c.fmcl_accounts`
            //: （与 `HomePage.localPath` / `SettingsPage.localPath` 同一实现）
            function localPath(url) {
                var text = String(url)
                text = text.replace(/^file:\/{2,3}/, "")
                try {
                    return decodeURIComponent(text)
                } catch (e) {
                    return text
                }
            }

            //: 类型徽标档位：microsoft=success / offline=warning / yggdrasil=accent
            function tagLevel(type) {
                if (type === "microsoft")
                    return "success"
                if (type === "offline")
                    return "warning"
                if (type === "yggdrasil")
                    return "accent"
                return "neutral"
            }

            // ── 刷新（全部在函数里赋值）──────────────────────────────

            function refreshUi() {
                if (!section)
                    return
                var source = (typeof Accounts !== "undefined" && Accounts) ? Accounts.accounts : []
                var rows = []
                for (var i = 0; i < source.length; i++) {
                    var row = source[i]
                    rows.push({
                        "id": String(row.id || ""),
                        "name": String(row.name || ""),
                        "display_name": String(row.display_name || row.name || ""),
                        "type": String(row.type || ""),
                        "type_label": section.t(String(row.type_key || "")),
                        "type_level": section.tagLevel(String(row.type || "")),
                        "uuid_short": String(row.uuid_short || "-"),
                        "current": !!row.current,
                        "is_microsoft": String(row.type || "") === "microsoft"
                    })
                }
                section.rows = rows

                if (!section.live) {
                    section.progressActive = false
                    section.progressText = ""
                    section.progressValue = 0
                    section.progressCancellable = false
                    return
                }

                var progress = Accounts.progress
                var total = Number(Accounts.progressTotal)
                var current = Number(Accounts.progressCurrent)
                section.progressActive = !!Accounts.busy
                section.progressCancellable = !!Accounts.cancellable
                section.progressValue = (total > 0) ? Math.max(0, Math.min(1, current / total)) : 0

                var message = String((progress && progress.message) ? progress.message : "")
                if (message.length > 0) {
                    //: 核心层的中文状态串**原样透出**：不在这里二次包装，待统一收口 i18n。
                    section.progressText = message
                } else {
                    var name = String((progress && progress.name) ? progress.name : "")
                    var base = section.t(String((progress && progress.kind === "refresh_all")
                                                ? "account_refresh_all" : "logging_in"))
                    section.progressText = (total > 1) ? (base + "  " + current + "/" + total)
                                                       : ((name.length > 0) ? (base + "  " + name) : base)
                }
            }

            Component.onCompleted: {
                if (section.live)
                    Accounts.refresh()
                section.refreshUi()
            }

            // ── 桥的信号 ─────────────────────────────────────────────

            Connections {
                target: (typeof Accounts !== "undefined" && Accounts) ? Accounts : null

                function onAccountsChanged() {
                    if (section)
                        section.refreshUi()
                }

                function onBusyChanged() {
                    if (section)
                        section.refreshUi()
                }

                function onProgressChanged() {
                    if (section)
                        section.refreshUi()
                }

                function onDeleteRequested(accountId, name) {
                    if (!section)
                        return
                    section.confirmRequest = {
                        "kind": "delete",
                        "id": String(accountId),
                        "title": section.t("confirm_delete"),
                        "message": section.format("account_delete_confirm", {"name": String(name)})
                    }
                }

                function onRefreshTokenRequested(accountId, name) {
                    if (!section)
                        return
                    section.confirmRequest = {
                        "kind": "refresh_token",
                        "id": String(accountId),
                        "title": section.t("account_refresh_token"),
                        "message": section.format("account_refresh_token_confirm", {"name": String(name)})
                    }
                }

                function onExportPasswordRequested() {
                    if (!section)
                        return
                    //: 进第 1 步。**错误文案不在这里清** —— 桥在"两次不一致"时是
                    //: `passwordMismatch` → `exportPasswordRequested` 连着发的，同一轮里清掉
                    //: 就会把刚到的文案抹没（推迟一轮也试过：那会让用户根本看不到提示）。
                    //: 正确的清理点是**用户重新点「导出」**时（`section.requestExport()`）
                    //: 与**取消**时 —— 见那两处。
                    section.exportConfirmStep = false
                    exportPasswordLoader.active = true
                }

                //: 导出第 2 步（再输一遍核对）
                function onExportPasswordConfirmRequested() {
                    if (!section)
                        return
                    section.exportConfirmStep = true
                    exportPasswordLoader.active = true
                }

                //: 两次不一致：桥把旧文案**随信号**送来（不再让页面去问"这次是不是重试"），
                //: 存下来，紧接着的 `exportPasswordRequested` 会把第 1 步重开。
                function onPasswordMismatch(message) {
                    if (section)
                        section.exportPasswordError = String(message)
                }

                function onExportFileRequested() {
                    if (section)
                        exportFileDialog.open()
                }

                function onImportPasswordRequested() {
                    if (section)
                        importPasswordLoader.active = true
                }

                function onImportFileRequested() {
                    if (section)
                        importFileDialog.open()
                }

                function onExported(path) {
                    if (!section)
                        return
                    section.exportPath = String(path)
                    section.exportResultVisible = true
                }

                //: 一条提示。`title` 非空 = **常驻提示条**（登录失败那条 —— Toast 会自己消失、
                //: 状态栏 10 秒就清，而登录失败往往要等一两分钟才出结果，回来时那两句都没了）。
                //: 其余情况仍是右下角 Toast（导入成功、重名提醒这类"顺手告知"）。
                function onNoticeRequested(message, level, title) {
                    var text = String(message)
                    var kind = (String(level) === "error") ? "error" : "info"
                    if (String(title).length > 0) {
                        //: 需要一条**看得见、不会自己消失**的提示；本分区用一条可关闭的
                        //: `FmInfoBar` 落地（QML 里没有"阻塞式弹窗"这种形态）。
                        section.failureTitle = String(title)
                        section.failureDetail = text
                        return
                    }
                    if (typeof Dialogs !== "undefined" && Dialogs && Dialogs.available)
                        Dialogs.notify({"level": kind, "message": text, "icon": "account"})
                }

                function onStatusMessage(text, level) {
                    if (typeof Shell !== "undefined" && Shell)
                        Shell.setStatus(String(text), String(level))
                }
            }

            // ══ 一、标题 + 说明 + 动作条 ═══════════════════════════════

            ColumnLayout {
                objectName: "accountLayout"
                anchors.fill: parent
                spacing: Theme?.spacingMd ?? 12

                FmCard {
                    objectName: "accountHeaderCard"
                    Layout.fillWidth: true

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: Theme?.spacingSm ?? 8

                        Text {
                            objectName: "accountTitle"
                            Layout.fillWidth: true
                            text: Tr?.map["account_manager_title"] ?? "account_manager_title"
                            color: Theme?.textPrimary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeTitle ?? 18
                            font.bold: true
                            elide: Text.ElideRight
                        }

                        Text {
                            objectName: "accountDescription"
                            Layout.fillWidth: true
                            text: Tr?.map["account_manager_desc"] ?? "account_manager_desc"
                            color: Theme?.textSecondary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeBase ?? 12
                            wrapMode: Text.WordWrap
                        }

                        //: 动作条。FmButton 只有 primary / danger 两档，
                        //: 所以微软用 primary（强调色）、另两个用次按钮。
                        RowLayout {
                            objectName: "accountActionRow"
                            Layout.fillWidth: true
                            Layout.topMargin: Theme?.spacingXs ?? 4
                            spacing: Theme?.spacingSm ?? 8

                            FmButton {
                                objectName: "accountAddMicrosoft"
                                Layout.preferredWidth: 110
                                text: Tr?.map["account_add_microsoft"] ?? "account_add_microsoft"
                                enabled: section.live && !section.progressActive
                                onClicked: {
                                    if (!section)
                                        return
                                    section.addError = ""
                                    section.addKind = "microsoft"
                                }
                            }

                            FmButton {
                                objectName: "accountAddOffline"
                                Layout.preferredWidth: 90
                                primary: false
                                text: Tr?.map["account_add_offline"] ?? "account_add_offline"
                                enabled: section.live && !section.progressActive
                                onClicked: {
                                    if (!section)
                                        return
                                    section.addError = ""
                                    section.addKind = "offline"
                                }
                            }

                            FmButton {
                                objectName: "accountAddYggdrasil"
                                Layout.preferredWidth: 100
                                primary: false
                                text: Tr?.map["account_add_yggdrasil"] ?? "account_add_yggdrasil"
                                enabled: section.live && !section.progressActive
                                onClicked: {
                                    if (!section)
                                        return
                                    section.addError = ""
                                    section.addKind = "yggdrasil"
                                }
                            }

                            Item { Layout.fillWidth: true }

                            //: A-25 在账号页的落点（3.5 新增）：**强制**刷新每个微软账号的 Token，
                            //: 不判是否过期（启动时那次静默刷新仍会判过期，语义分工见服务文档）。
                            FmButton {
                                objectName: "accountRefreshAll"
                                primary: false
                                iconName: "refresh"
                                text: Tr?.map["account_refresh_all"] ?? "account_refresh_all"
                                enabled: section.live && !section.progressActive
                                onClicked: {
                                    if (section.live)
                                        Accounts.refreshAll()
                                }
                            }

                            FmButton {
                                objectName: "accountImport"
                                primary: false
                                text: Tr?.map["account_import"] ?? "account_import"
                                enabled: section.live && !section.progressActive
                                onClicked: {
                                    if (section.live)
                                        Accounts.requestImport()
                                }
                            }

                            FmButton {
                                objectName: "accountExport"
                                primary: false
                                text: Tr?.map["account_export"] ?? "account_export"
                                enabled: section.live && !section.progressActive
                                onClicked: {
                                    if (section.live) {
                                        //: 用户重新点「导出」= 全新一轮，把上一轮的错误清掉
                                        section.exportPasswordError = ""
                                        Accounts.requestExport()
                                    }
                                }
                            }
                        }
                    }
                }

                // ══ 二、登录失败的常驻提示（**不要静默**）══
                //
                // 一条可关闭的 `FmInfoBar`（QML 里没有"阻塞式弹窗"这种形态），正文是
                // **真实原因**（核心层最后一条错误，例如
                // "认证失败: … Failed to resolve 'xsts.auth.xboxlive.com'"）——
                // 那句话才是用户判断"是网络问题还是账号没买游戏"的依据。

                FmInfoBar {
                    objectName: "accountFailureBar"
                    Layout.fillWidth: true
                    visible: section.failureTitle.length > 0
                    level: "error"
                    closable: true
                    text: section.failureTitle
                          + (section.failureDetail.length > 0 ? "\n" + section.failureDetail : "")
                    onClosed: {
                        if (section) {
                            section.failureTitle = ""
                            section.failureDetail = ""
                        }
                    }
                }

                // ══ 三、进度（登录 / 批量刷新进行中）═══════════════════

                FmCard {
                    objectName: "accountProgressCard"
                    Layout.fillWidth: true
                    visible: section.progressActive

                    ColumnLayout {
                        objectName: "accountProgressColumn"
                        Layout.fillWidth: true
                        spacing: Theme?.spacingXs ?? 4
                        visible: section.progressActive

                        RowLayout {
                            objectName: "accountProgressRow"
                            Layout.fillWidth: true
                            spacing: Theme?.spacingSm ?? 8

                            FmProgressRing {
                                objectName: "accountProgressRing"
                                Layout.preferredWidth: 20
                                Layout.preferredHeight: 20
                                indeterminate: true
                                visible: section.progressValue <= 0
                            }

                            Text {
                                objectName: "accountProgressLabel"
                                Layout.fillWidth: true
                                text: section.progressText
                                color: Theme?.textPrimary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeBase ?? 12
                                elide: Text.ElideRight
                            }

                            FmButton {
                                objectName: "accountCancel"
                                primary: false
                                danger: true
                                visible: section.progressCancellable
                                text: Tr?.map["cancel"] ?? "cancel"
                                onClicked: {
                                    if (section.live)
                                        Accounts.cancel()
                                }
                            }
                        }

                        FmProgressBar {
                            objectName: "accountProgressBar"
                            Layout.fillWidth: true
                            visible: section.progressActive && section.progressValue > 0
                            value: section.progressValue
                        }
                    }
                }

                // ══ 三、账号列表 ═══════════════════════════════════════

                FmCard {
                    objectName: "accountListCard"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    padding: Theme?.spacingMd ?? 12

                    FmEmptyState {
                        objectName: "accountEmptyState"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        visible: section.rows.length === 0
                        iconName: "account"
                        title: Tr?.map["account_no_accounts"] ?? "account_no_accounts"
                    }

                    ListView {
                        id: accountList
                        objectName: "accountListView"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        visible: section.rows.length > 0
                        clip: true
                        spacing: Theme?.spacingSm ?? 8
                        model: section.rows
                        ScrollBar.vertical: FmScrollBar {}

                        delegate: Item {
                            id: accountRow
                            objectName: "accountRow"
                            width: accountList.width
                            //: 委托高度 = 卡片自己的隐式高度（ListView 的委托不是布局托管的，
                            //: 不会被拉伸；显式写高度是为了让 `clip` 与滚动条的 contentHeight 算得准）
                            height: accountCard.implicitHeight

                            readonly property var row: (modelData === undefined) ? ({}) : modelData

                            FmCard {
                                id: accountCard
                                objectName: "accountCard"
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.top: parent.top
                                padding: Theme?.spacingMd ?? 12

                                RowLayout {
                                    objectName: "accountRowLayout"
                                    Layout.fillWidth: true
                                    spacing: Theme?.spacingMd ?? 12

                                    //: 当前账号的强调色指示条。用「指示条 + 名字前 ★ + 「当前」标签」
                                    //: 三重表达，避免同一屏两种卡片底色。
                                    //: 顺带满足视觉回归"每个页面至少一个 `Theme.accent` 像素"这条判据。
                                    Rectangle {
                                        objectName: "accountCurrentIndicator"
                                        Layout.preferredWidth: 3
                                        Layout.fillHeight: true
                                        Layout.minimumHeight: 30
                                        radius: 1.5
                                        color: Theme?.accent ?? "transparent"
                                        visible: accountRow.row.current === true
                                    }

                                    ColumnLayout {
                                        objectName: "accountRowInfo"
                                        Layout.fillWidth: true
                                        spacing: Theme?.spacingXs ?? 4

                                        RowLayout {
                                            Layout.fillWidth: true
                                            spacing: Theme?.spacingSm ?? 8

                                            FmTag {
                                                objectName: "accountTypeTag-" + String(accountRow.row.id || "")
                                                text: String(accountRow.row.type_label || "")
                                                level: String(accountRow.row.type_level || "neutral")
                                            }

                                            Text {
                                                objectName: "accountName-" + String(accountRow.row.id || "")
                                                Layout.fillWidth: true
                                                text: String(accountRow.row.display_name || "")
                                                color: Theme?.textPrimary ?? "transparent"
                                                font.pixelSize: Theme?.fontSizeBase ?? 12
                                                font.bold: true
                                                elide: Text.ElideRight
                                            }

                                            FmTag {
                                                objectName: "accountCurrentTag"
                                                visible: accountRow.row.current === true
                                                level: "accent"
                                                text: Tr?.map["account_current"] ?? "account_current"
                                            }
                                        }

                                        Text {
                                            objectName: "accountUuid-" + String(accountRow.row.id || "")
                                            Layout.fillWidth: true
                                            //: 截断规则在桥的 `uuid_short` 里，这里只负责显示
                                            text: "UUID: " + String(accountRow.row.uuid_short || "-")
                                            color: Theme?.textSecondary ?? "transparent"
                                            font.pixelSize: Theme?.fontSizeSmall ?? 11
                                            elide: Text.ElideRight
                                        }
                                    }

                                    RowLayout {
                                        objectName: "accountRowActions"
                                        spacing: Theme?.spacingXs ?? 4

                                        FmButton {
                                            //: 名字里带账号 id —— 探针要按 id 精确点到"某一行"的按钮
                                            //: （同名按钮会有 N 个，`item()` 只能拿到第一个）
                                            objectName: "accountSetCurrent-" + String(accountRow.row.id || "")
                                            visible: accountRow.row.current !== true
                                            Layout.preferredWidth: 90
                                            text: Tr?.map["account_set_current"] ?? "account_set_current"
                                            enabled: section.live && !section.progressActive
                                            onClicked: {
                                                if (section.live)
                                                    Accounts.setCurrent(String(accountRow.row.id || ""))
                                            }
                                        }

                                        FmButton {
                                            objectName: "accountRefreshToken-" + String(accountRow.row.id || "")
                                            //: 只有微软账号能刷 Token
                                            visible: accountRow.row.is_microsoft === true
                                            primary: false
                                            Layout.preferredWidth: 90
                                            text: Tr?.map["account_refresh_token"] ?? "account_refresh_token"
                                            enabled: section.live && !section.progressActive
                                            onClicked: {
                                                if (section.live)
                                                    Accounts.requestRefreshToken(String(accountRow.row.id || ""))
                                            }
                                        }

                                        FmButton {
                                            objectName: "accountDelete-" + String(accountRow.row.id || "")
                                            danger: true
                                            Layout.preferredWidth: 70
                                            text: Tr?.map["account_delete"] ?? "account_delete"
                                            enabled: section.live && !section.progressActive
                                            onClicked: {
                                                if (section.live)
                                                    Accounts.requestDelete(String(accountRow.row.id || ""))
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

        // ══ 四、对话框（全部声明式；见文件头纪律第 3、4 条）═════════

        Loader {
            id: confirmLoader
            objectName: "accountConfirmLoader"
            anchors.fill: parent
            active: Object.keys(section.confirmRequest).length > 0
            sourceComponent: confirmComponent
        }

        Component {
            id: confirmComponent

            ConfirmDialog {
                objectName: "accountConfirmDialog"
                request: ({"title": String((section.confirmRequest || {}).title || ""),
                           "message": String((section.confirmRequest || {}).message || ""),
                           "default": true})
                onAnswered: function (value) {
                    if (!section)
                        return
                    var request = section.confirmRequest || ({})
                    section.confirmRequest = ({})
                    if (!value)
                        return
                    if (request.kind === "delete")
                        Accounts.confirmDelete(String(request.id || ""))
                    else if (request.kind === "refresh_token")
                        Accounts.confirmRefreshToken(String(request.id || ""))
                }
            }
        }

        Loader {
            id: addLoader
            objectName: "accountAddLoader"
            anchors.fill: parent
            active: section.addKind.length > 0
            sourceComponent: addComponent
            onLoaded: {
                if (item) {
                    item.reset()
                    item.forceActiveFocus()
                }
            }
        }

        Component {
            id: addComponent

            AddAccountDialog {
                objectName: "accountAddDialog"
                kind: section.addKind
                errorText: section.addError
                onSubmitted: function (payload) {
                    if (!section)
                        return
                    section.addError = ""
                    section.addKind = ""
                    if (section.live)
                        Accounts.startLogin(String(payload.kind || ""), payload)
                }
                onCancelled: {
                    if (section) {
                        section.addError = ""
                        section.addKind = ""
                    }
                }
            }
        }

        Loader {
            id: importPasswordLoader
            objectName: "accountImportPasswordLoader"
            anchors.fill: parent
            active: false
            sourceComponent: importPasswordComponent
        }

        Component {
            id: importPasswordComponent

            PasswordDialog {
                objectName: "accountImportPasswordDialog"
                title: section.t("account_import_title")
                prompt: section.t("account_import_password_prompt")
                password: true
                onSubmitted: function (value) {
                    if (!section)
                        return
                    importPasswordLoader.active = false
                    if (section.live)
                        Accounts.setImportPassword(String(value))
                }
                onCancelled: {
                    if (!section)
                        return
                    importPasswordLoader.active = false
                    if (section.live)
                        Accounts.setImportPassword("")
                }
            }
        }

        Loader {
            id: exportPasswordLoader
            objectName: "accountExportPasswordLoader"
            anchors.fill: parent
            active: false
            sourceComponent: exportPasswordComponent
        }

        Component {
            id: exportPasswordComponent

            //: 导出**两步问答**：第 1 步设密码、第 2 步再输一遍核对。
            //: 两步共用这一个对话框实例 —— 第 2 步只换提示语与标题，
            //: `PasswordDialog.onPromptChanged` 会在换步时清空上一格的输入。
            PasswordDialog {
                objectName: "accountExportPasswordDialog"
                title: section.t("account_export_title")
                prompt: section.exportConfirmStep
                        ? section.t("account_export_password_confirm")
                        : section.t("account_export_password_prompt")
                //: 第 2 步的标题带一句"（再输一次）"，免得用户以为是同一个框卡住了。
                password: true
                errorText: section.exportPasswordError
                onSubmitted: function (value) {
                    if (!section)
                        return
                    if (section.exportConfirmStep) {
                        section.exportConfirmStep = false
                        //: 一致 → 桥会发 `exportFileRequested`；不一致 → 会发
                        //: `passwordMismatch` 并重开第 1 步的框。两种情况下这个框都该先收起来，
                        //: 由紧随其后的信号决定要不要再打开。
                        exportPasswordLoader.active = false
                        if (section.live)
                            Accounts.confirmExportPassword(String(value))
                    } else {
                        exportPasswordLoader.active = false
                        if (section.live)
                            Accounts.setExportPassword(String(value))
                    }
                }
                onCancelled: {
                    if (!section)
                        return
                    section.exportConfirmStep = false
                    section.exportPasswordError = ""
                    exportPasswordLoader.active = false
                    if (section.live)
                        Accounts.setExportPassword("")
                }
            }
        }

        Loader {
            id: exportResultLoader
            objectName: "accountExportResultLoader"
            anchors.fill: parent
            active: section.exportResultVisible
            sourceComponent: exportResultComponent
        }

        Component {
            id: exportResultComponent

            ExportResultDialog {
                objectName: "accountExportResultDialog"
                exportPath: section.exportPath
                onOpenFolderRequested: {
                    if (section.live)
                        Accounts.openExportFolder()
                }
                onClosed: {
                    if (section)
                        section.exportResultVisible = false
                }
            }
        }

        //: 导入文件（过滤器：`*.fmcl_accounts` + 全部文件）
        FileDialog {
            id: importFileDialog
            objectName: "accountImportFileDialog"
            title: section.t("account_import_title")
            nameFilters: ["*" + section.fileSuffix(), "*"]
            onAccepted: {
                if (section.live)
                    Accounts.submitImport(section.localPath(selectedFile))
            }
        }

        //: 导出文件（默认扩展名 `.fmcl_accounts`）
        FileDialog {
            id: exportFileDialog
            objectName: "accountExportFileDialog"
            title: section.t("account_export_title")
            fileMode: FileDialog.SaveFile
            defaultSuffix: "fmcl_accounts"
            nameFilters: ["*" + section.fileSuffix()]
            onAccepted: {
                if (section.live)
                    Accounts.submitExportPath(section.localPath(selectedFile))
            }
        }

        //: 桥上的后缀（`.fmcl_accounts`）；桥缺席时退回常量，保证文件框仍可用
        function fileSuffix() {
            return (typeof Accounts !== "undefined" && Accounts) ? String(Accounts.fileSuffix) : ".fmcl_accounts"
        }
        }
    }

    // ══ 分区九~十：AI / 插件 ════════════════════════════════════

    // ══ 分区九：AI 模型 / 工具权限 / Skills / 语音 / 净读账号 ══
    Component {
        id: jingduSection

        FmAgentSettingsSection {
            objectName: "settingsJingduSection"
        }
    }

    // ══ 分区十：插件 ════════════════════════════════════════════
    Component {
        id: pluginSection

        FmPluginSection {
            objectName: "settingsPluginSection"
        }
    }

    Component {
        id: placeholderSection

        FmEmptyState {
            objectName: "settingsPlaceholder"
            property string sectionKey: ""

            function refresh() {
                if (!page)
                    return
                var name = page.section
                sectionKey = (name === "ai") ? "settings_tab_ai" : "plugin_manager_title"
                title = page.t(sectionKey)
                description = page.t("page_placeholder_hint")
            }

            Component.onCompleted: refresh()

            Connections {
                target: (typeof Tr !== "undefined" && Tr) ? Tr : null

                function onLanguageChanged() {
                    if (page)
                        page.refreshUi()
                }
            }
        }
    }

    //: Java 可执行文件选择器
    FileDialog {
        id: javaDialog
        objectName: "settingsJavaDialog"
        title: page ? page.t("settings_java_custom_path") : ""
        nameFilters: ["*.exe", "*"]
        onAccepted: {
            if (page && page.live)
                Settings.setField("java_custom_path", page.localPath(selectedFile))
        }
    }

    // ── 文件选择器（B4 的动作型条目：导入主题 / 导出日志）────────
    FileDialog {
        id: themeDialog
        objectName: "settingsThemeDialog"
        title: page ? page.t("settings_theme_import_title") : ""
        nameFilters: ["*.json"]
        onAccepted: {
            if (page && page.live)
                Settings.importTheme(page.localPath(selectedFile))
        }
    }

    FileDialog {
        id: logDialog
        objectName: "settingsLogDialog"
        title: page ? page.t("settings_log_export_title") : ""
        fileMode: FileDialog.SaveFile
        defaultSuffix: "log"
        nameFilters: ["*.log"]
        onAccepted: {
            if (page && page.live && typeof Logs !== "undefined" && Logs)
                Logs.exportLog(page.localPath(selectedFile))
        }
    }

    //: `file:///D:/a b/c.png` → `D:/a b/c.png`（与 `HomePage.localPath` 同一实现）
    function localPath(url) {
        var text = String(url)
        text = text.replace(/^file:\/{2,3}/, "")
        try {
            return decodeURIComponent(text)
        } catch (e) {
            return text
        }
    }
}
