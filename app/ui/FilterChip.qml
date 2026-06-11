import QtQuick 2.15
import QtQuick.Layouts 1.15

// Filter chip for the sessions toolbar
Rectangle {
    id: root
    height: 32
    width: chipLabel.implicitWidth + 40
    radius: theme.radiusMD
    color: active ? theme.surface1 : theme.surface2
    border.color: active ? theme.accent : "transparent"
    border.width: 1

    required property var theme
    required property string label
    property bool active: false

    signal clicked()

    property bool hovered: false

    Text {
        id: chipLabel
        anchors.centerIn: parent
        text: root.label
        font.pixelSize: theme.fontSizeMD
        font.weight: Font.Medium
        color: root.active ? theme.accent : (root.hovered ? theme.textPrimary : theme.textSecondary)
    }

    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onEntered: root.hovered = true
        onExited:  root.hovered = false
        onClicked: root.clicked()
    }

    Behavior on color { ColorAnimation { duration: 120 } }
}
