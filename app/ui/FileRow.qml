import QtQuick 2.15
import QtQuick.Layouts 1.15

// File row in the upload list
Rectangle {
    id: root
    height: 52
    radius: theme.radiusMD
    property bool isDangerHovered: typeof delMouseArea !== 'undefined' ? delMouseArea.containsMouse : false
    color: isHovered || isDangerHovered ? theme.surface2 : theme.surface1
    border.color: isDangerHovered ? theme.danger : (isHovered ? theme.accent : theme.borderSubtle)
    border.width: isHovered || isDangerHovered ? 2 : 1

    required property var theme
    required property string fileName
    required property string fileMeta
    required property string fileStatus  // "done" | "processing" | "error"
    property int fileSymbols: 0
    property bool isOversized: fileSymbols > 150000
    property bool isHovered: false
    signal hoverStateChanged(bool hovering)
    signal dangerHoverStateChanged(bool hovering)

    signal deleteRequested()

    MouseArea {
        id: bgMouseArea
        anchors.fill: parent
        hoverEnabled: true
        // Implicitly z: 0, but rendered behind RowLayout because it's declared first
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: theme.sp3
        anchors.rightMargin: theme.sp3
        spacing: theme.sp3

        // File type icon
        Rectangle {
            width: 32; height: 32
            radius: theme.radiusMD
            color: theme.surface3
            Text { anchors.centerIn: parent; text: "📄"; font.pixelSize: 15 }
        }

        // Name + meta
        ColumnLayout {
            spacing: 2
            Layout.fillWidth: true
            Text {
                text: root.fileName
                font.pixelSize: theme.fontSizeMD
                font.weight: Font.Medium
                color: theme.textPrimary
                elide: Text.ElideMiddle
                Layout.fillWidth: true
            }
            Text {
                text: root.fileSymbols + " символів"
                font.pixelSize: theme.fontSizeXS
                color: theme.textTertiary
                font.family: "JetBrains Mono, Consolas, monospace"
            }
        }

        // Status
        RowLayout {
            spacing: 4
            visible: fileStatus === "done" && !isOversized
            Text { text: "✓"; font.pixelSize: theme.fontSizeSM; color: theme.success }
            Text { text: "Готово"; font.pixelSize: theme.fontSizeSM; color: theme.success }
        }
        RowLayout {
            spacing: 4
            visible: fileStatus === "done" && isOversized
            Text { text: "⚠"; font.pixelSize: theme.fontSizeSM; color: theme.warning }
            Text { text: "Файл завеликий"; font.pixelSize: theme.fontSizeSM; color: theme.warning }
        }
        RowLayout {
            spacing: 4
            visible: fileStatus === "error"
            Text { text: "✕"; font.pixelSize: theme.fontSizeSM; color: theme.danger }
            Text { text: "Помилка"; font.pixelSize: theme.fontSizeSM; color: theme.danger }
        }
        RowLayout {
            spacing: theme.sp1
            visible: fileStatus === "processing"
            Item {
                width: 12; height: 12
                Rectangle {
                    anchors.fill: parent; radius: 6; color: "transparent"
                    border.color: theme.accentSoft; border.width: 2
                }
                Item {
                    width: 6; height: 12; clip: true
                    Rectangle {
                        width: 12; height: 12; radius: 6; color: "transparent"
                        border.color: theme.accent; border.width: 2
                    }
                }
                RotationAnimation on rotation {
                    running: true; loops: Animation.Infinite
                    from: 0; to: 360; duration: 900
                }
            }
            Text { text: "Обробка..."; font.pixelSize: theme.fontSizeSM; color: theme.accent }
        }

        // Delete button (trash)
        Rectangle {
            id: delBtn
            width: 32; height: 32
            radius: theme.radiusMD
            color: delHovered ? theme.danger : theme.surface3
            property bool delHovered: delMouseArea.containsMouse
            Behavior on color { ColorAnimation { duration: 150 } }

            Text {
                anchors.centerIn: parent
                text: "🗑"
                font.pixelSize: 15
                color: delBtn.delHovered ? "white" : theme.textSecondary
                Behavior on color { ColorAnimation { duration: 150 } }
            }

            MouseArea {
                id: delMouseArea
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: root.deleteRequested()
            }
            
            // Tooltip
            Rectangle {
                visible: delBtn.delHovered
                color: theme.surface3
                radius: 4
                width: 120
                height: 24
                anchors.bottom: parent.top
                anchors.bottomMargin: 4
                anchors.horizontalCenter: parent.horizontalCenter
                Text {
                    anchors.centerIn: parent
                    text: "Видалити файл"
                    color: "white"
                    font.pixelSize: 11
                }
            }
        }
    }

    // Removed bgMouseArea from here

    property bool isRowHovered: bgMouseArea.containsMouse || delMouseArea.containsMouse
    onIsRowHoveredChanged: root.hoverStateChanged(isRowHovered)

    onIsDangerHoveredChanged: root.dangerHoverStateChanged(isDangerHovered)

    Behavior on color { ColorAnimation { duration: 100 } }
}
