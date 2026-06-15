import QtQuick 2.15
import QtQuick.Layouts 1.15

// Template card for the templates grid
Rectangle {
    id: root
    Layout.fillHeight: true
    implicitHeight: cardCol.implicitHeight + theme.sp5 * 2
    radius: theme.radiusLG
    color: hovered ? theme.surface2 : theme.surface1


    required property var theme
    required property var modelData
    required property int index
    property bool hovered: false

    ColumnLayout {
        id: cardCol
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: theme.sp5 }
        spacing: theme.sp3

        // Header row
        RowLayout {
            spacing: theme.sp3

            Rectangle {
                width: 40; height: 40; radius: theme.radiusMD
                color: modelData.is_builtin ? theme.accentSoft : theme.surface3
                Text { anchors.centerIn: parent; text: modelData.is_builtin ? "📋" : "📁"; font.pixelSize: 20 }
            }

            ColumnLayout {
                spacing: 2; Layout.fillWidth: true
                Text {
                    text: modelData.display_name
                    font.pixelSize: theme.fontSizeXL; font.weight: Font.DemiBold; color: theme.textPrimary
                    elide: Text.ElideRight; Layout.fillWidth: true
                }
                Text {
                    text: modelData.name
                    font.pixelSize: theme.fontSizeSM; color: theme.textTertiary
                    font.family: "JetBrains Mono, Consolas, monospace"
                }
            }
        }

        // Badges
        RowLayout {
            spacing: theme.sp2
            SmallBadge { theme: root.theme; label: modelData.is_builtin ? "Вбудований" : "Користувацький"; variant: modelData.is_builtin ? "accent" : "neutral" }
            SmallBadge {
                theme: root.theme
                label: modelData.has_instructions ? "✓ Інструкції" : "⚠ Без інструкцій"
                variant: modelData.has_instructions ? "success" : "warning"
            }
        }

        // Description
        Text {
            text: modelData.description || ""
            font.pixelSize: theme.fontSizeMD; color: theme.textSecondary
            wrapMode: Text.Wrap; Layout.fillWidth: true
        }

        Item { Layout.fillHeight: true }

        // Warning banner for no instructions
        Rectangle {
            visible: !modelData.has_instructions
            Layout.fillWidth: true
            height: warnText.implicitHeight + theme.sp2 * 2
            radius: theme.radiusMD; color: theme.warningSoft; border.color: "#FDE68A"; border.width: 1

            Text {
                id: warnText
                anchors { left: parent.left; right: parent.right; top: parent.top; margins: theme.sp2 }
                text: "Для цього шаблону не налаштовано інструкцій AI. Точність може бути неочікуваною."
                font.pixelSize: theme.fontSizeSM; color: "#92400E"; wrapMode: Text.Wrap
            }
        }

        // Actions
        RowLayout {
            spacing: theme.sp2
            AppButton { theme: root.theme; label: "Використати"; variant: "primary"; height: 34 }
            AppButton { theme: root.theme; label: "Інструкції"; variant: "secondary"; height: 34 }
            AppButton { theme: root.theme; label: "Редагувати"; variant: "ghost"; height: 34; visible: !modelData.is_builtin }
        }
    }

    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        onEntered: root.hovered = true
        onExited:  root.hovered = false
    }

    Rectangle {
        anchors.fill: parent
        color: "transparent"
        radius: parent.radius
        border.color: root.hovered ? theme.accent : theme.borderSubtle
        border.width: 1
        Behavior on border.color { ColorAnimation { duration: 120 } }
    }

    Behavior on color { ColorAnimation { duration: 120 } }
}
