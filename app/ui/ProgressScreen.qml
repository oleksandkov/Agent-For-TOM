import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

// Screen 3 тАФ Pipeline progress
Rectangle {
    id: root
    color: theme.surfaceBase

    required property var theme
    signal navigate(string screen)

    // Pipeline state
    property int progressPct: 0
    property string currentStepName: "Очікування..."
    property bool isRunning: true
    property bool isFinished: false
    property bool hasError: false
    property string errorMessage: ""
    property var logLines: []
    property var stepStates: ["pending","pending","pending","pending","pending","pending"]

    // Names for the 6 pipeline steps
    readonly property var stepNames: [
        "Конвертація файлів",
        "Pass 1: Text LLM",
        "Валідація",
        "Генерація зображень",
        "Виконання + Compose",
        "PDF компіляція"
    ]
    readonly property var stepDetails: [
        "PDF/DOCX → текст, дедуплікація SHA-256",
        "5 вхідних елементів → filled.py",
        "ast.parse + плейсхолдери + word count",
        "FLUX.1-schnell + matplotlib → PNG",
        "Заміна [[ANCHOR]] маркерів",
        "DOCX → PDF через reportlab"
    ]

    // Connect to bridge signals
    Connections {
        target: bridge

        function onPipelineStarted() {
            root.progressPct = 0
            root.isRunning = true
            root.isFinished = false
            root.hasError = false
            root.logLines = []
            root.stepStates = ["pending","pending","pending","pending","pending","pending"]
        }

        function onPipelineProgress(pct, stepName) {
            root.progressPct = pct
            root.currentStepName = stepName
        }

        function onPipelineStepActive(stepIndex) {
            var states = root.stepStates.slice()
            states[stepIndex] = "running"
            root.stepStates = states
        }

        function onPipelineStepDone(stepIndex, name, detail) {
            var states = root.stepStates.slice()
            states[stepIndex] = "done"
            root.stepStates = states
        }

        function onPipelineLog(timestamp, message) {
            var lines = root.logLines.slice()
            lines.push({time: timestamp, msg: message})
            if (lines.length > 50) lines = lines.slice(-50)
            root.logLines = lines
        }

        function onPipelineFinished(sessionId) {
            root.isRunning = false
            root.isFinished = true
            root.progressPct = 100
        }

        function onPipelineError(stage, message) {
            root.isRunning = false
            root.hasError = true
            root.errorMessage = "[" + stage + "] " + message
        }
    }

    ScrollView {
        anchors.fill: parent
        contentWidth: availableWidth
        contentHeight: contentCol.implicitHeight + theme.sp8 * 2
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ScrollBar.vertical.policy: ScrollBar.AlwaysOn

        ColumnLayout {
            id: contentCol
            y: theme.sp8
            anchors.horizontalCenter: parent.horizontalCenter
            width: Math.min(parent.width - theme.sp10 * 2, 640)
            spacing: theme.sp4

                // Status header
                ColumnLayout {
                    Layout.alignment: Qt.AlignHCenter
                    spacing: theme.sp4

                    // Spinning icon
                    Rectangle {
                        Layout.alignment: Qt.AlignHCenter
                        width: 56; height: 56
                        radius: 28
                        color: root.isFinished ? theme.successSoft : (root.hasError ? theme.dangerSoft : theme.accentSoft2)

                        Text {
                            anchors.centerIn: parent
                            text: root.isFinished ? "✓" : (root.hasError ? "✕" : "↻")
                            font.pixelSize: 24
                            font.weight: Font.DemiBold
                            color: root.isFinished ? theme.success : (root.hasError ? theme.danger : theme.accent)

                            RotationAnimation on rotation {
                                running: root.isRunning && !root.isFinished && !root.isError
                                loops: Animation.Infinite
                                from: 0; to: 360; duration: 1400
                            }
                        }
                    }

                    Text {
                        Layout.alignment: Qt.AlignHCenter
                        text: root.isFinished ? "Документ готовий!" : (root.hasError ? "Помилка!" : "Генерую документ…")
                        font.pixelSize: 28
                        font.weight: Font.DemiBold
                        color: theme.textPrimary
                        font.letterSpacing: -0.3
                    }

                    Text {
                        Layout.alignment: Qt.AlignHCenter
                        text: root.isFinished 
                            ? "AI успішно заповнив шаблон та згенерував DOCX і PDF"
                            : (root.hasError ? root.errorMessage : "AI заповнює шаблон, генерує зображення, компілює DOCX/PDF")
                        font.pixelSize: theme.fontSizeMD
                        color: root.hasError ? theme.danger : theme.textSecondary
                        wrapMode: Text.Wrap
                        horizontalAlignment: Text.AlignHCenter
                        Layout.fillWidth: true
                    }
                }

                // Progress bar
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: theme.sp2

                    Rectangle {
                        Layout.fillWidth: true
                        height: 8
                        radius: 4
                        color: theme.surface3

                        Rectangle {
                            id: progressFill
                            height: parent.height
                            radius: parent.radius
                            width: parent.width * root.progressPct / 100

                            gradient: Gradient {
                                orientation: Gradient.Horizontal
                                GradientStop { position: 0.0; color: theme.accent }
                                GradientStop { position: 1.0; color: "#14B8A6" }
                            }

                            // Shimmer effect when running
                            Rectangle {
                                id: shimmerEffect
                                visible: root.isRunning && !root.isFinished
                                anchors.fill: parent
                                radius: parent.radius
                                gradient: Gradient {
                                    orientation: Gradient.Horizontal
                                    GradientStop { position: 0.0; color: "transparent" }
                                    GradientStop { position: 0.5; color: Qt.rgba(1,1,1,0.35) }
                                    GradientStop { position: 1.0; color: "transparent" }
                                }

                                SequentialAnimation on x {
                                    running: root.isRunning && !root.isFinished
                                    loops: Animation.Infinite
                                    NumberAnimation { from: -shimmerEffect.width; to: shimmerEffect.width; duration: 1600 }
                                }
                            }

                            Behavior on width { NumberAnimation { duration: 400; easing.type: Easing.OutCubic } }
                        }
                    }

                    RowLayout {
                        Text {
                            text: root.progressPct + "%"
                            font.pixelSize: theme.fontSizeSM
                            font.weight: Font.DemiBold
                            color: theme.textPrimary
                            font.family: "JetBrains Mono, Consolas, monospace"
                        }
                        Item { Layout.fillWidth: true }
                        Text {
                            text: root.currentStepName
                            font.pixelSize: theme.fontSizeSM
                            color: theme.textSecondary
                        }
                    }
                }

                // Pipeline steps
                Rectangle {
                    Layout.fillWidth: true
                    height: stepsCol.implicitHeight + theme.sp2 * 2
                    radius: theme.radiusLG
                    color: theme.surface1
                    border.color: theme.borderSubtle
                    border.width: 1

                    ColumnLayout {
                        id: stepsCol
                        anchors {
                            left: parent.left; right: parent.right
                            top: parent.top; topMargin: theme.sp2
                            leftMargin: 0; rightMargin: 0
                        }
                        spacing: 0

                        Repeater {
                            model: 6
                            delegate: PipelineStepRow {
                                theme: root.theme
                                stepIndex: index
                                stepName: root.stepNames[index]
                                stepDetail: root.stepDetails[index]
                                stepState: root.stepStates[index]
                                isLast: index === 5
                                Layout.fillWidth: true
                            }
                        }
                    }
                }

                // Live log
                Rectangle {
                    Layout.fillWidth: true
                    height: logHeader.height + logBody.height
                    radius: theme.radiusLG
                    color: theme.surface1
                    border.color: theme.borderSubtle
                    border.width: 1
                    clip: true

                    ColumnLayout {
                        anchors { left: parent.left; right: parent.right; top: parent.top }
                        spacing: 0

                        // Log header
                        Rectangle {
                            id: logHeader
                            Layout.fillWidth: true
                            height: 40
                            color: theme.surface2
                            radius: theme.radiusLG

                            Rectangle {
                                anchors.bottom: parent.bottom; anchors.left: parent.left; anchors.right: parent.right
                                height: parent.height / 2; color: parent.color
                            }
                            Rectangle {
                                anchors.bottom: parent.bottom; anchors.left: parent.left; anchors.right: parent.right
                                height: 1; color: theme.borderSubtle
                            }

                            RowLayout {
                                anchors.fill: parent; anchors.margins: theme.sp4
                                RowLayout {
                                    spacing: theme.sp2
                                    Rectangle {
                                        width: 6; height: 6; radius: 3; color: theme.success
                                        SequentialAnimation on opacity {
                                            running: root.isRunning; loops: Animation.Infinite
                                            NumberAnimation { to: 0.4; duration: 700 }
                                            NumberAnimation { to: 1.0; duration: 700 }
                                        }
                                    }
                                    Text { text: "Live log"; font.pixelSize: theme.fontSizeMD; font.weight: Font.Medium; color: theme.textPrimary }
                                }
                                Item { Layout.fillWidth: true }
                                Text { text: root.logLines.length + " рядків"; font.pixelSize: theme.fontSizeSM; color: theme.textTertiary }
                            }
                        }

                        // Log body
                        Rectangle {
                            id: logBody
                            Layout.fillWidth: true
                            height: 180
                            color: theme.surfaceBase

                            ListView {
                                id: logView
                                anchors.fill: parent
                                anchors.margins: theme.sp4
                                model: root.logLines
                                clip: true
                                spacing: 2
                                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AlwaysOn }
                                onCountChanged: Qt.callLater(function() {
                                    positionViewAtEnd()
                                })

                                delegate: RowLayout {
                                    width: logView.width
                                    spacing: theme.sp4
                                    Text {
                                        text: modelData.time
                                        font.pixelSize: theme.fontSizeXS
                                        color: theme.textTertiary
                                        font.family: "JetBrains Mono, Consolas, monospace"
                                    }
                                    Text {
                                        text: modelData.msg
                                        font.pixelSize: theme.fontSizeXS
                                        color: modelData.msg.indexOf("[done]") >= 0 ? theme.success
                                             : (modelData.msg.indexOf("[llm]") >= 0 || modelData.msg.indexOf("[image]") >= 0 ? theme.accent
                                             : theme.textSecondary)
                                        font.family: "JetBrains Mono, Consolas, monospace"
                                        Layout.fillWidth: true
                                        wrapMode: Text.Wrap
                                    }
                                }
                            }
                        }
                    }
                }

                // Action buttons
                RowLayout {
                    Layout.alignment: Qt.AlignHCenter
                    spacing: theme.sp3

                    AppButton {
                        theme: root.theme
                        label: "Скасувати"
                        variant: "danger"
                        visible: root.isRunning
                        onClicked: {
                            bridge.cancelPipeline()
                            root.navigate("documents")
                        }
                    }

                    AppButton {
                        theme: root.theme
                        label: "← Назад до документів"
                        variant: "secondary"
                        visible: !root.isRunning
                        onClicked: root.navigate("documents")
                    }

                    AppButton {
                        theme: root.theme
                        label: "Переглянути результат →"
                        variant: "primary"
                        visible: root.isFinished
                        onClicked: root.navigate("result")
                    }
                }

                Item { height: theme.sp8 }
            }
    }
}
