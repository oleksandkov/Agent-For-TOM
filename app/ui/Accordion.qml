import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

// Reusable accordion (collapsible section) component
Rectangle {
    id: root
    radius: theme.radiusLG
    color: theme.surface1
    border.color: theme.borderSubtle; border.width: 1
    clip: true

    required property var theme
    required property string title
    property bool expanded: false
    default property alias content: contentPlaceholder.children

    implicitHeight: headerRow.implicitHeight + theme.sp5 * 2
                    + (expanded ? contentPlaceholder.implicitHeight + theme.sp4 + theme.sp5 + separatorLine.height : 0)
    Behavior on implicitHeight { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }

    ColumnLayout {
        id: innerCol
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: theme.sp5 }
        spacing: 0

        // Header row with title + arrow
        RowLayout {
            id: headerRow
            Layout.fillWidth: true
            spacing: theme.sp2

            Text {
                text: root.title
                font.pixelSize: theme.fontSizeXL
                font.weight: 600
                color: theme.textPrimary
                Layout.fillWidth: true
            }

            // Animated chevron arrow
            Canvas {
                id: chevron
                width: 20; height: 20
                property real angle: root.expanded ? 180 : 0
                property color arrowColor: theme.textSecondary
                Behavior on angle { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }
                onAngleChanged: requestPaint()
                onArrowColorChanged: requestPaint()

                onPaint: {
                    var ctx = getContext("2d");
                    ctx.reset();
                    ctx.strokeStyle = arrowColor;
                    ctx.lineWidth = 2;
                    ctx.lineCap = "round";
                    ctx.lineJoin = "round";
                    ctx.save();
                    ctx.translate(10, 10);
                    ctx.rotate(angle * Math.PI / 180);
                    ctx.beginPath();
                    ctx.moveTo(-5, -3);
                    ctx.lineTo(0, 3);
                    ctx.lineTo(5, -3);
                    ctx.stroke();
                    ctx.restore();
                }
            }
        }

        // Separator line (visible only when expanded)
        Rectangle {
            id: separatorLine
            Layout.fillWidth: true
            Layout.topMargin: theme.sp4
            height: 1
            color: theme.borderSubtle
            visible: root.expanded
            opacity: root.expanded ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: 200 } }
        }

        // Content area
        Item {
            id: contentPlaceholder
            Layout.fillWidth: true
            Layout.topMargin: root.expanded ? theme.sp4 : 0
            implicitHeight: root.expanded ? (children.length > 0 ? children[0].implicitHeight : 0) : 0
            visible: root.expanded
            opacity: root.expanded ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: 250; easing.type: Easing.OutCubic } }
        }
    }

    MouseArea {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        height: headerRow.implicitHeight + theme.sp5 * 2
        cursorShape: Qt.PointingHandCursor
        onClicked: root.expanded = !root.expanded
    }
}
