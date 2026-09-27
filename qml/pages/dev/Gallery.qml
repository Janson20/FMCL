// Gallery.qml —— 组件画廊（阶段 2 任务 2.17，开发自查页）。
//
// 它是什么：**每一个**组件库成员各有一个"名字 + 说明 + 活的实例"。活的实例指的是
//   真的能点、能输入、能切换状态（不是静态截图）—— 阶段 3 的人在这一页上挑件，
//   验收的人在这一页上过一遍"点/输/切"是否都正常。
//   本页同时是 `tests/test_components_qml.py` 的被测对象：探针在子进程里加载它、
//   逐个 `galleryItem_<组件名>` 核对、点几下断言状态真的变了、抓帧留证据。
//
// 它不是业务页：**不在 12 个一级导航里露出**。进入方式是深链 `fmcl://dev/gallery`
//   或显式 `Nav.push("dev/gallery")`（路由定义在 `app/bridges/nav_bridge.py`，
//   `parent` 挂 settings，所以 Shell.navItems() 不会把它当一级导航项；理由见 2.16 报告）。
//
// 纪律（与其它页面一致）：颜色只来自 Theme.*（闸门 R8）、图标走 FmIcon（闸门 R2）、
//   文案走 Tr.map[…]（闸门 R3/R4）。本页里以英文字面量出现的只有**组件名与属性名**
//   这类技术串（"FmButton"、"primary"、"indeterminate"），与其它页面的 pageRouteInfo 同理：
//   那是给开发者看的标识符，不是用户文案。
//   `Theme?.x ?? 兜底` 的判空是必需的：引擎析构时上下文属性先被清成 null 而绑定还排在
//   求值队列里，不判空会每个绑定刷一条 TypeError（2.2 实测的缺陷）。
//
// 本页需要的 26 个 i18n 键（`dev_gallery_*`，语言文件不在 2.16 的可改范围，阶段 3 补；
//   缺键时按契约 §5.2 的约定回退显示键名）：标题 1 + 分区 5 + 每个组件一条说明 20。

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../../components"

