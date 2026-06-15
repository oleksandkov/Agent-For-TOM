import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

// Screen 6 — Instructions manager
Rectangle {
    id: root
    color: theme.surfaceBase

    required property var theme
    signal navigate(string screen)

    property var instructions: []
    property string filterType: "all"

    function loadInstructions() {
        instructions = JSON.parse(bridge.getInstructionsFiltered(filterType))
    }

    Component.onCompleted: loadInstructions()

    Connections {
        target: bridge
        function onInstructionsChanged() {
            root.loadInstructions()
        }
    }

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
                    left: parent.left; leftMargin: theme.sp10
                    right: parent.right; rightMargin: theme.sp10
                }
                spacing: theme.sp4

                // Header
                RowLayout {
                    Layout.fillWidth: true
                    ColumnLayout {
                        spacing: 2
                        Text { text: "Інструкції"; font.pixelSize: theme.fontSizeH1; font.weight: Font.DemiBold; color: theme.textPrimary; font.letterSpacing: -0.3 }
                        Text { text: "Управління AI-інструкціями"; font.pixelSize: theme.fontSizeLG; color: theme.textSecondary }
                    }
                    Item { Layout.fillWidth: true }
                    AppButton { 
                        theme: root.theme; 
                        label: "+ Нові інструкції"; 
                        variant: "primary"
                        fontWeight: Font.Bold
                        onClicked: {
                            newInstructionPopup.templates = JSON.parse(bridge.getTemplates())
                            newInstructionPopup.open()
                        }
                    }
                }

                // Filter chips
                RowLayout {
                    spacing: theme.sp2
                    FilterChip { theme: root.theme; label: "Усі";            active: root.filterType === "all";          onClicked: { root.filterType = "all"; root.loadInstructions() } }
                    FilterChip { theme: root.theme; label: "Глобальні";      active: root.filterType === "global";       onClicked: { root.filterType = "global"; root.loadInstructions() } }
                    FilterChip { theme: root.theme; label: "Per template";   active: root.filterType === "special";      onClicked: { root.filterType = "special"; root.loadInstructions() } }
                    FilterChip { theme: root.theme; label: "Користувацькі";  active: root.filterType === "user_created"; onClicked: { root.filterType = "user_created"; root.loadInstructions() } }
                    FilterChip { theme: root.theme; label: "Неприкріплені";  active: root.filterType === "unattached";   onClicked: { root.filterType = "unattached"; root.loadInstructions() } }
                }

                // Instructions list
                Rectangle {
                    Layout.fillWidth: true
                    height: instrCol.implicitHeight
                    radius: theme.radiusLG
                    color: theme.surface1
                    border.color: theme.borderSubtle; border.width: 1
                    clip: true

                    ColumnLayout {
                        id: instrCol
                        anchors { left: parent.left; right: parent.right; top: parent.top }
                        spacing: 0

                        // Header row
                        Rectangle {
                            Layout.fillWidth: true; height: 40
                            color: theme.surface2; radius: theme.radiusLG

                            Rectangle { anchors.bottom: parent.bottom; anchors.left: parent.left; anchors.right: parent.right; height: parent.height / 2; color: parent.color }
                            Rectangle { anchors.bottom: parent.bottom; anchors.left: parent.left; anchors.right: parent.right; height: 1; color: theme.borderSubtle }

                            RowLayout {
                                anchors.fill: parent; anchors.leftMargin: theme.sp5; anchors.rightMargin: theme.sp5
                                spacing: theme.sp4
                                TableHeaderCell { theme: root.theme; label: "Назва"; Layout.fillWidth: true }
                                TableHeaderCell { theme: root.theme; label: "Тип"; width: 130 }
                                TableHeaderCell { theme: root.theme; label: "Прикріплено до"; width: 200 }
                                TableHeaderCell { theme: root.theme; label: "Дата"; width: 100 }
                                Item { width: 260 }
                            }
                        }

                        // Rows
                        Repeater {
                            model: root.instructions
                            delegate: InstructionRow {
                                theme: root.theme
                                
                                isLast: index === root.instructions.length - 1
                                Layout.fillWidth: true
                            }
                        }

                        // Empty state
                        Rectangle {
                            visible: root.instructions.length === 0
                            Layout.fillWidth: true; height: 100
                            color: "transparent"
                            Text {
                                anchors.centerIn: parent
                                text: "Інструкцій не знайдено"
                                font.pixelSize: theme.fontSizeLG; color: theme.textTertiary
                            }
                        }
                    }
                }

                Item { height: theme.sp8 }
            }
        }
    }

    // Popup for new instruction
    Popup {
        id: newInstructionPopup
        width: 600
        height: 500
        x: Math.round((parent.width - width) / 2)
        y: Math.round((parent.height - height) / 2)
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        
        property var templates: []

        background: Rectangle {
            color: theme.surface1
            radius: theme.radiusLG
            border.color: theme.borderSubtle
            border.width: 1
        }

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: theme.sp6
            spacing: theme.sp4

            Text {
                text: "Створити нову інструкцію"
                font.pixelSize: theme.fontSizeH2
                font.weight: 600
                color: theme.textPrimary
            }

            Text {
                text: "Шаблон (опціонально)"
                font.pixelSize: theme.fontSizeMD
                color: theme.textSecondary
            }

            ComboBox {
                id: templateCombo
                Layout.fillWidth: true
                model: [{text: "Загальна (без шаблону)", value: ""}].concat(newInstructionPopup.templates.map(t => ({text: t.display_name, value: t.id})))
                textRole: "text"
                valueRole: "value"
            }

            Text {
                text: "Вміст (Markdown)"
                font.pixelSize: theme.fontSizeMD
                color: theme.textSecondary
            }

            ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true

                TextArea {
                    id: instructionContentArea
                    placeholderText: "Введіть інструкцію тут..."
                    wrapMode: TextEdit.Wrap
                    font.pixelSize: theme.fontSizeMD
                    color: theme.textPrimary
                    background: Rectangle {
                        color: theme.surface2
                        radius: theme.radiusMD
                        border.color: instructionContentArea.activeFocus ? theme.accent : theme.borderSubtle
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignRight
                spacing: theme.sp3

                AppButton {
                    theme: root.theme
                    label: "Скасувати"
                    variant: "secondary"
                    onClicked: newInstructionPopup.close()
                }

                AppButton {
                    theme: root.theme
                    label: "Зберегти"
                    variant: "primary"
                    onClicked: {
                        let type_ = templateCombo.currentValue ? "special" : "user_created"
                        bridge.createInstruction(type_, templateCombo.currentValue, instructionContentArea.text)
                        instructionContentArea.text = ""
                        newInstructionPopup.close()
                    }
                }
            }
        }
    }
}
