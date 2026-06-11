import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

// Reusable card with step number + title + content
Rectangle {
    id: root
    radius: theme.radiusLG
    color: theme.surface1
    border.color: theme.borderSubtle
    border.width: 1
    height: cardContent.implicitHeight + theme.sp6 * 2
    clip: true

    required property var theme
    required property string stepNum
    required property string title
    property var contentItem: null
    default property alias content: contentPlaceholder.children

    ColumnLayout {
        id: cardContent
        anchors {
            left: parent.left; right: parent.right
            top: parent.top
            leftMargin: theme.sp6; rightMargin: theme.sp6; topMargin: theme.sp6
        }
        spacing: theme.sp4

        // Section header
        RowLayout {
            spacing: theme.sp3
            Rectangle {
                width: 28; height: 28
                radius: 14
                color: theme.accentSoft2
                Text {
                    anchors.centerIn: parent
                    text: root.stepNum
                    font.pixelSize: theme.fontSizeMD
                    font.weight: Font.DemiBold
                    color: theme.accent
                }
            }
            Text {
                text: root.title
                font.pixelSize: theme.fontSizeXL
                font.weight: Font.DemiBold
                color: theme.textPrimary
            }
        }

        // Content area
        Item {
            id: contentPlaceholder
            Layout.fillWidth: true
            height: children.length > 0 ? children[0].implicitHeight : 0
        }
    }
}
