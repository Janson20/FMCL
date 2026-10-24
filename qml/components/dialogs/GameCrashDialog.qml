// GameCrashDialog.qml —— 游戏客户端崩溃提示 + AI 分析弹窗（阶段 3.10 补充）。
//
// 与 2.13 的 DialogHost 不同：这个弹窗由**壳层**直接驱动（App.qml 接
// Home.crashDialogRequested），因为它必须覆盖整个窗口，而不是某个页面的内容区。
//
// 数据全部来自 Home 桥的崩溃快照：
//   { exitCode, message, hasCrashReport, hasGameLog, hasDebugLog,
//     hasJvmCrashLog, diagnoses: [{name, cause, advice}], canAnalyze }
// 诊断文本由 services/crash_service.py 的 CRASH_TYPES 提供（服务层的中文数据，
// 旧界面同样原样展示）；本组件不自己判崩溃类型。
//
// 纪律：颜色只来自 Theme.*（R8）、图标走 FmIcon/FmToolButton（R2）、文案走
// Tr.map/桥翻译（R3/R4）、只用 qml/components 里的 Fm*（R9）。

import QtQuick
import QtQuick.Layouts
import ".."

Item {
    id: root
    objectName: "gameCrashDialog"

    property var crashInfo: ({})
    property bool analyzing: false
    property string resultText: ""
    property string errorText: ""

    signal closeRequested()
    signal analyzeRequested()
    signal openFileRequested(string kind)

    readonly property var diagnoses: (crashInfo && crashInfo.diagnoses) ? crashInfo.diagnoses : []
    readonly property bool hasResult: resultText.length > 0
    readonly property bool canAnalyze: !!(crashInfo && crashInfo.canAnalyze)
    readonly property bool hasCrashReport: !!(crashInfo && crashInfo.hasCrashReport)
    readonly property bool hasGameLog: !!(crashInfo && crashInfo.hasGameLog)
    readonly property string messageText: (crashInfo && crashInfo.message)
                                          ? String(crashInfo.message) : ""

    Rectangle {
        objectName: "gameCrashScrim"
        anchors.fill: parent
        color: Theme?.bgDark ?? "transparent"
        opacity: 0.55
    }

    MouseArea { anchors.fill: parent }

    FmCard {
        objectName: "gameCrashCard"
        anchors.centerIn: parent
        width: Math.min(680, Math.max(420, parent.width - 2 * (Theme?.spacingXl ?? 24)))
        height: Math.min(640, Math.max(420, parent.height - 2 * (Theme?.spacingXl ?? 24)))
        padding: Theme?.spacingLg ?? 16

        RowLayout {
            objectName: "gameCrashHeader"
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 8

            FmIcon {
                name: "crash"
                color: Theme?.error ?? "transparent"
                size: Theme?.fontSizeTitle ?? 18
                Layout.alignment: Qt.AlignVCenter
            }

            Text {
                objectName: "gameCrashTitle"
                Layout.fillWidth: true
                text: Tr?.map["crash_title"] ?? "crash_title"
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeTitle ?? 18
                font.bold: true
                elide: Text.ElideRight
            }

            FmToolButton {
                objectName: "gameCrashCloseTop"
                iconName: "close"
                onClicked: root.closeRequested()
            }
        }

        Text {
            objectName: "gameCrashMessage"
            Layout.fillWidth: true
            text: root.messageText
            color: Theme?.textPrimary ?? "transparent"
            font.pixelSize: Theme?.fontSizeBase ?? 12
            wrapMode: Text.WordWrap
        }

        RowLayout {
            objectName: "gameCrashFileTags"
            Layout.fillWidth: true
            spacing: Theme?.spacingXs ?? 4

            FmTag {
                objectName: "gameCrashReportTag"
                visible: root.hasCrashReport
                text: Tr?.map["crash_report"] ?? "crash_report"
                level: "error"
            }

            FmTag {
                objectName: "gameCrashLogTag"
                visible: root.hasGameLog
                text: Tr?.map["crash_game_log"] ?? "crash_game_log"
                level: "warning"
            }

            Item { Layout.fillWidth: true }
        }

        FmLoadingState {
            objectName: "gameCrashLoading"
            visible: root.analyzing
            Layout.fillWidth: true
            Layout.fillHeight: true
            title: Tr?.map["crash_analyzing"] ?? "crash_analyzing"
        }

        FmScrollView {
            id: crashScroll
            objectName: "gameCrashScroll"
            visible: !root.analyzing
            Layout.fillWidth: true
            Layout.fillHeight: true

            Column {
                width: Math.max(0, crashScroll.availableWidth)
                spacing: Theme?.spacingSm ?? 8

                Repeater {
                    objectName: "gameCrashDiagnoses"
                    model: root.diagnoses

                    delegate: Rectangle {
                        objectName: "gameCrashDiagnosis"
                        required property var modelData
                        required property int index

                        width: parent.width
                        height: diagnosisColumn.implicitHeight + 2 * (Theme?.spacingSm ?? 8)
                        radius: Theme?.radiusMd ?? 6
                        color: Theme?.bgMedium ?? "transparent"
                        border.width: 1
                        border.color: Theme?.cardBorder ?? "transparent"

                        Column {
                            id: diagnosisColumn
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.margins: Theme?.spacingSm ?? 8
                            spacing: Theme?.spacingXs ?? 2

                            Text {
                                objectName: "gameCrashDiagnosisName"
                                width: parent.width
                                text: String(modelData.name || "")
                                color: Theme?.error ?? "transparent"
                                font.pixelSize: Theme?.fontSizeBase ?? 12
                                font.bold: true
                                wrapMode: Text.WordWrap
                            }

                            Text {
                                objectName: "gameCrashDiagnosisCause"
                                width: parent.width
                                visible: text.length > 0
                                text: String(modelData.cause || "")
                                color: Theme?.textSecondary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 11
                                wrapMode: Text.WordWrap
                            }

                            Text {
                                objectName: "gameCrashDiagnosisAdvice"
                                width: parent.width
                                visible: text.length > 0
                                text: String(modelData.advice || "")
                                color: Theme?.textPrimary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 11
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                }

                Column {
                    objectName: "gameCrashResultBlock"
                    width: parent.width
                    visible: root.hasResult
                    spacing: Theme?.spacingSm ?? 8

                    Text {
                        objectName: "gameCrashResultTitle"
                        width: parent.width
                        text: Tr?.map["crash_analyze_success"] ?? "crash_analyze_success"
                        color: Theme?.textPrimary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeLarge ?? 14
                        font.bold: true
                    }

                    Text {
                        objectName: "gameCrashResultText"
                        width: parent.width
                        text: root.resultText
                        color: Theme?.textPrimary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        wrapMode: Text.WordWrap
                    }
                }

                FmInfoBar {
                    objectName: "gameCrashErrorBar"
                    width: parent.width
                    visible: root.errorText.length > 0
                    level: "error"
                    text: root.errorText
                }

                Text {
                    objectName: "gameCrashLoginHint"
                    width: parent.width
                    visible: !root.analyzing && !root.hasResult && !root.canAnalyze
                    text: Tr?.map["ai_search_login_required"] ?? "ai_search_login_required"
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 11
                    wrapMode: Text.WordWrap
                }
            }
        }

        RowLayout {
            objectName: "gameCrashActions"
            Layout.fillWidth: true
            spacing: Theme?.spacingSm ?? 8

            FmButton {
                objectName: "gameCrashOpenReportButton"
                visible: root.hasCrashReport
                primary: false
                text: Tr?.map["crash_report"] ?? "crash_report"
                iconName: "file"
                onClicked: root.openFileRequested("crash_report")
            }

            FmButton {
                objectName: "gameCrashOpenLogButton"
                visible: root.hasGameLog
                primary: false
                text: Tr?.map["crash_game_log"] ?? "crash_game_log"
                iconName: "note"
                onClicked: root.openFileRequested("game_log")
            }

            Item { Layout.fillWidth: true }

            FmButton {
                objectName: "gameCrashCloseButton"
                primary: false
                text: Tr?.map["close"] ?? "close"
                onClicked: root.closeRequested()
            }

            FmButton {
                objectName: "gameCrashAnalyzeButton"
                visible: !root.analyzing && !root.hasResult
                primary: true
                text: Tr?.map["crash_ai_analyze"] ?? "crash_ai_analyze"
                enabled: root.canAnalyze
                onClicked: root.analyzeRequested()
            }
        }
    }
}
