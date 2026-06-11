import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

// Screen 5 — Templates management
Rectangle {
    id: root
    color: theme.surfaceBase

    required property var theme
    signal navigate(string screen)

    property var templates: []

    Component.onCompleted: {
        templates = JSON.parse(bridge.getTemplates())
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
                spacing: theme.sp6

                // Header
                RowLayout {
                    Layout.fillWidth: true
                    ColumnLayout {
                        spacing: 2
                        Text { text: "Шаблони"; font.pixelSize: theme.fontSizeH1; font.weight: Font.DemiBold; color: theme.textPrimary; font.letterSpacing: -0.3 }
                        Text { text: "Управління шаблонами DOCX/PDF"; font.pixelSize: theme.fontSizeLG; color: theme.textSecondary }
                    }
                    Item { Layout.fillWidth: true }
                    AppButton { theme: root.theme; label: "+ Створити шаблон"; variant: "primary" }
                }

                // Template grid
                GridLayout {
                    Layout.fillWidth: true
                    columns: Math.max(1, Math.floor(parent.width / 340))
                    rowSpacing: theme.sp4
                    columnSpacing: theme.sp4

                    Repeater {
                        model: root.templates
                        delegate: TemplateCard {
                            theme: root.theme
                            
                            Layout.fillWidth: true
                        }
                    }
                }

                // Info section
                Rectangle {
                    Layout.fillWidth: true
                    height: infoRow.implicitHeight + theme.sp4 * 2
                    radius: theme.radiusLG
                    color: theme.accentSoft2
                    border.color: theme.accentSoft; border.width: 1

                    RowLayout {
                        id: infoRow
                        anchors { left: parent.left; right: parent.right; top: parent.top; margins: theme.sp4 }
                        spacing: theme.sp3
                        Text { text: "ℹ"; font.pixelSize: 18; color: theme.accent }
                        ColumnLayout {
                            spacing: 2; Layout.fillWidth: true
                            Text { text: "Про шаблони"; font.pixelSize: theme.fontSizeLG; font.weight: Font.DemiBold; color: theme.accent }
                            Text {
                                text: "Шаблони — це Python-файли (create_docx / create_pdf), що генерують документи ДСТУ 3008:2015.\nВбудовані шаблони задані розробником. Ви можете завантажити PDF/DOCX та зробити власний шаблон."
                                font.pixelSize: theme.fontSizeMD; color: theme.accent; opacity: 0.85
                                wrapMode: Text.Wrap; Layout.fillWidth: true
                            }
                        }
                    }
                }

                Item { height: theme.sp8 }
            }
        }
    }
}
