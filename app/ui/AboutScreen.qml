import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    id: root
    color: theme.surfaceBase

    required property var theme
    signal navigate(string screen)

    Flickable {
        id: flickable
        anchors.fill: parent
        contentWidth: width
        contentHeight: contentCol.implicitHeight + theme.sp10 * 2
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
                    flickable.contentY = Math.max(0, Math.min(flickable.contentHeight - flickable.height, flickable.contentY - delta));
                } else {
                    var current = smoothScrollAnim.running ? smoothScrollAnim.to : flickable.contentY;
                    var step = event.angleDelta.y * speedMultiplier;
                    var newY = Math.max(0, Math.min(flickable.contentHeight - flickable.height, current - step));
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
            target: flickable
            property: "contentY"
            duration: 400
            easing.type: Easing.OutCubic
        }

        ColumnLayout {
            id: contentCol
            y: theme.sp10
            anchors.horizontalCenter: parent.horizontalCenter
            width: Math.min(parent.width - theme.sp10 * 2, 800)
            spacing: theme.sp8

            // ── Logo and Mission ───────────────────────────────────────────
            ColumnLayout {
                Layout.alignment: Qt.AlignHCenter
                spacing: theme.sp4

                Image {
                    Layout.alignment: Qt.AlignHCenter
                    Layout.preferredWidth: 320
                    Layout.preferredHeight: 320
                    sourceSize.width: 640
                    sourceSize.height: 640
                    source: "../assets/img/logo.png"
                    fillMode: Image.PreserveAspectFit
                    mipmap: true
                }

                Text {
                    Layout.alignment: Qt.AlignHCenter
                    text: "Agent-For-TOM"
                    font.pixelSize: theme.fontSizeH1 * 1.5
                    font.weight: Font.Bold
                    color: theme.textPrimary
                    Layout.topMargin: theme.sp2
                }

                Text {
                    Layout.alignment: Qt.AlignHCenter
                    Layout.maximumWidth: 640
                    text: "Головне завдання нашого додатку — зробити життя викладача легшим. Ми автоматизуємо паперову рутину, допомагаємо швидко створювати якісні навчальні матеріали та заощаджуємо ваш час для більш важливих речей: живого спілкування зі студентами."
                    font.pixelSize: theme.fontSizeLG
                    color: theme.textSecondary
                    wrapMode: Text.Wrap
                    horizontalAlignment: Text.AlignHCenter
                    lineHeight: 1.4
                }
            }

            // ── Developers ────────────────────────────────────────────────
            Text {
                text: "Розробники"
                font.pixelSize: theme.fontSizeH1
                font.weight: Font.DemiBold
                color: theme.textPrimary
                Layout.topMargin: theme.sp6
            }

            GridLayout {
                Layout.fillWidth: true
                columns: 2
                columnSpacing: theme.sp6
                rowSpacing: theme.sp6

                // Developer 1
                Rectangle {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    implicitHeight: dev1Col.implicitHeight + theme.sp6 * 2
                    radius: theme.radiusLG
                    color: theme.surface1
                    border.color: theme.accent
                    border.width: 2

                    ColumnLayout {
                        id: dev1Col
                        anchors.fill: parent
                        anchors.margins: theme.sp6
                        spacing: theme.sp2
                        Image {
                            Layout.alignment: Qt.AlignHCenter
                            Layout.preferredWidth: 192
                            Layout.preferredHeight: 224
                            sourceSize: Qt.size(384, 448)
                            source: "../assets/img/sych.png"
                            fillMode: Image.PreserveAspectCrop
                            mipmap: true
                        }
                        Text { text: "Ярослав Сич"; font.weight: Font.Bold; color: theme.textPrimary; font.pixelSize: theme.fontSizeXL; Layout.alignment: Qt.AlignHCenter }
                        Text { text: "Front-end"; color: theme.accent; font.weight: Font.Medium; font.pixelSize: theme.fontSizeMD; Layout.alignment: Qt.AlignHCenter }
                        Text {
                            text: "sych521@gmail.com<br><a href='https://www.linkedin.com/in/yaroslav-sych/' style='color:" + theme.accent + "'>LinkedIn</a>, <a href='https://github.com/iberikofer' style='color:" + theme.accent + "'>GitHub</a>, <a href='https://t.me/YSych' style='color:" + theme.accent + "'>Telegram</a>"
                            textFormat: Text.RichText
                            onLinkActivated: (link) => Qt.openUrlExternally(link)
                            horizontalAlignment: Text.AlignHCenter
                            color: theme.textTertiary
                            font.pixelSize: theme.fontSizeSM
                            Layout.alignment: Qt.AlignHCenter

                            MouseArea {
                                anchors.fill: parent
                                acceptedButtons: Qt.NoButton
                                cursorShape: parent.hoveredLink ? Qt.PointingHandCursor : Qt.ArrowCursor
                            }
                        }
                        Item { Layout.fillHeight: true }
                    }
                }

                // Developer 2
                Rectangle {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    implicitHeight: dev2Col.implicitHeight + theme.sp6 * 2
                    radius: theme.radiusLG
                    color: theme.surface1
                    border.color: theme.accent
                    border.width: 2

                    ColumnLayout {
                        id: dev2Col
                        anchors.fill: parent
                        anchors.margins: theme.sp6
                        spacing: theme.sp2
                        Image {
                            Layout.alignment: Qt.AlignHCenter
                            Layout.preferredWidth: 192
                            Layout.preferredHeight: 224
                            sourceSize: Qt.size(384, 448)
                            source: "../assets/img/koval.jpg"
                            fillMode: Image.PreserveAspectCrop
                            mipmap: true
                        }
                        Text { text: "Олександр Коваль"; font.weight: Font.Bold; color: theme.textPrimary; font.pixelSize: theme.fontSizeXL; Layout.alignment: Qt.AlignHCenter }
                        Text { text: "Back-end"; color: theme.accent; font.weight: Font.Medium; font.pixelSize: theme.fontSizeMD; Layout.alignment: Qt.AlignHCenter }
                        Text {
                            text: "oleksandr.kov.dm@gmail.com<br><a href='https://www.linkedin.com/in/oleksandr-koval-932015384/' style='color:" + theme.accent + "'>LinkedIn</a>, <a href='https://github.com/oleksandkov' style='color:" + theme.accent + "'>GitHub</a>, <a href='https://t.me/muaron_OK' style='color:" + theme.accent + "'>Telegram</a>"
                            textFormat: Text.RichText
                            onLinkActivated: (link) => Qt.openUrlExternally(link)
                            horizontalAlignment: Text.AlignHCenter
                            color: theme.textTertiary
                            font.pixelSize: theme.fontSizeSM
                            Layout.alignment: Qt.AlignHCenter

                            MouseArea {
                                anchors.fill: parent
                                acceptedButtons: Qt.NoButton
                                cursorShape: parent.hoveredLink ? Qt.PointingHandCursor : Qt.ArrowCursor
                            }
                        }
                        Item { Layout.fillHeight: true }
                    }
                }
            }

            // ── Inspired By ───────────────────────────────────────────────
            Text {
                text: "Натхненно"
                font.pixelSize: theme.fontSizeH1
                font.weight: Font.DemiBold
                color: theme.textPrimary
                Layout.topMargin: theme.sp4
            }

            Rectangle {
                Layout.fillWidth: true
                implicitHeight: inspiredRow.implicitHeight + theme.sp6 * 2
                radius: theme.radiusLG
                color: theme.surface1
                border.color: theme.accent
                border.width: 2

                RowLayout {
                    id: inspiredRow
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.margins: theme.sp6
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: theme.sp6
                    
                    Image {
                        Layout.preferredWidth: 200
                        Layout.preferredHeight: 260
                        source: "../assets/img/tkachenko.jpg"
                        fillMode: Image.PreserveAspectCrop
                        mipmap: true
                        Layout.alignment: Qt.AlignVCenter
                    }
                    
                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.alignment: Qt.AlignVCenter
                        spacing: theme.sp2
                        
                        Text { text: "Ткаченко Олександр Миколайович"; font.weight: Font.Bold; color: theme.textPrimary; font.pixelSize: theme.fontSizeXL; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        Text { text: "Доцент кафедри Програмного забезпечення у Вінницькому національному технічному університеті"; color: theme.textSecondary; font.weight: Font.Medium; font.pixelSize: theme.fontSizeMD; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        Text { text: "«Розділяй і володарюй» ©"; color: theme.textTertiary; font.pixelSize: theme.fontSizeMD; font.italic: true; wrapMode: Text.WordWrap; Layout.fillWidth: true; Layout.topMargin: theme.sp2 }
                    }
                }
            }
        }
    }
}
