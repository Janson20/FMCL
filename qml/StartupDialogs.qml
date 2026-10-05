// 启动期弹窗（阶段 2 任务 2.14）：用户协议 + AI 隐私同意（A-21）与公告展示（A-22）。
//
// 为什么单独一个文件而不是复用 2.13 的 `components/dialogs/`：这两个弹窗属于
// **启动链条**（`Startup` 驱动），必须在主窗口还没显示、对话框宿主还没握手之前就能弹出来；
// 而复用那套组件会让"启动流程"依赖"对话框基础设施已就绪"。归属分开更干净。
//
// 语义（对照表 A-21，逐条对齐旧实现）：
//   * 两个勾选框**都勾上**才允许点「同意并继续」（旧实现是"勾选后方可确认"）；
//   * 同意后由 `Startup.confirmAgreement()` 写回 `config.terms_consent` 与
//     `config.ai_privacy_consent` 并落盘；
//   * 文案全部来自语言文件（`terms_*` / `ai_privacy_*` / `notice_*`），QML 里没有中文；
//   * 公告关闭后由 `Startup.dismissNotice()` 继续"预下载"那一步。
//
// 留待阶段 3 的（已登记、不是遗漏）：`TERMS_OF_USE.md` **全文**的富文本渲染归
// 3.26（关于 / 链接 / 协议）；这里显示的是语言文件里那段正式声明（与旧弹窗的正文一致）。
//
// 上面那句已经**作废**（用户 3.1 人工验收报的缺陷 D-162：旧弹窗显示的是
// `TERMS_OF_USE.md` 全文 110 行，QML 版只显示了一句摘要 = 功能丢失）。现在协议区
// 优先渲染 `Startup.termsText`（Markdown 全文，`Text.MarkdownText`），只有在读不到
// 文件时才退回语言文件里的 `terms_content` 摘要 —— 与旧实现"读不到就给提示"等价。
// AI 隐私声明仍单独一段（它是**另一份**同意，旧弹窗里由同一个勾选框覆盖）。

import QtQuick
import QtQuick.Layouts
import "components"

