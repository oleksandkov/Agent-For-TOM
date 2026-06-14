import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

// Screen 5 — Storage (Generated results)
Rectangle {
    id: root
    color: theme.surfaceBase

    required property var theme
    signal navigate(string screen)

    property var files: []

    function loadFiles() {
        files = JSON.parse(bridge.getGeneratedFiles())
    }

    Component.onCompleted: loadFiles()

    ScrollView {
        anchors.fill: parent
        contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

        ColumnLayout {
            anchors {
                top: parent.top; topMargin: theme.sp8
                left: parent.left; leftMargin: theme.sp10
                right: parent.right; rightMargin: theme.sp10
            }
            spacing: theme.sp4

            RowLayout {
                Layout.fillWidth: true
                ColumnLayout {
                    spacing: 2
                    Text { text: "Сховище"; font.pixelSize: theme.fontSizeH1; font.weight: 600; color: theme.textPrimary }
                    Text { text: "Згенеровані документи (DOCX та PDF)"; font.pixelSize: theme.fontSizeLG; color: theme.textSecondary }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                height: fileCol.implicitHeight
                radius: theme.radiusLG
                color: theme.surface1
                border.color: theme.borderSubtle; border.width: 1
                clip: true

                ColumnLayout {
                    id: fileCol
                    anchors { left: parent.left; right: parent.right; top: parent.top }
                    spacing: 0

                    Rectangle {
                        Layout.fillWidth: true; height: 40
                        color: theme.surface2; radius: theme.radiusLG

                        Rectangle { anchors.bottom: parent.bottom; anchors.left: parent.left; anchors.right: parent.right; height: parent.height / 2; color: parent.color }
                        Rectangle { anchors.bottom: parent.bottom; anchors.left: parent.left; anchors.right: parent.right; height: 1; color: theme.borderSubtle }

                        RowLayout {
                            anchors.fill: parent; anchors.leftMargin: theme.sp5; anchors.rightMargin: theme.sp5
                            spacing: theme.sp4
                            TableHeaderCell { theme: root.theme; label: "Сесія"; Layout.fillWidth: true }
                            TableHeaderCell { theme: root.theme; label: "DOCX"; Layout.preferredWidth: 100 }
                            TableHeaderCell { theme: root.theme; label: "PDF"; Layout.preferredWidth: 100 }
                            TableHeaderCell { theme: root.theme; label: "Дата"; Layout.preferredWidth: 100 }
                            Item { Layout.preferredWidth: 160 }
                        }
                    }

                    Repeater {
                        model: root.files
                        delegate: Rectangle {
                            Layout.fillWidth: true
                            height: 52
                            color: hovered ? theme.surface2 : theme.surface1
                            property bool hovered: false

                            Rectangle { visible: index < root.files.length - 1; anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.borderSubtle }

                            RowLayout {
                                anchors { fill: parent; leftMargin: theme.sp5; rightMargin: theme.sp5 }
                                spacing: theme.sp4

                                Text {
                                    text: modelData.session_name
                                    font.pixelSize: theme.fontSizeMD; font.weight: Font.Medium; color: theme.textPrimary
                                    Layout.fillWidth: true; elide: Text.ElideRight
                                }

                                Text {
                                    text: modelData.docx_path ? "✓ Готово" : "✗ Помилка"
                                    font.pixelSize: theme.fontSizeSM; font.weight: Font.DemiBold
                                    color: modelData.docx_path ? theme.success : theme.danger
                                    Layout.preferredWidth: 100
                                    horizontalAlignment: Text.AlignLeft
                                }

                                Text {
                                    text: modelData.pdf_path ? "✓ Готово" : "✗ Помилка"
                                    font.pixelSize: theme.fontSizeSM; font.weight: Font.DemiBold
                                    color: modelData.pdf_path ? theme.success : theme.danger
                                    Layout.preferredWidth: 100
                                    horizontalAlignment: Text.AlignLeft
                                }

                                Text {
                                    text: modelData.created_at
                                    font.pixelSize: theme.fontSizeSM; color: theme.textSecondary
                                    Layout.preferredWidth: 100
                                    horizontalAlignment: Text.AlignLeft
                                }

                                RowLayout {
                                    Layout.preferredWidth: 160
                                    spacing: theme.sp2
                                    AppButton { 
                                        theme: root.theme; label: "Відкрити"; variant: "secondary"; height: 30; 
                                        onClicked: bridge.openFileExternal(modelData.docx_path || modelData.pdf_path) 
                                    }
                                    AppButton { 
                                        theme: root.theme; label: "Папка"; variant: "secondary"; height: 30; 
                                        onClicked: bridge.showInFolder(modelData.docx_path || modelData.pdf_path) 
                                    }
                                }
                            }

                            MouseArea {
                                anchors.fill: parent; hoverEnabled: true
                                onEntered: parent.hovered = true
                                onExited: parent.hovered = false
                            }
                            Behavior on color { ColorAnimation { duration: 100 } }
                        }
                    }

                    Rectangle {
                        visible: root.files.length === 0
                        Layout.fillWidth: true; height: 100
                        color: "transparent"
                        Text {
                            anchors.centerIn: parent
                            text: "Немає згенерованих файлів"
                            font.pixelSize: theme.fontSizeLG; color: theme.textTertiary
                        }
                    }
                }
            }

            Item { height: theme.sp8 }
        }
    }
}
