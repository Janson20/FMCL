// InstallVersionPage.qml —— 安装新版本向导（阶段 3 任务 3.3）。
//
// 这一页承载什么（03 的 3.3 摘要 + 对照表 B 组 + 2026-10-06 用户裁决）：
//   * 版本 ID 手动输入（B-09）；
//   * 9 种模组加载器下拉 + 兼容提示（B-10，提示 = 旧文案 + 真实支持性查询）；
//   * 安装版本：进度 / 取消 / 完成提示（B-11）；
//   * 安装整合包**入口**（B-12，只推 `versions/modpack` 路由，页面归 3.8）；
//   * 可用版本：正式版 / 测试版切换 + 每页 20 + 分页 + 点击回填（B-13）。
//
// 为什么自成一页而不是旧界面那种"右侧同栏面板"：用户 2026-10-06 裁决 A。
// 旧界面把列表、安装表单、可用版本挤在同一屏，代价是每块都被压得很小；
// 新界面按 `02` 的信息架构给它一条二级路由（面包屑「版本 / 安装新版本」）。
//
// 数据全部来自 `Install` 桥（`app/bridges/install_bridge.py`）—— 本文件不碰服务、
// 不做业务判断：版本 ID 截断、分页夹取、加载器映射、兼容判定、安装结果判读都在
// `InstallService`。这里只做三件事：绑属性、发槽调用、按状态显示。
//
// 三态（03 的 SOP 第 5 条）：**可用版本那块**有 loading / empty / error，
// 但整页**永远是 ready** —— 因为"取不到可用版本清单"（离线）恰恰是最需要手动输入
// 版本 ID 的场景，把整页切成错误态等于把唯一的离线通路也藏起来（旧界面同屏时
// 天然没有这个问题）。三态因此做在卡片内部。
//
// ── 两条硬约束（都是实测踩出来的）─────────────────────────────
//
// 1. **动态状态一律在 `refreshUi()` 里赋值，不用绑定。** 本页是**可弹出**的页面：
//    绑定里引用 `page.*` 时，页面销毁的那一刻绑定会被再求值一次，而作用域对象已经
//    变成 null —— 实测 8 条 `Cannot read property 'bridgeAvailable' of null`
//    （3.2 的版本页踩的是同一类坑，那次是 `page.t()`）。所以：
//      * 静态文案 → 绑定里直接写 `Tr?.map[…] ?? "键名"`（只引用外部单例，安全）；
//      * 只要随状态变的东西（可见性、文案、进度、下拉模型）→ 全部由 `refreshUi()`
//        在信号处理里赋值。桥的四个信号 + 语言切换 + 首次装配，五条路都接上了。
// 2. **带参数的句子走 `format()`**（QML 没有 i18n 的占位符替换），它读 `Tr.map`，
//    所以只在 `refreshUi()` 里调，不在绑定里调。
//
// 纪律：颜色只来自 `Theme.*`（R8）、图标走 `FmIcon`/`FmToolButton`（R2）、
// 文案走 `Tr.map[…]`（R3/R4）、只用 `qml/components` 里的 `Fm*`（R9）。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../components"

