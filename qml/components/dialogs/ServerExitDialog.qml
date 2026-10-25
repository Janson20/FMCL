// ServerExitDialog.qml —— 服务器退出质量确认 + AI 分析（阶段 3.10 L-34；3.12 反馈修复）。
//
// 为什么放在壳层：服务器可能在用户停留于首页 / 版本页 / 模组页时退出，旧实现把
// 确认框做成控制台页内的浮层，只有路由正好在控制台才看得见；进程退出不会等人
// 切页，用户看到的就是“异常退出没有弹窗”。本组件由 App.qml 直接驱动，覆盖整窗。
//
// 语义与旧 Tk 对话框一致：
//   * 标题是“服务器已停止”+ 非 0 退出码后缀，下面标出是哪台服务器；
//   * 「是」= 服务器运行正常，关闭；
//   * 「否（AI 分析日志）」= 复用 services/crash_service.py 的服务器分析；
//   * 分析中 / 结果 / 失败都在同一张卡里切换，页面切换不会丢掉这次退出。
//
// 主动停止（停止按钮 / 删除运行中的服务器 / 退出启动器）不会走到这里：
// services/server_session.py 的 _handle_exit 只在非主动退出时登记待确认项。
//
// 纪律：颜色只来自 Theme.*（R8）、图标走 FmIcon/FmToolButton（R2）、文案走
// Tr.map/桥翻译（R3/R4）、只用 qml/components 里的 Fm*（R9）。

import QtQuick
import QtQuick.Layouts
import ".."

Item {
    id: root
    objectName: "serverExitDialog"

    property string serverId: ""
    property string exitInfo: ""
    property bool analyzing: false
    property string resultText: ""
    property string errorText: ""
    //: 点「否」但暂时不能分析时的原因（例如未登录净读）——留在询问态里显示，
    //: 不能把对话框直接关掉，否则用户既没得到分析也不知道为什么。
    property string promptError: ""

    signal closeRequested()
    signal analyzeRequested()

    readonly property bool hasResult: resultText.length > 0
    readonly property bool hasError: errorText.length > 0
    readonly property bool promptVisible: !analyzing && !hasResult && !hasError
    readonly property string titleText: (Tr?.map["server_exit_question_title"] ?? "server_exit_question_title")
                                          + String(exitInfo || "")

    focus: true

    onVisibleChanged: {
        if (visible)
            root.forceActiveFocus()
    }

    Keys.onEscapePressed: {
        if (!root.analyzing)
            root.closeRequested()
    }

    Rectangle {
        objectName: "serverExitScrim"
        anchors.fill: parent
        color: Theme?.scrim ?? "transparent"
    }

    MouseArea { anchors.fill: parent }

    FmCard {
        id: card
        objectName: "serverExitCard"
        anchors.centerIn: parent
        width: Math.min(620, Math.max(420, parent.width - 2 * (Theme?.spacingXl ?? 24)))
        padding: Theme?.spacingLg ?? 16

        RowLayout {
            objectName: "serverExitHeader"
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 8

            FmIcon {
                name: "server"
                color: Theme?.error ?? "transparent"
                size: Theme?.fontSizeTitle ?? 18
                Layout.alignment: Qt.AlignTop
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: Theme?.spacingXs ?? 2

                Text {
                    objectName: "serverExitTitle"
                    Layout.fillWidth: true
                    text: root.titleText
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeTitle ?? 18
                    font.bold: true
                    wrapMode: Text.WordWrap
                }

                Text {
                    objectName: "serverExitServer"
                    Layout.fillWidth: true
                    visible: root.serverId.length > 0
                    text: root.serverId
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 11
                    elide: Text.ElideMiddle
                }
            }
        }

        Text {
            objectName: "serverExitMessage"
            Layout.fillWidth: true
            visible: root.promptVisible
            text: Tr?.map["server_exit_question_msg"] ?? "server_exit_question_msg"
            color: Theme?.textSecondary ?? "transparent"
            font.pixelSize: Theme?.fontSizeBase ?? 12
            wrapMode: Text.WordWrap
        }

        FmInfoBar {
            objectName: "serverExitPromptError"
            Layout.fillWidth: true
            visible: root.promptVisible && root.promptError.length > 0
            level: "warning"
            text: root.promptError
        }

        FmLoadingState {
            objectName: "serverExitLoading"
            Layout.fillWidth: true
            Layout.preferredHeight: 140
            visible: root.analyzing
            title: Tr?.map["crash_analyzing"] ?? "crash_analyzing"
        }

        Text {
            objectName: "serverExitResultTitle"
            Layout.fillWidth: true
            visible: root.hasResult
            text: Tr?.map["crash_analyze_success"] ?? "crash_analyze_success"
            color: Theme?.textPrimary ?? "transparent"
            font.pixelSize: Theme?.fontSizeLarge ?? 14
            font.bold: true
        }

        FmScrollView {
            id: resultScroll
            objectName: "serverExitResultScroll"
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(320, Math.max(64, resultColumn.implicitHeight + 16))
            visible: root.hasResult

            Column {
                id: resultColumn
                width: Math.max(0, resultScroll.availableWidth)
                spacing: Theme?.spacingSm ?? 8

                Text {
                    objectName: "serverExitResultText"
                    width: parent.width
                    text: root.resultText
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                    wrapMode: Text.WordWrap
                }
            }
        }

        FmInfoBar {
            objectName: "serverExitError"
            Layout.fillWidth: true
            visible: root.hasError
            level: "error"
            text: root.errorText
        }

        RowLayout {
            objectName: "serverExitActions"
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 8

            Item { Layout.fillWidth: true }

            FmButton {
                objectName: "serverExitAnalyzeButton"
                visible: root.promptVisible
                primary: true
                text: Tr?.map["server_exit_no_analyze"] ?? "server_exit_no_analyze"
                onClicked: root.analyzeRequested()
            }

            FmButton {
                objectName: "serverExitYesButton"
                visible: root.promptVisible
                primary: false
                text: Tr?.map["server_exit_yes"] ?? "server_exit_yes"
                onClicked: root.closeRequested()
            }

            FmButton {
                objectName: "serverExitCloseButton"
                visible: root.hasResult || root.hasError
                primary: false
                text: Tr?.map["close"] ?? "close"
                onClicked: root.closeRequested()
            }
        }
    }
}
