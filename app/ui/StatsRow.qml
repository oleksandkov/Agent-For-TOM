import QtQuick 2.15
import QtQuick.Layouts 1.15

// Stats row for the stats tab
Rectangle {
    Layout.fillWidth: true
    height: 36
    color: "transparent"

    Rectangle {
        anchors.bottom: parent.bottom
        anchors.left: parent.left; anchors.right: parent.right
        height: 1; color: theme.borderSubtle
    }

    required property var theme
    required property string label
    required property string value

    RowLayout {
        anchors.fill: parent
        Text {
            text: root.label
            font.pixelSize: theme.fontSizeMD
            color: theme.textSecondary
            Layout.preferredWidth: 280
        }
        Item { Layout.fillWidth: true }
        Text {
            text: root.value
            font.pixelSize: theme.fontSizeMD
            color: theme.textPrimary
            font.weight: Font.Medium
            font.family: "JetBrains Mono, Consolas, monospace"
        }
    }
}