FmPage {
    id: page
    objectName: "installVersionPage"

    //: 桥缺席（注册失败）时整页走错误态，而不是绑一堆 `undefined` 报错
    readonly property bool live: (typeof Install !== "undefined" && Install) ? true : false
    //: 打开页面时的入口参数：`{"version": "1.20.4"}`（深链 `fmcl://versions/install?version=…`）
    readonly property string wantedVersion: page.routeParams ? String(page.routeParams.version || "") : ""

    //: 整页三态：永远 ready（离线也要能手动输入）；可用版本那三态在卡片内部
    contentState: "ready"

    // ── 文案工具 ──────────────────────────────────────────────

    //: 取文案 + 填占位符（`{loader}` / `{version}` 这种）
    function format(key, params) {
        if (!key)
            return ""
        var text = Tr ? (Tr.map[key] ?? key) : key
        if (!params)
            return text
        for (var name in params)
            text = text.replace("{" + name + "}", String(params[name]))
        return text
    }

    //: 每页/每标签页的条数文案（"正式版 N 个 · 测试版 M 个"）
    function countsLabel() {
        if (!page.live)
            return ""
        var release = page.format("release_count", { "count": Install.releaseCount })
        var snapshot = page.format("snapshot_count", { "count": Install.snapshotCount })
        return release + "  ·  " + snapshot
    }

    //: 兼容提示的 i18n 键（服务只给状态，键在这里映射 —— 与桥里的映射同一张表）
    function compatKey() {
        if (!page.live)
            return ""
        return String(Install.compatKey || "")
    }

    //: 结果面板那一句（"X 安装成功!" / "已取消安装 X" / "X 安装失败"）
    function resultText() {
        if (!page.live || !Install.resultKey)
            return ""
        return page.format(String(Install.resultKey), { "version": String(Install.resultVersion) })
    }

    function progressLabel() {
        if (!page.live || !Install.busy)
            return ""
        if (Install.progressTotal > 0)
            return String(Install.progressCurrent) + " / " + String(Install.progressTotal)
        return Tr ? (Tr.map["installing"] ?? "installing") : "installing"
    }

    // ── 刷新整页（唯一的状态出口）─────────────────────────────

    function refreshUi() {
        var live = page.live
        var busy = live && Install.busy

        // 表单
        page.rebuildModel()
        versionField.text = live ? String(Install.versionId) : ""
        versionField.enabled = !busy
        loaderCombo.enabled = live && !busy
        loaderCombo.model = page.loaderModel
        loaderCombo.currentIndex = page.loaderIndexOf()
        startButton.enabled = live && !busy
        modpackButton.enabled = live && !busy

        // 兼容提示
        var key = page.compatKey()
        formCompatBar.text = key ? page.format(key, {
                                                   "loader": live ? String(Install.loaderName) : "",
                                                   "version": live ? String(Install.versionId) : ""
                                               }) : ""
        formCompatBar.level = !live ? "info"
                             : (Install.compatState === "bad" ? "warning"
                             : (Install.compatState === "ok" ? "success" : "info"))
        formCompatBar.visible = formCompatBar.text.length > 0

        // 可用版本：标签页 / 计数 / 分页 / 三态 / 列表
        var state = page.listState()
        tabRelease.checked = live ? Install.tab === "release" : true
        tabSnapshot.checked = live ? Install.tab === "snapshot" : false
        tabRelease.enabled = live && !busy
        tabSnapshot.enabled = live && !busy
        countsText.text = page.countsLabel()
        refreshButton.enabled = live && !busy
        pager.enabled = live
        pager.page = live ? Install.page : 1
        pager.pageCount = live ? Install.pageCount : 1
        stateLoading.visible = state === "loading"
        stateEmpty.visible = state === "empty"
        stateError.visible = state === "error"
        versionGrid.visible = state === "ready"
        versionGrid.model = live ? Install.versions : []
        versionGrid.enabled = !busy
        stateError.detailText = live ? String(Install.availableError) : ""

        // 进度与结果
        progressCard.visible = live && (busy || String(Install.resultState).length > 0)
        progressBar.visible = busy
        progressBar.value = (busy && Install.progressDeterminate) ? Install.progress : 0
        progressBar.indeterminate = busy ? !Install.progressDeterminate : true
        progressBar.label = page.progressLabel()
        progressBar.showPercent = busy ? Install.progressDeterminate : false
        var result = page.resultText()
        resultBar.text = result
        resultBar.visible = result.length > 0
        resultBar.level = !live ? "info"
                          : (Install.resultState === "done" ? "success"
                          : (Install.resultState === "cancelled" ? "info" : "error"))
        cancelButton.visible = busy
        cancelButton.enabled = live && Install.canCancel
        retryButton.visible = live && !busy && Install.resultState === "failed"
        backButton.visible = live && !busy && String(Install.resultState).length > 0
        backButton.primary = live && Install.resultState === "done"
    }

    function listState() {
        if (!page.live)
            return "error"
        if (Install.availableState === "loading")
            return "loading"
        if (Install.availableState === "empty")
            return "empty"
        if (Install.availableState === "error")
            return "error"
        return "ready"
    }

    function loaderModelFor() {
        if (!page.live)
            return []
        //: 标签是 i18n **键**（桥给的），译文在语言切换时重算 —— 见 refreshUi 的调用链
        var source = Install.loaderOptions
        var result = []
        for (var index = 0; index < source.length; index++) {
            var key = source[index].labelKey
            result.push({
                            "label": Tr ? (Tr.map[key] ?? key) : key,
                            "value": source[index].value
                        })
        }
        return result
    }

    function loaderIndexOf() {
        if (!page.live)
            return 0
        var current = String(Install.loader)
        for (var index = 0; index < page.loaderModel.length; index++) {
            if (page.loaderModel[index].value === current)
                return index
        }
        return 0
    }

    //: 下拉的模型缓存（`loaderIndexOf()` 用它做值 → 下标的映射）
    property var loaderModel: []

    //: 从列表里点一行回填版本 ID（旧 `_quick_select_version`）
    function pickVersion(versionId) {
        if (page.live)
            Install.quickSelect(String(versionId || ""))
    }

    //: 装完回版本列表：栈里没有上一页（深链直接进来的）就压一页 `versions`
    function backToVersions() {
        if (typeof Nav === "undefined" || !Nav)
            return
        if (!Nav.goBack())
            Nav.push("versions")
    }

    Component.onCompleted: {
        if (page.live && page.wantedVersion.length > 0)
            Install.setVersionId(page.wantedVersion)
        page.rebuildModel()
        page.refreshUi()
        if (page.live)
            Install.loadAvailable()
    }

    onRouteParamsChanged: {
        if (page.live && page.wantedVersion.length > 0) {
            Install.setVersionId(page.wantedVersion)
            page.refreshUi()
        }
    }

    //: `refreshUi()` 需要一份现成的模型来做下标映射，这里补一次（模型本身由 refreshUi 装给控件）
    function rebuildModel() {
        page.loaderModel = page.loaderModelFor()
    }

    Connections {
        id: installConnections
        target: (typeof Install !== "undefined" && Install) ? Install : null

        //: 状态条（桥已经翻好的整句）
        function onStatusMessage(text, level) {
            if (!page)
                return
            if (typeof Shell !== "undefined" && Shell)
                Shell.setStatus(text, level)
        }
        function onAvailableChanged() {
            if (!page)
                return
            page.refreshUi()
        }
        function onFormChanged() {
            if (!page)
                return
            page.refreshUi()
        }
        function onProgressChanged() {
            if (!page)
                return
            page.refreshUi()
        }
        function onInstalled(versionId) {
            if (!page)
                return
            page.refreshUi()
            returnTimer.restart()
        }
    }

    Connections {
        id: trConnections
        target: typeof Tr !== "undefined" ? Tr : null

        //: 语言热切换：下拉标签与带参数的句子都得重算（绑定做不到，见文件头）
        function onLanguageChanged() {
            if (!page)
                return
            page.refreshUi()
        }
    }

    //: 页面被弹出（销毁）时主动摘掉订阅并停表。
    //  为什么两道都要：桥是**常驻**对象，它发的进度/结束信号会继续投递给已经进入
    //  销毁流程的页面 —— 那时 `page` 已是 null，处理函数里的 `page.refreshUi()`
    //  就抛 `Cannot read property 'refreshUi' of null`（实测 3 条）。这里先摘订阅，
    //  处理函数里再补一道 `if (!page) return`（信号可能已经排进队列，
    //  `onDestruction` 挡不住已入队的那几条）。
    Component.onDestruction: {
        if (installConnections)
            installConnections.target = null
        if (trConnections)
            trConnections.target = null
        returnTimer.stop()
        compatTimer.stop()
    }

    //: 安装成功后让用户看一眼结果再自动回列表（用户裁决 4：装完自动选中新版本）
    Timer {
        id: returnTimer
        interval: 1500
        onTriggered: {
            if (page)
                page.backToVersions()
        }
    }

    //: 兼容查询的去抖：输入停下 400ms 才问（每次问都可能联网）
    Timer {
        id: compatTimer
        interval: 400
        onTriggered: {
            if (page && page.live)
                Install.checkCompatibility()
        }
    }

    ColumnLayout {
        objectName: "installPane"
        anchors.fill: parent
        spacing: Theme?.spacingMd ?? 8

        // ── 安装参数：版本 ID + 加载器 + 提示 + 两个动作 ──
        FmCard {
            objectName: "installFormCard"
            Layout.fillWidth: true
            Layout.fillHeight: false

            RowLayout {
                objectName: "installFormRow"
                Layout.fillWidth: true
                Layout.fillHeight: false
                spacing: Theme?.spacingSm ?? 8

                FmTextField {
                    id: versionField
                    objectName: "installVersionId"
                    Layout.fillWidth: true
                    label: Tr?.map["version_id"] ?? "version_id"
                    placeholder: Tr?.map["version_id_placeholder"] ?? "version_id_placeholder"
                    onEdited: function (value) {
                        if (!page.live)
                            return
                        Install.setVersionId(value)
                        compatTimer.restart()
                    }
                    onAccepted: {
                        if (page.live)
                            Install.install()
                    }
                }

                FmComboBox {
                    id: loaderCombo
                    objectName: "installLoader"
                    Layout.preferredWidth: 200
                    Layout.alignment: Qt.AlignBottom
                    textRole: "label"
                    valueRole: "value"
                    onActivated: {
                        if (!page.live)
                            return
                        Install.setLoader(currentValue)
                        compatTimer.restart()
                    }
                }
            }

            FmInfoBar {
                id: formCompatBar
                objectName: "installCompatBar"
                Layout.fillWidth: true
                Layout.fillHeight: false
                visible: false
            }

            RowLayout {
                objectName: "installActionRow"
                Layout.fillWidth: true
                Layout.fillHeight: false
                spacing: Theme?.spacingSm ?? 8

                FmButton {
                    id: startButton
                    objectName: "installStartButton"
                    primary: true
                    text: Tr?.map["install_version"] ?? "install_version"
                    onClicked: {
                        if (page.live)
                            Install.install()
                    }
                }

                FmButton {
                    id: modpackButton
                    objectName: "installModpackButton"
                    primary: false
                    text: Tr?.map["install_modpack"] ?? "install_modpack"
                    onClicked: {
                        if (page.live)
                            Install.installModpack()
                    }
                }

                Item { Layout.fillWidth: true }
            }
        }

        // ── 可用版本：正式版 / 测试版 + 分页 + 点一行回填（B-13）──
        FmCard {
            objectName: "installVersionsCard"
            Layout.fillWidth: true
            Layout.fillHeight: true
            title: Tr?.map["quick_select"] ?? "quick_select"

            RowLayout {
                objectName: "installTabsRow"
                Layout.fillWidth: true
                Layout.fillHeight: false
                spacing: Theme?.spacingSm ?? 8

                FmRadio {
                    id: tabRelease
                    objectName: "installTabRelease"
                    text: Tr?.map["release_version"] ?? "release_version"
                    onToggled: {
                        if (checked && page.live)
                            Install.setTab("release")
                    }
                }

                FmRadio {
                    id: tabSnapshot
                    objectName: "installTabSnapshot"
                    text: Tr?.map["snapshot_version"] ?? "snapshot_version"
                    onToggled: {
                        if (checked && page.live)
                            Install.setTab("snapshot")
                    }
                }

                Text {
                    id: countsText
                    objectName: "installCounts"
                    Layout.alignment: Qt.AlignVCenter
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                }

                Item { Layout.fillWidth: true }

                FmToolButton {
                    id: refreshButton
                    objectName: "installRefreshAvailable"
                    iconName: "refresh"
                    onClicked: {
                        if (page.live)
                            Install.refreshAvailable()
                    }
                }

                FmPagination {
                    id: pager
                    objectName: "installPager"
                    Layout.alignment: Qt.AlignVCenter
                    onPageRequested: function (target) {
                        if (page.live)
                            Install.setPage(target)
                    }
                }
            }

            //: 三态做在卡片里（整页始终 ready，理由见文件头）
            FmLoadingState {
                id: stateLoading
                objectName: "installListLoading"
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: false
                title: Tr?.map["loading_available"] ?? "loading_available"
            }

            FmEmptyState {
                id: stateEmpty
                objectName: "installListEmpty"
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: false
                iconName: "download"
                actionText: Tr?.map["version_refresh"] ?? "version_refresh"
                onActionTriggered: {
                    if (page.live)
                        Install.refreshAvailable()
                }
            }

            FmErrorState {
                id: stateError
                objectName: "installListError"
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: false
                description: Tr?.map["plugin_state_error"] ?? "plugin_state_error"
                retryText: Tr?.map["version_refresh"] ?? "version_refresh"
                onRetried: {
                    if (page.live)
                        Install.refreshAvailable()
                }
            }

            GridView {
                id: versionGrid
                objectName: "installVersionGrid"
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: false
                clip: true
                //: 每行几列**按可用宽度算**（2026-10-06 验收反馈：固定 2 列时 20 条要滚 10 行，
                //  而这页有一千多像素宽）。旧界面固定 2 列是因为它的面板只有 300px 宽。
                //  上限 6 列：再宽下去每个格子会短到看不出区别。
                readonly property int columns: Math.max(2, Math.min(6, Math.floor(width / 190)))
                //: `Math.floor` 不是装饰：它保证 `columns * cellWidth <= width`，内容宽度**永不溢出**，
                //  于是 GridView 不会自己弹一条**原生**横向滚动条（浅色外来件）。
                //  试过 `ScrollBar.horizontal.policy: ScrollBar.AlwaysOff` —— 在 GridView 上
                //  创建期附加对象还是 null（实测 `Cannot set properties on horizontal as it is null`
                //  直接把整页建不起来），别再用那个写法。
                cellWidth: Math.max(120, Math.floor(width / columns))
                cellHeight: 34
                ScrollBar.vertical: FmScrollBar {}

                delegate: FmButton {
                    id: versionChip
                    objectName: "installVersionChip"
                    required property var modelData
                    width: Math.max(80, versionGrid.cellWidth - (Theme?.spacingSm ?? 8))
                    height: Math.max(26, versionGrid.cellHeight - 6)
                    primary: false
                    text: modelData.id
                    onClicked: page.pickVersion(modelData.id)
                }
            }
        }

        // ── 进度与结果（B-11）：**一行紧凑条**，不再占掉列表的高度 ──
        //: 2026-10-06 验收反馈："进度卡一出现，版本列表只剩三行" —— 原来它是"标题 + 进度条 +
        //: 结果条 + 按钮行"四层堆叠（约 170px）。改成一行：装的时候是进度条 + 取消，
        //: 装完就换成结果条 + 重试/返回，高度只有原来的一半不到。
        FmCard {
            id: progressCard
            objectName: "installProgressCard"
            Layout.fillWidth: true
            Layout.fillHeight: false
            visible: false

            RowLayout {
                objectName: "installProgressRow"
                Layout.fillWidth: true
                Layout.fillHeight: false
                spacing: Theme?.spacingSm ?? 8

                FmProgressBar {
                    id: progressBar
                    objectName: "installProgressBar"
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignVCenter
                    visible: false
                }

                FmInfoBar {
                    id: resultBar
                    objectName: "installResultBar"
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignVCenter
                    visible: false
                }

                FmButton {
                    id: cancelButton
                    objectName: "installCancelButton"
                    primary: false
                    visible: false
                    text: Tr?.map["cancel"] ?? "cancel"
                    onClicked: {
                        if (page.live)
                            Install.cancel()
                    }
                }

                FmButton {
                    id: retryButton
                    objectName: "installRetryButton"
                    primary: true
                    visible: false
                    text: Tr?.map["version_install_retry"] ?? "version_install_retry"
                    onClicked: {
                        if (page.live)
                            Install.install()
                    }
                }

                FmButton {
                    id: backButton
                    objectName: "installBackButton"
                    visible: false
                    text: Tr?.map["version_back_to_list"] ?? "version_back_to_list"
                    onClicked: page.backToVersions()
                }
            }
        }
    }
}
