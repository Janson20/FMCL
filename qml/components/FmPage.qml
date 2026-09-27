// FmPage.qml —— 页面骨架（阶段 2 任务 2.16 组件库／界面返工 C 组新增）。
//
// 什么时候用它：**每一个页面** —— 12 个内置领域页、以及插件注册进来的页。
//   它替页面承担三件事：
//     1) 路由帧五件套（`routeId / routeTitleKey / routeDescriptionKey / routeIcon / routeParams`）：
//        `shell/PageStack.qml` 只给页面**声明过**的属性赋值，少声明一个就等于丢一个；
//     2) 页头（图标 + 标题 + 描述），标题/描述默认从路由键取 i18n，插件页可以直接给文案；
//     3) 内容区三态（`contentState`：ready / loading / empty / error）—— `03` 的 SOP 第 5 条
//        要求每页都覆盖三态，这里把"状态 → 哪个自研状态件"的分派固定下来，页面只填文案。
// 什么时候不要用：不是页面的东西 —— 对话框与浮层有各自的件（`dialogs/**`、`overlays/**`），
//   顶栏与导航栏属于壳层（`shell/AppBar.qml`、`shell/Navigation.qml`）。
//
// 内容怎么放：直接写在 `FmPage { }` 里（默认属性转发到内容区卡片内部的 `pageContentBody`），
//   在里面用 `anchors.fill: parent` 铺满，或再套一个 `ColumnLayout` 做竖排。
//   放进来的东西只在 `contentState === "ready"` 时可见 —— 所以"加载中显示旧内容"
//   这种半截状态不会发生（要么三态块，要么内容）。
//
// 为什么把三态的**默认文案**写在组件里（其它组件都刻意不带默认文案）：这三个键
//   （`plugin_state_loading` / `mod_browser_no_results` / `plugin_state_error`）就是
//   通用三态文案，12 个占位页本来各自抄了同一份；放在这里只有一份，真实页面按领域覆盖
//   （`emptyText: Tr?.map["versions_none"] ?? "versions_none"`）。没有引入任何新 i18n 键。
//
// `demoControlsVisible` 是**阶段 2 占位页专用**的开关（那三个演示按钮让验收的人
//   不用改代码就能看到三态）；阶段 3 的真实页面不要打开它，届时这个属性连同按钮一起删。

import QtQuick
import QtQuick.Layouts

