import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

// One row in the instructions table
Rectangle {
    id: root
    height: 52
    color: hovered ? theme.surface2 : theme.surface1

    required property var theme
    required property var modelData
    required property int index
    property bool isLast: false
    property bool hovered: false

    Rectangle {
        visible: !isLast
        anchors.bottom: parent.bottom
        anchors.left: parent.left; anchors.right: parent.right
        height: 1; color: theme.borderSubtle
    }

    RowLayout {
        anchors { fill: parent; leftMargin: theme.sp5; rightMargin: theme.sp5 }
        spacing: theme.sp4

        // Name
        Text {
            text: modelData.name
            font.pixelSize: theme.fontSizeMD; font.weight: Font.Medium; color: theme.textPrimary
            Layout.fillWidth: true; elide: Text.ElideRight
        }

        // Type badge
        Item {
            width: 130
            SmallBadge {
                anchors.verticalCenter: parent.verticalCenter
                theme: root.theme
                label: typeLabel(modelData.type)
                variant: typeVariant(modelData.type)
            }
        }

        // Attached to
        Item {
            width: 200
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: modelData.attached_to || "Не прикріплено"
                font.pixelSize: theme.fontSizeMD
                color: modelData.attached_to ? theme.textSecondary : theme.textTertiary
                elide: Text.ElideRight
                width: parent.width
            }
        }

        // Date
        Item {
            width: 100
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: modelData.created_at || ""
                font.pixelSize: theme.fontSizeSM; color: theme.textTertiary
            }
        }

        // Actions
        RowLayout {
            spacing: theme.sp1; width: 100
            AppButton { theme: root.theme; label: "Редагувати"; variant: "ghost"; height: 30 }
        }
    }

    MouseArea {
        anchors.fill: parent; hoverEnabled: true
        onEntered: root.hovered = true
        onExited:  root.hovered = false
    }

    function typeLabel(t) {
        switch(t) {
            case "global":       return "Глобальні"
            case "special":      return "Спеціальні"
            case "user_created": return "Користув."
            default:             return t
        }
    }
    function typeVariant(t) {
        switch(t) {
            case "global":       return "accent"
            case "special":      return "success"
            case "user_created": return "neutral"
            default:             return "neutral"
        }
    }

    Behavior on color { ColorAnimation { duration: 100 } }
}
