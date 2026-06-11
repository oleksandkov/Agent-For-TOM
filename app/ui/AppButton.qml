import QtQuick 2.15

// Reusable application button
Rectangle {
    id: root
    height: 40
    width: btnLabel.implicitWidth + 32
    radius: theme.radiusMD
    color: btnColor()

    required property var theme
    required property string label
    property string variant: "primary"   // "primary" | "secondary" | "ghost" | "danger"
    property bool hovered: false

    signal clicked()

    Text {
        id: btnLabel
        anchors.centerIn: parent
        text: root.label
        font.pixelSize: theme.fontSizeMD
        font.weight: Font.Medium
        color: textColor()
    }

    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: root.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
        onEntered: root.hovered = true
        onExited:  root.hovered = false
        onClicked: if (root.enabled) root.clicked()
    }

    function btnColor() {
        if (!enabled) return theme.surface3
        switch(variant) {
            case "primary":   return hovered ? theme.accentHover : theme.accent
            case "secondary": return hovered ? theme.surface3 : theme.surface1
            case "ghost":     return hovered ? theme.surface3 : "transparent"
            case "danger":    return hovered ? "#991B1B" : theme.danger
            default: return theme.accent
        }
    }
    function textColor() {
        if (!enabled) return theme.textTertiary
        switch(variant) {
            case "primary": return "white"
            case "danger":  return "white"
            default:        return theme.textPrimary
        }
    }

    border.color: variant === "secondary" ? theme.borderStrong : "transparent"
    border.width: 1

    Behavior on color { ColorAnimation { duration: 120 } }
}
