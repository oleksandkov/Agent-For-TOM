import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

// One row in the sessions table
Rectangle {
    id: root
    height: 56
    color: hovered ? theme.surface2 : theme.surface1

    required property var theme
    required property var modelData
    required property int index
    property bool isLast: false
    property bool hovered: false

    signal openSession()
    signal duplicateSession()
    signal deleteSession()

    // Bottom border (not for last)
    Rectangle {
        visible: !isLast
        anchors.bottom: parent.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        height: 1
        color: theme.borderSubtle
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: theme.sp5
        anchors.rightMargin: theme.sp5
        spacing: theme.sp4

        // Status badge
        Item {
            width: 110
            StatusBadge {
                theme: root.theme
                status: root.modelData.status
                anchors.verticalCenter: parent.verticalCenter
            }
        }

        // Name + date
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2
            Text {
                text: root.modelData.name
                font.pixelSize: theme.fontSizeMD
                font.weight: Font.Medium
                color: theme.textPrimary
                elide: Text.ElideRight
                Layout.fillWidth: true
            }
            Text {
                text: root.modelData.created_at
                font.pixelSize: theme.fontSizeXS
                color: theme.textTertiary
            }
        }

        // Template badge
        Item {
            width: 120
            Rectangle {
                anchors.verticalCenter: parent.verticalCenter
                height: 22
                width: Math.min(templateLabel.implicitWidth + 16, 110)
                radius: theme.radiusSM
                color: root.modelData.template === "custom" ? theme.surface3 : theme.accentSoft
                Text {
                    id: templateLabel
                    anchors.centerIn: parent
                    text: root.modelData.template
                    font.pixelSize: theme.fontSizeSM
                    font.weight: Font.Medium
                    color: root.modelData.template === "custom" ? theme.textSecondary : theme.accent
                    font.family: "JetBrains Mono, Consolas, monospace"
                }
            }
        }

        // Hardness
        Item {
            width: 110
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: hardnessLabel(root.modelData.hardness)
                font.pixelSize: theme.fontSizeMD
                color: theme.textSecondary
            }
        }

        // Duration
        Item {
            width: 70
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: root.modelData.duration
                font.pixelSize: theme.fontSizeSM
                color: theme.textTertiary
                font.family: "JetBrains Mono, Consolas, monospace"
            }
        }

        // Actions (visible on hover)
        Rectangle {
            width: 36; height: 36
            radius: theme.radiusSM
            color: actHovered ? theme.surface3 : "transparent"
            opacity: root.hovered ? 1.0 : 0.0
            property bool actHovered: false

            Text {
                anchors.centerIn: parent
                text: "⋯"
                font.pixelSize: 16
                color: theme.textSecondary
            }

            MouseArea {
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onEntered: parent.actHovered = true
                onExited:  parent.actHovered = false
                onClicked: contextMenu.open()
            }

            Menu {
                id: contextMenu
                MenuItem {
                    text: "Відкрити"
                    onTriggered: root.openSession()
                }
                MenuItem {
                    text: "Дублювати"
                    onTriggered: root.duplicateSession()
                }
                MenuItem {
                    text: "Відновити"
                    onTriggered: {
                        if (typeof bridge.restoreSession === "function") {
                            bridge.restoreSession(modelData.id)
                        }
                    }
                }
                MenuSeparator {}
                MenuItem {
                    text: "Видалити"
                    onTriggered: root.deleteSession()
                }
            }

            Behavior on opacity { NumberAnimation { duration: 150 } }
        }
    }

    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        onEntered: root.hovered = true
        onExited:  root.hovered = false
        onClicked: root.openSession()
        propagateComposedEvents: true
    }

    function hardnessLabel(h) {
        switch(h) {
            case "school":       return "Школа"
            case "university_1": return "Університет 1"
            case "university_2": return "Університет 2"
            case "bachelor":     return "Бакалавр"
            default:             return "—"
        }
    }

    Behavior on color { ColorAnimation { duration: 100 } }
}
