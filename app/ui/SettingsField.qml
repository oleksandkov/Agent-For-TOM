import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

// Settings field with label + input
ColumnLayout {
    id: root
    spacing: theme.sp4

    required property var theme
    required property string label
    property string placeholder: ""
    property bool isPassword: false
    property bool _passwordVisible: false
    property string fieldType: "text"  // "text" | "combo"
    property var options: []
    property alias text: inputFocus.text
    property int maximumLength: 32767

    Text {
        text: root.label
        font.pixelSize: theme.fontSizeLG
        font.weight: Font.DemiBold
        color: theme.textPrimary
    }

    // Text input
    TextField {
        id: inputFocus
        visible: fieldType === "text"
        Layout.fillWidth: true
        placeholderText: root.placeholder
        font.pixelSize: theme.fontSizeMD
        color: theme.textPrimary
        echoMode: root.isPassword && !root._passwordVisible ? TextInput.Password : TextInput.Normal
        maximumLength: root.maximumLength

        HoverHandler { cursorShape: Qt.IBeamCursor }

        Text {
            id: eyeIcon
            visible: root.isPassword
            text: root._passwordVisible ? "🙈" : "👁"
            font.pixelSize: 16
            color: theme.textTertiary
            
            SequentialAnimation {
                id: clickAnim
                NumberAnimation { target: eyeIcon; property: "scale"; to: 0.7; duration: 100; easing.type: Easing.OutQuad }
                NumberAnimation { target: eyeIcon; property: "scale"; to: 1.0; duration: 150; easing.type: Easing.OutBack }
            }
            
            anchors.right: parent.right
            anchors.rightMargin: theme.sp3
            anchors.verticalCenter: parent.verticalCenter
            
            MouseArea {
                id: eyeMouse
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: {
                    clickAnim.restart()
                    root._passwordVisible = !root._passwordVisible
                    inputFocus.forceActiveFocus() // Keep focus on input while toggling visibility
                }
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
