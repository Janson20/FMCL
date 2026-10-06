// SettingsPage.qml —— 设置域（阶段 3 任务 3.4；IA 见 `docs/refactor/02` 第 4.12 节）。
//
// ## 一、一页承载 8 条路由
//
// 路由表（`app/bridges/nav_bridge.py`）把设置拆成 8 条二级路由，全部指向本文件：
// `settings`（= 启动器）、`settings/launcher`、`settings/java`、`settings/theme`、
// `settings/log`、`settings/about`、`settings/account`、`settings/ai`、`settings/plugin`。
// 本文件按 `routeId` 切左侧分区导航 + 右侧内容（`Loader`），**一屏一个分区**。
// 分区清单**从 `Nav.routes()` 现取**（`parent === "settings"`），不写死在 QML 里 ——
// 路由表加一条，这里就多一个分区，不会出现"路由进得去但导航里看不见"。
//
// 3.4 只做「启动器 / Java / 主题 / 日志 / 关于」五个分区（用户 2026-10-06 裁决 A）；
// 账户 / AI / 插件三个分区按 D-174 的先例给**占位内容**，随 3.5 / 3.22 / 3.23 迁。
//
// ## 二、草稿范式（M-Q1 的 B1/B2/B6，用户 2026-10-06 裁决）
//
// 控件改动**只进草稿**（`Settings.setField`）；底部动作条上的「保存」才写盘、
// 「取消」丢弃并把预览还原成已保存值。主题/强调色/语言三项在改动瞬间**只改内存**
// 预览（服务层做，桥负责刷新 `Theme` / `Tr` 的只读快照）—— 用户点保存之前就看得见
// 效果，取消之后效果又退回去。带着未保存改动离开设置域时，由壳层的"离开守卫"
// 提示一次（`qml/App.qml` 的 `onLeaveBlocked`；守卫登记在 `SettingsBridge` 里）。
//
// ## 三、写法纪律（3.2/3.3 踩过的坑，这里写死）
//
// 1. **绑定里不出现 `page.*`**：页面被销毁时（切走路由 → `StackView` 弹栈）绑定会再
//    求值一次，而 id 作用域已经变成 null —— 3.2 报的是 `page.t()`、3.3 报的是
//    `page.bridgeAvailable`，两次都是"TypeError 刷满日志"。所以：
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

