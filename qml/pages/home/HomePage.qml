// HomePage.qml —— 首页（阶段 3 任务 3.1；IA 见 `docs/refactor/02` 第 4.1 节）。
//
// 这一页承载什么（03 的 3.1 摘要 + 对照表 A/B 组）：
//   * 当前账号卡片（B-16）+ 账号管理入口；
//   * 自定义皮肤：预览 / 选择 / 移除（B-17）；
//   * 最近使用版本 + 一键启动、游戏进程状态 + 启动/强杀（B-03 / B-04）；
//   * 公告入口（A-22）、每日签到与成就摘要（F-09、3.1 摘要里的"签到、成就同步"）。
//
// 数据全部来自 `Home` 桥（`app/bridges/home_bridge.py`）——本文件**不碰任何服务**，
// 也不做业务判断：皮肤尺寸校验在 `AccountService`、退出码归因在 `GameService`。
// 这里只做三件事：绑属性、发槽调用、按状态显示。
//
// 三态（03 的 SOP 第 5 条）：
//   * `loading` —— 桥还没取过数据（`Home.ready` 为假）；
//   * `empty`   —— 首启：没账号、没皮肤、没启动过版本、成就也是 0；
//   * `error`   —— 桥整个缺席（注册失败）；
//   * 其余为 `ready`。卡片内部还有自己的"这一块没数据"提示（例如没启动过任何版本）。
//
// 纪律：颜色只来自 `Theme.*`（R8）、图标走 `FmIcon`（R2）、文案走 `Tr.map[…]`（R3/R4）、
// 只用 `qml/components` 里的 `Fm*`（R9）；`FileDialog` 是 QtQuick.Dialogs 的**文件选择器**
// （不是 R9 名单里的视觉控件，名单里的是 `Dialog`），皮肤路径拿本地文件路径后再交给桥。

import QtQuick
import QtQuick.Layouts
import QtQuick.Dialogs
import "../../components"

