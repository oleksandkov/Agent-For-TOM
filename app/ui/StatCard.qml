import QtQuick 2.15
import QtQuick.Layouts 1.15

// Stats card for overview grid
Rectangle {
    id: root
    Layout.fillWidth: true
    height: cardCol.implicitHeight + theme.sp4 * 2
    radius: theme.radiusLG
    color: theme.surface2
    border.color: theme.borderSubtle; border.width: 1

    required property var theme
    required property string label
    required property string value
    property string icon: ""

    ColumnLayout {
        id: cardCol
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: theme.sp4 }
        spacing: theme.sp1

        RowLayout {
            spacing: theme.sp2
            Text { text: root.icon; font.pixelSize: 14; visible: root.icon !== "" }
            Text {
                text: root.label
                font.pixelSize: theme.fontSizeSM
                color: theme.textSecondary
                font.weight: Font.Medium
            }
        }

        Text {
            text: root.value
            font.pixelSize: 20
            font.weight: Font.DemiBold
            color: theme.textPrimary
            font.family: "JetBrains Mono, Consolas, monospace"
        }
    }
}
