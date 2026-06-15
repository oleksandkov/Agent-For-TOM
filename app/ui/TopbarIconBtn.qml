import QtQuick 2.15
import QtQuick.Controls 2.15

// Small icon button for topbar
Rectangle {
    id: root
    width: 36; height: 36
    radius: theme.radiusMD
    color: hovered ? theme.surface3 : theme.surface2

    required property var theme
    property string tooltip: ""
    property string icon: "⋯"
    property bool hovered: mouseArea.containsMouse
    signal clicked()

    ToolTip.visible: hovered && tooltip !== ""
    ToolTip.text: tooltip
    ToolTip.delay: 800

    Text {
        anchors.centerIn: parent
        text: root.icon
        font.pixelSize: 16
        color: root.hovered ? theme.textPrimary : theme.textSecondary
    }

    MouseArea {
        id: mouseArea
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: root.clicked()
    }

    Behavior on color { ColorAnimation { duration: 100 } }
}