Item {
    id: page
    objectName: "fmPage"

    // ── 路由帧（名字不能改：见 shell/PageStack.qml 的 pushFrame()） ──
    property string routeId: ""
    property string routeTitleKey: ""
    property string routeDescriptionKey: ""
    property string routeIcon: ""
    property var routeParams: ({})

    // ── 页头（留空则回落到路由帧里的键；插件页可以只给 title / iconName） ──
    property string title: ""
    property string description: ""
    property string iconName: ""

    readonly property string resolvedTitle: page.title.length > 0
                                            ? page.title
                                            : (Tr?.map[page.routeTitleKey] ?? page.routeTitleKey)
    readonly property string resolvedDescription: page.description.length > 0
                                                  ? page.description
                                                  : (page.routeDescriptionKey
                                                     ? (Tr?.map[page.routeDescriptionKey] ?? page.routeDescriptionKey)
                                                     : "")
    readonly property string resolvedIcon: page.iconName.length > 0 ? page.iconName : page.routeIcon

    // ── 内容区 ──
    //: ready = 显示页面放的内容；loading / empty / error = 显示对应的状态块
    property string contentState: "ready"
    //: 页面内容（默认属性）；只在 ready 时可见
    default property alias content: body.data

    //: 三态文案（默认是三个通用键；真实页面按领域覆盖）
    property string loadingText: Tr?.map["plugin_state_loading"] ?? "plugin_state_loading"
    property string loadingDetail: ""
    property string emptyText: Tr?.map["mod_browser_no_results"] ?? "mod_browser_no_results"
    property string emptyDetail: ""
    //: 空状态的行动按钮文案（非空才出现；点了发 `emptyActionTriggered()`）
    property string emptyActionText: ""
    property string errorText: Tr?.map["plugin_state_error"] ?? "plugin_state_error"
    property string errorDetail: ""
    //: 错误态的重试按钮文案（非空才出现；点了发 `retried()`）
    property string retryText: ""

    signal retried()
    signal emptyActionTriggered()

    //: 阶段 2 占位页的演示按钮（阶段 3 的真实页面不要打开）
    property bool demoControlsVisible: false

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme?.spacingLg ?? 0
        spacing: Theme?.spacingMd ?? 0

        // ── 页头 ──
        RowLayout {
            id: header
            objectName: "pageHeader"
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 0

            FmIcon {
                objectName: "pageIcon"
                visible: page.resolvedIcon.length > 0
                name: page.resolvedIcon
                color: Theme?.textPrimary ?? "transparent"
                size: Theme?.fontSizeTitle ?? 18
                Layout.alignment: Qt.AlignVCenter
            }

            Text {
                objectName: "pageTitle"
                Layout.fillWidth: true
                text: page.resolvedTitle
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeTitle ?? 18
                font.bold: true
                elide: Text.ElideRight
                Layout.alignment: Qt.AlignVCenter
            }
        }

        Text {
            objectName: "pageDescription"
            Layout.fillWidth: true
            visible: text.length > 0
            text: page.resolvedDescription
            color: Theme?.textSecondary ?? "transparent"
            font.pixelSize: Theme?.fontSizeBase ?? 12
            wrapMode: Text.WordWrap
        }

        // ── 三态演示按钮（阶段 2 占位页；阶段 3 删） ──
        RowLayout {
            objectName: "pageDemoControls"
            Layout.fillWidth: true
            visible: page.demoControlsVisible
            spacing: Theme?.spacingSm ?? 0

            FmButton {
                objectName: "demoLoadingButton"
                primary: false
                text: page.loadingText
                onClicked: page.contentState = "loading"
            }

            FmButton {
                objectName: "demoEmptyButton"
                primary: false
                text: page.emptyText
                onClicked: page.contentState = "empty"
            }

            FmButton {
                objectName: "demoErrorButton"
                primary: false
                text: page.errorText
                onClicked: page.contentState = "error"
            }
        }

        // ── 内容区：卡片做外框，里面要么是三态块、要么是页面内容 ──
        FmCard {
            id: contentCard
            objectName: "pageContentArea"
            Layout.fillWidth: true
            Layout.fillHeight: true

            //: 三态展示中
            readonly property bool showingState: page.contentState !== "ready"

            Loader {
                objectName: "pageStateArea"
                // 可见性与 fillHeight 都跟着状态走：QtQuick Layouts 会忽略不可见项，
                // 但这里不赌那个行为 —— 两种解释下布局都必须是"谁显示谁占满"。
                visible: contentCard.showingState
                Layout.fillWidth: true
                Layout.fillHeight: contentCard.showingState

                sourceComponent: page.contentState === "loading" ? loadingBlock
                               : (page.contentState === "empty" ? emptyBlock
                               : (page.contentState === "error" ? errorBlock : null))
            }

            Item {
                id: body
                objectName: "pageContentBody"
                visible: !contentCard.showingState
                Layout.fillWidth: true
                Layout.fillHeight: !contentCard.showingState
            }
        }
    }

    // ── 三个状态块（自研件；文案由页面给，动作由页面接） ──
    Component {
        id: loadingBlock

        FmLoadingState {
            title: page.loadingText
            description: page.loadingDetail
        }
    }

    Component {
        id: emptyBlock

        FmEmptyState {
            title: page.emptyText
            description: page.emptyDetail
            actionText: page.emptyActionText
            onActionTriggered: page.emptyActionTriggered()
        }
    }

    Component {
        id: errorBlock

        FmErrorState {
            title: page.errorText
            description: page.errorDetail
            detailText: page.errorDetail
            retryText: page.retryText
            onRetried: page.retried()
        }
    }
}
