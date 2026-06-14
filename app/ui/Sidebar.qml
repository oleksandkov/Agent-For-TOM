import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

// Left sidebar navigation
Rectangle {
    id: root
    color: theme.surface2

    // Right border line
    Rectangle {
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        width: 1
        color: theme.borderSubtle
    }

    required property var theme
    required property string currentScreen
    property bool isCollapsed: false
    signal navigate(string screen)

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 0
        spacing: 0

        // Brand / Logo area
        Rectangle {
            Layout.fillWidth: true
            height: 56
            color: theme.surface1

            Rectangle {
                anchors.bottom: parent.bottom
                anchors.left: parent.left
                anchors.right: parent.right
                height: 1
                color: theme.borderSubtle
            }

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: theme.sp4
                anchors.rightMargin: theme.sp4
                spacing: theme.sp2

                // Logo badge
                Image {
                    Layout.preferredWidth: 32
                    Layout.preferredHeight: 32
                    sourceSize.width: 64
                    sourceSize.height: 64
                    source: "../assets/img/logo.png"
                    fillMode: Image.PreserveAspectFit
                    mipmap: true
                }
                Text {
                    text: "Agent-For-TOM"
                    font.pixelSize: theme.fontSizeLG
                    font.weight: Font.DemiBold
                    color: theme.textPrimary
                    Layout.fillWidth: true
                    visible: !root.isCollapsed
                    clip: true
                }
            }
        }

        // Nav items
        ColumnLayout {
            Layout.fillWidth: true
            Layout.topMargin: theme.sp3
            Layout.leftMargin: theme.sp3
            Layout.rightMargin: theme.sp3
            spacing: 2

            NavItem {
                theme: root.theme
                label: "Документи"
                screen: "documents"
                icon: "🗂️"
                isCollapsed: root.isCollapsed
                currentScreen: root.currentScreen
                onNavigate: (s) => root.navigate(s)
            }
            NavItem {
                theme: root.theme
                label: "Створити"
                screen: "new_document"
                icon: "➕"
                isCollapsed: root.isCollapsed
                currentScreen: root.currentScreen
                onNavigate: (s) => root.navigate(s)
            }
            NavItem {
                theme: root.theme
                label: "Сховище"
                screen: "storage"
                icon: "📦"
                isCollapsed: root.isCollapsed
                currentScreen: root.currentScreen
                onNavigate: (s) => root.navigate(s)
            }
            NavItem {
                theme: root.theme
                label: "Шаблони"
                screen: "templates"
                count: "5"
                icon: "💾"
                isCollapsed: root.isCollapsed
                currentScreen: root.currentScreen
                onNavigate: (s) => root.navigate(s)
            }
            NavItem {
                theme: root.theme
                label: "Інструкції"
                screen: "instructions"
                icon: "📝"
                isCollapsed: root.isCollapsed
                currentScreen: root.currentScreen
                onNavigate: (s) => root.navigate(s)
            }

            // Divider
            Rectangle {
                Layout.fillWidth: true
                height: 1
                color: Qt.colorEqual(theme.surface1, "#1E293B") ? theme.borderStrong : theme.borderSubtle
                Layout.topMargin: theme.sp2
                Layout.bottomMargin: theme.sp2
            }

            NavItem {
                theme: root.theme
                label: "Налаштування"
                screen: "settings"
                icon: "⚙️"
                isCollapsed: root.isCollapsed
                currentScreen: root.currentScreen
                onNavigate: (s) => root.navigate(s)
            }
            NavItem {
                theme: root.theme
                label: "Про нас"
                screen: "about"
                icon: "ℹ️"
                isCollapsed: root.isCollapsed
                currentScreen: root.currentScreen
                onNavigate: (s) => root.navigate(s)
            }
            NavItem {
                theme: root.theme
                label: "Повідомити<br>про баг"
                screen: "report"
                icon: "🐞"
                isCollapsed: root.isCollapsed
                currentScreen: root.currentScreen
                onNavigate: { bugModal.open() }
            }
        }

        Popup {
            id: bugModal
            width: Math.min(400, parent.width - 40)
            height: Math.min(300, parent.height - 40)
            parent: Overlay.overlay
            anchors.centerIn: parent
            modal: true
            focus: true
            closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
            Overlay.modal: Rectangle {
                color: "#80000000"
            }
            background: Rectangle {
                radius: theme.radiusLG; color: theme.surface1
                border.color: theme.borderSubtle; border.width: 1
            }
            contentItem: ColumnLayout {
                spacing: theme.sp4
                Text { text: "Звітувати про баг"; font.pixelSize: theme.fontSizeXL; font.weight: Font.DemiBold; color: theme.textPrimary }
                TextArea {
                    Layout.fillWidth: true; Layout.fillHeight: true
                    placeholderText: "Опишіть проблему тут..."
                    font.pixelSize: theme.fontSizeMD
                    wrapMode: Text.Wrap
                }
                RowLayout {
                    Layout.alignment: Qt.AlignRight
                    AppButton { theme: root.theme; label: "Скасувати"; variant: "ghost"; onClicked: bugModal.close() }
                    AppButton { theme: root.theme; label: "Відправити"; variant: "primary"; onClicked: bugModal.close() }
                }
            }
        }

        Item { Layout.fillHeight: true }

        // ── Collapse / Expand toggle button ──────────────────────────────────
        Item {
            Layout.fillWidth: true
            height: 52

            Rectangle {
                anchors.top: parent.top
                anchors.left: parent.left
                anchors.right: parent.right
                height: 1
                color: theme.borderSubtle
            }

            // Centered rounded-rectangle button
            Rectangle {
                id: collapseBtn
                anchors.centerIn: parent
                width: root.isCollapsed ? 40 : 130
                height: 32
                radius: 8
                color: collapseBtn.collapseBtnHover ? theme.accentSoft2 : theme.surface1
                border.color: collapseBtn.collapseBtnHover ? theme.accent : theme.borderSubtle
                border.width: 1
                property bool collapseBtnHover: false

                Behavior on width { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
                Behavior on color { ColorAnimation { duration: 150 } }

                RowLayout {
                    anchors.centerIn: parent
                    spacing: 4

                    Canvas {
                        width: 10
                        height: 16
                        Layout.alignment: Qt.AlignCenter
                        property color strokeColor: collapseBtn.collapseBtnHover ? theme.accent : theme.textSecondary
                        Behavior on strokeColor { ColorAnimation { duration: 150 } }
                        
                        onStrokeColorChanged: requestPaint()
                        property bool collapsed: root.isCollapsed
                        onCollapsedChanged: requestPaint()

                        onPaint: {
                            var ctx = getContext("2d")
                            ctx.reset()
                            ctx.lineWidth = 2
                            ctx.strokeStyle = strokeColor
                            ctx.lineJoin = "round"
                            ctx.lineCap = "round"
                            ctx.beginPath()
                            if (collapsed) {
                                ctx.moveTo(2, 2)
                                ctx.lineTo(8, 8)
                                ctx.lineTo(2, 14)
                            } else {
                                ctx.moveTo(8, 2)
                                ctx.lineTo(2, 8)
                                ctx.lineTo(8, 14)
                            }
                            ctx.stroke()
                        }
                    }

                    Text {
                        visible: !root.isCollapsed
                        text: "Згорнути"
                        font.pixelSize: theme.fontSizeSM
                        font.weight: Font.Medium
                        color: collapseBtn.collapseBtnHover ? theme.accent : theme.textSecondary
                        Behavior on color { ColorAnimation { duration: 150 } }
                    }
                }

                MouseArea {
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onEntered: parent.collapseBtnHover = true
                    onExited: parent.collapseBtnHover = false
                    onClicked: {
                        root.isCollapsed = !root.isCollapsed
                        bridge.isSidebarCollapsed = root.isCollapsed
                    }
                }
            }
        }
    }
}
