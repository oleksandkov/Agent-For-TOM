import QtQuick 2.15
import QtQuick.Layouts 1.15

// One step row in the pipeline steps card
Rectangle {
    id: root
    height: stepBody.implicitHeight + theme.sp3 * 2
    color: stepState === "running" ? theme.accentSoft2 : "transparent"

    required property var theme
    required property int stepIndex
    required property string stepName
    required property string stepDetail
    required property string stepState   // "pending" | "running" | "done"
    property bool isLast: false

    // Bottom border
    Rectangle {
        visible: !isLast
        anchors.bottom: parent.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        height: 1
        color: theme.borderSubtle
        opacity: stepState === "running" ? 0 : 1
    }

    RowLayout {
        id: stepBody
        anchors {
            left: parent.left; right: parent.right
            top: parent.top; topMargin: theme.sp3
            leftMargin: theme.sp4; rightMargin: theme.sp4
        }
        spacing: theme.sp3

        // Step status icon
        Rectangle {
            width: 24; height: 24
            radius: 12
            color: iconBg()

            // Done checkmark
            Text {
                visible: stepState === "done"
                anchors.centerIn: parent
                text: "✓"
                font.pixelSize: 11
                font.weight: Font.DemiBold
                color: "white"
            }

            // Running spinner
            Rectangle {
                visible: stepState === "running"
                anchors.centerIn: parent
                width: 10; height: 10
                radius: 5
                color: "transparent"
                border.color: "white"
                border.width: 2

                RotationAnimation on rotation {
                    running: stepState === "running"
                    loops: Animation.Infinite
                    from: 0; to: 360; duration: 900
                }

                // Pulse ring
                Rectangle {
                    anchors.centerIn: parent
                    width: 20; height: 20
                    radius: 10
                    color: "transparent"
                    border.color: theme.accent
                    border.width: 2
                    opacity: 0.4

                    SequentialAnimation on scale {
                        running: stepState === "running"
                        loops: Animation.Infinite
                        NumberAnimation { to: 1.3; duration: 750; easing.type: Easing.OutCubic }
                        NumberAnimation { to: 1.0; duration: 750; easing.type: Easing.InCubic }
                    }
                    SequentialAnimation on opacity {
                        running: stepState === "running"
                        loops: Animation.Infinite
                        NumberAnimation { to: 0; duration: 750 }
                        NumberAnimation { to: 0.4; duration: 750 }
                    }
                }
            }

            // Step number for pending
            Text {
                visible: stepState === "pending"
                anchors.centerIn: parent
                text: (root.stepIndex + 1).toString()
                font.pixelSize: 10
                font.weight: Font.DemiBold
                color: theme.textTertiary
            }

            function iconBg() {
                switch(stepState) {
                    case "done":    return theme.success
                    case "running": return theme.accent
                    default:        return theme.surface3
                }
            }

            Behavior on color { ColorAnimation { duration: 200 } }
        }

        // Step info
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2

            Text {
                text: root.stepName
                font.pixelSize: theme.fontSizeLG
                font.weight: Font.Medium
                color: root.stepState === "pending" ? theme.textTertiary
                     : (root.stepState === "running" ? theme.accent : theme.textPrimary)
            }
            Text {
                text: root.stepDetail
                font.pixelSize: theme.fontSizeSM
                color: root.stepState === "pending" ? theme.surface3
                     : (root.stepState === "running" ? theme.accent : theme.textSecondary)
                opacity: root.stepState === "pending" ? 0.6 : 1.0
            }
        }

        // Time indicator for done steps
        Text {
            visible: root.stepState === "done"
            text: mockTimes[root.stepIndex] || "—"
            font.pixelSize: theme.fontSizeSM
            color: theme.textTertiary
            font.family: "JetBrains Mono, Consolas, monospace"
        }
        Text {
            visible: root.stepState === "running"
            text: "~" + mockTimes[root.stepIndex] || "…"
            font.pixelSize: theme.fontSizeSM
            color: theme.accent
            font.family: "JetBrains Mono, Consolas, monospace"
        }

        readonly property var mockTimes: ["2.1s","4.8s","0.2s","6.3s","1.8s","1.2s"]
    }

    Behavior on color { ColorAnimation { duration: 150 } }
}