FmPage {
    id: page
    objectName: "settingsPage"

    //: 桥缺席（注册失败）时整页走错误态，而不是绑一堆 `undefined` 报错
    readonly property bool live: (typeof Settings !== "undefined" && Settings) ? true : false

    //: 当前分区（`settings/theme` → `theme`；无子路径的 `settings` → `launcher`，
    //: 与旧界面把启动器设置放在第一个标签页一致）。
    //:
    //: **刻意不做成绑定**，原因是一次实测：`PageStack.pushFrame()` 是
    //: `createObject()` 之后再用 `StackView.push(item, {routeId: …})` 写属性的，
    //: 那一刻 `routeIdChanged` 处理器**看得到新值**（探针日志 `[settings/theme→launcher]`），
    //: 而依赖它的绑定却还是旧值 —— 于是页面停在启动器分区、主题分区永远不加载。
    //: 所以这里由 `refreshUi()` 现算现赋（与"动态状态一律在函数里赋值"同一条纪律）。
    property string section: "launcher"

    //: 显示顺序：先做好的五个分区在上，三个占位分区在下 —— 打开设置先看到能用的东西。
    //: 清单之外的 id 追加在末尾（路由表加新分区时不会漏，只是排在后面）。
    readonly property var sectionOrder: ["launcher", "java", "theme", "log", "about",
                                          "account", "ai", "plugin"]

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
            //: 一级导航位"的开发自查页）。它**不是**设置分区，这里必须排除 ——
            //: 第一版没排，设置页左侧就多出一个"组件画廊"。
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
        if (name === "java")
            return javaSection
        if (name === "theme")
            return themeSection
        if (name === "log")
            return logSection
        if (name === "about")
            return aboutSection
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
            //: （布局托管的项带锚点会被 Qt 判定为未定义行为，实测刷 8 条警告）。
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
                        //: **必须卡住高度**：只给 `preferredHeight` 不够 —— 实测
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
            RowLayout {
                objectName: "settingsActionBar"
                Layout.fillWidth: true
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

                //: 旧底部的「应用」按钮（M-24）—— 它其实是**重启启动器进程**。
                //: 用户裁决 B5：保留按钮但改名「保存并重启」，且先落盘再重启。
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

    // ══ 分区一：启动器功能（M-01 / M-02 / M-06 / M-12）══════════
    //
    // 旧界面把这些放在第一个标签页（`launcher_settings.py:110-567`）。
    // 语言（M-06）在本分区而不是独立分区：路由表没有 `settings/language`
    // 这一条，而旧界面也把它放在 tab1（`launcher_settings.py:325-361`）。

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
                            //: 旧实现这里放的是「重启启动器后生效」（`settings_restart_hint`）。
                            //: M-Q2 已裁决"照实升级为热切换"，所以那句提示现在是**错的** ——
                            //: 换成如实描述，这条差异登记在 `07-known-defects.md`。
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

    // ══ 分区二：Java 运行时（M-03 / M-04 / M-05）═══════════════

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

                                    //: 单选应用（旧实现的 `CTkRadioButton` + `_on_java_scan_select`）。
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

    // ══ 分区三：主题（M-07 ~ M-11）══════════════════════════════

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

            //: 应用自定义强调色（M-09）。空输入 = 清除（旧实现的判据：
            //: 空串走 `settings_accent_cleared`，非法格式走 `settings_accent_invalid`）。
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

    // ══ 分区四：日志（A-13 / A-18；03 章的 3.4 摘要要求"查看与导出"）══

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

                //: 日志面板（阶段 2 任务 2.18 交付的通用件）。
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

    // ══ 分区五：关于（A-08 / J-01 / J-02 / J-03）════════════════
    //
    // 旧界面是一个独立 Toplevel（`ui/app_handlers.py:1487-1700`）：图标 + FMCL/Fusion
    // Minecraft Launcher + 4 行系统信息 + 9 个鸣谢项目（各带"地址 / 许可证"两个外链）
    // + 许可证一行 + 赞赏按钮 + 确定。这里换成设置域里的一条路由（02 §4.12 的 L2"关于"），
    // 内容逐条保留；协议全文（J-01）复用 3.1 的 `legal_service` 渲染方式。

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

    // ══ 分区六~八：账户 / AI / 插件（占位，随 3.5 / 3.22 / 3.23 迁）══
    //
    // 用户 2026-10-06 裁决 A：3.4 只做「启动器 / Java / 主题 / 日志 / 关于」。
    // 这三个分区在旧界面里是同一个设置窗口的另外三个标签页（账户管理 / AI 模型 / 插件），
    // 内容分别属于 3.5 / 3.22 / 3.23 —— 这里按 D-174 的先例给一个明确的占位，
    // **不留白屏**（"这个页面还没做好"比"点了没反应"强得多）。

    Component {
        id: placeholderSection

        FmEmptyState {
            objectName: "settingsPlaceholder"
            property string sectionKey: ""

            function refresh() {
                if (!page)
                    return
                var name = page.section
                sectionKey = name === "account" ? "account_manager_title"
                           : (name === "ai" ? "settings_tab_ai" : "plugin_manager_title")
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

    //: Java 可执行文件选择器（旧 `_on_java_custom_browse` 的 `filedialog.askopenfilename`）
    FileDialog {
        id: javaDialog
        objectName: "settingsJavaDialog"
        title: page.t("settings_java_custom_path")
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
        title: page.t("settings_theme_import_title")
        nameFilters: ["*.json"]
        onAccepted: {
            if (page && page.live)
                Settings.importTheme(page.localPath(selectedFile))
        }
    }

    FileDialog {
        id: logDialog
        objectName: "settingsLogDialog"
        title: page.t("settings_log_export_title")
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
