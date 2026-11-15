// FmAchievementRow.qml —— 成就列表行。
//
// 什么时候用它：成就分类页的列表委托，一行展示名称 / 描述 / 阶段进度 / 解锁徽章。
// 什么时候不要用：成就总览的分类入口（用 FmCard）、详情页（在详情页里展开阶段列表）。
//
// 点击整行发 activated()；本件不导航、不读服务。阶段名由"成就名 + 罗马数字"生成，
// 因为定义表里的 stage_names 是中文硬编码，直接显示会破坏四语言一致性。

import QtQuick
import QtQuick.Layouts
import ".."

Rectangle {
    id: row
    objectName: "fmAchievementRow"

    property var achievement: ({})
    signal activated()

    readonly property bool hasData: achievement && achievement.id !== undefined
    readonly property bool unlocked: hasData && achievement.unlocked === true
    readonly property bool maxed: hasData && achievement.maxed === true
    readonly property int maxStage: hasData ? Number(achievement.maxStage || 0) : 0
    readonly property int stage: hasData ? Number(achievement.stage || 0) : 0
    readonly property bool showProgress: maxStage > 1

    implicitHeight: showProgress ? 96 : 76
    radius: Theme?.radiusLg ?? 0
    color: unlocked ? (Theme?.cardBg ?? "transparent") : (Theme?.bgMedium ?? "transparent")
    border.width: 1
    border.color: area.containsMouse ? (Theme?.accent ?? "transparent")
                                     : (Theme?.cardBorder ?? "transparent")

    function t(key) {
        return (typeof Tr !== "undefined" && Tr) ? (Tr.map[String(key)] ?? String(key)) : String(key)
    }

    function nameText() {
        return hasData ? t(achievement.nameKey) : ""
    }

    function descText() {
        return hasData ? t(achievement.descKey) : ""
    }

    function stageText() {
        if (!hasData)
            return ""
        if (maxed)
            return t("ach_maxed")
        if (!unlocked)
            return t("ach_locked")
        if (maxStage <= 1)
            return t("ach_unlocked")
        var numeral = ["", "I", "II", "III", "IV", "V", "VI"][stage] || String(stage)
        return nameText() + " " + numeral
    }

    function progressText() {
        if (!hasData)
            return ""
        if (maxed)
            return t("ach_maxed")
        return String(achievement.current || 0) + "/" + String(achievement.nextThreshold || 0)
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: Theme?.spacingMd ?? 12
        spacing: Theme?.spacingMd ?? 12

        Rectangle {
            Layout.preferredWidth: 40
            Layout.preferredHeight: 40
            radius: width / 2
            color: row.unlocked ? (Theme?.accentSoft ?? "transparent")
                                : (Theme?.bgLight ?? "transparent")
            border.width: 1
            border.color: row.unlocked ? (Theme?.accent ?? "transparent")
                                       : (Theme?.cardBorder ?? "transparent")

            FmIcon {
                anchors.centerIn: parent
                name: row.hasData ? String(row.achievement.icon || "trophy") : "trophy"
                color: row.unlocked ? (Theme?.accent ?? "transparent")
                                    : (Theme?.textSecondary ?? "transparent")
                size: Theme?.iconSize ?? 20
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: Theme?.spacingXs ?? 4

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme?.spacingSm ?? 8

                Text {
                    objectName: "achievementRowName"
                    Layout.fillWidth: true
                    text: row.nameText()
                    color: row.unlocked ? (Theme?.accent ?? "transparent")
                                        : (Theme?.textPrimary ?? "transparent")
                    font.pixelSize: Theme?.fontSizeLarge ?? 14
                    font.bold: true
                    elide: Text.ElideRight
                }

                FmTag {
                    objectName: "achievementRowBadge"
                    visible: row.hasData
                    text: row.stageText()
                    level: row.maxed ? "success" : (row.unlocked ? "accent" : "neutral")
                    iconName: row.maxed ? "check" : (row.unlocked ? "" : "lock")
                }
            }

            Text {
                objectName: "achievementRowDesc"
                Layout.fillWidth: true
                text: row.descText()
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                elide: Text.ElideRight
            }

            FmProgressBar {
                objectName: "achievementRowProgress"
                Layout.fillWidth: true
                visible: row.showProgress
                value: row.hasData ? Number(row.achievement.percent || 0) / 100 : 0
                label: row.progressText()
                showPercent: true
                barHeight: 6
            }
        }

        FmIcon {
            Layout.preferredWidth: Theme?.iconSize ?? 20
            Layout.preferredHeight: Theme?.iconSize ?? 20
            name: "next"
            color: Theme?.textSecondary ?? "transparent"
            size: Theme?.iconSize ?? 20
        }
    }

    MouseArea {
        id: area
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: row.activated()
    }
}
