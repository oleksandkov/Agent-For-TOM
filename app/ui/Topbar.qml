import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

// Top application bar
Rectangle {
    id: root
    height: 56
    color: theme.surface1

    required property var theme
    required property string currentScreen
    signal navigate(string screen)
    signal toggleThemeRequested()
    signal toggleBigFontRequested()

    // Bottom border
    Rectangle {
        anchors.bottom: parent.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        height: 1
        color: theme.borderSubtle
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: theme.sp6
        anchors.rightMargin: theme.sp6
        spacing: theme.sp4

        // Breadcrumb
        RowLayout {
            spacing: 4
            Text {
                text: breadcrumbText()
                font.pixelSize: theme.fontSizeMD
                color: theme.textSecondary
            }
        }

        Item { Layout.fillWidth: true }

        // Icon buttons on right
        TopbarIconBtn {
            theme: root.theme
            tooltip: "Збільшити текст"
            icon: "🔎"
            onClicked: root.toggleBigFontRequested()
        }

        TopbarIconBtn {
            theme: root.theme
            tooltip: "Тема"
            icon: Qt.colorEqual(theme.surface1, "#FFFFFF") ? "🌙" : "☀️"
            onClicked: root.toggleThemeRequested()
        }
    }

    function breadcrumbText() {
        switch(root.currentScreen) {
            case "documents":     return "Документи"
            case "new_document":  return "Документи  /  Створення"
            case "progress":     return "Документи  /  Pipeline"
            case "result":       return "Документи  /  Результат"
            case "templates":    return "Шаблони"
            case "instructions": return "Інструкції"
            case "settings":     return "Налаштування"
            default: return ""
        }
    }
}
