import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

// Screen 1 — Sessions list
Rectangle {
    id: root
    color: theme.surfaceBase

    required property var theme
    signal navigate(string screen)

    property var sessionsData: []
    property string filterStatus: "all"
    property string searchText: ""

    function loadSessions() {
        var raw = bridge.getSessionsFiltered(filterStatus)
        var all = JSON.parse(raw)
        if (searchText.length > 0) {
            all = all.filter(function(s) {
                return s.name.toLowerCase().indexOf(searchText.toLowerCase()) >= 0
            })
        }
        sessionsData = all
    }

    Component.onCompleted: loadSessions()

    Connections {
        target: bridge
        function onSessionsChanged() { root.loadSessions() }
    }

    Flickable {
        id: scrollView
        anchors.fill: parent
        contentWidth: width
        contentHeight: contentCol.implicitHeight
        boundsBehavior: Flickable.StopAtBounds
        clip: true
        interactive: false
        
        WheelHandler {
            orientation: Qt.Vertical
            onWheel: (event) => {
                var speedMultiplier = 0.5;
                if (event.pixelDelta.y !== 0) {
                    smoothScrollAnim.stop();
                    var delta = event.pixelDelta.y * speedMultiplier;
                    scrollView.contentY = Math.max(0, Math.min(scrollView.contentHeight - scrollView.height, scrollView.contentY - delta));
                } else {
                    var current = smoothScrollAnim.running ? smoothScrollAnim.to : scrollView.contentY;
                    var step = event.angleDelta.y * speedMultiplier;
                    var newY = Math.max(0, Math.min(scrollView.contentHeight - scrollView.height, current - step));
                    if (newY !== current) {
                        smoothScrollAnim.to = newY;
                        smoothScrollAnim.restart();
                    }
                }
            }
        }

        ScrollBar.vertical: ScrollBar { id: vbar; policy: ScrollBar.AsNeeded }

        NumberAnimation {
            id: smoothScrollAnim
            target: scrollView
            property: "contentY"
            duration: 400
            easing.type: Easing.OutCubic
        }

        ColumnLayout {
            id: contentCol
            width: scrollView.width
            spacing: 0

            // Page content
            Item {
                Layout.fillWidth: true
                Layout.preferredHeight: mainCol.implicitHeight + theme.sp8 * 2
                Layout.minimumHeight: mainCol.implicitHeight + theme.sp8 * 2

                ColumnLayout {
                    id: mainCol
                    anchors {
                        top: parent.top; topMargin: theme.sp8
                        left: parent.left; leftMargin: theme.sp10
                        right: parent.right; rightMargin: theme.sp10
                    }
                    spacing: theme.sp4

                    // Header row
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: theme.sp4

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2

                            Text {
                                text: "Усі запуски генерації документів"
                                font.pixelSize: theme.fontSizeLG
                                color: theme.textSecondary
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                            }
                        }

                        Item { Layout.fillWidth: true }

                        // New session button
                        Rectangle {
                            implicitWidth: btnRow.implicitWidth + 32
                            implicitHeight: 40
                            radius: theme.radiusMD
                            color: hovered ? theme.accentHover : theme.accent
                            property bool hovered: false

                            Row {
                                id: btnRow
                                anchors.centerIn: parent
                                spacing: theme.sp2
                                Text {
                                    text: "+"
                                    font.pixelSize: 18; font.weight: Font.Medium
                                    color: "white"
                                    anchors.verticalCenter: parent.verticalCenter
                                    // Adjust visually if the + sits too high in the font
                                    anchors.verticalCenterOffset: -1
                                }
                                Text {
                                    id: btnText
                                    text: "Новий документ"
                                    font.pixelSize: theme.fontSizeMD
                                    font.weight: Font.Bold
                                    color: "white"
                                    anchors.verticalCenter: parent.verticalCenter
                                }
                            }

                            MouseArea {
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onEntered: parent.hovered = true
                                onExited:  parent.hovered = false
                                onClicked: {
                                    if (typeof ApplicationWindow.window.clearSessionPayload === "function") {
                                        ApplicationWindow.window.clearSessionPayload()
                                    }
                                    root.navigate("new_document")
                                }
                            }

                            Behavior on color { ColorAnimation { duration: 120 } }
                        }
                    }

                    // Toolbar: search + filters
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: theme.sp3

                        // Search input
                        Rectangle {
                            width: 300; height: 36
                            radius: theme.radiusMD
                            color: theme.surface1
                            border.color: searchField.activeFocus ? theme.accent : theme.borderStrong
                            border.width: searchField.activeFocus ? 2 : 1

                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: theme.sp2
                                anchors.rightMargin: theme.sp2
                                spacing: theme.sp1

                                Text {
                                    text: "🔍"
                                    font.pixelSize: 12
                                    color: theme.textTertiary
                                }

                                TextInput {
                                    id: searchField
                                    Layout.fillWidth: true
                                    font.pixelSize: theme.fontSizeMD
                                    color: theme.textPrimary
                                    clip: true
                                    verticalAlignment: TextInput.AlignVCenter
                                    onTextChanged: {
                                        root.searchText = text
                                        root.loadSessions()
                                    }

                                    Text {
                                        text: "Пошук за назвою…"
                                        color: theme.textTertiary
                                        font.pixelSize: theme.fontSizeMD
                                        visible: !searchField.text && !searchField.activeFocus
                                        anchors.verticalCenter: parent.verticalCenter
                                    }
                                }
                            }
                        }

                        // Filter chips
                        FilterChip { theme: root.theme; label: "Усі"; active: root.filterStatus === "all"; onClicked: { root.filterStatus = "all"; root.loadSessions() } }
                        FilterChip { theme: root.theme; label: "Завершені"; active: root.filterStatus === "completed"; onClicked: { root.filterStatus = "completed"; root.loadSessions() } }
                        FilterChip { theme: root.theme; label: "Помилки"; active: root.filterStatus === "failed"; onClicked: { root.filterStatus = "failed"; root.loadSessions() } }
                        FilterChip { theme: root.theme; label: "Чернетки"; active: root.filterStatus === "draft"; onClicked: { root.filterStatus = "draft"; root.loadSessions() } }

                        Item { Layout.fillWidth: true }
                    }

                    // Table card
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: tableContent.implicitHeight
                        radius: theme.radiusLG
                        color: theme.surface1
                        border.color: theme.borderSubtle
                        border.width: 1
                        clip: true

                        // Drop shadow
                        layer.enabled: true
                        layer.effect: null

                        ColumnLayout {
                            id: tableContent
                            anchors { left: parent.left; right: parent.right; top: parent.top }
                            spacing: 0

                            // Table header
                            Rectangle {
                                Layout.fillWidth: true
                                height: 40
                                color: theme.surface2
                                radius: theme.radiusLG

                                // Only round top corners
                                Rectangle {
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    anchors.bottom: parent.bottom
                                    height: parent.height / 2
                                    color: parent.color
                                }

                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: theme.sp5
                                    anchors.rightMargin: theme.sp5
                                    spacing: theme.sp4

                                    TableHeaderCell { theme: root.theme; label: "Статус";   width: 110 }
                                    TableHeaderCell { theme: root.theme; label: "Назва";     Layout.fillWidth: true }
                                    TableHeaderCell { theme: root.theme; label: "Шаблон";   width: 120 }
                                    TableHeaderCell { theme: root.theme; label: "Складність"; width: 110 }
                                    TableHeaderCell { theme: root.theme; label: "Час";       width: 70 }
                                    Item { width: 36 }
                                }

                                Rectangle {
                                    anchors.bottom: parent.bottom
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    height: 1
                                    color: theme.borderSubtle
                                }
                            }

                            // Table rows
                            Repeater {
                                model: root.sessionsData
                                delegate: DocumentTableRow {
                                    theme: root.theme
                                    
                                    Layout.fillWidth: true
                                    isLast: index === root.sessionsData.length - 1
                                    onOpenSession: root.navigate("result")
                                    onDuplicateSession: {
                                        bridge.duplicateSession(modelData.id)
                                    }
                                    onDeleteSession: {
                                        bridge.deleteSession(modelData.id)
                                    }
                                }
                            }

                            // Empty state
                            Rectangle {
                                visible: root.sessionsData.length === 0
                                Layout.fillWidth: true
                                height: 120
                                color: "transparent"

                                ColumnLayout {
                                    anchors.centerIn: parent
                                    spacing: theme.sp2
                                    Text {
                                        Layout.alignment: Qt.AlignHCenter
                                        text: "📭"
                                        font.pixelSize: 32
                                    }
                                    Text {
                                        Layout.alignment: Qt.AlignHCenter
                                        text: "Сесій не знайдено"
                                        font.pixelSize: theme.fontSizeLG
                                        font.weight: Font.Medium
                                        color: theme.textSecondary
                                    }
                                    Text {
                                        Layout.alignment: Qt.AlignHCenter
                                        text: "Змініть фільтр або створіть нову сесію"
                                        font.pixelSize: theme.fontSizeMD
                                        color: theme.textTertiary
                                    }
                                }
                            }

                            // Pagination footer
                            Rectangle {
                                Layout.fillWidth: true
                                height: 44
                                color: theme.surface2
                                radius: theme.radiusLG

                                // Only round bottom corners
                                Rectangle {
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    anchors.top: parent.top
                                    height: parent.height / 2
                                    color: parent.color
                                }

                                Rectangle {
                                    anchors.top: parent.top
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    height: 1
                                    color: theme.borderSubtle
                                }

                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: theme.sp5
                                    anchors.rightMargin: theme.sp5

                                    Text {
                                        text: "Показано " + root.sessionsData.length + " сесій"
                                        font.pixelSize: theme.fontSizeSM
                                        color: theme.textSecondary
                                    }
                                    Item { Layout.fillWidth: true }
                                    Text {
                                        text: "Сторінка 1 / 1"
                                        font.pixelSize: theme.fontSizeSM
                                        color: theme.textTertiary
                                    }
                                }
                            }
                        }
                    }

                    Item { height: theme.sp8 }

                    // Library Section
                    RowLayout {
                        Layout.fillWidth: true
                        Layout.topMargin: theme.sp4
                        ColumnLayout {
                            spacing: 2
                            Text { text: "Бібліотека завантажених файлів"; font.pixelSize: theme.fontSizeH2; font.weight: 600; color: theme.textPrimary }
                            Text { text: "Усі файли, коли-небудь завантажені користувачем"; font.pixelSize: theme.fontSizeMD; color: theme.textSecondary }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: Math.max(100, libraryCol.implicitHeight)
                        radius: theme.radiusLG
                        color: theme.surface1
                        border.color: theme.borderSubtle; border.width: 1
                        clip: true

                        property var libraryFiles: []

                        function loadLibrary() {
                            libraryFiles = JSON.parse(bridge.getLibraryFiles())
                        }

                        Component.onCompleted: loadLibrary()

                        ColumnLayout {
                            id: libraryCol
                            anchors { left: parent.left; right: parent.right; top: parent.top }
                            spacing: 0

                            Rectangle {
                                Layout.fillWidth: true; height: 40
                                color: theme.surface2; radius: theme.radiusLG

                                Rectangle { anchors.bottom: parent.bottom; anchors.left: parent.left; anchors.right: parent.right; height: parent.height / 2; color: parent.color }
                                Rectangle { anchors.bottom: parent.bottom; anchors.left: parent.left; anchors.right: parent.right; height: 1; color: theme.borderSubtle }

                                RowLayout {
                                    anchors.fill: parent; anchors.leftMargin: theme.sp5; anchors.rightMargin: theme.sp5
                                    spacing: theme.sp4
                                    TableHeaderCell { theme: root.theme; label: "Оригінальна назва"; Layout.fillWidth: true }
                                    TableHeaderCell { theme: root.theme; label: "Розмір (Б)"; width: 100 }
                                    TableHeaderCell { theme: root.theme; label: "Дата"; width: 100 }
                                    Item { width: 140 }
                                }
                            }

                            Repeater {
                                model: parent.parent.libraryFiles
                                delegate: Rectangle {
                                    Layout.fillWidth: true
                                    height: 52
                                    color: hovered ? theme.surface2 : theme.surface1
                                    property bool hovered: false

                                    Rectangle { visible: index < parent.parent.libraryFiles.length - 1; anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.borderSubtle }

                                    RowLayout {
                                        anchors { fill: parent; leftMargin: theme.sp5; rightMargin: theme.sp5 }
                                        spacing: theme.sp4

                                        Text {
                                            text: modelData.original_name
                                            font.pixelSize: theme.fontSizeMD; font.weight: Font.Medium; color: theme.textPrimary
                                            Layout.fillWidth: true; elide: Text.ElideRight
                                        }

                                        Text {
                                            text: modelData.file_size_bytes.toString()
                                            font.pixelSize: theme.fontSizeMD; color: theme.textSecondary
                                            width: 100
                                        }

                                        Text {
                                            text: modelData.created_at
                                            font.pixelSize: theme.fontSizeSM; color: theme.textTertiary
                                            width: 100
                                        }

                                        RowLayout {
                                            width: 140
                                            spacing: theme.sp1
                                            AppButton { 
                                                theme: root.theme; label: "Завантажити"; variant: "ghost"; height: 30; 
                                                onClicked: bridge.downloadFile(modelData.path) 
                                            }
                                        }
                                    }

                                    MouseArea {
                                        anchors.fill: parent; hoverEnabled: true
                                        onEntered: parent.hovered = true
                                        onExited: parent.hovered = false
                                    }
                                    Behavior on color { ColorAnimation { duration: 100 } }
                                }
                            }

                            Rectangle {
                                visible: parent.parent.libraryFiles.length === 0
                                Layout.fillWidth: true; height: 60
                                color: "transparent"
                                Text {
                                    anchors.centerIn: parent
                                    text: "Бібліотека порожня"
                                    font.pixelSize: theme.fontSizeLG; color: theme.textTertiary
                                }
                            }
                        }
                    }

                    Item { height: theme.sp8 }
                }
            }
        }
    }
}
