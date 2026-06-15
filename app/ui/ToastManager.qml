import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Item {
    id: root
    width: 300
    height: 0
    
    property var theme
    property var toasts: []
    
    function showToast(message, type) {
        var t = {
            "id": Date.now() + Math.random(),
            "message": message,
            "type": type || "info" // "info", "success", "error", "warning"
        }
        toasts.push(t)
        toastsChanged()
        
        // Auto-remove after 5s
        var timer = Qt.createQmlObject("import QtQml 2.15; Timer { interval: 5000; repeat: false; running: true }", root, "toastTimer" + t.id)
        timer.triggered.connect(function() {
            root.removeToast(t.id)
            timer.destroy()
        })
    }
    
    function removeToast(id) {
        var newToasts = []
        for (var i = 0; i < toasts.length; i++) {
            if (toasts[i].id !== id) {
                newToasts.push(toasts[i])
            }
        }
        toasts = newToasts
        toastsChanged()
    }
    
    ColumnLayout {
        anchors.top: parent.top
        anchors.right: parent.right
        spacing: 8
        
        Repeater {
            model: root.toasts
            delegate: Rectangle {
                Layout.alignment: Qt.AlignRight
                width: 300
                height: toastRow.implicitHeight + 24
                radius: 8
                
                property color bgColor
                property color borderColor
                property color iconColor
                property string iconChar
                
                Component.onCompleted: {
                    if (modelData.type === "success") {
                        bgColor = theme.successSoft || "#DCFCE7"
                        borderColor = "#86EFAC"
                        iconColor = theme.success || "#10B981"
                        iconChar = "✓"
                    } else if (modelData.type === "error") {
                        bgColor = theme.dangerSoft || "#FEE2E2"
                        borderColor = "#FCA5A5"
                        iconColor = theme.danger || "#EF4444"
                        iconChar = "✕"
                    } else if (modelData.type === "warning") {
                        bgColor = theme.warningSoft || "#FEF3C7"
                        borderColor = "#FDE68A"
                        iconColor = theme.warning || "#F59E0B"
                        iconChar = "⚠"
                    } else {
                        bgColor = theme.surface3 || "#313244"
                        borderColor = theme.borderSubtle || "#45475A"
                        iconColor = theme.textPrimary || "#CDD6F4"
                        iconChar = "ℹ"
                    }
                }
                
                color: bgColor
                border.color: borderColor
                border.width: 1
                
                RowLayout {
                    id: toastRow
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 12
                    
                    Rectangle {
                        width: 24; height: 24; radius: 12
                        color: parent.parent.iconColor
                        Text { anchors.centerIn: parent; text: parent.parent.parent.iconChar; color: "white"; font.pixelSize: 12; font.weight: Font.Bold }
                    }
                    
                    Text {
                        Layout.fillWidth: true
                        text: modelData.message
                        font.pixelSize: theme.fontSizeMD || 14
                        color: theme.textPrimary || "#CDD6F4"
                        wrapMode: Text.Wrap
                    }
                    
                    Rectangle {
                        width: 24; height: 24; radius: 4
                        color: "transparent"
                        Text { anchors.centerIn: parent; text: "✕"; color: theme.textSecondary || "#A6ADC8"; font.pixelSize: 14 }
                        MouseArea {
                            anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                            onEntered: parent.color = "rgba(0,0,0,0.1)"
                            onExited: parent.color = "transparent"
                            onClicked: root.removeToast(modelData.id)
                        }
                    }
                }
                
                NumberAnimation on opacity { from: 0; to: 1; duration: 200 }
            }
        }
    }
}
