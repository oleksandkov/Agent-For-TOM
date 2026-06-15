import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtMultimedia

// Screen 7 — Settings
Rectangle {
    id: root
    color: theme.surfaceBase

    required property var theme
    signal navigate(string screen)

    property bool scrollToInstructions: false

    Component.onCompleted: {
        hfTokenField.text = bridge.getHfToken()
        userStyleArea.text = bridge.getUserStyle()
        cacheTtlInput.text = bridge.getCacheTtlDays().toString()
        sessionRetentionInput.text = bridge.getSessionRetentionDays().toString()
        if (scrollToInstructions) scrollTimer.start()
    }

    onScrollToInstructionsChanged: {
        if (scrollToInstructions) scrollTimer.start()
    }

    NumberAnimation {
        id: smoothScrollAnim
        target: scrollView
        property: "contentY"
        duration: 800
        easing.type: Easing.InOutQuad
    }

    Timer {
        id: scrollTimer
        interval: 1000 // 1 second delay as requested
        onTriggered: {
            // Safely map coordinates relative to the flickable's content item
            var pos = instructionAccordion.mapToItem(scrollView.contentItem, 0, 0)
            var targetY = pos.y - theme.sp4
            var maxScroll = Math.max(0, scrollView.contentHeight - scrollView.height)
            smoothScrollAnim.to = Math.min(targetY, maxScroll)
            smoothScrollAnim.restart()
            // Auto-expand the accordion when scrolling to it
            instructionAccordion.expanded = true
        }
    }

    Flickable {
        id: scrollView
        anchors.fill: parent
        contentWidth: width
        contentHeight: mainCol.implicitHeight + 160
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        interactive: false

        WheelHandler {
            orientation: Qt.Vertical
            onWheel: (event) => {
                var speedMultiplier = 0.5;
                if (event.pixelDelta.y !== 0) {
                    wheelScrollAnim.stop();
                    smoothScrollAnim.stop();
                    var delta = event.pixelDelta.y * speedMultiplier;
                    scrollView.contentY = Math.max(0, Math.min(scrollView.contentHeight - scrollView.height, scrollView.contentY - delta));
                } else {
                    var current = wheelScrollAnim.running ? wheelScrollAnim.to : scrollView.contentY;
                    var step = event.angleDelta.y * speedMultiplier;
                    var newY = Math.max(0, Math.min(scrollView.contentHeight - scrollView.height, current - step));
                    if (newY !== current) {
                        smoothScrollAnim.stop();
                        wheelScrollAnim.to = newY;
                        wheelScrollAnim.restart();
                    }
                }
            }
        }

        NumberAnimation {
            id: wheelScrollAnim
            target: scrollView
            property: "contentY"
            duration: 400
            easing.type: Easing.OutCubic
        }

        ScrollBar.vertical: ScrollBar {}

        Item {
            width: scrollView.width
            height: mainCol.implicitHeight + 160

            MouseArea {
                anchors.fill: parent
                z: -1
                onClicked: mainRootLayout.forceActiveFocus()
            }

            ColumnLayout {
                id: mainCol
                anchors {
                    top: parent.top; topMargin: theme.sp8
                    horizontalCenter: parent.horizontalCenter
                }
                width: Math.min(parent.width - theme.sp10 * 2, 680)
                spacing: theme.sp4

                Text { text: "Налаштування"; font.pixelSize: theme.fontSizeH1; font.weight: Font.DemiBold; color: theme.textPrimary; font.letterSpacing: -0.3 }

                // About section
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: aboutCol.implicitHeight + theme.sp4 * 2
                    radius: theme.radiusLG; color: theme.surface2
                    border.color: theme.borderSubtle; border.width: 1

                    ColumnLayout {
                        id: aboutCol
                        anchors { left: parent.left; right: parent.right; top: parent.top; margins: theme.sp4 }
                        spacing: theme.sp1
                        Text { text: "Agent-For-Labs"; font.pixelSize: theme.fontSizeXL; font.weight: Font.DemiBold; color: theme.textPrimary; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        Text { text: "Версія 1.0.0  ·  PyQt6 + QML"; font.pixelSize: theme.fontSizeMD; color: theme.textSecondary; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        Text { text: "2-pass pipeline  ·  SQLite  ·  3 cache levels"; font.pixelSize: theme.fontSizeSM; color: theme.textTertiary; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    }
                }

                // API Keys section
                SettingsSection {
                    theme: root.theme
                    title: "API Ключі"
                    Layout.fillWidth: true

                    ColumnLayout {
                        width: parent.width
                        spacing: theme.sp4

                        SettingsField {
                            id: hfTokenField
                            theme: root.theme
                            label: "Hugging Face"
                            placeholder: "hf_(34 символа)"
                            isPassword: true
                            maximumLength: 37
                            Layout.fillWidth: true
                        }

                        // ─── Accordion: Де взяти API ключі? ───────────────────
                Accordion {
                    id: instructionAccordion
                    theme: root.theme
                    title: "Де взяти API ключі?"
                    Layout.fillWidth: true
                    Layout.topMargin: theme.sp4

                    // Needed for scrollToInstructions feature
                    property alias instructionHeader: instructionAccordion

                    ColumnLayout {
                        width: parent.width
                        spacing: theme.sp4

                        Text {
                            text: "Нижче наведені детальні відео та текстові інструкції."
                            font.pixelSize: theme.fontSizeMD
                            color: theme.textSecondary
                        }

                SettingsSection {
                    theme: root.theme
                    title: "Відео інструкція"
                    Layout.fillWidth: true

                    ColumnLayout {
                        width: parent.width

                        // Video Player
                        Rectangle {
                            id: inlineContainer
                            property bool isScrubbing: false
                            property bool isDecodingSeek: false
                            property bool initialFrameLoaded: false
                    readonly property color primaryColor: root.theme ? root.theme.accent : "#3b82f6"
                    Layout.fillWidth: true
                    Layout.preferredHeight: width * (9/16) // 16:9 aspect ratio
                    color: "black"
                    radius: theme.radiusLG
                    clip: true

                    MediaPlayer {
                        id: player
                        source: bridge ? bridge.getVideoUrl() : ""
                        videoOutput: videoOutput
                        audioOutput: AudioOutput { id: audioOut }
                        
                        onMediaStatusChanged: {
                            if (mediaStatus === MediaPlayer.LoadedMedia && !inlineContainer.initialFrameLoaded) {
                                inlineContainer.initialFrameLoaded = true
                                audioOut.muted = true
                                player.play()
                                firstFrameTimer.start()
                            }
                        }
                    }
                    
                    Timer {
                        id: firstFrameTimer
                        interval: 400
                        onTriggered: {
                            player.pause()
                            player.position = 0
                            audioOut.muted = false
                        }
                    }

                    Item {
                        id: playerView
                        anchors.fill: parent
                        focus: true
                        Keys.onPressed: (event) => {
                            if (event.key === Qt.Key_Space) {
                                if (inlineContainer.isDecodingSeek) {
                                    inlineContainer.isDecodingSeek = false
                                    decodeWaitTimer.stop()
                                } else if (player.playbackState === MediaPlayer.PlayingState) {
                                    player.pause()
                                } else {
                                    player.play()
                                }
                                event.accepted = true
                            } else if (event.key === Qt.Key_F || event.key === Qt.Key_F11 || event.text.toLowerCase() === "f" || event.text.toLowerCase() === "а" || event.key === 1040 || event.key === 1072) {
                                if (fullscreenPopup.opened) fullscreenPopup.close()
                                else fullscreenPopup.open()
                                event.accepted = true
                            } else if (event.key === Qt.Key_Escape) {
                                if (fullscreenPopup.opened) {
                                    fullscreenPopup.close()
                                    event.accepted = true
                                }
                            } else if (event.key === Qt.Key_Left) {
                                var wasPlayingL = (player.playbackState === MediaPlayer.PlayingState) || inlineContainer.isDecodingSeek
                                audioOut.muted = true
                                seekMuteTimer.restart()
                                player.pause()
                                player.position = Math.max(0, player.position - 5000)
                                if (wasPlayingL) {
                                    inlineContainer.isDecodingSeek = true
                                    decodeWaitTimer.restart()
                                }
                                controlsTempTimer.restart()
                                event.accepted = true
                            } else if (event.key === Qt.Key_Right) {
                                var wasPlayingR = (player.playbackState === MediaPlayer.PlayingState) || inlineContainer.isDecodingSeek
                                audioOut.muted = true
                                seekMuteTimer.restart()
                                player.pause()
                                player.position = Math.min(player.duration, player.position + 5000)
                                if (wasPlayingR) {
                                    inlineContainer.isDecodingSeek = true
                                    decodeWaitTimer.restart()
                                }
                                controlsTempTimer.restart()
                                event.accepted = true
                            }
                        }

                        Timer {
                            id: decodeWaitTimer
                            interval: 500
                            onTriggered: {
                                inlineContainer.isDecodingSeek = false
                                player.play()
                            }
                        }

                        Timer {
                            id: seekMuteTimer
                            interval: 500
                            onTriggered: audioOut.muted = false
                        }

                        Timer {
                            id: controlsTempTimer
                            interval: 3000
                        }

                        HoverHandler {
                            id: playerHover
                        }

                        VideoOutput {
                            id: videoOutput
                            anchors.fill: parent
                        }

                        // Big Play Icon overlay
                        Rectangle {
                            id: bigPlayOverlay
                            z: 2
                            anchors.centerIn: parent
                            width: 64
                            height: 64
                            radius: 32
                            color: bigPlayMouseArea.containsMouse ? "#B3000000" : "#80000000"
                            scale: bigPlayMouseArea.containsMouse ? 1.1 : 1.0
                            Behavior on scale { NumberAnimation { duration: 150 } }
                            Behavior on color { ColorAnimation { duration: 150 } }
                            visible: player.playbackState !== MediaPlayer.PlayingState && !inlineContainer.isScrubbing && !inlineContainer.isDecodingSeek
                            property bool isFinished: player.duration > 0 && player.position >= player.duration - 200

                            Canvas {
                                width: 24
                                height: 28
                                anchors.centerIn: parent
                                anchors.horizontalCenterOffset: 4
                                visible: !parent.isFinished
                                onPaint: {
                                    var ctx = getContext("2d");
                                    ctx.reset();
                                    ctx.fillStyle = "white";
                                    ctx.beginPath();
                                    ctx.moveTo(0, 0);
                                    ctx.lineTo(24, 14);
                                    ctx.lineTo(0, 28);
                                    ctx.closePath();
                                    ctx.fill();
                                }
                            }

                            Canvas {
                                anchors.fill: parent
                                visible: parent.isFinished
                                onPaint: {
                                    var ctx = getContext("2d");
                                    ctx.reset();
                                    ctx.strokeStyle = "white";
                                    ctx.lineWidth = 3;
                                    ctx.lineCap = "round";
                                    ctx.beginPath();
                                    ctx.arc(32, 32, 12, Math.PI, -Math.PI/2, true);
                                    ctx.stroke();

                                    ctx.fillStyle = "white";
                                    ctx.beginPath();
                                    ctx.moveTo(36, 15);
                                    ctx.lineTo(28, 20);
                                    ctx.lineTo(36, 25);
                                    ctx.fill();
                                }
                            }

                            MouseArea {
                                id: bigPlayMouseArea
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: {
                                    playerView.forceActiveFocus()
                                    if (parent.isFinished) player.position = 0;
                                    player.play()
                                }
                            }

                        }

                        Canvas {
                            id: customSpinner
                            z: 3
                            anchors.centerIn: parent
                            width: 48; height: 48
                            visible: inlineContainer.isScrubbing || inlineContainer.isDecodingSeek
                            property real rotationAngle: 0
                            
                            NumberAnimation on rotationAngle {
                                from: 0; to: Math.PI * 2
                                duration: 1000
                                loops: Animation.Infinite
                                running: customSpinner.visible
                            }
                            
                            onRotationAngleChanged: requestPaint()
                            
                            onPaint: {
                                var ctx = getContext("2d");
                                ctx.reset();
                                ctx.lineWidth = 4;
                                ctx.lineCap = "round";
                                ctx.strokeStyle = "white";
                                ctx.beginPath();
                                ctx.arc(24, 24, 20, rotationAngle, rotationAngle + Math.PI * 1.5, false);
                                ctx.stroke();
                            }
                        }

                        // Bottom Controls
                        Rectangle {
                            id: controlsBar
                            anchors.bottom: parent.bottom
                            anchors.left: parent.left
                            anchors.right: parent.right
                            height: 48
                            color: "#AA000000"
                            opacity: (playerHover.hovered || player.playbackState !== MediaPlayer.PlayingState || controlsTempTimer.running) ? 1.0 : 0.0
                            Behavior on opacity { NumberAnimation { duration: 200 } }

                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 16
                                anchors.rightMargin: 16
                                spacing: 12

                                Item {
                                    width: 24
                                    height: 24

                                    // Play Triangle
                                    Canvas {
                                        width: 14; height: 16
                                        anchors.centerIn: parent
                                        anchors.horizontalCenterOffset: 2
                                        visible: player.playbackState !== MediaPlayer.PlayingState && !inlineContainer.isScrubbing && !inlineContainer.isDecodingSeek
                                        onPaint: {
                                            var ctx = getContext("2d");
                                            ctx.reset();
                                            ctx.fillStyle = "white";
                                            ctx.beginPath();
                                            ctx.moveTo(0, 0);
                                            ctx.lineTo(14, 8);
                                            ctx.lineTo(0, 16);
                                            ctx.closePath();
                                            ctx.fill();
                                        }
                                    }

                                    // Pause Bars
                                    Row {
                                        spacing: 4; anchors.centerIn: parent
                                        visible: player.playbackState === MediaPlayer.PlayingState || inlineContainer.isScrubbing || inlineContainer.isDecodingSeek
                                        Rectangle { width: 4; height: 14; color: "white" }
                                        Rectangle { width: 4; height: 14; color: "white" }
                                    }

                                    MouseArea {
                                        anchors.fill: parent
                                        // Removed negative margins to reduce hover area
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: {
                                            playerView.forceActiveFocus()
                                            if (inlineContainer.isDecodingSeek) {
                                                inlineContainer.isDecodingSeek = false
                                                decodeWaitTimer.stop()
                                            } else if (player.playbackState === MediaPlayer.PlayingState) {
                                                player.pause()
                                            } else {
                                                player.play()
                                            }
                                        }
                                    }
                                }

                                function formatTime(ms) {
                                    if (!ms || isNaN(ms)) return "0:00"
                                    var s = Math.floor(ms / 1000)
                                    var m = Math.floor(s / 60)
                                    s = s % 60
                                    return m + ":" + (s < 10 ? "0" + s : s)
                                }

                                Text {
                                    text: parent.formatTime(player.position) + " / " + parent.formatTime(player.duration)
                                    color: "white"
                                    font.pixelSize: 12
                                }

                                Slider {
                                    id: progressSlider
                                    Layout.fillWidth: true
                                    from: 0
                                    to: player.duration > 0 ? player.duration : 1
                                    
                                    Connections {
                                        target: player
                                        function onPositionChanged() {
                                            if (!progressSlider.pressed) {
                                                progressSlider.value = player.position
                                            }
                                        }
                                    }
                                    property bool wasPlaying: false
                                    onPressedChanged: {
                                        inlineContainer.isScrubbing = pressed
                                        if (pressed) {
                                            wasPlaying = (player.playbackState === MediaPlayer.PlayingState) || inlineContainer.isDecodingSeek
                                            player.pause()
                                        } else {
                                            audioOut.muted = true
                                            seekMuteTimer.restart()
                                            player.position = value
                                            if (wasPlaying) {
                                                inlineContainer.isDecodingSeek = true
                                                decodeWaitTimer.restart()
                                            }
                                        }
                                    }
                                    
                                    onMoved: {
                                        if (!scrubThrottleTimer.running) {
                                            audioOut.muted = true
                                            seekMuteTimer.restart()
                                            player.position = value
                                            scrubThrottleTimer.start()
                                        }
                                    }
                                    
                                    Timer {
                                        id: scrubThrottleTimer
                                        interval: 150
                                    }
                                    
                                    background: Rectangle {
                                        x: progressSlider.leftPadding
                                        y: progressSlider.topPadding + progressSlider.availableHeight / 2 - height / 2
                                        width: progressSlider.availableWidth
                                        height: 4
                                        radius: 2
                                        color: "#40ffffff"
                                        Rectangle {
                                            width: progressSlider.visualPosition * parent.width
                                            height: parent.height
                                            color: "white"
                                            radius: 2
                                        }
                                    }
                                    handle: Rectangle {
                                        x: progressSlider.leftPadding + progressSlider.visualPosition * (progressSlider.availableWidth - width)
                                        y: progressSlider.topPadding + progressSlider.availableHeight / 2 - height / 2
                                        width: 14
                                        height: 14
                                        radius: 7
                                        color: "white"
                                    }
                                }

                                Item {
                                    width: 24; height: 24
                                    Canvas {
                                        id: fsIconCanvas
                                        anchors.fill: parent
                                        onPaint: {
                                            var ctx = getContext("2d");
                                            ctx.reset();
                                            ctx.strokeStyle = "white";
                                            ctx.lineWidth = 3;
                                            ctx.lineCap = "round";
                                            ctx.lineJoin = "round";
                                            ctx.beginPath();
                                            
                                            if (!fullscreenPopup.opened) {
                                                // Expand icon
                                                ctx.moveTo(8, 2); ctx.lineTo(2, 2); ctx.lineTo(2, 8);
                                                ctx.moveTo(16, 2); ctx.lineTo(22, 2); ctx.lineTo(22, 8);
                                                ctx.moveTo(8, 22); ctx.lineTo(2, 22); ctx.lineTo(2, 16);
                                                ctx.moveTo(16, 22); ctx.lineTo(22, 22); ctx.lineTo(22, 16);
                                            } else {
                                                // Shrink icon
                                                ctx.moveTo(2, 8); ctx.lineTo(8, 8); ctx.lineTo(8, 2);
                                                ctx.moveTo(22, 8); ctx.lineTo(16, 8); ctx.lineTo(16, 2);
                                                ctx.moveTo(2, 16); ctx.lineTo(8, 16); ctx.lineTo(8, 22);
                                                ctx.moveTo(22, 16); ctx.lineTo(16, 16); ctx.lineTo(16, 22);
                                            }
                                            ctx.stroke();
                                        }
                                    }
                                    MouseArea {
                                        anchors.fill: parent
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: {
                                            playerView.forceActiveFocus()
                                            if (fullscreenPopup.opened) fullscreenPopup.close()
                                            else fullscreenPopup.open()
                                        }
                                    }
                                }
                            }
                        }

                        MouseArea {
                            id: videoMouseArea
                            anchors.fill: parent
                            anchors.bottomMargin: 48
                            hoverEnabled: true
                            onClicked: {
                                playerView.forceActiveFocus()
                                if (player.playbackState === MediaPlayer.PlayingState) player.pause()
                                else player.play()
                            }
                            onDoubleClicked: {
                                playerView.forceActiveFocus()
                                if (fullscreenPopup.opened) fullscreenPopup.close()
                                else fullscreenPopup.open()
                            }
                        }
                    }
                }

                // Timecodes
                    Text { 
                        text: "Таймкоди"
                        font.pixelSize: theme.fontSizeLG * 1.1
                        font.weight: Font.DemiBold
                        color: theme.textPrimary
                        Layout.topMargin: theme.sp4
                    }
                    Text {
                        text: "<a href='0' style='text-decoration:none;'><font color='" + String(theme.accent) + "'><b>0:00</b></font></a> — Крок 1 (Реєстрація на сервісі Hugging Face)<br>" +
                              "<a href='60000' style='text-decoration:none;'><font color='" + String(theme.accent) + "'><b>1:00</b></font></a> — Крок 2 (Створення API ключа)<br>" +
                              "<a href='89000' style='text-decoration:none;'><font color='" + String(theme.accent) + "'><b>1:29</b></font></a> — Крок 3 (Підключення API ключа до застосунку)"
                        Layout.fillWidth: true
                        textFormat: Text.RichText
                        font.pixelSize: theme.fontSizeMD
                        color: theme.textSecondary
                        linkColor: theme.accent
                        
                        HoverHandler {
                            cursorShape: parent.hoveredLink ? Qt.PointingHandCursor : Qt.ArrowCursor
                        }
                        onLinkActivated: (link) => {
                            var wasPlaying = (player.playbackState === MediaPlayer.PlayingState)
                            player.position = parseInt(link)
                            if (wasPlaying) {
                                player.play()
                            } else {
                                player.pause()
                            }
                        }
                    }
                }
            }

                Item { height: theme.sp4 }

                // Text Instructions
                SettingsSection {
                    theme: root.theme
                    title: "Текстова інструкція"
                    Layout.fillWidth: true

                    ColumnLayout {
                        width: parent.width
                        spacing: theme.sp4

                        // Step 1
                        Text { text: "Крок 1 - Реєстрація на сервісі Hugging Face"; font.pixelSize: theme.fontSizeLG * 1.1; font.weight: Font.Bold; color: theme.textPrimary }
                        Text {
                            property string linkCol: (typeof bridge !== "undefined" && bridge) ? (bridge.isDarkTheme ? "#3b82f6" : "#10b981") : "#10b981"
                            text: "Якщо у вас ще немає акаунта на платформі, його потрібно створити, оскільки ключі доступу прив'язуються до конкретного профілю.<br><br>" +
                                  "1. Перейдіть на офіційний сайт: <a href='https://huggingface.co/' style='color: " + linkCol + "; text-decoration: none;'><b>huggingface.co</b></a>.<br>" +
                                  "2. У правому верхньому куті натисніть кнопку <b>Sign Up</b> (Зареєструватися).<br>" +
                                  "3. Введіть вашу електронну адресу та придумайте надійний пароль (Також доступна швидка реєстрація через акаунти GitHub або Google). <b>Вимоги до пароля:</b><br>" +
                                  "&nbsp;&nbsp;&nbsp;• Мінімум 8 символів.<br>" +
                                  "&nbsp;&nbsp;&nbsp;• Обов'язково має містити великі літери, малі літери та цифри.<br>" +
                                  "&nbsp;&nbsp;&nbsp;• Якщо пароль коротший за 12 символів, він додатково має містити хоча б один спеціальний символ (наприклад, !, @, #, $, %).<br>" +
                                  "4. Заповніть базові дані профілю (нікнейм та Ваше повне ім'я) та поставте галочку про згоду з <a href='https://huggingface.co/terms-of-service' style='color: " + linkCol + "; text-decoration: none;'><b>Terms of Service</b></a> та <a href='https://huggingface.co/code-of-conduct' style='color: " + linkCol + "; text-decoration: none;'><b>Code of Conduct</b></a>.<br>" +
                                  "5. <b>Важливий момент:</b> Перевірте свою електронну пошту. Hugging Face надішле лист для підтвердження. Натисніть на посилання в листі, щоб активувати ваш акаунт. Без підтвердження пошти система не дозволить вам створити токен."
                            Layout.fillWidth: true
                            wrapMode: Text.WordWrap
                            textFormat: Text.RichText
                            font.pixelSize: theme.fontSizeMD
                            color: theme.textSecondary
                            linkColor: (typeof bridge !== "undefined" && bridge) ? (bridge.isDarkTheme ? "#3b82f6" : "#10b981") : "#10b981"
                            onLinkActivated: (link) => Qt.openUrlExternally(link)
                            HoverHandler {
                                cursorShape: parent.hoveredLink ? Qt.PointingHandCursor : Qt.ArrowCursor
                            }
                        }

                        // Step 2
                        Text { text: "Крок 2 - Створення API Ключа"; font.pixelSize: theme.fontSizeLG * 1.1; font.weight: Font.Bold; color: theme.textPrimary; Layout.topMargin: theme.sp4 }
                        Text {
                            text: "Після успішної реєстрації та входу в акаунт, вам потрібно згенерувати спеціальний ключ (Access Token), який слугуватиме \"паролем\" для вашої програми.<br><br>" +
                                  "1. У правому верхньому куті екрана натисніть на іконку вашого профілю (ваша аватарка).<br>" +
                                  "2. У випадаючому меню виберіть пункт <b>Access Tokens</b> (Токени доступу).<br>" +
                                  "3. Натисніть кнопку <b>Create new token</b> (Створити новий токен).<br>" +
                                  "4. Налаштуйте параметри токена:<br>" +
                                  "&nbsp;&nbsp;&nbsp;• Token type (тип): обов'язково поставте \"Write\"<br>" +
                                  "&nbsp;&nbsp;&nbsp;• Token name (Назва): Введіть будь-яку зрозумілу назву, наприклад, <code>My_Application_Token</code>, щоб у майбутньому ви пам'ятали, для чого він створений.<br>" +
                                  "5. Натисніть кнопку <b>Create token</b> (Створити токен).<br>" +
                                  "6. Ваш токен (довгий рядок символів, що починається з <code>hf_</code>) з'явиться на екрані. Натисніть кнопку <b>Copy</b> (іконка копіювання) поруч із ним.<br><br>" +
                                  "<b>Примітка щодо безпеки:</b> Ставтеся до цього токена як до пароля. Не діліться ним з іншими людьми та не публікуйте у відкритому доступі."
                            Layout.fillWidth: true
                            wrapMode: Text.WordWrap
                            textFormat: Text.RichText
                            font.pixelSize: theme.fontSizeMD
                            color: theme.textSecondary
                        }

                        // Step 3
                        Text { text: "Крок 3 - Підключення API ключа до застосунку"; font.pixelSize: theme.fontSizeLG * 1.1; font.weight: Font.Bold; color: theme.textPrimary; Layout.topMargin: theme.sp4 }
                        Text {
                            text: "Тепер, коли ви маєте скопійований ключ, його потрібно інтегрувати у вашу програму.<br><br>" +
                                  "1. Відкрийте вашу програму та перейдіть до розділу <b>Налаштування</b>.<br>" +
                                  "2. Знайдіть поле, призначене для введення ключа Hugging Face.<br>" +
                                  "3. Вставте скопійований токен у це поле.<br>" +
                                  "4. Збережіть зміни, натиснувши відповідну кнопку <b>Зберегти налаштування</b>."
                            Layout.fillWidth: true
                            wrapMode: Text.WordWrap
                            textFormat: Text.RichText
                            font.pixelSize: theme.fontSizeMD
                            color: theme.textSecondary
                        }
                    }
                }

                    } // end ColumnLayout inside Accordion
                } // end Accordion "Де взяти API ключі?"
                    } // end ColumnLayout inside SettingsSection
                } // end SettingsSection API Ключі

                // ─── Accordion: Стиль користувача ────────────────────
                Accordion {
                    theme: root.theme
                    title: "Стиль користувача (User Style)"
                    Layout.fillWidth: true
                    Layout.topMargin: theme.sp2

                    ColumnLayout {
                        width: parent.width
                        spacing: theme.sp4

                        Text {
                            text: "Опишіть ваш бажаний стиль написання у форматі Markdown. Ця інструкція додається до контексту кожної генерації."
                            font.pixelSize: theme.fontSizeMD
                            color: theme.textSecondary
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }

                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 260
                            radius: theme.radiusMD
                            color: theme.surface2
                            border.color: userStyleArea.activeFocus ? theme.accent : theme.borderSubtle
                            border.width: userStyleArea.activeFocus ? 2 : 1
                            Behavior on border.color { ColorAnimation { duration: 200; easing.type: Easing.OutCubic } }
                            Behavior on border.width { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }

                            Flickable {
                                id: userStyleFlickable
                                anchors.fill: parent
                                contentHeight: userStyleArea.implicitHeight
                                clip: true
                                interactive: true
                                boundsBehavior: Flickable.StopAtBounds

                                ScrollBar.vertical: ScrollBar {
                                    active: userStyleFlickable.moving || userStyleFlickable.contentHeight > userStyleFlickable.height
                                    policy: userStyleFlickable.contentHeight > userStyleFlickable.height ? ScrollBar.AlwaysOn : ScrollBar.AsNeeded
                                }

                                TextArea {
                                    id: userStyleArea
                                    width: userStyleFlickable.width
                                    height: Math.max(userStyleFlickable.height, implicitHeight)
                                    topPadding: theme.sp4
                                    bottomPadding: theme.sp4
                                    leftPadding: theme.sp4
                                    rightPadding: theme.sp4
                                    placeholderText: "Наприклад:\n- Використовуй формальний академічний стиль\n- Уникай жаргонізмів\n- Речення мають бути короткими"
                                    font.pixelSize: theme.fontSizeMD
                                    font.family: "Consolas"
                                    color: theme.textPrimary
                                    placeholderTextColor: theme.textTertiary
                                    wrapMode: TextArea.Wrap
                                    background: null
                                    selectByMouse: true
                                    HoverHandler { cursorShape: Qt.IBeamCursor }
                                    onTextChanged: {
                                        if (length > 5000) {
                                            var cur = cursorPosition;
                                            text = text.substring(0, 5000);
                                            cursorPosition = Math.min(cur, 5000);
                                        }
                                    }
                                }
                            }
                            
                            Text {
                                text: userStyleArea.length + " / 5000"
                                font.pixelSize: theme.fontSizeSM
                                color: userStyleArea.length >= 5000 ? theme.warning : theme.textTertiary
                                anchors.right: parent.right
                                anchors.bottom: parent.bottom
                                anchors.margins: theme.sp1
                            }
                        }
                    }
                }
                // ─── Cache & Retention Settings ─────────────────────
                SettingsSection {
                    theme: root.theme
                    title: "Кеш та збереження"
                    Layout.fillWidth: true
                    Layout.topMargin: theme.sp4

                    ColumnLayout {
                        width: parent.width
                        spacing: theme.sp4

                        // Session Retention
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: theme.sp4

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 4
                                Text {
                                    text: "Зберігання сесій (днів)"
                                    font.pixelSize: theme.fontSizeMD
                                    color: theme.textPrimary
                                }
                                Text {
                                    text: "Старі сесії автоматично видаляються"
                                    font.pixelSize: theme.fontSizeSM
                                    color: theme.textTertiary
                                }
                            }

                            Rectangle {
                                Layout.preferredWidth: 80
                                Layout.preferredHeight: 36
                                radius: theme.radiusMD
                                color: theme.surface2
                                border.color: sessionRetentionInput.activeFocus ? theme.accent : theme.borderSubtle
                                border.width: sessionRetentionInput.activeFocus ? 2 : 1
                                Behavior on border.color { ColorAnimation { duration: 200; easing.type: Easing.OutCubic } }
                                Behavior on border.width { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }

                                TextInput {
                                    id: sessionRetentionInput
                                    anchors.fill: parent
                                    anchors.margins: 8
                                    horizontalAlignment: TextInput.AlignHCenter
                                    verticalAlignment: TextInput.AlignVCenter
                                    font.pixelSize: theme.fontSizeMD
                                    color: theme.textPrimary
                                    validator: IntValidator { bottom: 1; top: 365 }
                                    selectByMouse: true
                                    HoverHandler { cursorShape: Qt.IBeamCursor }
                                }
                            }
                        }

                        // Cache TTL
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: theme.sp4

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 4
                                Text {
                                    text: "Час життя кешу (днів)"
                                    font.pixelSize: theme.fontSizeMD
                                    color: theme.textPrimary
                                }
                                Text {
                                    text: "Автоматичне очищення старих кешованих файлів"
                                    font.pixelSize: theme.fontSizeSM
                                    color: theme.textTertiary
                                }
                            }

                            Rectangle {
                                Layout.preferredWidth: 80
                                Layout.preferredHeight: 36
                                radius: theme.radiusMD
                                color: theme.surface2
                                border.color: cacheTtlInput.activeFocus ? theme.accent : theme.borderSubtle
                                border.width: cacheTtlInput.activeFocus ? 2 : 1
                                Behavior on border.color { ColorAnimation { duration: 200; easing.type: Easing.OutCubic } }
                                Behavior on border.width { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }

                                TextInput {
                                    id: cacheTtlInput
                                    anchors.fill: parent
                                    anchors.margins: 8
                                    horizontalAlignment: TextInput.AlignHCenter
                                    verticalAlignment: TextInput.AlignVCenter
                                    font.pixelSize: theme.fontSizeMD
                                    color: theme.textPrimary
                                    validator: IntValidator { bottom: 1; top: 365 }
                                    selectByMouse: true
                                    HoverHandler { cursorShape: Qt.IBeamCursor }
                                }
                            }
                        }

                        // Clear cache button
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: theme.sp4

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 4
                                Text {
                                    text: "Очищення кешу"
                                    font.pixelSize: theme.fontSizeMD
                                    color: theme.textPrimary
                                }
                                Text {
                                    text: "Видалити всі кешовані файли (LLM, зображення, документи)"
                                    font.pixelSize: theme.fontSizeSM
                                    color: theme.textTertiary
                                }
                            }

                            AppButton {
                                theme: root.theme
                                label: "Очистити"
                                variant: "secondary"
                                onClicked: {
                                    confirmClearPopup.open()
                                }
                            }
                        }

                        // Cache cleared status
                        Item {
                            id: cacheCleared
                            Layout.fillWidth: true
                            Layout.preferredHeight: showProgress > 0 ? 20 : 0
                            clip: true
                            property real showProgress: 0
                            Behavior on showProgress { NumberAnimation { duration: 400; easing.type: Easing.OutExpo } }

                            Text {
                                anchors.right: parent.right
                                text: "✓ Кеш очищено"
                                color: "#10B981"
                                font.pixelSize: theme.fontSizeSM
                                opacity: cacheCleared.showProgress
                            }

                            Timer {
                                id: cacheClearedTimer
                                interval: 3000
                                onTriggered: cacheCleared.showProgress = 0
                            }
                        }
                    }
                }

                Popup {
                    id: confirmClearPopup
                    parent: Overlay.overlay
                    x: Math.round((parent.width - width) / 2)
                    y: Math.round((parent.height - height) / 2)
                    width: Math.min(parent.width - 40, 400)
                    modal: true
                    dim: true
                    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
                    
                    background: Rectangle {
                        color: theme.surface1
                        radius: theme.radiusLG
                        border.color: theme.borderSubtle
                        border.width: 1
                    }
                    
                    contentItem: ColumnLayout {
                        spacing: theme.sp4
                        
                        Text {
                            text: "Очистити кеш?"
                            font.pixelSize: theme.fontSizeXL
                            font.weight: Font.DemiBold
                            color: theme.textPrimary
                        }
                        
                        Text {
                            text: "Ви дійсно хочете видалити всі кешовані файли? Цю дію неможливо скасувати."
                            font.pixelSize: theme.fontSizeMD
                            color: theme.textSecondary
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                        
                        RowLayout {
                            Layout.alignment: Qt.AlignRight
                            spacing: theme.sp3
                            Layout.topMargin: theme.sp2
                            
                            AppButton {
                                theme: root.theme
                                label: "Скасувати"
                                variant: "secondary"
                                onClicked: confirmClearPopup.close()
                            }
                            
                            AppButton {
                                theme: root.theme
                                label: "Очистити"
                                variant: "danger"
                                onClicked: {
                                    bridge.clearCache()
                                    cacheCleared.showProgress = 1
                                    cacheClearedTimer.restart()
                                    confirmClearPopup.close()
                                }
                            }
                        }
                    }
                }

                // ─── Save button ──────────────────────────────────────
                RowLayout {
                    Layout.alignment: Qt.AlignRight
                    Layout.topMargin: theme.sp4
                    spacing: 0

                    Item {
                        id: saveStatusContainer
                        Layout.preferredHeight: saveStatusText.implicitHeight
                        Layout.preferredWidth: showProgress * (saveStatusText.implicitWidth + theme.sp4)
                        Layout.alignment: Qt.AlignVCenter
                        clip: true

                        property real showProgress: 0
                        Behavior on showProgress { NumberAnimation { duration: 400; easing.type: Easing.OutExpo } }

                        Text {                            id: saveStatusText
                            x: 0
                            anchors.verticalCenter: parent.verticalCenter
                            text: "✓ Збережено"
                            color: "#10B981"
                            font.pixelSize: theme.fontSizeMD
                            font.weight: Font.Medium
                            opacity: saveStatusContainer.showProgress
                        }
                    }

                    Timer {
                        id: saveStatusTimer
                        interval: 2000
                        onTriggered: saveStatusContainer.showProgress = 0
                    }

                    AppButton {
                        theme: root.theme
                        label: "Зберегти налаштування"
                        variant: "primary"
                        onClicked: {
                            bridge.saveHfToken(hfTokenField.text)
                            bridge.saveSettings(
                                userStyleArea.text,
                                parseInt(cacheTtlInput.text) || 30,
                                parseInt(sessionRetentionInput.text) || 90
                            )
                            hfTokenField.focus = false
                            saveStatusContainer.showProgress = 1
                            saveStatusTimer.restart()
                        }
                    }
                }

                Item { height: theme.sp8 }
            }
        }
        }
        
        // Scroll to top button
        Rectangle {
            id: scrollToTopBtn
            width: 48
            height: 48
            radius: theme.radiusMD
            color: mouseAreaTopBtn.containsMouse ? theme.surface3 : theme.surface2
            border.color: theme.borderSubtle
            border.width: 1
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.margins: theme.sp5
            visible: scrollView.contentY > 200
            opacity: visible ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: 200 } }

            Canvas {
                anchors.centerIn: parent
                width: 24
                height: 24
                onPaint: {
                    var ctx = getContext("2d");
                    ctx.reset();
                    ctx.strokeStyle = theme.textPrimary;
                    ctx.lineWidth = 2;
                    ctx.lineCap = "round";
                    ctx.lineJoin = "round";
                    ctx.beginPath();
                    ctx.moveTo(4, 15);
                    ctx.lineTo(12, 7);
                    ctx.lineTo(20, 15);
                    ctx.stroke();
                }
            }

            MouseArea {
                id: mouseAreaTopBtn
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: {
                    smoothScrollAnim.to = 0
                    smoothScrollAnim.restart()
                }
            }
        }

        // Fullscreen video popup
    Popup {
        id: fullscreenPopup
        parent: Overlay.overlay
        width: Overlay.overlay ? Overlay.overlay.width : parent.width
        height: Overlay.overlay ? Overlay.overlay.height : parent.height
        x: 0
        y: 0
        margins: 0
        padding: 0
        background: Rectangle { color: "black" }
        closePolicy: Popup.CloseOnEscape
        
        function refreshPausedFrame() {
            if (player.playbackState !== MediaPlayer.PlayingState) {
                var pos = player.position;
                player.position = pos > 10 ? pos - 10 : pos + 10;
            }
        }
        
        onOpened: {
            playerView.parent = fullscreenPopup.contentItem
            playerView.forceActiveFocus()
            fsIconCanvas.requestPaint()
            refreshPausedFrame()
        }
        onClosed: {
            playerView.parent = inlineContainer
            playerView.forceActiveFocus()
            fsIconCanvas.requestPaint()
            refreshPausedFrame()
        }
    }
}
