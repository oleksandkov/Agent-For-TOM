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
                        model: {
                            var items = [
                                { label: "Назва документу:", value: sessionPayload.documentName || "Не вказано", aiChecked: sessionPayload.nameAiCheck },
                                { label: "Шаблон документа:", value: (function() {
                                    var tmplId = sessionPayload.template_id;
                                    if (!tmplId) return "Не вказано";
                                    var templates = JSON.parse(bridge.getTemplates());
                                    for (var i = 0; i < templates.length; i++) {
                                        if (templates[i].id === tmplId) return templates[i].display_name;
                                    }
                                    return "Невідомий шаблон";
                                })(), aiChecked: false },
                                { label: "Враховувати користувацький стиль (user_style.md):", value: sessionPayload.includeUserStyle === true ? "Так" : "Ні", aiChecked: false },
                                { label: "Тема:", value: sessionPayload.documentTheme || "Не вказано", aiChecked: sessionPayload.themeAiCheck },
                                { label: "Мета:", value: sessionPayload.documentGoal ? sessionPayload.documentGoal : "Немає", aiChecked: sessionPayload.goalAiCheck },
                                { label: "Теоретичні відомості:", value: sessionPayload.documentTheory ? sessionPayload.documentTheory : "Немає", aiChecked: sessionPayload.theoryAiCheck },
                                { label: "Завдання:", value: sessionPayload.documentTasks ? sessionPayload.documentTasks : "Немає", aiChecked: sessionPayload.tasksAiCheck },
                                { label: "Контрольні запитання:", value: sessionPayload.documentQuestions ? sessionPayload.documentQuestions : "Немає", aiChecked: sessionPayload.questionsAiCheck },
                                { label: "Література:", value: sessionPayload.documentBibliography ? sessionPayload.documentBibliography : "Немає", aiChecked: sessionPayload.bibliographyAiCheck },
                                { label: "Номер лабораторної:", value: sessionPayload.labNumber || "Не вказано", aiChecked: false },
                                { label: "Обсяг:", value: sessionPayload.lengthMode || "middle", aiChecked: false },
                                { label: "Режим зображень:", value: sessionPayload.image_mode === "references" ? "Посилання" : (sessionPayload.image_mode === "full" ? "Генерація" : "Без зображень"), aiChecked: false }
                            ];
                            if (sessionPayload.template_id === "lab2") {
                                items.push({ label: "Індивідуальні завдання:", value: sessionPayload.hasVariants === "yes" ? sessionPayload.variantsNumber : (sessionPayload.hasVariants === "no" ? "Немає" : "Не вказано"), aiChecked: false });
                            }
                            items.push({ label: "Спеціальні інструкції шаблону:", value: sessionPayload.includeSpecialInstructions === true ? "Так" : "Ні", aiChecked: false });
                            items.push({ label: "Додаткові вказівки:", value: sessionPayload.sessionHints ? sessionPayload.sessionHints : "Немає", aiChecked: false });
                            items.push({ label: "Файли контексту:", value: sessionPayload.uploadedFiles && sessionPayload.uploadedFiles.length > 0 ? sessionPayload.uploadedFiles.map(function(f){return f.name}).join(", ") : "Немає", aiChecked: false });
                            return items;
                        }
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

                        bridge.startGeneration(JSON.stringify(sessionPayload))
                        
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
