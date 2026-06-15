import QtQuick 2.15
import QtQuick.Layouts 1.15

// Status badge for session table
Item {
    id: root
    width: badgeRow.implicitWidth + 16
    height: 24

    required property var theme
    required property string status

    Rectangle {
        anchors.fill: parent
        radius: theme.radiusSM
        color: bgColor()
    }

    RowLayout {
        id: badgeRow
        anchors.centerIn: parent
        spacing: 4

        // Status dot / spinner
        Rectangle {
            width: 8; height: 8
            radius: 4
            color: dotColor()
            visible: status !== "processing"

            SequentialAnimation on opacity {
                running: root.status === "warning"
                loops: Animation.Infinite
                NumberAnimation { to: 0.4; duration: 700 }
                NumberAnimation { to: 1.0; duration: 700 }
            }
        }
        Rectangle {
            width: 10; height: 10
            radius: 5
            visible: status === "processing"
            color: "transparent"
            border.color: root.theme.accent
            border.width: 2

            RotationAnimation on rotation {
                running: status === "processing"
                loops: Animation.Infinite
                from: 0; to: 360; duration: 900
            }

            Rectangle {
                width: 4; height: 4
                radius: 2
                color: root.theme.accent
                anchors.top: parent.top
                anchors.horizontalCenter: parent.horizontalCenter
            }
        }

        Text {
            text: labelText()
            font.pixelSize: theme.fontSizeSM
            font.weight: Font.Medium
            color: textColor()
        }
    }

    function bgColor() {
        switch(status) {
            case "completed":  return theme.successSoft
            case "processing": return theme.accentSoft
            case "warning":    return theme.warningSoft
            case "failed":     return theme.dangerSoft
            case "draft":      return theme.surface3
            default:           return theme.surface3
        }
    }
    function textColor() {
        switch(status) {
            case "completed":  return theme.success
            case "processing": return theme.accent
            case "warning":    return theme.warning
            case "failed":     return theme.danger
            case "draft":      return theme.textSecondary
            default:           return theme.textSecondary
        }
    }
    function dotColor() {
        switch(status) {
            case "completed":  return theme.success
            case "processing": return theme.accent
            case "warning":    return theme.warning
            case "failed":     return theme.danger
            case "draft":      return theme.textTertiary
            default:           return theme.textTertiary
        }
    }
    function labelText() {
        switch(status) {
            case "completed":  return "Готово"
            case "processing": return "Обробка"
            case "warning":    return "Попередж."
            case "failed":     return "Помилка"
            case "cancelled":  return "Скасовано"
            case "draft":      return "Чернетка"
            default:           return status
        }
    }
}
