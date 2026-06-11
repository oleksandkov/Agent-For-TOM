import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

// Sidebar navigation item
Rectangle {
    id: root
    Layout.fillWidth: true
    height: Math.max(36, labelText.implicitHeight + 16)
    radius: theme.radiusMD
    color: isActive ? theme.accentSoft2 : (hovered ? theme.surface3 : theme.surface2)
    Behavior on color { ColorAnimation { duration: 150 } }

    required property var theme
    required property string label
    required property string screen
    required property string currentScreen
    property string count: ""
    property string icon: ""
    property bool isCollapsed: false

    signal navigate(string screen)

    property bool isActive: currentScreen === screen
    property bool hovered: mouseArea.containsMouse

    // Active indicator bar on left
    Rectangle {
        opacity: isActive ? 1.0 : 0.0
        anchors.left: parent.left
        anchors.leftMargin: -theme.sp3
        anchors.verticalCenter: parent.verticalCenter
        width: 3
        height: isActive ? 16 : 0
        radius: 1.5
        color: theme.accent
        Behavior on opacity { NumberAnimation { duration: 150 } }
        Behavior on height { NumberAnimation { duration: 200; easing.type: Easing.OutBack } }
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: theme.sp3
        anchors.rightMargin: theme.sp3
        spacing: theme.sp3

        Text {
            Layout.preferredWidth: 20
            Layout.preferredHeight: 20
            text: root.icon
            font.pixelSize: 16
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            Layout.alignment: Qt.AlignVCenter
            topPadding: root.icon === "➕" ? 2 : 0
            color: root.isActive ? theme.accent : theme.textSecondary
            visible: root.icon !== ""
        }

        Text {
            id: labelText
            text: root.label
            font.pixelSize: theme.fontSizeMD
            font.weight: Font.Medium
            color: root.isActive ? theme.accent : (root.hovered ? theme.textPrimary : theme.textSecondary)
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignVCenter
            visible: !root.isCollapsed
            elide: Text.ElideRight
        }

        Text {
            visible: root.count !== "" && !root.isCollapsed
            text: root.count
            font.pixelSize: theme.fontSizeSM
            color: root.isActive ? theme.accent : theme.textTertiary
        }
    }

    MouseArea {
        id: mouseArea
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: root.navigate(root.screen)
    }
}
