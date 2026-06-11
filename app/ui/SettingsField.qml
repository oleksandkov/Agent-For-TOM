import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

// Settings field with label + input
ColumnLayout {
    id: root
    spacing: theme.sp2

    required property var theme
    required property string label
    property string placeholder: ""
    property bool isPassword: false
    property string fieldType: "text"  // "text" | "combo"
    property var options: []

    Text {
        text: root.label
        font.pixelSize: theme.fontSizeMD; font.weight: Font.Medium; color: theme.textPrimary
    }

    // Text input
    Rectangle {
        visible: fieldType === "text"
        Layout.fillWidth: true; height: 44
        radius: theme.radiusMD; color: theme.surface1
        border.color: inputFocus.activeFocus ? theme.accent : theme.borderStrong
        border.width: inputFocus.activeFocus ? 2 : 1

        RowLayout {
            anchors { fill: parent; leftMargin: theme.sp3; rightMargin: theme.sp3 }
            TextInput {
                id: inputFocus
                Layout.fillWidth: true
                font.pixelSize: theme.fontSizeLG; color: theme.textPrimary
                echoMode: root.isPassword ? TextInput.Password : TextInput.Normal
                Text {
                    visible: parent.text.length === 0
                    text: root.placeholder
                    color: theme.textTertiary
                    font.pixelSize: parent.font.pixelSize
                }
            }
            Text {
                visible: root.isPassword
                text: "👁"
                font.pixelSize: 14; color: theme.textTertiary
            }
        }
    }

    // Combo box
    ComboBox {
        visible: fieldType === "combo"
        Layout.fillWidth: true; height: 44
        model: root.options
        font.pixelSize: theme.fontSizeLG
    }
}
