// ServerConfigPage.qml —— 服务器配置编辑器（阶段 3 任务 3.11，对照表 L-36 ~ L-49）。
//
// 数据全部来自 ServerConfig 桥；本页只做三件事：把行快照画成控件、把用户动作
// 发回桥、把桥的状态翻成页面级提示。分类规则、默认值、INT 范围、写盘策略都在
// services/server_config_service.py，QML 不复制任何一条。
//
// 布局：搜索行 → 状态提示 → 左侧 7 分类导航 + 右侧表单/原文 → 底部动作条。
// 表单用 ListView（49 行 + 分组标题），原文用 FmTextArea；两者都只占内容区。
// 关闭按钮先走页面自己的确认框；顶栏返回 / 侧栏跳转由 Nav 的页面级离开守卫拦下，
// 壳层根据 Nav.pendingLeaveSource 选择 server_config_close_confirm_* 文案。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../components"
import "../../components/dialogs"

FmPage {
    id: page
    objectName: "serverConfigPage"

    readonly property bool live: (typeof ServerConfig !== "undefined" && ServerConfig) ? true : false
    readonly property var params: page.routeParams ? page.routeParams : ({})
    readonly property string version: {
        if (!page.live)
            return ""
        var fromRoute = page.params ? String(page.params.server || "") : ""
        if (fromRoute.length > 0)
            return fromRoute
        if (typeof Servers !== "undefined" && Servers && Servers.hasSelection)
            return String(Servers.selected || "")
        return String(ServerConfig.version || "")
    }

    property bool activating: true
    property string infoText: ""
    property string infoLevel: "info"
    property string rawDraft: ""
    property bool resetConfirmVisible: false
    property bool rawConfirmVisible: false
    property bool closeConfirmVisible: false
    property bool validationVisible: false
    property string validationText: ""
    property string hintText: ""

    title: page.version.length > 0
           ? page.format("server_config_title", {"version": page.version})
           : (Tr?.map["server_config_panel_btn"] ?? "server_config_panel_btn")
    description: Tr?.map["server_config_subtitle"] ?? "server_config_subtitle"
    iconName: "slider"

    contentState: (!page.live) ? "error"
                  : (page.activating ? "loading"
                  : ((ServerConfig.pageState === "ready") ? "ready" : "error"))
    loadingText: Tr?.map["plugin_state_loading"] ?? "plugin_state_loading"
    errorText: Tr?.map["plugin_state_error"] ?? "plugin_state_error"
    errorDetail: (page && page.live && !page.version.length)
                 ? (Tr?.map["server_no_version_selected"] ?? "server_no_version_selected")
                 : (page && page.live ? String(ServerConfig.errorText) : "")
    retryText: Tr?.map["version_refresh"] ?? "version_refresh"

    onRetried: page.reloadPage()

    function t(key) {
        return Tr ? (Tr?.map[key] ?? key) : key
    }

    function format(key, values) {
        var text = page.t(key)
        if (!values)
            return text
        for (var name in values)
            text = text.split("{" + name + "}").join(String(values[name]))
        return text
    }

    function applyRoute() {
        if (!page || !page.live)
            return
        var wanted = page.params ? String(page.params.server || "") : ""
        if (!wanted.length && typeof Servers !== "undefined" && Servers && Servers.hasSelection)
            wanted = String(Servers.selected || "")
        if (!wanted.length) {
            page.activating = false
            return
        }
        page.activating = true
        ServerConfig.activate(wanted)
        page.activating = false
        page.rawDraft = ServerConfig.rawText
    }

    function reloadPage() {
        if (!page.live)
            return
        page.activating = true
        ServerConfig.release()
        page.applyRoute()
        page.activating = false
    }

    function selectCategory(categoryId) {
        if (!page.live)
            return
        page.infoText = ""
        searchField.text = ""
        ServerConfig.setCategory(String(categoryId))
        if (String(categoryId) === "raw")
            page.rawDraft = ServerConfig.rawText
    }

    function applySearch(text) {
        if (!page.live)
            return
        ServerConfig.setSearch(String(text || ""))
    }

    function savePage() {
        if (!page.live)
            return
        page.validationVisible = false
        var result = ServerConfig.save()
        if (result && result.ok)
            page.rawDraft = ServerConfig.rawText
    }

    function requestClose() {
        if (!page.live)
            return
        if (ServerConfig.dirtyCount > 0) {
            page.closeConfirmVisible = true
            return
        }
        page.leaveNow()
    }

    function leaveNow() {
        page.closeConfirmVisible = false
        if (page.live)
            ServerConfig.discardDraft()
        if (typeof Nav !== "undefined" && Nav) {
            Nav.goBack()
            return
        }
        if (typeof Servers !== "undefined" && Servers)
            Servers.goBack()
    }

    function memoryIndex(items, value) {
        for (var i = 0; i < items.length; i++) {
            if (String(items[i]) === String(value))
                return i
        }
        return 0
    }

    function enumIndex(items, value) {
        if (!items)
            return 0
        for (var i = 0; i < items.length; i++) {
            if (String(items[i].value) === String(value))
                return i
        }
        return 0
    }

    function showGroup(index, key) {
        if (index <= 0 || !page.live)
            return true
        var rows = ServerConfig.rows
        if (!rows || index >= rows.length)
            return true
        var previous = rows[index - 1]
        if (!previous)
            return true
        return String(previous.groupKey || "") !== String(key || "")
    }

    function groupTitle(key) {
        return Tr ? (Tr?.map[key] ?? key) : key
    }

    function resetScroll() {
        if (formList)
            formList.positionViewAtBeginning()
    }

    function resetTip() {
        return page.t("server_config_reset_tip")
    }

    function folderTip() {
        return page.t("server_config_open_folder")
    }

    function scheduleRoute() {
        Qt.callLater(page.applyRoute)
    }

    Component.onCompleted: page.scheduleRoute()
    onRouteParamsChanged: page.scheduleRoute()

    Connections {
        id: configConnections
        target: (typeof ServerConfig !== "undefined" && ServerConfig) ? ServerConfig : null

        function onStatusMessage(text, level) {
            page.infoText = String(text)
            page.infoLevel = String(level)
        }

        function onValidationFailed(text, key) {
            page.validationText = String(text)
            page.validationVisible = true
        }

        function onDirtyChanged() {
            //: 结果提示说的是"上一次操作"，用户一改就不该继续挂着；错误/警告保留。
            if (page.live && ServerConfig.dirtyCount > 0
                    && page.infoLevel !== "error" && page.infoLevel !== "warning")
                page.infoText = ""
        }

        function onRowsChanged() {
            Qt.callLater(page.resetScroll)
        }

        function onRawChanged() {
            page.rawDraft = ServerConfig.rawText
        }
    }

    Connections {
        id: languageConnections
        target: (typeof Tr !== "undefined" && Tr) ? Tr : null

        function onLanguageChanged() {
            if (page.live)
                ServerConfig.refreshLabels()
        }
    }

    Component.onDestruction: {
        if (configConnections)
            configConnections.target = null
        if (languageConnections)
            languageConnections.target = null
        if (page.live)
            ServerConfig.release()
    }

    Timer {
        id: runtimeTimer
        interval: 2000
        repeat: true
        running: page.visible && page.live
        onTriggered: {
            if (page.live)
                ServerConfig.refreshRuntime()
        }
    }

    ColumnLayout {
        objectName: "serverConfigPane"
        anchors.fill: parent
        spacing: Theme?.spacingMd ?? 10

        // ── 搜索 + 搜索结果 + 脏标记 ──
        RowLayout {
            objectName: "serverConfigSearchRow"
            Layout.fillWidth: true
            Layout.fillHeight: false
            spacing: Theme?.spacingSm ?? 8

            FmSearchField {
                id: searchField
                objectName: "serverConfigSearchField"
                Layout.fillWidth: true
                placeholderText: Tr?.map["server_config_search_placeholder"] ?? "server_config_search_placeholder"
                onTextEdited: function (text) { page.applySearch(text) }
                onCleared: page.applySearch("")
            }

            Text {
                objectName: "serverConfigSearchCount"
                visible: page.live && ServerConfig.searching
                text: page.live
                      ? page.format("server_config_search_result", {"count": ServerConfig.searchCount})
                      : ""
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                Layout.alignment: Qt.AlignVCenter
            }

            FmTag {
                objectName: "serverConfigDirtyTag"
                text: (page.live && ServerConfig.dirtyCount > 0)
                      ? page.format("server_config_modified", {"count": ServerConfig.dirtyCount})
                      : (Tr?.map["server_config_no_changes"] ?? "server_config_no_changes")
                level: (page.live && ServerConfig.dirtyCount > 0) ? "warning" : "neutral"
                Layout.alignment: Qt.AlignVCenter
            }
        }

        // ── 运行状态 / 文件缺失 / 路径 ──
        RowLayout {
            objectName: "serverConfigRuntimeRow"
            Layout.fillWidth: true
            Layout.fillHeight: false
            spacing: Theme?.spacingSm ?? 8

            Text {
                objectName: "serverConfigRuntimeText"
                text: (page.live && ServerConfig.running)
                      ? (Tr?.map["server_config_running_warning"] ?? "server_config_running_warning")
                      : (Tr?.map["server_config_not_running"] ?? "server_config_not_running")
                color: (page.live && ServerConfig.running)
                       ? (Theme?.warning ?? "transparent")
                       : (Theme?.textSecondary ?? "transparent")
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            Text {
                objectName: "serverConfigFileMissing"
                visible: page.live && ServerConfig.fileMissing
                text: Tr?.map["server_config_file_missing"] ?? "server_config_file_missing"
                color: Theme?.warning ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                wrapMode: Text.WordWrap
                Layout.alignment: Qt.AlignRight
            }
        }

        Text {
            objectName: "serverConfigPath"
            Layout.fillWidth: true
            Layout.fillHeight: false
            text: page.live ? String(ServerConfig.propertiesPath || "") : ""
            color: Theme?.textTertiary ?? "transparent"
            font.pixelSize: Theme?.fontSizeSmall ?? 10
            elide: Text.ElideMiddle
        }

        FmInfoBar {
            id: infoBar
            objectName: "serverConfigInfoBar"
            Layout.fillWidth: true
            Layout.fillHeight: false
            visible: page.infoText.length > 0
            text: page.infoText
            level: page.infoLevel
            closable: true
            onClosed: page.infoText = ""
        }

        // ── 主体：分类导航 + 表单/原文 ──
        RowLayout {
            objectName: "serverConfigBody"
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Theme?.spacingSm ?? 8

            ListView {
                id: categoryList
                objectName: "serverConfigCategoryList"
                Layout.preferredWidth: 168
                Layout.fillHeight: true
                clip: true
                spacing: 2
                model: page.live ? ServerConfig.categories : []
                ScrollBar.vertical: FmScrollBar {}

                delegate: FmListItem {
                    required property var modelData
                    width: categoryList.width
                    title: page.groupTitle(String(modelData.titleKey || ""))
                    iconName: "slider"
                    selected: page.live && !ServerConfig.searching
                              && String(ServerConfig.category) === String(modelData.id)
                    onClicked: page.selectCategory(String(modelData.id))
                }
            }

            Rectangle {
                objectName: "serverConfigDivider"
                Layout.preferredWidth: 1
                Layout.fillHeight: true
                color: Theme?.divider ?? "transparent"
            }

            Item {
                objectName: "serverConfigContent"
                Layout.fillWidth: true
                Layout.fillHeight: true

                ListView {
                    id: formList
                    objectName: "serverConfigFormList"
                    anchors.fill: parent
                    visible: page.live && (String(ServerConfig.category) !== "raw" || ServerConfig.searching)
                    clip: true
                    spacing: Theme?.spacingXs ?? 4
                    model: page.live ? ServerConfig.rows : []
                    ScrollBar.vertical: FmScrollBar {}

                    delegate: Column {
                        id: rowBlock
                        required property var modelData
                        required property int index

                        readonly property bool showHeader: page.showGroup(index, String(modelData.groupKey || ""))

                        width: formList.width
                        spacing: Theme?.spacingXs ?? 4

                        Text {
                            objectName: "serverConfigGroupHeader"
                            width: parent.width
                            visible: rowBlock.showHeader
                            text: page.groupTitle(String(modelData.groupTitleKey || ""))
                            color: Theme?.textSecondary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                            font.bold: true
                            topPadding: Theme?.spacingXs ?? 0
                        }

                        Rectangle {
                            objectName: "serverConfigOptionRow"
                            width: parent.width
                            implicitHeight: optionBody.implicitHeight + 2 * (Theme?.spacingSm ?? 8)
                            radius: Theme?.radiusMd ?? 6
                            color: Theme?.bgMedium ?? "transparent"
                            border.width: 1
                            border.color: Theme?.cardBorder ?? "transparent"

                            RowLayout {
                                id: optionBody
                                anchors.fill: parent
                                anchors.margins: Theme?.spacingSm ?? 8
                                spacing: Theme?.spacingSm ?? 8

                                ColumnLayout {
                                    Layout.fillWidth: true
                                    Layout.minimumWidth: 180
                                    spacing: 2

                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: Theme?.spacingXs ?? 4

                                        Text {
                                            objectName: "serverConfigOptionName"
                                            Layout.fillWidth: true
                                            text: String(modelData.name || "")
                                            color: Theme?.textPrimary ?? "transparent"
                                            font.pixelSize: Theme?.fontSizeBase ?? 12
                                            font.bold: true
                                            elide: Text.ElideRight
                                        }

                                        FmTag {
                                            objectName: "serverConfigAdvancedTag"
                                            visible: !!modelData.advanced
                                            text: Tr?.map["server_config_advanced_badge"] ?? "server_config_advanced_badge"
                                            level: "warning"
                                        }
                                    }

                                    Text {
                                        objectName: "serverConfigOptionKey"
                                        Layout.fillWidth: true
                                        visible: String(modelData.key || "").charAt(0) !== "@"
                                        text: String(modelData.key || "")
                                        color: Theme?.textTertiary ?? "transparent"
                                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                                        elide: Text.ElideRight
                                    }

                                    Text {
                                        objectName: "serverConfigOptionDescription"
                                        Layout.fillWidth: true
                                        text: String(modelData.description || "")
                                        color: Theme?.textSecondary ?? "transparent"
                                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                                        wrapMode: Text.WordWrap
                                    }

                                    Text {
                                        objectName: "serverConfigOptionRange"
                                        Layout.fillWidth: true
                                        visible: String(modelData.rangeText || "").length > 0
                                        text: String(modelData.rangeText || "")
                                        color: Theme?.textTertiary ?? "transparent"
                                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                                    }
                                }

                                ColumnLayout {
                                    objectName: "serverConfigOptionControl"
                                    Layout.minimumWidth: 240
                                    Layout.maximumWidth: 240
                                    Layout.preferredWidth: 240
                                    Layout.alignment: Qt.AlignVCenter
                                    spacing: Theme?.spacingXs ?? 4

                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: Theme?.spacingXs ?? 4

                                        FmSwitch {
                                            objectName: "serverConfigBoolControl"
                                            visible: String(modelData.kind) === "bool"
                                            Layout.fillWidth: true
                                            text: checked
                                                  ? (Tr?.map["server_config_bool_on"] ?? "server_config_bool_on")
                                                  : (Tr?.map["server_config_bool_off"] ?? "server_config_bool_off")
                                            checked: String(modelData.value).toLowerCase() === "true"
                                            onToggled: {
                                                if (page.live)
                                                    ServerConfig.setValue(String(modelData.key), checked)
                                            }
                                        }

                                        FmTextField {
                                            objectName: "serverConfigTextControl"
                                            visible: String(modelData.kind) === "int" || String(modelData.kind) === "str"
                                            Layout.fillWidth: true
                                            text: String(modelData.value || "")
                                            errorText: page.live ? String(ServerConfig.errors[String(modelData.key)] || "") : ""
                                            onEdited: function (value) {
                                                if (page.live)
                                                    ServerConfig.setValue(String(modelData.key), value)
                                            }
                                        }

                                        FmComboBox {
                                            objectName: "serverConfigEnumControl"
                                            visible: String(modelData.kind) === "enum"
                                            Layout.fillWidth: true
                                            model: modelData.enumItems
                                            textRole: "label"
                                            valueRole: "value"
                                            currentIndex: page.enumIndex(modelData.enumItems, modelData.value)
                                            onActivated: {
                                                if (page.live)
                                                    ServerConfig.setValue(String(modelData.key), currentValue)
                                            }
                                        }

                                        FmComboBox {
                                            objectName: "serverConfigMemoryControl"
                                            visible: String(modelData.kind) === "memory"
                                            Layout.fillWidth: true
                                            model: page.live ? ServerConfig.memoryOptions : []
                                            currentIndex: page.memoryIndex(model, modelData.value)
                                            onActivated: {
                                                if (page.live)
                                                    ServerConfig.setMemory(String(currentText))
                                            }
                                        }

                                        FmTag {
                                            objectName: "serverConfigInfoTag"
                                            visible: String(modelData.infoKind) === "eula"
                                            text: String(modelData.infoText || "")
                                            level: String(modelData.infoLevel || "neutral")
                                        }

                                        FmToolButton {
                                            objectName: "serverConfigResetOne"
                                            visible: !!modelData.canReset
                                            iconName: "refresh"
                                            iconSize: (Theme?.iconSize ?? 20) - 4
                                            enabled: page.live
                                            onClicked: {
                                                if (page.live)
                                                    ServerConfig.resetOne(String(modelData.key))
                                            }
                                            ToolTip.visible: hovered
                                            ToolTip.text: page.resetTip()
                                            onHoveredChanged: {
                                                if (hovered) {
                                                    page.hintText = page.resetTip()
                                                } else if (page.hintText === page.resetTip()) {
                                                    page.hintText = ""
                                                }
                                            }
                                        }
                                    }

                                    RowLayout {
                                        objectName: "serverConfigDirRow"
                                        Layout.fillWidth: true
                                        visible: String(modelData.infoKind) === "dir"

                                        Text {
                                            objectName: "serverConfigDirText"
                                            Layout.fillWidth: true
                                            text: String(modelData.infoText || "")
                                            color: Theme?.textSecondary ?? "transparent"
                                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                                            wrapMode: Text.WrapAnywhere
                                        }

                                        FmToolButton {
                                            objectName: "serverConfigOpenFolder"
                                            iconName: "folder-open"
                                            iconSize: (Theme?.iconSize ?? 20) - 4
                                            onClicked: {
                                                if (page.live)
                                                    ServerConfig.openFolder()
                                            }
                                            ToolTip.visible: hovered
                                            ToolTip.text: page.folderTip()
                                            onHoveredChanged: {
                                                if (hovered) {
                                                    page.hintText = page.folderTip()
                                                } else if (page.hintText === page.folderTip()) {
                                                    page.hintText = ""
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                FmEmptyState {
                    objectName: "serverConfigEmptyState"
                    anchors.centerIn: parent
                    width: Math.min(360, parent.width)
                    visible: page.live && formList.visible && ServerConfig.rowCount === 0
                    title: Tr?.map["server_config_search_empty"] ?? "server_config_search_empty"
                }

                ColumnLayout {
                    objectName: "serverConfigRawPanel"
                    anchors.fill: parent
                    visible: page.live && String(ServerConfig.category) === "raw" && !ServerConfig.searching
                    spacing: Theme?.spacingSm ?? 8

                    Text {
                        objectName: "serverConfigRawTitle"
                        Layout.fillWidth: true
                        text: Tr?.map["server_config_raw_title"] ?? "server_config_raw_title"
                        color: Theme?.textPrimary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeLarge ?? 14
                        font.bold: true
                    }

                    Text {
                        objectName: "serverConfigRawDescription"
                        Layout.fillWidth: true
                        text: Tr?.map["server_config_raw_desc"] ?? "server_config_raw_desc"
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        wrapMode: Text.WordWrap
                    }

                    // 原文可能有几十行：必须由可滚容器夹住高度，否则 FmTextArea 会按
                    // 全文撑高，把底部动作条顶出内容区（用户 1600×1050 实测）。
                    // Flickable + 显式 FmScrollBar 比 FmScrollView 的附着条更可靠：
                    // 后者的条在 ScrollView 下拿不到高度，只剩一个 10×10 的隐形容器。
                    Flickable {
                        id: rawScroll
                        objectName: "serverConfigRawScroll"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        contentWidth: width
                        contentHeight: rawArea.height
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: FmScrollBar {
                            policy: ScrollBar.AlwaysOn
                            thickness: 6
                        }

                        FmTextArea {
                            id: rawArea
                            objectName: "serverConfigRawText"
                            width: rawScroll.width - 10
                            lines: 24
                            text: page.rawDraft
                            onEdited: function (value) { page.rawDraft = value }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Layout.fillHeight: false

                        Item { Layout.fillWidth: true }

                        FmButton {
                            objectName: "serverConfigRawWriteButton"
                            primary: true
                            text: Tr?.map["server_config_raw_write_btn"] ?? "server_config_raw_write_btn"
                            enabled: page.live
                            onClicked: page.rawConfirmVisible = true
                        }
                    }
                }
            }
        }

        // ── 底部动作条 ──
        RowLayout {
            objectName: "serverConfigFooter"
            Layout.fillWidth: true
            Layout.fillHeight: false
            spacing: Theme?.spacingSm ?? 8

            FmButton {
                objectName: "serverConfigResetAll"
                primary: false
                text: Tr?.map["server_config_reset_btn"] ?? "server_config_reset_btn"
                enabled: page.live && ServerConfig.rowCount > 0
                onClicked: page.resetConfirmVisible = true
            }

            FmButton {
                objectName: "serverConfigReload"
                primary: false
                text: Tr?.map["server_config_reload_btn"] ?? "server_config_reload_btn"
                enabled: page.live
                onClicked: {
                    if (page.live)
                        ServerConfig.reload()
                }
            }

            Text {
                objectName: "serverConfigHint"
                Layout.fillWidth: true
                text: page.hintText
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                elide: Text.ElideRight
                Layout.alignment: Qt.AlignVCenter
            }

            FmButton {
                objectName: "serverConfigClose"
                primary: false
                text: Tr?.map["server_config_close_btn"] ?? "server_config_close_btn"
                enabled: page.live
                onClicked: page.requestClose()
            }

            FmButton {
                objectName: "serverConfigSave"
                primary: true
                text: Tr?.map["server_config_save_btn"] ?? "server_config_save_btn"
                iconName: "save"
                enabled: page.live && ServerConfig.pageState === "ready"
                onClicked: page.savePage()
            }
        }
    }

    // ── 确认框：全部作为内容区的浮层，盖住表单与底部动作条 ──
    Loader {
        objectName: "serverConfigResetLoader"
        anchors.fill: parent
        z: 50
        active: page.resetConfirmVisible
        sourceComponent: resetComponent
    }

    Loader {
        objectName: "serverConfigRawLoader"
        anchors.fill: parent
        z: 50
        active: page.rawConfirmVisible
        sourceComponent: rawComponent
    }

    Loader {
        objectName: "serverConfigCloseLoader"
        anchors.fill: parent
        z: 50
        active: page.closeConfirmVisible
        sourceComponent: closeComponent
    }

    Loader {
        objectName: "serverConfigValidationLoader"
        anchors.fill: parent
        z: 50
        active: page.validationVisible
        sourceComponent: validationComponent
    }

    Component {
        id: resetComponent

        ConfirmDialog {
            objectName: "serverConfigResetDialog"
            modalScrim: true
            request: ({
                "title": page.t("server_config_reset_confirm_title"),
                "message": page.format("server_config_reset_confirm_msg", {"category": ServerConfig.scopeLabel}),
                "default": false
            })
            onAnswered: function (value) {
                page.resetConfirmVisible = false
                if (value === true && page.live)
                    ServerConfig.resetAll()
            }
        }
    }

    Component {
        id: rawComponent

        ConfirmDialog {
            objectName: "serverConfigRawDialog"
            modalScrim: true
            request: ({
                "title": page.t("server_config_raw_confirm_title"),
                "message": page.t("server_config_raw_confirm_msg"),
                "default": false
            })
            onAnswered: function (value) {
                page.rawConfirmVisible = false
                if (value === true && page.live)
                    ServerConfig.writeRaw(page.rawDraft)
            }
        }
    }

    Component {
        id: closeComponent

        ConfirmDialog {
            objectName: "serverConfigCloseDialog"
            modalScrim: true
            request: ({
                "title": page.t("server_config_close_confirm_title"),
                "message": page.format("server_config_close_confirm_msg", {"count": ServerConfig.dirtyCount}),
                "default": false
            })
            onAnswered: function (value) {
                page.closeConfirmVisible = false
                if (value === true)
                    page.leaveNow()
            }
        }
    }

    Component {
        id: validationComponent

        ConfirmDialog {
            objectName: "serverConfigValidationDialog"
            modalScrim: true
            cancelVisible: false
            request: ({
                "title": page.t("error"),
                "message": page.validationText,
                "default": true
            })
            onAnswered: function () {
                page.validationVisible = false
            }
        }
    }
}
