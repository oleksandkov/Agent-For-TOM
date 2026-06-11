import QtQuick 2.15

// Single pill radio button
Rectangle {
    id: root
    height: Math.max(36, pillText.implicitHeight + 16)
    width: pillText.implicitWidth + 32
    radius: 18  // fully rounded
    color: active ? theme.accentSoft2 : (hovered ? theme.surface3 : theme.surface1)


    property var theme
    property string label: ""
    property bool active: false
    property bool hovered: false

    signal clicked()

    Text {
        id: pillText
        anchors.centerIn: parent
        text: (root.active ? "● " : "○ ") + root.label
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

    Rectangle {
        anchors.fill: parent
        color: "transparent"
        radius: parent.radius
        border.color: root.active ? theme.accent : theme.borderStrong
        border.width: 1
        Behavior on border.color { ColorAnimation { duration: 120 } }
    }

    Behavior on color { ColorAnimation { duration: 120 } }
}
