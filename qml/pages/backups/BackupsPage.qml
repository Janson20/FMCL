// BackupsPage.qml —— 存档备份（阶段 3 任务 3.14）。
//
// 四条路由共用这一页：
//   * backups                  -> 世界列表 + 备份区（旧版一比一的主从布局）
//   * backups/world            -> 选中某个世界并聚焦备份区
//   * backups/world/backup     -> 打开某条备份的详情浮层
//   * backups/settings         -> 备份设置（独立视图，独立草稿）
//
// 纪律：颜色只来自 Theme.*（R8）、图标走 FmIcon/FmToolButton（R2）、文案一律
// page.t / page.format（R3/R4）、控件只用 qml/components 的自研件（R9）、
// 所有上下文属性读取先判空。

import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import "../../components"
import "../../components/dialogs"

FmPage {
    id: page
    objectName: "backupsPage"

    readonly property bool live: (typeof Backups !== "undefined" && Backups) ? true : false
    readonly property bool settingsMode: String(page.routeId || "").indexOf("/settings") >= 0

    property string pendingBackup: ""
    property string detailId: ""
    property var detailData: ({})
    property string restoreId: ""
    property string restoreTime: ""
    property string restoreNote: ""
    property string deleteId: ""
    property string deleteTime: ""
    property string deleteSize: ""
    property string deleteNote: ""
    property string exportId: ""

    title: page.settingsMode ? page.t("backup_settings_title") : page.t("backup_manage_title")
    iconName: page.settingsMode ? "settings-gear" : "saves"

    contentState: !(page && page.live) ? "error"
                  : (page.settingsMode ? "ready"
                  : ((page && page.live) && Backups.worldsState === "error" && Backups.worlds.length === 0 ? "error"
                  : ((page && page.live) && Backups.worlds.length === 0
                     && (Backups.worldsState === "idle" || Backups.worldsState === "loading") ? "loading"
                  : "ready")))
    loadingText: page.t("backup_scanning")
    emptyText: page.t("backup_no_worlds")
    errorText: page.t("plugin_state_error")
    errorDetail: (page && page.live) ? String(Backups.worldsError || "") : ""
    retryText: page.t("refresh")

    onRetried: {
        if ((page && page.live))
            Backups.refresh();
    }

    // ─── 文案 / 路由 ───────────────────────────────────────

    function t(key, params) {
        var name = String(key || "");
        if (params !== undefined && params !== null)
            return (typeof Tr !== "undefined" && Tr) ? Tr.tf(name, params) : name;
        return (typeof Tr !== "undefined" && Tr) ? (Tr.map[name] ?? name) : name;
    }

    function format(key, params) {
        return page.t(key, params);
    }

    function routeParam(name) {
        var raw = page.routeParams;
        if (raw === undefined || raw === null)
            return "";
        return String(raw[name] || "");
    }

    function wantedWorld() {
        return page.routeParam("world") || page.routeParam("name");
    }

    function wantedBackup() {
        return page.routeParam("backup") || page.routeParam("id");
    }

    function activateFromRoute() {
        if (!(page && page.live))
            return;
        if (page.settingsMode) {
            page.closeDetail();
            page.restoreId = "";
            page.deleteId = "";
            Backups.openSettings();
            return;
        }
        page.closeDetail();
        Backups.closeSettings();
        page.pendingBackup = page.wantedBackup();
        Backups.activate(page.wantedWorld(), page.pendingBackup);
        page.refreshUi();
    }

    onRouteParamsChanged: Qt.callLater(function () {
        if (page)
            page.activateFromRoute();
    })
    onRouteIdChanged: Qt.callLater(function () {
        if (page)
            page.activateFromRoute();
    })
    onVisibleChanged: {
        if (page.visible)
            Qt.callLater(function () {
                if (page)
                    page.activateFromRoute();
            });
    }
    Component.onCompleted: Qt.callLater(function () {
        if (page) {
            page.refreshUi();
            page.activateFromRoute();
        }
    })

    // ─── 状态刷新 ──────────────────────────────────────────

    function refreshUi() {
        if (!page || !(page && page.live))
            return;
        if (autoLaunchSwitch)
            autoLaunchSwitch.checked = Backups.autoLaunch;
        if (autoExitSwitch)
            autoExitSwitch.checked = Backups.autoExit;
        page.refreshSettings();
        if (page.detailId.length > 0) {
            var current = page.findBackup(page.detailId);
            if (current === null)
                page.closeDetail();
            else
                page.detailData = current;
        }
        if (page.pendingBackup.length > 0) {
            var wanted = page.findBackup(page.pendingBackup);
            if (wanted !== null) {
                page.pendingBackup = "";
                page.openDetail(wanted);
            }
        }
    }

    function findBackup(entryId) {
        var rows = (page && page.live) ? Backups.backups : [];
        for (var i = 0; i < rows.length; ++i) {
            if (String(rows[i].id || "") === String(entryId || ""))
                return rows[i];
        }
        return null;
    }

    function refreshSettings() {
        if (!page || !(page && page.live))
            return;
        var data = Backups.settings || ({});
        if (settingsPathField && String(settingsPathField.text || "") !== String(data.path || ""))
            settingsPathField.text = String(data.path || "");
        if (settingsCompressCombo)
            settingsCompressCombo.currentIndex = page.indexOfValue(settingsCompressCombo.model, data.compressLevel);
        if (settingsMaxCombo)
            settingsMaxCombo.currentIndex = page.indexOfValue(settingsMaxCombo.model, data.maxPerWorld);
        if (settingsRestoreCombo)
            settingsRestoreCombo.currentIndex = page.indexOfValue(settingsRestoreCombo.model, data.restoreMode);
    }

    function indexOfValue(model, value) {
        if (!model)
            return 0;
        for (var i = 0; i < model.length; ++i) {
            if (String(model[i].value) === String(value))
                return i;
        }
        return 0;
    }


    function openDetail(row) {
        if (!row)
            return;
        page.detailId = String(row.id || "");
        page.detailData = row;
    }

    function closeDetail() {
        page.detailId = "";
        page.detailData = ({});
    }

    function requestRestore(row) {
        if (!row)
            return;
        page.restoreId = String(row.id || "");
        page.restoreTime = String(row.fullTime || row.timeText || "");
        page.restoreNote = String(row.noteText || "");
        page.closeDetail();
    }

    function requestDelete(row) {
        if (!row)
            return;
        page.deleteId = String(row.id || "");
        page.deleteTime = String(row.fullTime || row.timeText || "");
        page.deleteSize = String(row.sizeText || "");
        page.deleteNote = String(row.noteText || "");
        page.closeDetail();
    }

    function requestExport(row) {
        if (!row)
            return;
        page.exportId = String(row.id || "");
        page.closeDetail();
        exportDialog.open();
    }

    function localPath(url) {
        var text = String(url || "");
        text = text.replace(/^file:\/{2,3}/, "");
        try {
            return decodeURIComponent(text);
        } catch (e) {
            return text;
        }
    }

    function goBack() {
        if (typeof Nav !== "undefined" && Nav) {
            if (!Nav.goBack())
                Nav.push("backups");
        }
    }

    // ─── 主从布局 ──────────────────────────────────────────

    RowLayout {
        id: mainContent
        objectName: "backupMainContent"
        anchors.fill: parent
        visible: !page.settingsMode
        spacing: 0

        Rectangle {
            objectName: "backupWorldPanel"
            Layout.preferredWidth: 252
            Layout.fillHeight: true
            color: Theme?.bgMedium ?? "transparent"
            radius: Theme?.radiusMd ?? 0

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: Theme?.spacingSm ?? 0
                spacing: Theme?.spacingSm ?? 0

                RowLayout {
                    objectName: "backupWorldHeader"
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 0

                    Text {
                        Layout.fillWidth: true
                        text: page.t("backup_world_list_title")
                        color: Theme?.textPrimary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeBase ?? 12
                        font.bold: true
                        elide: Text.ElideRight
                    }

                    FmToolButton {
                        objectName: "backupRefreshButton"
                        iconName: "refresh"
                        side: 28
                        enabled: (page && page.live) && !Backups.busy
                        onClicked: {
                            if ((page && page.live))
                                Backups.refresh();
                        }
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 1
                    color: Theme?.cardBorder ?? "transparent"
                }

                Text {
                    objectName: "backupNoWorlds"
                    Layout.fillWidth: true
                    Layout.topMargin: Theme?.spacingSm ?? 0
                    visible: (page && page.live) && Backups.worldsState === "empty"
                    horizontalAlignment: Text.AlignHCenter
                    text: page.t("backup_no_worlds")
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                    wrapMode: Text.WordWrap
                }

                ListView {
                    id: worldList
                    objectName: "backupWorldList"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 1
                    model: (page && page.live) ? Backups.worlds : []
                    ScrollBar.vertical: FmScrollBar {}

                    delegate: FmListItem {
                        required property var modelData
                        width: worldList.width
                        title: String(modelData.name || "")
                        iconName: String(modelData.iconName || "world")
                        selected: (page && page.live) && String(Backups.selectedWorld) === String(modelData.name || "")
                        clickable: (page && page.live) && !Backups.busy
                        onClicked: {
                            if ((page && page.live)) {
                                page.pendingBackup = "";
                                Backups.selectWorld(String(modelData.name || ""));
                            }
                        }
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 1
                    color: Theme?.cardBorder ?? "transparent"
                }

                Text {
                    Layout.fillWidth: true
                    text: page.t("backup_auto_section")
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                    font.bold: true
                }

                FmSwitch {
                    id: autoLaunchSwitch
                    objectName: "backupAutoLaunch"
                    Layout.fillWidth: true
                    text: page.t("backup_auto_launch_label")
                    onToggled: {
                        if ((page && page.live) && checked !== Backups.autoLaunch)
                            Backups.setAutoLaunch(checked);
                    }
                }

                FmSwitch {
                    id: autoExitSwitch
                    objectName: "backupAutoExit"
                    Layout.fillWidth: true
                    text: page.t("backup_auto_exit_label")
                    onToggled: {
                        if ((page && page.live) && checked !== Backups.autoExit)
                            Backups.setAutoExit(checked);
                    }
                }
            }
        }

        Rectangle {
            objectName: "backupListPanel"
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: "transparent"

            ColumnLayout {
                anchors.fill: parent
                anchors.leftMargin: Theme?.spacingLg ?? 0
                anchors.rightMargin: Theme?.spacingSm ?? 0
                spacing: Theme?.spacingSm ?? 0

                RowLayout {
                    objectName: "backupToolbar"
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 0

                    Text {
                        Layout.fillWidth: true
                        text: page.t("backup_list")
                        color: Theme?.textPrimary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeLarge ?? 14
                        font.bold: true
                        elide: Text.ElideRight
                    }

                    Text {
                        visible: (page && page.live) && Backups.hasSelection
                        text: (page && page.live) ? page.format("backup_backup_count", {"count": Backups.backupCount}) : ""
                        color: Theme?.textSecondary ?? "transparent"
                        font.pixelSize: Theme?.fontSizeSmall ?? 10
                    }

                    FmButton {
                        objectName: "backupFolderButton"
                        primary: false
                        iconName: "folder-open"
                        text: page.t("backup_open_folder_btn")
                        enabled: (page && page.live) && !Backups.busy
                        onClicked: {
                            if ((page && page.live))
                                Backups.openFolder();
                        }
                    }

                    FmButton {
                        objectName: "backupSettingsButton"
                        primary: false
                        iconName: "settings-gear"
                        text: page.t("backup_settings_btn")
                        enabled: (page && page.live) && !Backups.busy
                        onClicked: {
                            if ((page && page.live) && typeof Nav !== "undefined" && Nav)
                                Nav.push("backups/settings");
                        }
                    }
                }

                RowLayout {
                    objectName: "backupActionRow"
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 0

                    FmTextField {
                        id: noteField
                        objectName: "backupNote"
                        Layout.fillWidth: true
                        placeholder: page.t("backup_note_hint")
                        enabled: (page && page.live) && !Backups.busy
                    }

                    FmButton {
                        objectName: "backupNowButton"
                        primary: true
                        iconName: "save"
                        text: page.t("backup_now")
                        enabled: (page && page.live) && Backups.hasSelection && !Backups.busy
                        onClicked: {
                            if ((page && page.live) && Backups.hasSelection && !Backups.busy) {
                                var note = String(noteField.text || "").trim();
                                Backups.createBackup(note);
                                noteField.text = "";
                            }
                        }
                    }
                }

                FmProgressBar {
                    objectName: "backupProgress"
                    Layout.fillWidth: true
                    visible: (page && page.live) && Backups.progressVisible
                    value: (page && page.live) ? Number(Backups.progress.fraction || 0) : 0
                    indeterminate: (page && page.live) ? Boolean(Backups.progress.indeterminate) : false
                    label: (page && page.live) ? String(Backups.progress.message || "") : ""
                    showPercent: !((page && page.live) && Boolean(Backups.progress.indeterminate))
                }

                Rectangle {
                    objectName: "backupHeader"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 30
                    color: Theme?.bgMedium ?? "transparent"
                    radius: Theme?.radiusSm ?? 0

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: Theme?.spacingSm ?? 0
                        anchors.rightMargin: Theme?.spacingSm ?? 0
                        spacing: Theme?.spacingSm ?? 0

                        Text {
                            Layout.preferredWidth: 96
                            text: page.t("backup_header_time")
                            color: Theme?.textSecondary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                            font.bold: true
                            elide: Text.ElideRight
                        }
                        Text {
                            Layout.preferredWidth: 70
                            text: page.t("backup_header_size")
                            color: Theme?.textSecondary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                            font.bold: true
                            elide: Text.ElideRight
                        }
                        Text {
                            Layout.preferredWidth: 108
                            text: page.t("backup_header_version")
                            color: Theme?.textSecondary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                            font.bold: true
                            elide: Text.ElideRight
                        }
                        Text {
                            Layout.fillWidth: true
                            text: page.t("backup_header_note")
                            color: Theme?.textSecondary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                            font.bold: true
                            elide: Text.ElideRight
                        }
                        Text {
                            Layout.preferredWidth: 148
                            horizontalAlignment: Text.AlignRight
                            text: page.t("backup_header_actions")
                            color: Theme?.textSecondary ?? "transparent"
                            font.pixelSize: Theme?.fontSizeSmall ?? 10
                            font.bold: true
                            elide: Text.ElideRight
                        }
                    }
                }

                ListView {
                    id: backupList
                    objectName: "backupList"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 1
                    visible: (page && page.live) && Backups.hasSelection && Backups.backups.length > 0
                    model: (page && page.live) ? Backups.backups : []
                    ScrollBar.vertical: FmScrollBar {}

                    delegate: Rectangle {
                        id: backupRow
                        required property var modelData
                        required property int index
                        width: backupList.width
                        height: 40
                        radius: Theme?.radiusSm ?? 0
                        color: index % 2 === 0 ? "transparent" : (Theme?.bgMedium ?? "transparent")

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: Theme?.spacingSm ?? 0
                            anchors.rightMargin: Theme?.spacingSm ?? 0
                            spacing: Theme?.spacingSm ?? 0

                            Text {
                                Layout.preferredWidth: 96
                                text: String(backupRow.modelData.timeText || "")
                                color: Theme?.textPrimary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 10
                                elide: Text.ElideRight
                            }
                            Text {
                                Layout.preferredWidth: 70
                                text: String(backupRow.modelData.sizeText || "")
                                color: Theme?.textSecondary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 10
                                elide: Text.ElideRight
                            }
                            Text {
                                Layout.preferredWidth: 108
                                text: String(backupRow.modelData.versionText || "-")
                                color: Theme?.textSecondary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 10
                                elide: Text.ElideRight
                            }
                            Text {
                                Layout.fillWidth: true
                                text: String(backupRow.modelData.noteText || "")
                                color: Theme?.textPrimary ?? "transparent"
                                font.pixelSize: Theme?.fontSizeSmall ?? 10
                                elide: Text.ElideRight
                            }
                            RowLayout {
                                Layout.preferredWidth: 148
                                Layout.alignment: Qt.AlignRight
                                spacing: 0

                                FmToolButton {
                                    iconName: "info"
                                    side: 28
                                    iconSize: 16
                                    enabled: (page && page.live) && !Backups.busy
                                    onClicked: page.openDetail(backupRow.modelData)
                                }
                                FmToolButton {
                                    iconName: "history"
                                    side: 28
                                    iconSize: 16
                                    enabled: (page && page.live) && !Backups.busy
                                    onClicked: page.requestRestore(backupRow.modelData)
                                }
                                FmToolButton {
                                    iconName: "upload"
                                    side: 28
                                    iconSize: 16
                                    enabled: (page && page.live) && !Backups.busy
                                    onClicked: page.requestExport(backupRow.modelData)
                                }
                                FmToolButton {
                                    iconName: "check"
                                    side: 28
                                    iconSize: 16
                                    enabled: (page && page.live) && !Backups.busy
                                    onClicked: {
                                        if ((page && page.live))
                                            Backups.verifyBackup(String(backupRow.modelData.id || ""));
                                    }
                                }
                                FmToolButton {
                                    iconName: "trash"
                                    side: 28
                                    iconSize: 16
                                    enabled: (page && page.live) && !Backups.busy
                                    onClicked: page.requestDelete(backupRow.modelData)
                                }
                            }
                        }
                    }
                }

                Text {
                    objectName: "backupEmptyHint"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    visible: (page && page.live) && (!Backups.hasSelection || Backups.backups.length === 0)
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    text: !(page && page.live) ? ""
                          : (!Backups.hasSelection ? page.t("backup_select_world_first")
                          : (Backups.backupsState === "error" ? String(Backups.backupsError || "")
                          : page.format("backup_no_backups_hint", {"world": Backups.selectedWorld})))
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                    wrapMode: Text.WordWrap
                }
            }
        }
    }

    // ─── 备份设置 ──────────────────────────────────────────

    FmScrollView {
        id: settingsScroll
        objectName: "backupSettingsView"
        anchors.fill: parent
        visible: page.settingsMode
        contentWidth: availableWidth

        ColumnLayout {
            id: settingsColumn
            width: Math.max(320, Math.min(680, settingsScroll.availableWidth - 2 * (Theme?.spacingLg ?? 0)))
            x: Math.max(0, (settingsScroll.availableWidth - width) / 2)
            spacing: Theme?.spacingMd ?? 0

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme?.spacingSm ?? 0

                Item { Layout.fillWidth: true }

                FmTag {
                    objectName: "backupSettingsDirty"
                    visible: (page && page.live) && Backups.settingsDirty
                    text: page.t("settings_unsaved_tag")
                    level: "warning"
                }
            }

            Text {
                Layout.fillWidth: true
                text: page.t("backup_settings_path_label")
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeBase ?? 12
                font.bold: true
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme?.spacingSm ?? 0

                FmTextField {
                    id: settingsPathField
                    objectName: "backupSettingsPath"
                    Layout.fillWidth: true
                    placeholder: page.t("backup_dir")
                    onEdited: {
                        if ((page && page.live))
                            Backups.setSettingsField("path", value);
                    }
                }

                FmButton {
                    objectName: "backupSettingsBrowse"
                    primary: false
                    iconName: "folder-open"
                    text: page.t("backup_settings_browse")
                    onClicked: settingsFolderDialog.open()
                }

                FmButton {
                    objectName: "backupSettingsReset"
                    primary: false
                    iconName: "refresh"
                    text: page.t("backup_settings_reset")
                    onClicked: {
                        if ((page && page.live))
                            Backups.resetSettingsPath();
                    }
                }
            }

            Text {
                Layout.fillWidth: true
                text: page.t("backup_settings_compress_label")
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeBase ?? 12
                font.bold: true
            }

            Text {
                Layout.fillWidth: true
                text: page.t("backup_settings_compress_desc")
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                wrapMode: Text.WordWrap
            }

            FmComboBox {
                id: settingsCompressCombo
                objectName: "backupSettingsCompress"
                Layout.fillWidth: true
                model: (page && page.live) ? Backups.compressOptions : []
                textRole: "label"
                valueRole: "value"
                onActivated: {
                    if ((page && page.live) && Backups.compressOptions.length > currentIndex)
                        Backups.setSettingsField("compressLevel", Backups.compressOptions[currentIndex].value);
                }
            }

            Text {
                Layout.fillWidth: true
                text: page.t("backup_settings_max_label")
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeBase ?? 12
                font.bold: true
            }

            Text {
                Layout.fillWidth: true
                text: page.t("backup_settings_max_desc")
                color: Theme?.textSecondary ?? "transparent"
                font.pixelSize: Theme?.fontSizeSmall ?? 10
                wrapMode: Text.WordWrap
            }

            FmComboBox {
                id: settingsMaxCombo
                objectName: "backupSettingsMax"
                Layout.fillWidth: true
                model: (page && page.live) ? Backups.maxOptions : []
                textRole: "label"
                valueRole: "value"
                onActivated: {
                    if ((page && page.live) && Backups.maxOptions.length > currentIndex)
                        Backups.setSettingsField("maxPerWorld", Backups.maxOptions[currentIndex].value);
                }
            }

            Text {
                Layout.fillWidth: true
                text: page.t("backup_settings_restore_label")
                color: Theme?.textPrimary ?? "transparent"
                font.pixelSize: Theme?.fontSizeBase ?? 12
                font.bold: true
            }

            FmComboBox {
                id: settingsRestoreCombo
                objectName: "backupSettingsRestore"
                Layout.fillWidth: true
                model: (page && page.live) ? Backups.restoreOptions : []
                textRole: "label"
                valueRole: "value"
                onActivated: {
                    if ((page && page.live) && Backups.restoreOptions.length > currentIndex)
                        Backups.setSettingsField("restoreMode", Backups.restoreOptions[currentIndex].value);
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: Theme?.spacingSm ?? 0
                spacing: Theme?.spacingSm ?? 0

                Item { Layout.fillWidth: true }

                FmButton {
                    objectName: "backupSettingsCancel"
                    primary: false
                    text: page.t("backup_settings_cancel")
                    onClicked: {
                        if ((page && page.live))
                            Backups.closeSettings();
                        page.goBack();
                    }
                }

                FmButton {
                    objectName: "backupSettingsSave"
                    primary: true
                    iconName: "save"
                    text: page.t("backup_settings_save_btn")
                    enabled: (page && page.live)
                    onClicked: {
                        if ((page && page.live))
                            Backups.saveSettings();
                    }
                }
            }
        }
    }

    // ─── 备份详情浮层 ──────────────────────────────────────

    Loader {
        id: detailLoader
        objectName: "backupDetailLoader"
        anchors.fill: parent
        active: page.detailId.length > 0
        sourceComponent: detailComponent
    }

    Component {
        id: detailComponent

        Item {
            anchors.fill: parent
            focus: true

            Rectangle {
                anchors.fill: parent
                color: Theme?.scrim ?? "transparent"
            }

            MouseArea {
                anchors.fill: parent
                acceptedButtons: Qt.AllButtons
                onClicked: page.closeDetail()
            }

            FmCard {
                objectName: "backupDetailDialog"
                anchors.centerIn: parent
                width: Math.min(560, parent.width - 2 * (Theme?.spacingLg ?? 0))
                title: page.t("backup_detail_title")

                Text {
                    Layout.fillWidth: true
                    text: page.t("backup_header_time") + ": " + String(page.detailData.fullTime || page.detailData.timeText || "")
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                    wrapMode: Text.WordWrap
                }
                Text {
                    Layout.fillWidth: true
                    text: page.t("backup_header_size") + ": " + String(page.detailData.sizeText || "")
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                }
                Text {
                    Layout.fillWidth: true
                    text: page.t("backup_header_version") + ": " + String(page.detailData.versionText || "-")
                    color: Theme?.textSecondary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                }
                Text {
                    Layout.fillWidth: true
                    text: page.t("backup_header_note") + ": " + String(page.detailData.noteText || "")
                    color: Theme?.textPrimary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeBase ?? 12
                    wrapMode: Text.WordWrap
                }
                Text {
                    Layout.fillWidth: true
                    text: String(page.detailData.fileName || "")
                    color: Theme?.textTertiary ?? "transparent"
                    font.pixelSize: Theme?.fontSizeSmall ?? 10
                    elide: Text.ElideMiddle
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme?.spacingSm ?? 0
                    Item { Layout.fillWidth: true }
                    FmButton {
                        primary: false
                        text: page.t("close")
                        onClicked: page.closeDetail()
                    }
                    FmButton {
                        primary: false
                        iconName: "upload"
                        text: page.t("backup_export")
                        enabled: (page && page.live) && !Backups.busy
                        onClicked: page.requestExport(page.detailData)
                    }
                    FmButton {
                        primary: false
                        danger: true
                        iconName: "trash"
                        text: page.t("backup_delete")
                        enabled: (page && page.live) && !Backups.busy
                        onClicked: page.requestDelete(page.detailData)
                    }
                    FmButton {
                        primary: true
                        iconName: "history"
                        text: page.t("backup_restore")
                        enabled: (page && page.live) && !Backups.busy
                        onClicked: page.requestRestore(page.detailData)
                    }
                }
            }
        }
    }

    // ─── 恢复确认 ──────────────────────────────────────────

    Loader {
        id: restoreLoader
        objectName: "backupRestoreLoader"
        anchors.fill: parent
        active: page.restoreId.length > 0
        sourceComponent: restoreComponent
    }

    Component {
        id: restoreComponent

        ConfirmDialog {
            objectName: "backupRestoreDialog"
            modalScrim: true
            request: ({
                "title": page.t("backup_restore_confirm_title"),
                "message": page.format("backup_restore_confirm_msg", {
                    "world": (page && page.live) ? Backups.selectedWorld : "",
                    "time": page.restoreTime,
                    "note": page.restoreNote
                }),
                "default": false
            })
            onAnswered: function (value) {
                var entryId = page.restoreId;
                page.restoreId = "";
                if (value === true && (page && page.live) && entryId.length > 0)
                    Backups.restoreBackup(entryId);
            }
        }
    }

    // ─── 删除确认 ──────────────────────────────────────────

    Loader {
        id: deleteLoader
        objectName: "backupDeleteLoader"
        anchors.fill: parent
        active: page.deleteId.length > 0
        sourceComponent: deleteComponent
    }

    Component {
        id: deleteComponent

        ConfirmDialog {
            objectName: "backupDeleteDialog"
            modalScrim: true
            request: ({
                "title": page.t("backup_delete_confirm_title"),
                "message": page.format("backup_delete_confirm_msg", {
                    "time": page.deleteTime,
                    "size": page.deleteSize,
                    "note": page.deleteNote
                }),
                "default": false
            })
            onAnswered: function (value) {
                var entryId = page.deleteId;
                page.deleteId = "";
                if (value === true && (page && page.live) && entryId.length > 0)
                    Backups.deleteBackup(entryId);
            }
        }
    }

    // ─── 文件 / 目录选择 ───────────────────────────────────

    FileDialog {
        id: exportDialog
        objectName: "backupExportDialog"
        title: page.t("backup_export_title")
        fileMode: FileDialog.SaveFile
        defaultSuffix: "zip"
        nameFilters: [(page && page.live) ? Backups.zipFilter : "*.zip"]
        onAccepted: {
            var entryId = page.exportId;
            page.exportId = "";
            if ((page && page.live) && entryId.length > 0)
                Backups.exportBackup(entryId, page.localPath(selectedFile));
        }
        onRejected: page.exportId = ""
    }

    FolderDialog {
        id: settingsFolderDialog
        objectName: "backupSettingsFolderDialog"
        title: page.t("backup_settings_select_dir")
        onAccepted: {
            if ((page && page.live))
                Backups.takeSettingsDirectory(selectedFolder);
        }
    }

    // ─── 桥 → 页面 ─────────────────────────────────────────

    Connections {
        target: (page && page.live) ? Backups : null
        ignoreUnknownSignals: true

        function onViewChanged() {
            if (page)
                page.refreshUi();
        }

        function onSettingsChanged() {
            if (page)
                page.refreshSettings();
        }

        function onStatusMessage(text, level) {
            if (typeof Shell !== "undefined" && Shell)
                Shell.setStatus(String(text), String(level));
        }

        function onSettingsSaved() {
            if (page)
                page.goBack();
        }
    }
}
