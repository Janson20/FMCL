// QuizPage.qml —— MC 知识问答（阶段 3 任务 3.20 / C-07）。
//
// 题库由桥持久化到 quiz.json；生成/自动补充都在工作线程里调净读 AI。
// 页面只显示当前题、选项/填空、判分结果与剩余数量。

import QtQuick
import QtQuick.Layouts
import "../../components"

FmPage {
    id: page
    objectName: "quizPage"

    readonly property bool live: (typeof Tools !== "undefined" && Tools && Tools.available) ? true : false
    property string answerText: ""
    property int lastQuestionId: -1

    function t(key) {
        return Tr ? (Tr?.map[key] ?? key) : key
    }

    function tf(key, params) {
        return Tr ? Tr.tf(key, params) : key
    }

    function submitText() {
        if (!(page ? page.live : false) || Tools.quizAnswered)
            return;
        if (String(answerField.text || "").trim().length === 0)
            return;
        Tools.answerQuizText(String(answerField.text));
    }

    contentState: (page ? page.live : false) ? "ready" : "error"
    errorText: Tr?.map["plugin_state_error"] ?? "plugin_state_error"
    errorDetail: Tr?.map["tool_unavailable"] ?? "tool_unavailable"

    Component.onCompleted: {
        if ((page ? page.live : false))
            Tools.activateQuiz();
    }

    Connections {
        target: (page ? page.live : false) ? Tools : null

        function onQuizChanged() {
            if (!page)
                return;
            var id = Number(Tools.quizCurrent.id || -1)
            if (id !== page.lastQuestionId) {
                page.lastQuestionId = id;
                page.answerText = "";
                if (answerField)
                    answerField.text = "";
            }
        }

    }

    FmScrollView {
        id: scroller
        anchors.fill: parent
        contentWidth: availableWidth

        ColumnLayout {
            width: scroller.availableWidth
            spacing: Theme?.spacingMd ?? 12

            FmCard {
                Layout.fillWidth: true

                FmInfoBar {
                    Layout.fillWidth: true
                    visible: (page ? page.live : false) && !Tools.quizToken
                    text: page && page.t("tool_quiz_login_hint")
                    level: "warning"
                    actionText: page && page.t("login")
                    onActionTriggered: Nav.push("settings/ai")
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 8

                    FmButton {
                        objectName: "quizGenerateButton"
                        text: (page ? page.live : false) && Tools.quizBusy
                              ? page && page.t("tool_quiz_generating")
                              : page && page.t("tool_quiz_generate")
                        loading: (page ? page.live : false) && Tools.quizBusy
                        enabled: (page ? page.live : false) && Tools.quizToken && !Tools.quizBusy && !Tools.quizRefilling
                        onClicked: {
                            if ((page ? page.live : false))
                                Tools.generateQuiz();
                        }
                    }

                    FmButton {
                        objectName: "quizNextButton"
                        primary: false
                        text: page && page.t("tool_quiz_next")
                        enabled: (page ? page.live : false) && Tools.quizHasQuestions && !Tools.quizBusy
                        onClicked: {
                            if ((page ? page.live : false))
                                Tools.nextQuiz();
                        }
                    }

                    Text {
                        visible: (page ? page.live : false) && Tools.quizRefilling
                        text: page && page.t("tool_quiz_refilling")
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                    }

                    Item { Layout.fillWidth: true }

                    Text {
                        text: (page ? page.live : false)
                              ? page && page.tf("tool_quiz_remaining", {"count": String(Tools.quizRemaining || 0)})
                              : ""
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                    }
                }

                FmInfoBar {
                    Layout.fillWidth: true
                    visible: (page ? page.live : false) && String(Tools.quizError || "").length > 0
                    text: (page ? page.live : false) ? String(Tools.quizError || "") : ""
                    level: "error"
                }

                FmInfoBar {
                    Layout.fillWidth: true
                    visible: (page ? page.live : false) && String(Tools.quizNotice || "").length > 0
                    text: (page ? page.live : false) ? String(Tools.quizNotice || "") : ""
                    level: "success"
                }
            }

            FmCard {
                Layout.fillWidth: true

                FmLoadingState {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 180
                    visible: (page ? page.live : false) && Tools.quizBusy
                    title: page && page.t("tool_quiz_ai_thinking")
                }

                FmEmptyState {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 200
                    visible: (page ? page.live : false) && !Tools.quizBusy && !Tools.quizHasQuestions
                    iconName: "guide"
                    title: page && page.t("tool_quiz_no_questions")
                    description: (page ? page.live : false) && !Tools.quizToken
                                 ? page && page.t("tool_quiz_login_hint")
                                 : page && page.t("tool_quiz_generate")
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    visible: (page ? page.live : false) && !Tools.quizBusy && Tools.quizHasQuestions
                    spacing: Theme?.spacingSm ?? 8

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme?.spacingSm ?? 8

                        FmTag {
                            text: (page ? page.live : false)
                                  ? page && page.tf("tool_quiz_progress", {
                                        "current": String(Tools.quizCurrent.id || 0)
                                    })
                                  : ""
                            level: "accent"
                        }

                        Item { Layout.fillWidth: true }
                    }

                    Text {
                        Layout.fillWidth: true
                        text: (page ? page.live : false) ? String(Tools.quizCurrent.question || "") : ""
                        color: Theme?.textPrimary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeLarge ?? 14
                        font.bold: true
                        wrapMode: Text.WordWrap
                    }

                    Repeater {
                        model: (page ? page.live : false) ? Tools.quizCurrent.options : []

                        FmButton {
                            objectName: "quizOption_" + index
                            Layout.fillWidth: true
                            primary: false
                            text: String(modelData)
                            enabled: (page ? page.live : false) && !Tools.quizAnswered
                            onClicked: {
                                if ((page ? page.live : false))
                                    Tools.answerQuizChoice(index);
                            }
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        visible: (page ? page.live : false) && !Tools.quizCurrent.isChoice
                        spacing: Theme?.spacingSm ?? 8

                        FmTextField {
                            id: answerField
                            objectName: "quizAnswerField"
                            Layout.fillWidth: true
                            placeholder: page && page.t("tool_quiz_answer_placeholder")
                            text: answerText
                            readOnly: (page ? page.live : false) && Tools.quizAnswered
                            onEdited: function(value) { page.answerText = value }
                            onAccepted: page.submitText()
                        }

                        FmButton {
                            objectName: "quizSubmitButton"
                            primary: false
                            text: page && page.t("tool_quiz_submit")
                            enabled: (page ? page.live : false) && !Tools.quizAnswered
                                     && String(answerField.text || "").trim().length > 0
                            onClicked: page.submitText()
                        }
                    }

                    FmInfoBar {
                        Layout.fillWidth: true
                        visible: (page ? page.live : false) && Tools.quizAnswered
                        level: (page ? page.live : false) && Tools.quizResult.correct ? "success" : "error"
                        text: (page ? page.live : false) && Tools.quizResult.correct
                              ? page && page.t("tool_quiz_correct")
                              : ((page ? page.live : false)
                                 ? page && page.tf("tool_quiz_wrong", {
                                       "correct": String(Tools.quizResult.correctAnswer || "")
                                   })
                                 : "")
                    }

                    Text {
                        Layout.fillWidth: true
                        visible: (page ? page.live : false) && Tools.quizAnswered
                                 && String(Tools.quizResult.explanation || "").length > 0
                        text: (page ? page.live : false) ? String(Tools.quizResult.explanation || "") : ""
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        wrapMode: Text.WordWrap
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme?.spacingSm ?? 8

                        Item { Layout.fillWidth: true }

                        FmButton {
                            primary: false
                            text: page && page.t("tool_quiz_next")
                            enabled: (page ? page.live : false) && Tools.quizHasQuestions && !Tools.quizBusy
                            onClicked: {
                                if ((page ? page.live : false))
                                    Tools.nextQuiz();
                            }
                        }
                    }
                }
            }
        }
    }
}
