import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// servers 领域占位页（02 第 4.4 节）—— 阶段 2 任务 2.12 的窗口骨架的一部分；
// 阶段 3 会逐页用真实内容替换本文件（迁移红线 2：数据来自各页自己的桥，不从这里取）。
//
// 三态骨架（加载中 / 空数据 / 出错）按 `03` 的 SOP 第 5 条先立起来：阶段 3 每页都必须
// 覆盖这三态，这里把"状态 → 图标 + 文案"的写法先摆出来。真实页面把 `demoState`
// 换成自己桥的属性（例如 `VersionsPage.loadState`），三个演示按钮随之删掉。
//
// 本文件没有硬编码颜色（一律 `Theme.*`，闸门 R8）、没有中文字面量（一律 `Tr?.map[...]`，
// 闸门 R3/R4）、没有 emoji（图标一律走 `Runtime` 的 iconUrl，见 qml/assets/icons，闸门 R2）。
//
// `Theme?.x ?? 兜底` 的写法是必须的：上下文属性在引擎析构时先被清成 null，而绑定还排在
// 求值队列里，不判空就每个绑定刷一条 `TypeError: … of null`（2.2 实测的缺陷，
// `tests/test_main_qml_entry.py` 钉住它）。兜底值只在那一瞬间用到，屏幕上看不到。

Item {
    id: page
    objectName: "serversPage"

    // PageStack 在 push 时设进来的**这一帧的快照**（见 PageStack.qml 的说明）：
    // 过渡动画期间有两页同时在屏上，读 `Nav.current*` 的那一页会在动画中途改文案。
    property string routeId: ""
    property string routeTitleKey: ""
    property string routeDescriptionKey: ""
    property string routeIcon: ""
    property var routeParams: ({})

    //: 三态演示：loading | empty | error
    property string demoState: "loading"

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme?.spacingLg ?? 0
        spacing: Theme?.spacingMd ?? 0

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 0

            Image {
                objectName: "pageIcon"
                visible: page.routeIcon.length > 0
                // 判空是必需的：路由还没设进来时传空串，桥会打一条"图标名不合法"的 warning
                // （界面没坏，但日志会脏）；`?? ""` 兜住"桥缺席"的情况（Engine 析构瞬间）
                source: page.routeIcon.length > 0 ? (Runtime?.iconUrl(page.routeIcon) ?? "") : ""
                sourceSize.width: Theme?.fontSizeTitle ?? 18
                sourceSize.height: Theme?.fontSizeTitle ?? 18
                width: Theme?.fontSizeTitle ?? 18
                height: Theme?.fontSizeTitle ?? 18
                smooth: true
            }

            Text {
                objectName: "pageTitle"
                text: Tr?.map[page.routeTitleKey] ?? page.routeTitleKey
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeTitle ?? 18
            }
        }

        Text {
            objectName: "pageDescription"
            Layout.fillWidth: true
            visible: text.length > 0
            text: page.routeDescriptionKey ? (Tr?.map[page.routeDescriptionKey] ?? page.routeDescriptionKey) : ""
            color: Theme?.textSecondary ?? "transparent"
            font.pixelSize: Theme?.fontSizeBase ?? 12
            wrapMode: Text.WordWrap
        }

        // 技术信息行：路由 id 与参数。深链与 Agent 跳转"参数到底有没有传到页面"的
        // 直接证据，是技术串而不是用户文案，所以不走 i18n。
        Text {
            objectName: "pageRouteInfo"
            text: "route: " + page.routeId + "  params: " + JSON.stringify(page.routeParams)
            color: Theme?.textSecondary ?? "transparent"
            font.pixelSize: Theme?.fontSizeSmall ?? 10
        }

        // 三态切换（阶段 3 删除）
        RowLayout {
            spacing: Theme?.spacingSm ?? 0

            Button {
                objectName: "demoLoadingButton"
                text: Tr?.map["plugin_state_loading"] ?? "plugin_state_loading"
                onClicked: page.demoState = "loading"
            }

            Button {
                objectName: "demoEmptyButton"
                text: Tr?.map["mod_browser_no_results"] ?? "mod_browser_no_results"
                onClicked: page.demoState = "empty"
            }

            Button {
                objectName: "demoErrorButton"
                text: Tr?.map["plugin_state_error"] ?? "plugin_state_error"
                onClicked: page.demoState = "error"
            }
        }

        // 三态区域：阶段 3 换成真实的列表 / 表单 / 详情
        Rectangle {
            objectName: "pageStateArea"
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: Theme?.cardBg ?? "transparent"
            border.width: 1
            border.color: Theme?.cardBorder ?? "transparent"
            radius: Theme?.radiusLg ?? 0

            ColumnLayout {
                anchors.centerIn: parent
                spacing: Theme?.spacingSm ?? 0

                Image {
                    objectName: "pageStateIcon"
                    Layout.alignment: Qt.AlignHCenter
                    // 跨行的调用在 `poc/_guard_context_props.py` 里匹配不到，这里直接写全
                    source: (Runtime?.iconUrl(page.demoState === "loading" ? "loading"
                                              : (page.demoState === "empty" ? "folder-open" : "error")) ?? "")
                    sourceSize.width: (Theme?.iconSize ?? 0) * 2
                    sourceSize.height: (Theme?.iconSize ?? 0) * 2
                    width: (Theme?.iconSize ?? 0) * 2
                    height: (Theme?.iconSize ?? 0) * 2
                    smooth: true
                }

                Text {
                    objectName: "pageStateText"
                    Layout.alignment: Qt.AlignHCenter
                    text: page.demoState === "loading" ? (Tr?.map["plugin_state_loading"] ?? "plugin_state_loading")
                          : (page.demoState === "empty" ? (Tr?.map["mod_browser_no_results"] ?? "mod_browser_no_results")
                          : (Tr?.map["plugin_state_error"] ?? "plugin_state_error"))
                    color: page.demoState === "error" ? Theme?.error ?? "transparent" : Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeLarge ?? 14
                }
            }
        }
    }
}