Item {
    id: page
    objectName: "galleryPage"

    // PageStack 在 push 时设进来的**这一帧的快照**（见 shell/PageStack.qml 的说明）
    property string routeId: ""
    property string routeTitleKey: ""
    property string routeDescriptionKey: ""
    property string routeIcon: ""
    property var routeParams: ({})

    // ── 演示状态：本页所有交互都落到这几个属性上，探针直接断言它们 ──
    property int clickCount: 0
    property bool demoToggle: true
    property int demoChoice: 2
    property int demoMirror: 0
    property real demoLevel: 40
    property int demoPage: 1
    property int demoRow: -1
    property bool demoTagAlive: true
    property string demoKeyword: ""
    property int demoRetries: 0
    property real demoFraction: 0.15
    property int toolButtonClicks: 0
    //: 返工 C 组新增的两件（FmCheckBox 的勾选态、FmSpinBox 的当前值）
    property bool demoAgreed: false
    property int demoThreads: 8

    //: 进度条的自动演示（每 100ms 加 5%，到 1 回到 0）：让"活的进度条"不需要人动手
    Timer {
        objectName: "galleryProgressTimer"
        interval: 100
        repeat: true
        running: page.visible
        onTriggered: page.demoFraction = page.demoFraction >= 0.95 ? 0.05 : page.demoFraction + 0.05
    }

    FmScrollView {
        id: scroller
        objectName: "galleryScroll"
        anchors.fill: parent
        // 内容宽度一律跟随可用宽度（FmScrollView 默认就不出横向滚动条）；
        // 纵向滚动条按需出现，样式由 FmScrollView 用令牌统一画（闸门 R9 禁原生控件）。
        contentWidth: availableWidth

        ColumnLayout {
            id: content
            width: scroller.availableWidth - 2 * (Theme?.spacingLg ?? 0)
            x: Theme?.spacingLg ?? 0
            spacing: Theme?.spacingSm ?? 0

            // ── 页头 ────────────────────────────────────────────
            Text {
                objectName: "galleryTitle"
                Layout.fillWidth: true
                Layout.topMargin: Theme?.spacingLg ?? 0
                text: Tr?.map[page.routeTitleKey] ?? page.routeTitleKey
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeTitle ?? 18
                font.bold: true
            }

            Text {
                objectName: "galleryIntro"
                Layout.fillWidth: true
                text: Tr?.map["dev_gallery_intro"] ?? "dev_gallery_intro"
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                wrapMode: Text.WordWrap
            }

            // ══ 第一节：动作与标记 ══════════════════════════════
            Text {
                objectName: "gallerySection_actions"
                Layout.fillWidth: true
                Layout.topMargin: Theme?.spacingLg ?? 0
                text: Tr?.map["dev_gallery_sec_actions"] ?? "dev_gallery_sec_actions"
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeLarge ?? 14
                font.bold: true
            }

            // ── FmButton：三种语义 + 三种状态 ──
            ColumnLayout {
                objectName: "galleryItem_FmButton"
                Layout.fillWidth: true
                spacing: Theme?.spacingXs ?? 0

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 0

                    Text {
                        text: "FmButton"
                        color: Theme?.accent ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        font.bold: true
                    }

                    Text {
                        objectName: "galleryHint_button"
                        Layout.fillWidth: true
                        text: Tr?.map["dev_gallery_hint_button"] ?? "dev_gallery_hint_button"
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        elide: Text.ElideRight
                    }
                }

                RowLayout {
                    spacing: Theme?.spacingSm ?? 0

                    FmButton {
                        objectName: "galleryButtonPrimary"
                        text: "primary"
                        onClicked: page.clickCount += 1
                    }

                    FmButton {
                        objectName: "galleryButtonSecondary"
                        primary: false
                        iconName: "folder-open"
                        text: "secondary"
                        onClicked: page.clickCount += 1
                    }

                    FmButton {
                        objectName: "galleryButtonDanger"
                        primary: false
                        danger: true
                        iconName: "trash"
                        text: "danger"
                        onClicked: page.clickCount += 1
                    }

                    FmButton {
                        objectName: "galleryButtonDisabled"
                        primary: false
                        enabled: false
                        text: "disabled"
                    }

                    FmButton {
                        objectName: "galleryButtonLoading"
                        iconName: "download"
                        loading: true
                        text: "loading"
                    }

                    Text {
                        objectName: "galleryClickCount"
                        Layout.alignment: Qt.AlignVCenter
                        text: String(page.clickCount)
                        color: Theme?.textPrimary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                    }
                }
            }

            // ── FmIcon：主题上色（provider 真在干活才不是黑的） ──
            ColumnLayout {
                objectName: "galleryItem_FmIcon"
                Layout.fillWidth: true
                spacing: Theme?.spacingXs ?? 0

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 0

                    Text {
                        text: "FmIcon"
                        color: Theme?.accent ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        font.bold: true
                    }

                    Text {
                        objectName: "galleryHint_icon"
                        Layout.fillWidth: true
                        text: Tr?.map["dev_gallery_hint_icon"] ?? "dev_gallery_hint_icon"
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        elide: Text.ElideRight
                    }
                }

                RowLayout {
                    spacing: Theme?.spacingMd ?? 0

                    FmIcon {
                        objectName: "galleryIconPrimary"
                        name: "check"
                        color: Theme?.textPrimary ?? "transparent"
                        size: Theme?.iconSize ?? 0
                    }

                    FmIcon {
                        objectName: "galleryIconAccent"
                        name: "rocket"
                        color: Theme?.accent ?? "transparent"
                        size: Theme?.iconSize ?? 0
                    }

                    FmIcon {
                        objectName: "galleryIconSuccess"
                        name: "success"
                        color: Theme?.success ?? "transparent"
                        size: Theme?.iconSize ?? 0
                    }

                    FmIcon {
                        objectName: "galleryIconWarning"
                        name: "warning"
                        color: Theme?.warning ?? "transparent"
                        size: Theme?.iconSize ?? 0
                    }

                    FmIcon {
                        objectName: "galleryIconError"
                        name: "error"
                        color: Theme?.error ?? "transparent"
                        size: Theme?.iconSize ?? 0
                    }
                }
            }

            // ── FmToolButton：纯图标命中区（顶栏/行尾）与它的三态 ──
            ColumnLayout {
                objectName: "galleryItem_FmToolButton"
                Layout.fillWidth: true
                spacing: Theme?.spacingXs ?? 0

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 0

                    Text {
                        text: "FmToolButton"
                        color: Theme?.accent ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        font.bold: true
                    }

                    Text {
                        objectName: "galleryHint_toolbutton"
                        Layout.fillWidth: true
                        text: Tr?.map["dev_gallery_hint_toolbutton"] ?? "dev_gallery_hint_toolbutton"
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        elide: Text.ElideRight
                    }
                }

                RowLayout {
                    spacing: Theme?.spacingMd ?? 0

                    FmToolButton {
                        objectName: "galleryToolButtonNormal"
                        iconName: "refresh"
                        onClicked: page.toolButtonClicks += 1
                    }

                    FmToolButton {
                        objectName: "galleryToolButtonSelected"
                        iconName: "trophy"
                        selected: true
                        onClicked: page.toolButtonClicks += 1
                    }

                    FmToolButton {
                        objectName: "galleryToolButtonBadge"
                        iconName: "notify"
                        badgeText: "3"
                        onClicked: page.toolButtonClicks += 1
                    }

                    FmToolButton {
                        objectName: "galleryToolButtonDisabled"
                        iconName: "trash"
                        enabled: false
                    }

                    Text {
                        objectName: "galleryToolButtonReadout"
                        text: String(page.toolButtonClicks)
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        // 父项是 RowLayout：用 Layout.alignment，别用 anchors
                        // （anchors 在布局管理的项上是 undefined behavior，Qt 会打一条警告）
                        Layout.alignment: Qt.AlignVCenter
                    }
                }
            }

            // ── FmTag：五档语义 + 可关闭 ──
            ColumnLayout {
                objectName: "galleryItem_FmTag"
                Layout.fillWidth: true
                spacing: Theme?.spacingXs ?? 0

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 0

                    Text {
                        text: "FmTag"
                        color: Theme?.accent ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        font.bold: true
                    }

                    Text {
                        objectName: "galleryHint_tag"
                        Layout.fillWidth: true
                        text: Tr?.map["dev_gallery_hint_tag"] ?? "dev_gallery_hint_tag"
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        elide: Text.ElideRight
                    }
                }

                RowLayout {
                    spacing: Theme?.spacingSm ?? 0

                    FmTag {
                        objectName: "galleryTagNeutral"
                        text: "neutral"
                    }

                    FmTag {
                        objectName: "galleryTagAccent"
                        text: "accent"
                        level: "accent"
                        iconName: "modpack"
                    }

                    FmTag {
                        objectName: "galleryTagSuccess"
                        text: "success"
                        level: "success"
                        iconName: "check"
                    }

                    FmTag {
                        objectName: "galleryTagWarning"
                        text: "warning"
                        level: "warning"
                        iconName: "refresh"
                    }

                    FmTag {
                        objectName: "galleryTagError"
                        text: "error"
                        level: "error"
                        iconName: "error"
                    }

                    FmTag {
                        objectName: "galleryTagClosable"
                        visible: page.demoTagAlive
                        text: "closable"
                        level: "accent"
                        closable: true
                        onClosed: page.demoTagAlive = false
                    }

                    FmButton {
                        objectName: "galleryTagRestore"
                        visible: !page.demoTagAlive
                        primary: false
                        text: "restore"
                        onClicked: page.demoTagAlive = true
                    }
                }
            }

            // ── FmPagination：边界禁用 + 翻页 ──
            ColumnLayout {
                objectName: "galleryItem_FmPagination"
                Layout.fillWidth: true
                spacing: Theme?.spacingXs ?? 0

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 0

                    Text {
                        text: "FmPagination"
                        color: Theme?.accent ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        font.bold: true
                    }

                    Text {
                        objectName: "galleryHint_pagination"
                        Layout.fillWidth: true
                        text: Tr?.map["dev_gallery_hint_pagination"] ?? "dev_gallery_hint_pagination"
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        elide: Text.ElideRight
                    }
                }

                RowLayout {
                    spacing: Theme?.spacingMd ?? 0

                    FmPagination {
                        objectName: "galleryPagination"
                        page: page.demoPage
                        pageCount: 7
                        onPageRequested: function (target) {
                            page.demoPage = target
                        }
                    }

                    Text {
                        objectName: "galleryPageReadout"
                        Layout.alignment: Qt.AlignVCenter
                        text: String(page.demoPage)
                        color: Theme?.textPrimary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                    }
                }
            }

            // ══ 第二节：表单控件 ════════════════════════════════
            Text {
                objectName: "gallerySection_inputs"
                Layout.fillWidth: true
                Layout.topMargin: Theme?.spacingLg ?? 0
                text: Tr?.map["dev_gallery_sec_inputs"] ?? "dev_gallery_sec_inputs"
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeLarge ?? 14
                font.bold: true
            }

            // ── FmTextField：正常 / 出错 / 密码 / 只读 ──
            ColumnLayout {
                objectName: "galleryItem_FmTextField"
                Layout.fillWidth: true
                spacing: Theme?.spacingXs ?? 0

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 0

                    Text {
                        text: "FmTextField"
                        color: Theme?.accent ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        font.bold: true
                    }

                    Text {
                        objectName: "galleryHint_textfield"
                        Layout.fillWidth: true
                        text: Tr?.map["dev_gallery_hint_textfield"] ?? "dev_gallery_hint_textfield"
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        elide: Text.ElideRight
                    }
                }

                RowLayout {
                    spacing: Theme?.spacingMd ?? 0

                    FmTextField {
                        objectName: "galleryTextFieldNormal"
                        Layout.preferredWidth: 220
                        label: "version_id"
                        placeholder: "1.21.4"
                        onEdited: function (value) {
                            page.demoKeyword = value
                        }
                    }

                    FmTextField {
                        objectName: "galleryTextFieldError"
                        Layout.preferredWidth: 220
                        label: "version_id"
                        text: "1.21"
                        errorText: Tr?.map["version_not_found"] ?? "version_not_found"
                    }

                    FmTextField {
                        objectName: "galleryTextFieldPassword"
                        Layout.preferredWidth: 180
                        label: "token"
                        password: true
                        text: "secret"
                    }

                    FmTextField {
                        objectName: "galleryTextFieldReadOnly"
                        Layout.preferredWidth: 180
                        label: "path"
                        readOnly: true
                        text: "D:/fmcl"
                    }
                }

                Text {
                    objectName: "galleryTypedReadout"
                    text: page.demoKeyword
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                }
            }

            // ── FmTextArea：多行 ──
            ColumnLayout {
                objectName: "galleryItem_FmTextArea"
                Layout.fillWidth: true
                spacing: Theme?.spacingXs ?? 0

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 0

                    Text {
                        text: "FmTextArea"
                        color: Theme?.accent ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        font.bold: true
                    }

                    Text {
                        objectName: "galleryHint_textarea"
                        Layout.fillWidth: true
                        text: Tr?.map["dev_gallery_hint_textarea"] ?? "dev_gallery_hint_textarea"
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        elide: Text.ElideRight
                    }
                }

                FmTextArea {
                    objectName: "galleryTextArea"
                    Layout.preferredWidth: 420
                    lines: 3
                    label: "jvm_args"
                    placeholder: "-Xmx4G"
                    text: "-Xmx4G\n-XX:+UseG1GC"
                }
            }

            // ── FmSearchField：回车搜索 + 一键清空 ──
            ColumnLayout {
                objectName: "galleryItem_FmSearchField"
                Layout.fillWidth: true
                spacing: Theme?.spacingXs ?? 0

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 0

                    Text {
                        text: "FmSearchField"
                        color: Theme?.accent ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        font.bold: true
                    }

                    Text {
                        objectName: "galleryHint_searchfield"
                        Layout.fillWidth: true
                        text: Tr?.map["dev_gallery_hint_searchfield"] ?? "dev_gallery_hint_searchfield"
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        elide: Text.ElideRight
                    }
                }

                RowLayout {
                    spacing: Theme?.spacingMd ?? 0

                    FmSearchField {
                        objectName: "gallerySearchField"
                        Layout.preferredWidth: 260
                        placeholderText: Tr?.map["search"] ?? "search"
                        text: "sodium"
                        onAccepted: page.demoKeyword = text
                        onCleared: page.demoKeyword = ""
                    }

                    Text {
                        objectName: "gallerySearchReadout"
                        Layout.alignment: Qt.AlignVCenter
                        text: page.demoKeyword
                        color: Theme?.textPrimary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                    }
                }
            }

            // ── FmSwitch：开 / 关 / 禁用 ──
            ColumnLayout {
                objectName: "galleryItem_FmSwitch"
                Layout.fillWidth: true
                spacing: Theme?.spacingXs ?? 0

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 0

                    Text {
                        text: "FmSwitch"
                        color: Theme?.accent ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        font.bold: true
                    }

                    Text {
                        objectName: "galleryHint_switch"
                        Layout.fillWidth: true
                        text: Tr?.map["dev_gallery_hint_switch"] ?? "dev_gallery_hint_switch"
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        elide: Text.ElideRight
                    }
                }

                RowLayout {
                    spacing: Theme?.spacingLg ?? 0

                    FmSwitch {
                        objectName: "gallerySwitch"
                        text: "minimize_on_start"
                        checked: page.demoToggle
                        onToggled: page.demoToggle = checked
                    }

                    FmSwitch {
                        objectName: "gallerySwitchOff"
                        text: "auto_update"
                        checked: false
                    }

                    FmSwitch {
                        objectName: "gallerySwitchDisabled"
                        text: "disabled"
                        checked: true
                        enabled: false
                    }

                    Text {
                        objectName: "gallerySwitchReadout"
                        Layout.alignment: Qt.AlignVCenter
                        text: String(page.demoToggle)
                        color: Theme?.textPrimary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                    }
                }
            }

            // ── FmRadio：同父项自动互斥 ──
            ColumnLayout {
                objectName: "galleryItem_FmRadio"
                Layout.fillWidth: true
                spacing: Theme?.spacingXs ?? 0

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 0

                    Text {
                        text: "FmRadio"
                        color: Theme?.accent ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        font.bold: true
                    }

                    Text {
                        objectName: "galleryHint_radio"
                        Layout.fillWidth: true
                        text: Tr?.map["dev_gallery_hint_radio"] ?? "dev_gallery_hint_radio"
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        elide: Text.ElideRight
                    }
                }

                RowLayout {
                    spacing: Theme?.spacingLg ?? 0

                    FmRadio {
                        objectName: "galleryRadio1"
                        text: "download"
                        checked: page.demoChoice === 1
                        onToggled: page.demoChoice = 1
                    }

                    FmRadio {
                        objectName: "galleryRadio2"
                        text: "online"
                        checked: page.demoChoice === 2
                        onToggled: page.demoChoice = 2
                    }

                    FmRadio {
                        objectName: "galleryRadio3"
                        text: "offline"
                        checked: page.demoChoice === 3
                        onToggled: page.demoChoice = 3
                    }

                    Text {
                        objectName: "galleryRadioReadout"
                        Layout.alignment: Qt.AlignVCenter
                        text: String(page.demoChoice)
                        color: Theme?.textPrimary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                    }
                }
            }

            // ── FmComboBox：下拉 ──
            ColumnLayout {
                objectName: "galleryItem_FmComboBox"
                Layout.fillWidth: true
                spacing: Theme?.spacingXs ?? 0

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 0

                    Text {
                        text: "FmComboBox"
                        color: Theme?.accent ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        font.bold: true
                    }

                    Text {
                        objectName: "galleryHint_combobox"
                        Layout.fillWidth: true
                        text: Tr?.map["dev_gallery_hint_combobox"] ?? "dev_gallery_hint_combobox"
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        elide: Text.ElideRight
                    }
                }

                RowLayout {
                    spacing: Theme?.spacingMd ?? 0

                    FmComboBox {
                        objectName: "galleryComboBox"
                        Layout.preferredWidth: 240
                        model: ["official", "bmclapi", "mirror_a", "mirror_b"]
                        currentIndex: page.demoMirror
                        onActivated: function (index) {
                            page.demoMirror = index
                        }
                    }

                    Text {
                        objectName: "galleryComboReadout"
                        Layout.alignment: Qt.AlignVCenter
                        text: String(page.demoMirror)
                        color: Theme?.textPrimary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                    }
                }
            }

            // ── FmSlider：连续取值 ──
            ColumnLayout {
                objectName: "galleryItem_FmSlider"
                Layout.fillWidth: true
                spacing: Theme?.spacingXs ?? 0

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 0

                    Text {
                        text: "FmSlider"
                        color: Theme?.accent ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        font.bold: true
                    }

                    Text {
                        objectName: "galleryHint_slider"
                        Layout.fillWidth: true
                        text: Tr?.map["dev_gallery_hint_slider"] ?? "dev_gallery_hint_slider"
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        elide: Text.ElideRight
                    }
                }

                RowLayout {
                    spacing: Theme?.spacingMd ?? 0

                    FmSlider {
                        objectName: "gallerySlider"
                        Layout.preferredWidth: 320
                        from: 0
                        to: 100
                        stepSize: 1
                        value: page.demoLevel
                        showRange: true
                        onMoved: page.demoLevel = value
                    }

                    Text {
                        objectName: "gallerySliderReadout"
                        Layout.alignment: Qt.AlignVCenter
                        text: String(Math.round(page.demoLevel))
                        color: Theme?.textPrimary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                    }
                }
            }

            // ══ 第三节：容器与数据展示 ══════════════════════════
            Text {
                objectName: "gallerySection_display"
                Layout.fillWidth: true
                Layout.topMargin: Theme?.spacingLg ?? 0
                text: Tr?.map["dev_gallery_sec_display"] ?? "dev_gallery_sec_display"
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeLarge ?? 14
                font.bold: true
            }

            // ── FmCard：标题 + 内容 + 页脚 ──
            ColumnLayout {
                objectName: "galleryItem_FmCard"
                Layout.fillWidth: true
                spacing: Theme?.spacingXs ?? 0

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 0

                    Text {
                        text: "FmCard"
                        color: Theme?.accent ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        font.bold: true
                    }

                    Text {
                        objectName: "galleryHint_card"
                        Layout.fillWidth: true
                        text: Tr?.map["dev_gallery_hint_card"] ?? "dev_gallery_hint_card"
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        elide: Text.ElideRight
                    }
                }

                FmCard {
                    objectName: "galleryCard"
                    Layout.fillWidth: true
                    title: "FmCard"
                    subtitle: Tr?.map["dev_gallery_hint_card"] ?? "dev_gallery_hint_card"

                    Text {
                        text: Tr?.map["dev_gallery_demo_body"] ?? "dev_gallery_demo_body"
                        color: Theme?.textPrimary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        wrapMode: Text.WordWrap
                    }

                    FmListItem {
                        objectName: "galleryCardRow"
                        Layout.fillWidth: true
                        title: "sodium"
                        subtitle: "0.5.8"
                        iconName: "mods"
                        trailingText: "1.2 MB"
                    }

                    footer: [
                        FmButton {
                            objectName: "galleryCardAction"
                            primary: false
                            text: "open_folder"
                            onClicked: page.clickCount += 1
                        }
                    ]
                }
            }

            // ── FmListItem：悬停 / 选中 / 禁用 ──
            ColumnLayout {
                objectName: "galleryItem_FmListItem"
                Layout.fillWidth: true
                spacing: Theme?.spacingXs ?? 0

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 0

                    Text {
                        text: "FmListItem"
                        color: Theme?.accent ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        font.bold: true
                    }

                    Text {
                        objectName: "galleryHint_listitem"
                        Layout.fillWidth: true
                        text: Tr?.map["dev_gallery_hint_listitem"] ?? "dev_gallery_hint_listitem"
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        elide: Text.ElideRight
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 0

                    FmListItem {
                        objectName: "galleryListItem1"
                        Layout.fillWidth: true
                        title: "1.21.4"
                        subtitle: "fabric"
                        iconName: "package"
                        trailingText: "1.2 GB"
                        selected: page.demoRow === 0
                        onClicked: page.demoRow = 0
                    }

                    FmListItem {
                        objectName: "galleryListItem2"
                        Layout.fillWidth: true
                        title: "1.20.1"
                        subtitle: "forge"
                        iconName: "package"
                        trailingText: "980 MB"
                        selected: page.demoRow === 1
                        onClicked: page.demoRow = 1
                    }

                    FmListItem {
                        objectName: "galleryListItemDisabled"
                        Layout.fillWidth: true
                        enabled: false
                        title: "1.19.2"
                        subtitle: "vanilla"
                        iconName: "package"
                        trailingText: "-"
                    }
                }
            }

            // ── FmTable：多列数据 + 行点击 ──
            ColumnLayout {
                objectName: "galleryItem_FmTable"
                Layout.fillWidth: true
                spacing: Theme?.spacingXs ?? 0

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 0

                    Text {
                        text: "FmTable"
                        color: Theme?.accent ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        font.bold: true
                    }

                    Text {
                        objectName: "galleryHint_table"
                        Layout.fillWidth: true
                        text: Tr?.map["dev_gallery_hint_table"] ?? "dev_gallery_hint_table"
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        elide: Text.ElideRight
                    }
                }

                FmTable {
                    objectName: "galleryTable"
                    Layout.fillWidth: true
                    clickable: true
                    columns: [
                        { key: "name", title: "name", width: 220 },
                        { key: "version", title: "version", width: 120 },
                        { key: "size", title: "size", width: 100, align: "right" },
                        { key: "state", title: "state", width: 140 }
                    ]
                    rows: [
                        { name: "sodium", version: "0.5.8", size: "1.2 MB", state: "enabled" },
                        { name: "lithium", version: "0.12.1", size: "640 KB", state: "enabled" },
                        { name: "iris", version: "1.7.0", size: "3.4 MB", state: "disabled" }
                    ]
                    onRowActivated: function (index, row) {
                        page.demoRow = index
                        page.demoKeyword = String(row.name)
                    }
                }

                Text {
                    objectName: "galleryTableReadout"
                    text: page.demoRow + " " + page.demoKeyword
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                }
            }

            // ══ 第四节：进度 ════════════════════════════════════
            Text {
                objectName: "gallerySection_progress"
                Layout.fillWidth: true
                Layout.topMargin: Theme?.spacingLg ?? 0
                text: Tr?.map["dev_gallery_sec_progress"] ?? "dev_gallery_sec_progress"
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeLarge ?? 14
                font.bold: true
            }

            // ── FmProgressBar：确定 / 不确定 ──
            ColumnLayout {
                objectName: "galleryItem_FmProgressBar"
                Layout.fillWidth: true
                spacing: Theme?.spacingXs ?? 0

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 0

                    Text {
                        text: "FmProgressBar"
                        color: Theme?.accent ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        font.bold: true
                    }

                    Text {
                        objectName: "galleryHint_progressbar"
                        Layout.fillWidth: true
                        text: Tr?.map["dev_gallery_hint_progressbar"] ?? "dev_gallery_hint_progressbar"
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        elide: Text.ElideRight
                    }
                }

                FmProgressBar {
                    objectName: "galleryProgressBar"
                    Layout.fillWidth: true
                    Layout.preferredWidth: 420
                    label: "download"
                    value: page.demoFraction
                }

                FmProgressBar {
                    objectName: "galleryProgressBarIndeterminate"
                    Layout.fillWidth: true
                    Layout.preferredWidth: 420
                    label: "scanning"
                    indeterminate: true
                }
            }

            // ── FmProgressRing：确定 / 不确定 ──
            ColumnLayout {
                objectName: "galleryItem_FmProgressRing"
                Layout.fillWidth: true
                spacing: Theme?.spacingXs ?? 0

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 0

                    Text {
                        text: "FmProgressRing"
                        color: Theme?.accent ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        font.bold: true
                    }

                    Text {
                        objectName: "galleryHint_progressring"
                        Layout.fillWidth: true
                        text: Tr?.map["dev_gallery_hint_progressring"] ?? "dev_gallery_hint_progressring"
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        elide: Text.ElideRight
                    }
                }

                RowLayout {
                    spacing: Theme?.spacingMd ?? 0

                    FmProgressRing {
                        objectName: "galleryProgressRing"
                        value: page.demoFraction
                        showPercent: true
                    }

                    FmProgressRing {
                        objectName: "galleryProgressRingIndeterminate"
                        indeterminate: true
                        lineWidth: 3
                    }
                }
            }

            // ══ 第五节：提示与三态 ══════════════════════════════
            Text {
                objectName: "gallerySection_states"
                Layout.fillWidth: true
                Layout.topMargin: Theme?.spacingLg ?? 0
                text: Tr?.map["dev_gallery_sec_states"] ?? "dev_gallery_sec_states"
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeLarge ?? 14
                font.bold: true
            }

            // ── FmInfoBar：四档语义 + 动作 + 关闭 ──
            ColumnLayout {
                objectName: "galleryItem_FmInfoBar"
                Layout.fillWidth: true
                spacing: Theme?.spacingXs ?? 0

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 0

                    Text {
                        text: "FmInfoBar"
                        color: Theme?.accent ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        font.bold: true
                    }

                    Text {
                        objectName: "galleryHint_infobar"
                        Layout.fillWidth: true
                        text: Tr?.map["dev_gallery_hint_infobar"] ?? "dev_gallery_hint_infobar"
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        elide: Text.ElideRight
                    }
                }

                FmInfoBar {
                    objectName: "galleryInfoBarInfo"
                    Layout.fillWidth: true
                    level: "info"
                    text: Tr?.map["dev_gallery_demo_info"] ?? "dev_gallery_demo_info"
                }

                FmInfoBar {
                    objectName: "galleryInfoBarSuccess"
                    Layout.fillWidth: true
                    level: "success"
                    text: Tr?.map["dev_gallery_demo_success"] ?? "dev_gallery_demo_success"
                }

                FmInfoBar {
                    objectName: "galleryInfoBarWarning"
                    Layout.fillWidth: true
                    level: "warning"
                    text: Tr?.map["dev_gallery_demo_warning"] ?? "dev_gallery_demo_warning"
                    actionText: Tr?.map["refresh"] ?? "refresh"
                    closable: true
                    onActionTriggered: page.clickCount += 1
                    onClosed: page.clickCount += 1
                }

                FmInfoBar {
                    objectName: "galleryInfoBarError"
                    Layout.fillWidth: true
                    level: "error"
                    text: Tr?.map["dev_gallery_demo_error"] ?? "dev_gallery_demo_error"
                }
            }

            // ── FmLoadingState ──
            ColumnLayout {
                objectName: "galleryItem_FmLoadingState"
                Layout.fillWidth: true
                spacing: Theme?.spacingXs ?? 0

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 0

                    Text {
                        text: "FmLoadingState"
                        color: Theme?.accent ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        font.bold: true
                    }

                    Text {
                        objectName: "galleryHint_loadingstate"
                        Layout.fillWidth: true
                        text: Tr?.map["dev_gallery_hint_loadingstate"] ?? "dev_gallery_hint_loadingstate"
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        elide: Text.ElideRight
                    }
                }

                FmCard {
                    objectName: "galleryLoadingCard"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 140

                    FmLoadingState {
                        objectName: "galleryLoadingState"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        iconName: "loading"
                        title: Tr?.map["plugin_state_loading"] ?? "plugin_state_loading"
                    }
                }
            }

            // ── FmEmptyState ──
            ColumnLayout {
                objectName: "galleryItem_FmEmptyState"
                Layout.fillWidth: true
                spacing: Theme?.spacingXs ?? 0

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 0

                    Text {
                        text: "FmEmptyState"
                        color: Theme?.accent ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        font.bold: true
                    }

                    Text {
                        objectName: "galleryHint_emptystate"
                        Layout.fillWidth: true
                        text: Tr?.map["dev_gallery_hint_emptystate"] ?? "dev_gallery_hint_emptystate"
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        elide: Text.ElideRight
                    }
                }

                FmCard {
                    objectName: "galleryEmptyCard"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 190

                    FmEmptyState {
                        objectName: "galleryEmptyState"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        iconName: "folder-open"
                        title: Tr?.map["mod_browser_no_results"] ?? "mod_browser_no_results"
                        actionText: Tr?.map["install_new_version"] ?? "install_new_version"
                        onActionTriggered: page.clickCount += 1
                    }
                }
            }

            // ── FmErrorState ──
            ColumnLayout {
                objectName: "galleryItem_FmErrorState"
                Layout.fillWidth: true
                Layout.bottomMargin: Theme?.spacingLg ?? 0
                spacing: Theme?.spacingXs ?? 0

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 0

                    Text {
                        text: "FmErrorState"
                        color: Theme?.accent ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        font.bold: true
                    }

                    Text {
                        objectName: "galleryHint_errorstate"
                        Layout.fillWidth: true
                        text: Tr?.map["dev_gallery_hint_errorstate"] ?? "dev_gallery_hint_errorstate"
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        elide: Text.ElideRight
                    }
                }

                FmCard {
                    objectName: "galleryErrorCard"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 220

                    FmErrorState {
                        objectName: "galleryErrorState"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        iconName: "error"
                        title: Tr?.map["plugin_state_error"] ?? "plugin_state_error"
                        detailText: "TimeoutError: read timed out (attempt 3)"
                        retryText: Tr?.map["refresh"] ?? "refresh"
                        onRetried: page.demoRetries += 1
                    }
                }

                Text {
                    objectName: "galleryRetryReadout"
                    text: String(page.demoRetries)
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                }
            }

            // ── FmPage：页面骨架（返工 C 组新增）—— 页头 + 三态区都在里面 ──
            ColumnLayout {
                objectName: "galleryItem_FmPage"
                Layout.fillWidth: true
                spacing: Theme?.spacingXs ?? 0

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 0

                    Text {
                        text: "FmPage"
                        color: Theme?.accent ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        font.bold: true
                    }

                    Text {
                        objectName: "galleryHint_page"
                        Layout.fillWidth: true
                        text: Tr?.map["dev_gallery_hint_page"] ?? "dev_gallery_hint_page"
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        elide: Text.ElideRight
                    }
                }

                // 嵌套一个**真的**页面骨架：它自带的三个演示按钮能切三态（探针点它们），
                // 下面那行读数直接读它的 `contentState`。12 个领域页现在就是这么写的。
                FmPage {
                    id: galleryNestedPage
                    objectName: "galleryNestedPage"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 300
                    iconName: "guide"
                    title: "FmPage"
                    description: Tr?.map["dev_gallery_hint_page"] ?? "dev_gallery_hint_page"
                    contentState: "empty"
                    demoControlsVisible: true
                }

                Text {
                    objectName: "galleryPageReadout"
                    text: "contentState: " + galleryNestedPage.contentState
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                }
            }

            // ── FmCheckBox：勾选 / 半选 / 禁用（返工 C 组新增） ──
            ColumnLayout {
                objectName: "galleryItem_FmCheckBox"
                Layout.fillWidth: true
                spacing: Theme?.spacingXs ?? 0

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 0

                    Text {
                        text: "FmCheckBox"
                        color: Theme?.accent ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        font.bold: true
                    }

                    Text {
                        objectName: "galleryHint_checkbox"
                        Layout.fillWidth: true
                        text: Tr?.map["dev_gallery_hint_checkbox"] ?? "dev_gallery_hint_checkbox"
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        elide: Text.ElideRight
                    }
                }

                RowLayout {
                    spacing: Theme?.spacingMd ?? 0

                    FmCheckBox {
                        objectName: "galleryCheckBoxLive"
                        text: "live"
                        checked: page.demoAgreed
                        onToggled: page.demoAgreed = checked
                    }

                    FmCheckBox {
                        objectName: "galleryCheckBoxChecked"
                        text: "checked"
                        checked: true
                    }

                    FmCheckBox {
                        objectName: "galleryCheckBoxPartial"
                        text: "partial"
                        tristate: true
                        checkState: Qt.PartiallyChecked
                    }

                    FmCheckBox {
                        objectName: "galleryCheckBoxDisabled"
                        text: "disabled"
                        checked: true
                        enabled: false
                    }

                    Text {
                        objectName: "galleryCheckBoxReadout"
                        text: String(page.demoAgreed)
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        Layout.alignment: Qt.AlignVCenter
                    }
                }
            }

            // ── FmSpinBox：数值步进 + 可编辑 / 禁用（返工 C 组新增） ──
            ColumnLayout {
                objectName: "galleryItem_FmSpinBox"
                Layout.fillWidth: true
                spacing: Theme?.spacingXs ?? 0

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 0

                    Text {
                        text: "FmSpinBox"
                        color: Theme?.accent ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        font.bold: true
                    }

                    Text {
                        objectName: "galleryHint_spinbox"
                        Layout.fillWidth: true
                        text: Tr?.map["dev_gallery_hint_spinbox"] ?? "dev_gallery_hint_spinbox"
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        elide: Text.ElideRight
                    }
                }

                RowLayout {
                    spacing: Theme?.spacingMd ?? 0

                    FmSpinBox {
                        objectName: "gallerySpinBox"
                        from: 1
                        to: 64
                        stepSize: 1
                        value: page.demoThreads
                        editable: true
                        onValueModified: page.demoThreads = value
                    }

                    FmSpinBox {
                        objectName: "gallerySpinBoxDisabled"
                        from: 0
                        to: 100
                        value: 40
                        enabled: false
                    }

                    Text {
                        objectName: "gallerySpinBoxReadout"
                        text: String(page.demoThreads)
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        Layout.alignment: Qt.AlignVCenter
                    }
                }
            }

            // ── FmScrollView：整块内容比可视区高（返工 C 组新增） ──
            ColumnLayout {
                objectName: "galleryItem_FmScrollView"
                Layout.fillWidth: true
                spacing: Theme?.spacingXs ?? 0

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 0

                    Text {
                        text: "FmScrollView"
                        color: Theme?.accent ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        font.bold: true
                    }

                    Text {
                        objectName: "galleryHint_scrollview"
                        Layout.fillWidth: true
                        text: Tr?.map["dev_gallery_hint_scrollview"] ?? "dev_gallery_hint_scrollview"
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        elide: Text.ElideRight
                    }
                }

                FmScrollView {
                    id: galleryScrollerDemo
                    objectName: "galleryScrollView"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 150
                    contentWidth: availableWidth

                    Column {
                        width: galleryScrollerDemo.availableWidth
                        spacing: Theme?.spacingXs ?? 0

                        Repeater {
                            model: 24

                            Text {
                                width: parent.width
                                text: "scroll row " + index
                                color: Theme?.textSecondary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 10
                            }
                        }
                    }
                }
            }

            // ── FmScrollBar：贴在 ListView 上的滚动条（返工 C 组新增） ──
            ColumnLayout {
                objectName: "galleryItem_FmScrollBar"
                Layout.fillWidth: true
                spacing: Theme?.spacingXs ?? 0

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 0

                    Text {
                        text: "FmScrollBar"
                        color: Theme?.accent ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        font.bold: true
                    }

                    Text {
                        objectName: "galleryHint_scrollbar"
                        Layout.fillWidth: true
                        text: Tr?.map["dev_gallery_hint_scrollbar"] ?? "dev_gallery_hint_scrollbar"
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                        elide: Text.ElideRight
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 150
                    color: Theme?.cardBg ?? "transparent"
                    border.width: 1
                    border.color: Theme?.cardBorder ?? "transparent"
                    radius: Theme?.radiusMd ?? 0

                    // `ScrollBar.vertical` 是 Qt 的附着属性名（改不了）——
                    // 它需要 QtQuick.Controls 在作用域里，所以本页保留了那个 import；
                    // 闸门 R9 判的是控件的**声明**，附着属性名不算（见 COMPONENTS.md 第五.4）。
                    ListView {
                        id: galleryScrollBarList
                        objectName: "galleryScrollBarList"
                        anchors.fill: parent
                        anchors.margins: 2
                        clip: true
                        model: 40
                        spacing: 0
                        ScrollBar.vertical: FmScrollBar {
                            objectName: "galleryScrollBar"
                            alwaysVisible: true
                        }

                        delegate: Text {
                            width: galleryScrollBarList.width
                            height: 22
                            text: "line " + index
                            color: Theme?.textSecondary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                            verticalAlignment: Text.AlignVCenter
                        }
                    }
                }
            }
        }
    }
}
