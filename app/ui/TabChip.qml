import QtQuick 2.15
import QtQuick.Layouts 1.15

// Tab chip for ResultScreen
Rectangle {
    id: root
    height: 34
    width: chipLabel.implicitWidth + 24
    radius: theme.radiusMD
    color: isActive ? theme.surface1 : "transparent"
    border.color: isActive ? theme.borderStrong : "transparent"
    border.width: 1

    required property var theme
    required property string label
    required property string tabId
    required property string activeTab

    property bool isActive: tabId === activeTab
    property bool hovered: false

    signal activate(string tabId)

    Text {
        id: chipLabel
        anchors.centerIn: parent
        text: root.label
        font.pixelSize: theme.fontSizeMD
        font.weight: root.isActive ? Font.DemiBold : Font.Normal
        color: root.isActive ? theme.textPrimary : (root.hovered ? theme.textPrimary : theme.textSecondary)
    }

    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onEntered: root.hovered = true
        onExited:  root.hovered = false
        onClicked: root.activate(root.tabId)
    }

    Behavior on color { ColorAnimation { duration: 120 } }
}
