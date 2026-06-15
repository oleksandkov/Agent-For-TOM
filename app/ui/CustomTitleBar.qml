import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtQuick.Window 2.15

Rectangle {
    id: root
    height: 36
    color: theme.surface1

    required property var theme
    required property var window // Reference to the ApplicationWindow

    property bool isVisualMax: window.visibility === Window.Maximized || window.visibility === Window.FullScreen

    // Drag handler
    MouseArea {
        anchors.fill: parent
        onPressed: function(mouse) {
            if (!root.isVisualMax) {
                window.startSystemMove()
            }
        }
        onDoubleClicked: {
            window.toggleMaximized()
        }
    }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        // App Icon & Title
        Item { width: theme.sp3 }
        Text {
            text: "🤖"
            color: theme.accent
            font.pixelSize: 16
        }
        Item { width: theme.sp2 }
        Text {
            text: "Agent-For-TOM"
            color: theme.textPrimary
            font.pixelSize: theme.fontSizeSM
            font.weight: Font.Medium
        }

        Item { Layout.fillWidth: true } // Spacer

        // Window Controls
        Row {
            height: parent.height
            
            // Minimize
            Rectangle {
                width: 46; height: parent.height
                color: minMouse.containsMouse ? theme.surface2 : "transparent"
                Rectangle {
                    width: 10; height: 1
                    color: theme.textPrimary
                    anchors.centerIn: parent
                }
                MouseArea {
                    id: minMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    onClicked: minimizeAnim.start()
                }
                SequentialAnimation {
                    id: minimizeAnim
                    ParallelAnimation {
                        NumberAnimation { target: window.contentItem; property: "y"; to: 50; duration: 150; easing.type: Easing.InCubic }
                        NumberAnimation { target: window.contentItem; property: "opacity"; to: 0; duration: 150; easing.type: Easing.InCubic }
                    }
                    ScriptAction { script: window.showMinimized() }
                }
            }

            // Maximize / Restore
            Rectangle {
                id: maxBtn
                width: 46; height: parent.height
                color: maxMouse.containsMouse ? theme.surface2 : "transparent"
                
                Item {
                    id: maxIconContainer
                    anchors.centerIn: parent
                    width: 10; height: 10
                    
                    Behavior on scale { NumberAnimation { duration: 150; easing.type: Easing.OutBack } }

                    Item {
                        anchors.fill: parent
                        visible: !root.isVisualMax
                        Rectangle {
                            anchors.fill: parent
                            color: "transparent"
                            border.color: theme.textPrimary
                            border.width: 1
                        }
                    }
                    
                    Item {
                        anchors.fill: parent
                        visible: root.isVisualMax
                        // Back rectangle
                        Rectangle {
                            x: 2; y: -2; width: 8; height: 8
                            color: "transparent"
                            border.color: theme.textPrimary
                            border.width: 1
                        }
                        // Background mask for front rectangle
                        Rectangle {
                            x: -2; y: 2; width: 8; height: 8
                            color: maxBtn.color === "transparent" ? theme.surface1 : theme.surface2
                        }
                        // Front rectangle
                        Rectangle {
                            x: -2; y: 2; width: 8; height: 8
                            color: "transparent"
                            border.color: theme.textPrimary
                            border.width: 1
                        }
                    }
                }

                MouseArea {
                    id: maxMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    onClicked: {
                        // Trigger bounce animation
                        maxIconContainer.scale = 0.5
                        maxIconTimer.start()
                        
                        window.toggleMaximized()
                    }
                }
                
                Timer {
                    id: maxIconTimer
                    interval: 50
                    onTriggered: maxIconContainer.scale = 1.0
                }
            }

            // Close
            Rectangle {
                width: 46; height: parent.height
                color: closeMouse.containsMouse ? "#E81123" : "transparent"
                Text { anchors.centerIn: parent; text: "✕"; color: closeMouse.containsMouse ? "white" : theme.textPrimary; font.pixelSize: 14; font.weight: Font.Light }
                MouseArea {
                    id: closeMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    onClicked: window.close()
                }
            }
        }
    }
}
