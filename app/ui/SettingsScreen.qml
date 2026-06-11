import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

// Screen 7 — Settings
Rectangle {
    id: root
    color: theme.surfaceBase

    required property var theme
    signal navigate(string screen)

    ScrollView {
        anchors.fill: parent
        contentWidth: availableWidth

        Item {
            width: parent.parent.width
            height: mainCol.implicitHeight + 160

            ColumnLayout {
                id: mainCol
                anchors {
                    top: parent.top; topMargin: theme.sp8
                    horizontalCenter: parent.horizontalCenter
                }
                width: Math.min(parent.width - theme.sp10 * 2, 680)
                spacing: theme.sp4

                Text { text: "Налаштування"; font.pixelSize: theme.fontSizeH1; font.weight: Font.DemiBold; color: theme.textPrimary; font.letterSpacing: -0.3 }
                Text { text: "Конфігурація Agent-For-TOM"; font.pixelSize: theme.fontSizeLG; color: theme.textSecondary; Layout.bottomMargin: theme.sp4 }

                // API Keys section
                SettingsSection {
                    theme: root.theme
                    title: "API Ключі"
                    Layout.fillWidth: true

                    contentItem: ColumnLayout {
                        spacing: theme.sp4

                        SettingsField {
                            theme: root.theme
                            label: "Anthropic API Key"
                            placeholder: "sk-ant-..."
                            isPassword: true
                            Layout.fillWidth: true
                        }
                        SettingsField {
                            theme: root.theme
                            label: "HuggingFace API Token"
                            placeholder: "hf_..."
                            isPassword: true
                            Layout.fillWidth: true
                        }
                        SettingsField {
                            theme: root.theme
                            label: "OpenAI API Key (опційно)"
                            placeholder: "sk-..."
                            isPassword: true
                            Layout.fillWidth: true
                        }
                    }
                }

                // Model settings
                SettingsSection {
                    theme: root.theme
                    title: "Модель"
                    Layout.fillWidth: true

                    contentItem: ColumnLayout {
                        spacing: theme.sp4

                        SettingsField {
                            theme: root.theme
                            label: "Text LLM"
                            placeholder: ""
                            fieldType: "combo"
                            options: ["claude-sonnet-4-5", "claude-opus-4", "gpt-4o", "gpt-4-turbo"]
                            Layout.fillWidth: true
                        }
                        SettingsField {
                            theme: root.theme
                            label: "Image model"
                            placeholder: ""
                            fieldType: "combo"
                            options: ["FLUX.1-schnell (HF)", "DALL-E 3 (OpenAI)", "matplotlib only"]
                            Layout.fillWidth: true
                        }
                        SettingsField {
                            theme: root.theme
                            label: "Max context tokens"
                            placeholder: "200000"
                            Layout.fillWidth: true
                        }
                    }
                }

                // Storage settings
                SettingsSection {
                    theme: root.theme
                    title: "Сховище"
                    Layout.fillWidth: true

                    contentItem: ColumnLayout {
                        spacing: theme.sp4
                        SettingsField {
                            theme: root.theme
                            label: "Шлях до бази даних"
                            placeholder: "./data/agent.db"
                            Layout.fillWidth: true
                        }
                        SettingsField {
                            theme: root.theme
                            label: "Шлях до сховища файлів"
                            placeholder: "./data/library"
                            Layout.fillWidth: true
                        }
                    }
                }

                // About section
                Rectangle {
                    Layout.fillWidth: true
                    height: aboutCol.implicitHeight + theme.sp4 * 2
                    radius: theme.radiusLG; color: theme.surface2
                    border.color: theme.borderSubtle; border.width: 1

                    ColumnLayout {
                        id: aboutCol
                        anchors { left: parent.left; right: parent.right; top: parent.top; margins: theme.sp4 }
                        spacing: theme.sp1
                        Text { text: "Agent-For-TOM"; font.pixelSize: theme.fontSizeXL; font.weight: Font.DemiBold; color: theme.textPrimary }
                        Text { text: "Версія 1.0.0  ·  PyQt6 + QML"; font.pixelSize: theme.fontSizeMD; color: theme.textSecondary }
                        Text { text: "2-pass pipeline  ·  SQLite  ·  3 cache levels"; font.pixelSize: theme.fontSizeSM; color: theme.textTertiary }
                    }
                }

                // Save button
                RowLayout {
                    Layout.alignment: Qt.AlignRight
                    AppButton { theme: root.theme; label: "Зберегти налаштування"; variant: "primary" }
                }

                Item { height: theme.sp8 }
            }
        }
    }
}