Item {
    id: root
    anchors.fill: parent

    property bool agreementOpen: false
    property bool noticeOpen: false
    property string noticeText: ""

    //: 协议全文（`Startup.termsText`，Markdown 原文）。**做成根上的只读属性**而不是
    //: 内联在滚动视图里：`typeof Startup !== "undefined"` 的判空只写一处，
    //: 也让下面那句 `textFormat` 有单一的真值来源。读不到时为空串 → 用摘要兜底。
    readonly property bool hasTermsText: (typeof Startup !== "undefined" && Startup)
                                         ? (String(Startup.termsText ?? "").length > 0) : false
    readonly property string termsBody: hasTermsText ? String(Startup.termsText)
                                                     : (Tr?.map["terms_content"] ?? "terms_content")

    function openAgreement() {
        agreementOpen = true
        termsChecked.checked = false
        privacyChecked.checked = false
    }

    function openNotice(content) {
        noticeText = content
        noticeOpen = true
    }

    function t(key) {
        return Tr ? (Tr?.map[key] ?? key) : key
    }

    Connections {
        // `target: Startup` 在 `Startup` **完全没声明**时会抛 `ReferenceError`
        // （实测：shell 的 QML 探针只提供 Theme/Tr/Nav 等，不提供 Startup）。
        // 所以用 `typeof` 判一下 —— 这与 `?.`/`??` 适用的情况不同。
        target: typeof Startup !== "undefined" ? Startup : null

        function onAgreementRequired() {
            root.openAgreement()
        }

        function onNoticeReady(content) {
            root.openNotice(content)
        }
    }

    // ── 全屏遮罩 + 协议弹窗 ───────────────────────────────────────
    // 遮罩用派生令牌 `Theme.scrim`（黑 55% 透明），**不再是不透明的 bgDark** ——
    // 返工 B 组实测：不透明遮罩会把整个界面盖死，启动完成后用户看到的是
    // "一片纯色底 + 一个弹窗"，看不到背后的壳层，观感上像卡住了。
    // 卡片用 `Theme.overlayBg`（比 cardBg 再"浮"一层），遮罩变透明之后仍然分得清层次。
    Rectangle {
        anchors.fill: parent
        visible: root.agreementOpen
        color: Theme?.scrim ?? "transparent"
        z: 200

        Rectangle {
            id: termsCard
            anchors.centerIn: parent
            width: Math.min(720, parent.width - 80)
            height: Math.min(560, parent.height - 80)
            color: Theme?.overlayBg ?? "transparent"
            border.width: 1
            border.color: Theme?.cardBorder ?? "transparent"
            radius: Theme?.radiusLg ?? 12

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: Theme?.spacingLg ?? 20
                spacing: Theme?.spacingMd ?? 15

                Text {
                    Layout.fillWidth: true
                    text: root.t("terms_title")
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeTitle ?? 18
                    font.bold: true
                }

                Text {
                    Layout.fillWidth: true
                    text: root.t("terms_scroll_hint")
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                    wrapMode: Text.WordWrap
                }

                FmScrollView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true

                    ColumnLayout {
                        width: termsCard.width - (Theme?.spacingLg ?? 20) * 2
                        spacing: Theme?.spacingMd ?? 15

                        Text {
                            objectName: "termsContentText"
                            Layout.fillWidth: true
                            //: 协议**全文**（`TERMS_OF_USE.md` 的 Markdown）；读不到时用摘要兜底
                            text: root.termsBody
                            //: Markdown 原文由 Qt 自己渲染；颜色/字号来自 Theme，所以深色主题下可读
                            textFormat: root.hasTermsText ? Text.MarkdownText : Text.PlainText
                            color: Theme?.textPrimary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeBase ?? 12
                            wrapMode: Text.WordWrap
                        }

                        Text {
                            Layout.fillWidth: true
                            text: root.t("ai_privacy_title")
                            color: Theme?.textPrimary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeLarge ?? 14
                            font.bold: true
                        }

                        Text {
                            objectName: "privacyContentText"
                            Layout.fillWidth: true
                            text: root.t("ai_privacy_content")
                            color: Theme?.textPrimary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeBase ?? 12
                            wrapMode: Text.WordWrap
                        }
                    }
                }

                // 返工 C 组：勾选框换成 FmCheckBox（闸门 R9）。语义上本来就该是复选框 ——
                // 这两个勾选**跟"同意并继续"一起生效**，而 FmSwitch 的定位是"立即生效"。
                FmCheckBox {
                    id: termsChecked
                    objectName: "termsCheckBox"
                    Layout.fillWidth: true
                    text: root.t("terms_agree")
                }

                FmCheckBox {
                    id: privacyChecked
                    objectName: "privacyCheckBox"
                    Layout.fillWidth: true
                    text: root.t("ai_privacy_agreement")
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 10
                    Item { Layout.fillWidth: true }

                    FmButton {
                        objectName: "agreeButton"
                        // 勾选前禁用 —— 这是合规语义，不是样式选择
                        enabled: termsChecked.checked && privacyChecked.checked
                        text: root.t("ai_privacy_accept")
                        onClicked: {
                            root.agreementOpen = false
                            if (Startup)
                                Startup.confirmAgreement()
                        }
                    }
                }
            }
        }
    }

    // ── 公告弹窗 ─────────────────────────────────────────────────
    Rectangle {
        anchors.fill: parent
        visible: root.noticeOpen
        color: Theme?.scrim ?? "transparent"
        z: 210

        Rectangle {
            anchors.centerIn: parent
            width: Math.min(640, parent.width - 80)
            height: Math.min(480, parent.height - 80)
            color: Theme?.overlayBg ?? "transparent"
            border.width: 1
            border.color: Theme?.cardBorder ?? "transparent"
            radius: Theme?.radiusLg ?? 12

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: Theme?.spacingLg ?? 20
                spacing: Theme?.spacingMd ?? 15

                Text {
                    Layout.fillWidth: true
                    text: root.t("notice_title")
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeTitle ?? 18
                    font.bold: true
                }

                FmScrollView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true

                    Text {
                        objectName: "noticeContentText"
                        width: parent.width
                        // 公告正文来自网络（数据，不是界面文案）—— 闸门 R3 只管字面量
                        text: root.noticeText
                        color: Theme?.textPrimary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        wrapMode: Text.WordWrap
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Item { Layout.fillWidth: true }

                    FmButton {
                        objectName: "noticeCloseButton"
                        text: root.t("confirm")
                        onClicked: {
                            root.noticeOpen = false
                            if (Startup)
                                Startup.dismissNotice()
                        }
                    }
                }
            }
        }
    }
}
