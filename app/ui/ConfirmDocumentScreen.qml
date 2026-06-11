import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

// Screen 2.5 — Session Confirmation
Rectangle {
    id: root
    focus: true
    color: theme.surfaceBase

    required property var theme
    signal navigate(string screen)

    Flickable {
        anchors.fill: parent
        contentWidth: parent.width
        contentHeight: formCol.implicitHeight + 100
        clip: true

        ColumnLayout {
            id: formCol
            anchors {
                top: parent.top; topMargin: theme.sp8
                horizontalCenter: parent.horizontalCenter
            }
            width: Math.min(parent.width - theme.sp10 * 2, 720)
            spacing: theme.sp4

            Text {
                text: "Підтвердження даних"
                font.pixelSize: theme.fontSizeH1
                font.weight: Font.DemiBold
                color: theme.textPrimary
                font.letterSpacing: -0.3
            }
            Text {
                text: "Перевірте всі введені параметри перед початком генерації."
                font.pixelSize: theme.fontSizeLG
                color: theme.textSecondary
                Layout.bottomMargin: theme.sp4
            }

            Rectangle {
                Layout.fillWidth: true
                radius: theme.radiusLG; color: theme.surface1
                border.color: theme.borderSubtle; border.width: 1
                Layout.preferredHeight: dataCol.implicitHeight + theme.sp6 * 2

                ColumnLayout {
                    id: dataCol
                    anchors { left: parent.left; right: parent.right; top: parent.top; margins: theme.sp6 }
                    spacing: theme.sp4

                    // List of user choices
                    Repeater {
                        model: [
                            { label: "Назва документу:", value: sessionPayload.documentName || "Не вказано", aiChecked: sessionPayload.nameAiCheck },
                            { label: "Тема:", value: sessionPayload.documentTheme || "Не вказано", aiChecked: sessionPayload.themeAiCheck },
                            { label: "Мета:", value: sessionPayload.documentGoal ? sessionPayload.documentGoal : "Немає", aiChecked: sessionPayload.goalAiCheck },
                            { label: "Номер лабораторної:", value: sessionPayload.labNumber || "Не вказано", aiChecked: false },
                            { label: "Обсяг:", value: sessionPayload.lengthMode || "middle", aiChecked: false },
                            { label: "Індивідуальні завдання:", value: sessionPayload.hasVariants === "yes" ? sessionPayload.variantsNumber : (sessionPayload.hasVariants === "no" ? "Немає" : "Не вказано"), aiChecked: false },
                            { label: "Додаткові вказівки:", value: sessionPayload.sessionHints ? sessionPayload.sessionHints : "Немає", aiChecked: false },
                            { label: "Файли контексту:", value: sessionPayload.uploadedFiles && sessionPayload.uploadedFiles.length > 0 ? sessionPayload.uploadedFiles.map(function(f){return f.name}).join(", ") : "Немає", aiChecked: false }
                        ]
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: theme.sp4
                            Text {
                                text: modelData.label
                                font.pixelSize: theme.fontSizeMD
                                color: theme.textSecondary
                                Layout.preferredWidth: 200
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: theme.sp2
                                Text {
                                    text: modelData.value
                                    font.pixelSize: theme.fontSizeMD
                                    font.weight: Font.Medium
                                    color: theme.textPrimary
                                    Layout.fillWidth: true
                                    wrapMode: Text.Wrap
                                }
                                Text {
                                    text: "✨ [ШІ покращить це поле]"
                                    font.pixelSize: theme.fontSizeSM
                                    color: theme.accent
                                    visible: modelData.aiChecked === true
                                    Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
                                }
                            }
                        }
                    }
                }
            }

            // Buttons
            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: theme.sp4
                
                AppButton {
                    theme: root.theme
                    label: "✏️ Редагувати внесені дані"
                    variant: "secondary"
                    onClicked: root.navigate("new_document")
                }
                AppButton {
                    theme: root.theme
                    label: "💾 Зберегти чернетку"
                    variant: "secondary"
                    onClicked: {
                        if (ApplicationWindow.window) {
                            ApplicationWindow.window.sessionPayload = {}
                        }
                        root.navigate("documents")
                    }
                }
                
                Item { Layout.fillWidth: true } // spacer pushes generate to the right

                AppButton {
                    theme: root.theme
                    label: "✦ Створити"
                    variant: "primary"
                    onClicked: {
                        function formatDateTime() {
                            var d = new Date()
                            var yy = String(d.getFullYear())
                            return String(d.getDate()).padStart(2, '0') + "-" +
                                   String(d.getMonth()+1).padStart(2, '0') + "-" +
                                   yy + "-" +
                                   String(d.getHours()).padStart(2, '0') + "-" +
                                   String(d.getMinutes()).padStart(2, '0') + "-" +
                                   String(d.getSeconds()).padStart(2, '0')
                        }

                        var safeName = sessionPayload.documentName ? sessionPayload.documentName : "document"
                        bridge.saveSessionJson(JSON.stringify(sessionPayload), safeName + "_" + formatDateTime())

                        bridge.startGeneration(
                            sessionPayload.documentName,
                            sessionPayload.selectedTemplate,
                            sessionPayload.lengthMode,
                            "university_1", // default since it was removed
                            "none", // image mode
                            sessionPayload.documentGoal || ""
                        )
                        
                        if (typeof ApplicationWindow.window.clearSessionPayload === "function") {
                            ApplicationWindow.window.clearSessionPayload()
                        }
                        
                        bridge.clearTransitFolder()
                        
                        root.navigate("progress")
                    }
                }
            }
        }
    }
}
