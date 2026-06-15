import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

// Screen 4 — Result viewer
Rectangle {
    id: root
    color: theme.surfaceBase

    required property var theme
    signal navigate(string screen)

    property var resultData: ({})
    property string filledPyCode: ""
    property string activeTab: "overview"

    property bool isFailed: resultData.status === "failed"

    Component.onCompleted: {
        resultData = JSON.parse(bridge.getSessionResult())
        filledPyCode = bridge.getSessionFilledPy()
    }

    ScrollView {
        anchors.fill: parent
        contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

        Item {
            width: parent.parent.width
            height: Math.max(mainLayout.implicitHeight + 120, parent.parent.height)

            ColumnLayout {
                id: mainLayout
                anchors {
                    top: parent.top; topMargin: theme.sp8
                    left: parent.left; leftMargin: theme.sp10
                    right: parent.right; rightMargin: theme.sp10
                }
                spacing: theme.sp4

                // Banner
                Rectangle {
                    Layout.fillWidth: true
                    height: bannerRow.implicitHeight + theme.sp3 * 2
                    radius: theme.radiusLG
                    color: isFailed ? theme.dangerSoft : theme.successSoft
                    border.color: isFailed ? "#FCA5A5" : "#86EFAC"
                    border.width: 1

                    RowLayout {
                        id: bannerRow
                        anchors { left: parent.left; right: parent.right; top: parent.top; margins: theme.sp4 }
                        spacing: theme.sp3

                        Rectangle {
                            width: 32; height: 32; radius: 16; color: isFailed ? theme.danger : theme.success
                            Text { anchors.centerIn: parent; text: isFailed ? "✕" : "✓"; font.pixelSize: 15; color: "white"; font.weight: Font.Bold }
                        }
                        ColumnLayout {
                            spacing: 1; Layout.fillWidth: true
                            Text { text: isFailed ? "Помилка коду, спробуйте ще раз" : "Документ успішно згенерований"; font.pixelSize: theme.fontSizeLG; font.weight: Font.DemiBold; color: isFailed ? theme.danger : theme.success }
                            Text { text: resultData.session_name || ""; font.pixelSize: theme.fontSizeMD; color: isFailed ? theme.danger : theme.success; opacity: 0.8 }
                            Text { visible: isFailed; text: (resultData.error_stage || "") + ": " + (resultData.error_message || ""); font.pixelSize: theme.fontSizeSM; color: theme.danger; wrapMode: Text.Wrap; Layout.fillWidth: true }
                        }

                        // Download buttons
                        AppButton { visible: !isFailed && resultData.docx_path; theme: root.theme; label: "⬇  DOCX"; variant: "secondary"; onClicked: bridge.downloadFile(resultData.docx_path) }
                        AppButton { visible: !isFailed && resultData.pdf_path; theme: root.theme; label: "⬇  PDF"; variant: "primary"; onClicked: bridge.downloadFile(resultData.pdf_path) }
                    }
                }

                // Warnings (if any)
                Repeater {
                    model: resultData.warnings || []
                    Rectangle {
                        Layout.fillWidth: true
                        height: warnRow.implicitHeight + theme.sp2 * 2
                        radius: theme.radiusMD
                        color: theme.warningSoft
                        border.color: "#FDE68A"; border.width: 1

                        RowLayout {
                            id: warnRow
                            anchors { fill: parent; margins: theme.sp3 }
                            spacing: theme.sp2
                            Text { text: "⚠"; font.pixelSize: 14; color: theme.warning }
                            Text { text: modelData; font.pixelSize: theme.fontSizeMD; color: "#92400E"; Layout.fillWidth: true }
                        }
                    }
                }

                // Tabs
                RowLayout {
                    spacing: 4
                    visible: !isFailed
                    TabChip { theme: root.theme; label: "Огляд";       tabId: "overview"; activeTab: root.activeTab; onActivate: root.activeTab = tabId }
                    TabChip { theme: root.theme; label: "Статистика";  tabId: "stats";    activeTab: root.activeTab; onActivate: root.activeTab = tabId }
                }

                // ── Overview tab ──────────────────────────────────────────────
                Rectangle {
                    visible: activeTab === "overview"
                    Layout.fillWidth: true
                    height: overviewCol.implicitHeight + theme.sp6 * 2
                    radius: theme.radiusLG; color: theme.surface1
                    border.color: theme.borderSubtle; border.width: 1

                    ColumnLayout {
                        id: overviewCol
                        anchors { left: parent.left; right: parent.right; top: parent.top; margins: theme.sp6 }
                        spacing: theme.sp4

                        // Stats grid
                        GridLayout {
                            Layout.fillWidth: true
                            columns: 3; rowSpacing: theme.sp4; columnSpacing: theme.sp4

                            StatCard { theme: root.theme; label: "Тривалість"; value: resultData.duration || "-"; icon: "⏱" }
                            StatCard { theme: root.theme; label: "Зображень"; value: (resultData.image_count || 0).toString(); icon: "🖼" }
                            StatCard { theme: root.theme; label: "Слів"; value: (resultData.word_count || 0).toString(); icon: "📝" }
                            StatCard { theme: root.theme; label: "Input tokens"; value: resultData.input_tokens || "0"; icon: "→" }
                            StatCard { theme: root.theme; label: "Output tokens"; value: resultData.output_tokens || "0"; icon: "←" }
                            StatCard { theme: root.theme; label: "Кеш"; value: resultData.cached_tokens || "0"; icon: "⚡" }
                        }

                        // Word count bar
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: theme.sp2

                            RowLayout {
                                Text { text: "Word count"; font.pixelSize: theme.fontSizeMD; font.weight: Font.Medium; color: theme.textPrimary }
                                Item { Layout.fillWidth: true }
                                Text {
                                    text: (resultData.word_count || 0) + " / " + (resultData.word_count_min || 0) + "–" + (resultData.word_count_max || 0)
                                    font.pixelSize: theme.fontSizeSM; color: theme.textSecondary
                                    font.family: "JetBrains Mono, Consolas, monospace"
                                }
                            }
                            Rectangle {
                                Layout.fillWidth: true; height: 8; radius: 4; color: theme.surface3
                                Rectangle {
                                    height: parent.height; radius: parent.radius
                                    width: parent.width * Math.min(
                                        (resultData.word_count - resultData.word_count_min) /
                                        (resultData.word_count_max - resultData.word_count_min), 1.0)
                                    color: theme.accent
                                    Behavior on width { NumberAnimation { duration: 600; easing.type: Easing.OutCubic } }
                                }
                            }
                        }
                    }
                }


                // ── Stats tab ─────────────────────────────────────────────────
                Rectangle {
                    visible: activeTab === "stats"
                    Layout.fillWidth: true
                    height: statsCol.implicitHeight + theme.sp6 * 2
                    radius: theme.radiusLG; color: theme.surface1
                    border.color: theme.borderSubtle; border.width: 1

                    ColumnLayout {
                        id: statsCol
                        anchors { left: parent.left; right: parent.right; top: parent.top; margins: theme.sp6 }
                        spacing: theme.sp4

                        Text { text: "Деталі виконання"; font.pixelSize: theme.fontSizeXL; font.weight: Font.DemiBold; color: theme.textPrimary }

                        StatsRow { theme: root.theme; label: "Тривалість виконання"; value: resultData.duration || "-" }
                        StatsRow { theme: root.theme; label: "Input tokens (text model)"; value: resultData.input_tokens || "0" }
                        StatsRow { theme: root.theme; label: "Output tokens (text model)"; value: resultData.output_tokens || "0" }
                        StatsRow { theme: root.theme; label: "Cached tokens"; value: resultData.cached_tokens || "0" }
                        StatsRow { theme: root.theme; label: "Модель"; value: resultData.llm_model || "-" }
                        StatsRow { theme: root.theme; label: "Зображень згенеровано"; value: (resultData.image_count || 0).toString() }
                        StatsRow { theme: root.theme; label: "Word count"; value: (resultData.word_count || 0) + " / " + (resultData.word_count_min || 0) + "–" + (resultData.word_count_max || 0) }
                        StatsRow { theme: root.theme; label: "Шаблон"; value: resultData.template || "-" }
                        StatsRow { theme: root.theme; label: "Hardness"; value: resultData.hardness || "-" }
                        StatsRow { theme: root.theme; label: "Image mode"; value: resultData.image_mode || "-" }

                        // Code spoiler
                        Rectangle {
                            id: codeSpoiler
                            Layout.fillWidth: true
                            height: codeSpoilerCol.implicitHeight
                            radius: theme.radiusLG; color: theme.surface1
                            border.color: theme.borderSubtle; border.width: 1
                            clip: true
                            
                            property bool isExpanded: false

                            ColumnLayout {
                                id: codeSpoilerCol
                                anchors { left: parent.left; right: parent.right; top: parent.top }
                                spacing: 0

                                Rectangle {
                                    Layout.fillWidth: true; height: 40
                                    color: theme.surface2; radius: theme.radiusLG

                                    Rectangle { anchors.bottom: parent.bottom; anchors.left: parent.left; anchors.right: parent.right; height: parent.height / 2; color: parent.color; visible: codeSpoiler.isExpanded }

                                    RowLayout {
                                        anchors { fill: parent; margins: theme.sp4 }
                                        Text { text: "Показати вихідний код (filled.py)"; font.pixelSize: theme.fontSizeMD; font.weight: Font.Medium; color: theme.textPrimary; Layout.fillWidth: true }
                                        Text { text: codeSpoiler.isExpanded ? "▲" : "▼"; color: theme.textSecondary }
                                    }

                                    MouseArea {
                                        anchors.fill: parent
                                        onClicked: codeSpoiler.isExpanded = !codeSpoiler.isExpanded
                                    }
                                }

                                Rectangle {
                                    visible: parent.parent.isExpanded
                                    Layout.fillWidth: true; height: codeText.implicitHeight + 32
                                    color: "#1E1E2E"

                                    Text {
                                        id: codeText
                                        anchors { left: parent.left; right: parent.right; top: parent.top; margins: theme.sp4 }
                                        text: root.filledPyCode || "Код відсутній"
                                        font.pixelSize: theme.fontSizeSM
                                        font.family: "JetBrains Mono, Consolas, Courier New, monospace"
                                        color: "#CDD6F4"
                                        wrapMode: Text.Wrap
                                        lineHeight: 1.6
                                    }
                                }
                            }
                        }
                    }
                }

                Item { height: theme.sp8 }
            }
        }
    }
}