FmPage {
    id: page
    objectName: "homePage"

    //: 桥缺席（注册失败）时整页走错误态，而不是绑一堆 `undefined` 报错
    readonly property bool bridgeAvailable: (typeof Home !== "undefined" && Home) ? true : false

    //: 首启判定：四块内容全空才算"空数据"
    readonly property bool firstRun: bridgeAvailable
                                      && !Home.hasAccount
                                      && !Home.hasRecentVersion
                                      && !Home.hasSkin
                                      && Home.achievementTotal === 0

    contentState: !page.bridgeAvailable ? "error"
                  : (!Home.ready ? "loading"
                  : (page.firstRun ? "empty" : "ready"))

    loadingText: Tr?.map["plugin_state_loading"] ?? "plugin_state_loading"
    emptyText: Tr?.map["home_no_recent_version"] ?? "home_no_recent_version"
    emptyActionText: Tr?.map["home_open_versions"] ?? "home_open_versions"
    //: 首启时"去管理版本"是这一页唯一的下一步 —— 用主按钮（也是整页的强调色来源）
    emptyActionPrimary: true
    errorText: Tr?.map["plugin_state_error"] ?? "plugin_state_error"
    errorDetail: ""
    retryText: Tr?.map["refresh"] ?? "refresh"

    onEmptyActionTriggered: {
        if (page.bridgeAvailable)
            Home.openVersions()
    }

    onRetried: {
        if (page.bridgeAvailable)
            Home.refresh()
    }

    // 语言切换不影响本页（文案都是 Tr.map 绑定）；页面一出现就取一次数据 ——
    // 桥在 bind() 时也刷过一次，但那会儿 launcher / 账号系统还没就绪（启动流程是异步的）。
    Component.onCompleted: {
        if (page.bridgeAvailable)
            Home.refresh()
    }

    function t(key) {
        return Tr ? (Tr?.map[key] ?? key) : key
    }

    FmScrollView {
        anchors.fill: parent
        clip: true

        ColumnLayout {
            width: parent.width
            spacing: Theme?.spacingMd ?? 10

            // ── 账号卡片（B-16）──────────────────────────────────
            FmCard {
                objectName: "homeAccountCard"
                Layout.fillWidth: true
                title: page.t("account_sidebar_title")

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 8

                    FmIcon {
                        objectName: "homeAccountIcon"
                        name: page.bridgeAvailable && Home.hasAccount ? "account" : "info"
                        color: page.bridgeAvailable && Home.hasAccount
                               ? (Theme?.textPrimary ?? "transparent")
                               : (Theme?.textSecondary ?? "transparent")
                        size: Theme?.iconSize ?? 20
                        Layout.alignment: Qt.AlignVCenter
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: Theme?.spacingXs ?? 4

                        Text {
                            objectName: "homeAccountName"
                            Layout.fillWidth: true
                            text: (page.bridgeAvailable && Home.hasAccount)
                                  ? Home.accountName
                                  : page.t("account_sidebar_none")
                            color: (page.bridgeAvailable && Home.hasAccount)
                                   ? (Theme?.textPrimary ?? "transparent")
                                   : (Theme?.textSecondary ?? "transparent")
                            font.pixelSize: Theme?.fontSizeLarge ?? 14
                            font.bold: true
                            elide: Text.ElideRight
                        }

                        FmTag {
                            objectName: "homeAccountTypeTag"
                            visible: page.bridgeAvailable && Home.hasAccount && Home.accountTypeKey.length > 0
                            text: page.t(Home ? Home.accountTypeKey : "")
                            level: "accent"
                        }

                        Text {
                            objectName: "homeAccountUuid"
                            Layout.fillWidth: true
                            visible: page.bridgeAvailable && Home.hasAccount && Home.accountUuid.length > 0
                            text: (page.bridgeAvailable && Home.hasAccount) ? Home.accountUuid : ""
                            color: Theme?.textSecondary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                            elide: Text.ElideMiddle
                        }
                    }

                    FmButton {
                        objectName: "homeAccountManageButton"
                        text: page.t("account_sidebar_manage")
                        iconName: "account"
                        enabled: page.bridgeAvailable
                        Layout.alignment: Qt.AlignVCenter
                        // 账号管理在设置领域（3.5 落地）；这里只负责跳过去
                        onClicked: {
                            if (typeof Nav !== "undefined" && Nav)
                                Nav.push("settings/account")
                        }
                    }
                }
            }

            // ── 游戏卡片：最近版本 + 进程状态 + 启停（B-03 / B-04）──
            FmCard {
                objectName: "homeGameCard"
                Layout.fillWidth: true
                title: page.t("home_recent_version")

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 8

                    FmIcon {
                        objectName: "homeGameStateIcon"
                        name: page.bridgeAvailable && Home.gameRunning ? "play" : "stop"
                        color: page.bridgeAvailable && Home.gameRunning
                               ? (Theme?.success ?? "transparent")
                               : (Theme?.textSecondary ?? "transparent")
                        size: Theme?.iconSize ?? 20
                        Layout.alignment: Qt.AlignVCenter
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: Theme?.spacingXs ?? 4

                        Text {
                            objectName: "homeRecentVersion"
                            Layout.fillWidth: true
                            text: (page.bridgeAvailable && Home.hasRecentVersion)
                                  ? Home.recentVersion
                                  : page.t("home_no_recent_version")
                            color: (page.bridgeAvailable && Home.hasRecentVersion)
                                   ? (Theme?.textPrimary ?? "transparent")
                                   : (Theme?.textSecondary ?? "transparent")
                            font.pixelSize: Theme?.fontSizeLarge ?? 14
                            font.bold: true
                            elide: Text.ElideRight
                        }

                        Text {
                            objectName: "homeGameStateText"
                            Layout.fillWidth: true
                            text: (page.bridgeAvailable && Home.gameRunning)
                                  ? page.t("home_status_running")
                                  : page.t("home_status_idle")
                            color: (page.bridgeAvailable && Home.gameRunning)
                                   ? (Theme?.success ?? "transparent")
                                   : (Theme?.textSecondary ?? "transparent")
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                        }
                    }
                }

                footer: RowLayout {
                    spacing: Theme?.spacingSm ?? 8

                    FmButton {
                        objectName: "homeOpenVersionsButton"
                        text: page.t("home_open_versions")
                        iconName: "package"
                        onClicked: {
                            if (page.bridgeAvailable)
                                Home.openVersions()
                        }
                    }

                    FmButton {
                        objectName: "homeKillButton"
                        text: page.t("home_kill_game")
                        iconName: "stop"
                        danger: true
                        enabled: page.bridgeAvailable && Home.canKill
                        onClicked: {
                            if (page.bridgeAvailable)
                                Home.killGame()
                        }
                    }

                    FmButton {
                        objectName: "homeLaunchButton"
                        primary: true
                        text: page.t("version_launch_title")
                        iconName: "play"
                        loading: page.bridgeAvailable && Home.gameStarting
                        enabled: page.bridgeAvailable && Home.canLaunch
                        onClicked: {
                            if (page.bridgeAvailable)
                                Home.launch("")
                        }
                    }
                }
            }

            // ── 皮肤卡片（B-17）──────────────────────────────────
            FmCard {
                objectName: "homeSkinCard"
                Layout.fillWidth: true
                title: page.t("home_skin_title")

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 8

                    FmIcon {
                        objectName: "homeSkinIcon"
                        name: page.bridgeAvailable && Home.hasSkin ? "success" : "unknown"
                        color: page.bridgeAvailable && Home.hasSkin
                               ? (Theme?.success ?? "transparent")
                               : (Theme?.textSecondary ?? "transparent")
                        size: Theme?.iconSize ?? 20
                        Layout.alignment: Qt.AlignVCenter
                    }

                    Text {
                        objectName: "homeSkinName"
                        Layout.fillWidth: true
                        text: (page.bridgeAvailable && Home.hasSkin)
                              ? Home.skinFileName
                              : page.t("skin_no_preview")
                        color: (page.bridgeAvailable && Home.hasSkin)
                               ? (Theme?.textPrimary ?? "transparent")
                               : (Theme?.textSecondary ?? "transparent")
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        elide: Text.ElideMiddle
                    }
                }

                footer: RowLayout {
                    spacing: Theme?.spacingSm ?? 8

                    FmButton {
                        objectName: "homeSkinRemoveButton"
                        text: page.t("home_skin_remove")
                        iconName: "trash"
                        enabled: page.bridgeAvailable && Home.hasSkin
                        onClicked: {
                            if (page.bridgeAvailable)
                                Home.removeSkin()
                        }
                    }

                    FmButton {
                        objectName: "homeSkinSelectButton"
                        primary: true
                        text: page.t("home_skin_select")
                        iconName: "folder-open"
                        onClicked: skinDialog.open()
                    }
                }
            }

            // ── 签到 + 成就摘要（F-09 / 3.1 摘要）────────────────
            FmCard {
                objectName: "homeProgressCard"
                Layout.fillWidth: true
                title: page.t("ach_stats_title")

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 8

                    Text {
                        objectName: "homeCheckinTitle"
                        text: page.t("home_checkin_title")
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        Layout.alignment: Qt.AlignVCenter
                    }

                    Text {
                        objectName: "homeCheckinStreak"
                        //: 天数由 JS 填进 `{days}`；整句文案来自语言文件（R3：QML 里不许有中文）
                        text: page.t("home_checkin_streak").replace("{days}",
                              String(page.bridgeAvailable ? Home.checkinStreak : 0))
                        color: (page.bridgeAvailable && Home.checkinStreak > 0)
                               ? (Theme?.success ?? "transparent")
                               : (Theme?.textSecondary ?? "transparent")
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        font.bold: true
                        Layout.alignment: Qt.AlignVCenter
                    }

                    Item { Layout.fillWidth: true }
                }

                FmProgressBar {
                    objectName: "homeAchievementBar"
                    Layout.fillWidth: true
                    value: (page.bridgeAvailable && Home.achievementTotal > 0)
                           ? (Home.achievementUnlocked / Home.achievementTotal)
                           : 0
                    label: (page.bridgeAvailable && Home.achievementsLoaded)
                           ? page.t("ach_stats_detail")
                             .replace("{unlocked}", String(Home.achievementUnlocked))
                             .replace("{total}", String(Home.achievementTotal))
                           : page.t("plugin_state_loading")
                    showPercent: true
                }

                footer: RowLayout {
                    spacing: Theme?.spacingSm ?? 8

                    FmButton {
                        objectName: "homeNoticeButton"
                        text: page.t("home_notice_view")
                        iconName: "notify"
                        enabled: page.bridgeAvailable && Home.hasNotice
                        onClicked: {
                            if (page.bridgeAvailable)
                                Home.openNotice()
                        }
                    }
                }
            }

            Item { Layout.fillHeight: true }
        }
    }

    // ── 皮肤文件选择器（B-17 的第一步；校验与复制在服务里）──────
    FileDialog {
        id: skinDialog
        objectName: "homeSkinDialog"
        title: page.t("select_skin_title")
        nameFilters: ["*.png"]
        onAccepted: {
            if (page.bridgeAvailable)
                Home.selectSkin(page.localPath(selectedFile))
        }
    }

    //: `file:///D:/a b/c.png` → `D:/a b/c.png`（去掉 scheme 并还原百分号转义）
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
