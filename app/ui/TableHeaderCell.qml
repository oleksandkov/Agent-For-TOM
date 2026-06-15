import QtQuick 2.15
import QtQuick.Layouts 1.15

// Table header cell
Item {
    property var theme
    property string label: ""
    height: 40

    Text {
        anchors.verticalCenter: parent.verticalCenter
        text: parent.label
        font.pixelSize: theme.fontSizeXS
        font.weight: Font.Medium
        font.letterSpacing: 0.6
        color: theme.textTertiary
        font.capitalization: Font.AllUppercase
    }
}
