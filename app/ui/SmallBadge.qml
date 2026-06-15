import QtQuick 2.15

// Small badge component
Rectangle {
    id: root
    height: 22
    width: badgeText.implicitWidth + 16
    radius: theme.radiusSM
    color: bgColor()

    required property var theme
    required property string label
    property string variant: "neutral"  // "accent" | "success" | "warning" | "danger" | "neutral"

    Text {
        id: badgeText
        anchors.centerIn: parent
        text: root.label
        font.pixelSize: theme.fontSizeXS
        font.weight: Font.Medium
        color: textColor()
    }

    function bgColor() {
        switch(variant) {
            case "accent":  return theme.accentSoft
            case "success": return theme.successSoft
            case "warning": return theme.warningSoft
            case "danger":  return theme.dangerSoft
            default:        return theme.surface3
        }
    }
    function textColor() {
        switch(variant) {
            case "accent":  return theme.accent
            case "success": return theme.success
            case "warning": return theme.warning
            case "danger":  return theme.danger
            default:        return theme.textSecondary
        }
    }
}
