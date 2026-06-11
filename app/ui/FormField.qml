import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

// Form field wrapper with label + helper text
ColumnLayout {
    id: root
    spacing: theme.sp2

    required property var theme
    required property string label
    property string helperText: ""
    default property alias content: contentItem.children

    Text {
        text: root.label
        font.pixelSize: theme.fontSizeMD
        font.weight: Font.Medium
        color: theme.textPrimary
    }

    Item {
        id: contentItem
        Layout.fillWidth: true
        height: children.length > 0 ? children[0].implicitHeight : 0
    }

    Text {
        visible: root.helperText !== ""
        text: root.helperText
        font.pixelSize: theme.fontSizeSM
        color: theme.textTertiary
        wrapMode: Text.Wrap
        Layout.fillWidth: true
    }
}
