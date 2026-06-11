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

    Component.onCompleted: {
        resultData = JSON.parse(bridge.getMockResult())
        filledPyCode = bridge.getMockFilledPy()
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

                // Success banner
                Rectangle {
                    Layout.fillWidth: true
                    height: bannerRow.implicitHeight + theme.sp3 * 2
                    radius: theme.radiusLG
                    color: theme.successSoft
                    border.color: "#86EFAC"
                    border.width: 1

                    RowLayout {
                        id: bannerRow
                        anchors { left: parent.left; right: parent.right; top: parent.top; margins: theme.sp4 }
                        spacing: theme.sp3

                        Rectangle {
                            width: 32; height: 32; radius: 16; color: theme.success
                            Text { anchors.centerIn: parent; text: "✓"; font.pixelSize: 15; color: "white"; font.weight: Font.Bold }
                        }
                        ColumnLayout {
                            spacing: 1; Layout.fillWidth: true
                            Text { text: "Документ успішно згенерований"; font.pixelSize: theme.fontSizeLG; font.weight: Font.DemiBold; color: theme.success }
                            Text { text: resultData.session_name || ""; font.pixelSize: theme.fontSizeMD; color: theme.success; opacity: 0.8 }
                        }

                        // Download buttons
                        AppButton { theme: root.theme; label: "⬇  DOCX"; variant: "secondary" }
                        AppButton { theme: root.theme; label: "⬇  PDF"; variant: "primary" }
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
                    TabChip { theme: root.theme; label: "Огляд";       tabId: "overview"; activeTab: root.activeTab; onActivate: root.activeTab = tabId }
                    TabChip { theme: root.theme; label: "filled.py";   tabId: "code";     activeTab: root.activeTab; onActivate: root.activeTab = tabId }
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

                            StatCard { theme: root.theme; label: "Тривалість"; value: "16.4s"; icon: "⏱" }
                            StatCard { theme: root.theme; label: "Зображень"; value: (resultData.image_count || 0).toString(); icon: "🖼" }
                            StatCard { theme: root.theme; label: "Слів"; value: (resultData.word_count || 0).toString(); icon: "📝" }
                            StatCard { theme: root.theme; label: "Input tokens"; value: "4 821"; icon: "→" }
                            StatCard { theme: root.theme; label: "Output tokens"; value: "2 103"; icon: "←" }
                            StatCard { theme: root.theme; label: "Кеш (%)"; value: "67%"; icon: "⚡" }
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

                // ── Code tab ──────────────────────────────────────────────────
                Rectangle {
                    visible: activeTab === "code"
                    Layout.fillWidth: true
                    height: codeHeader.height + codeScroll.height
                    radius: theme.radiusLG; color: theme.surface1
                    border.color: theme.borderSubtle; border.width: 1
                    clip: true

                    ColumnLayout {
                        anchors { left: parent.left; right: parent.right; top: parent.top }
                        spacing: 0

                        Rectangle {
                            id: codeHeader
                            Layout.fillWidth: true; height: 40
                            color: "#1E1E2E"; radius: theme.radiusLG

                            Rectangle {
                                anchors { bottom: parent.bottom; left: parent.left; right: parent.right }
                                height: parent.height / 2; color: parent.color
                            }

                            RowLayout {
                                anchors { fill: parent; margins: theme.sp4 }
                                Text { text: "filled.py"; font.pixelSize: theme.fontSizeMD; font.weight: Font.Medium; color: "#CDD6F4" }
                                Item { Layout.fillWidth: true }
                                Text { text: "Python"; font.pixelSize: theme.fontSizeSM; color: "#6C7086" }
                            }
                        }

                        ScrollView {
                            id: codeScroll
                            Layout.fillWidth: true; height: 380
                            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                            Rectangle {
                                width: parent.width; height: codeText.implicitHeight + 32
                                color: "#1E1E2E"

                                Text {
                                    id: codeText
                                    anchors { left: parent.left; right: parent.right; top: parent.top; margins: theme.sp4 }
                                    text: root.filledPyCode
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

                        StatsRow { theme: root.theme; label: "Тривалість виконання"; value: "16 400 мс" }
                        StatsRow { theme: root.theme; label: "Input tokens (text model)"; value: "4 821" }
                        StatsRow { theme: root.theme; label: "Output tokens (text model)"; value: "2 103" }
                        StatsRow { theme: root.theme; label: "Cached tokens (67%)"; value: "3 200" }
                        StatsRow { theme: root.theme; label: "Зображень згенеровано"; value: "2" }
                        StatsRow { theme: root.theme; label: "Word count"; value: "1 923 / 1700–2500" }
                        StatsRow { theme: root.theme; label: "Шаблон"; value: "lab1 (Вбудований)" }
                        StatsRow { theme: root.theme; label: "Hardness"; value: "university_1" }
                        StatsRow { theme: root.theme; label: "Image mode"; value: "full" }
                    }
                }

                Item { height: theme.sp8 }
            }
        }
    }
}
