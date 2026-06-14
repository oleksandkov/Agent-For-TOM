import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

// Settings section card
Rectangle {
    id: root
    radius: theme.radiusLG
    color: theme.surface1
    border.color: theme.borderSubtle; border.width: 1
    implicitHeight: sectionCol.implicitHeight + theme.sp5 * 2
    clip: true

    required property var theme
    required property string title
    property var contentItem: null
    default property alias content: contentPlaceholder.children

    ColumnLayout {
        id: sectionCol
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: theme.sp5 }
        spacing: theme.sp4

        Text {
            text: root.title
            font.pixelSize: theme.fontSizeXL; font.weight: Font.DemiBold; color: theme.textPrimary
        }

        Rectangle { Layout.fillWidth: true; height: 1; color: theme.borderSubtle }

        Item {
            id: contentPlaceholder
            Layout.fillWidth: true
            implicitHeight: children.length > 0 ? children[0].implicitHeight : 0
        }
    }
}
