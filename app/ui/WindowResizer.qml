import QtQuick 2.15
import QtQuick.Window 2.15

Item {
    id: root
    anchors.fill: parent
    required property var window // Reference to the ApplicationWindow

    // Only allow resizing if windowed and ready
    visible: window.isReadyForResize && window.visibility !== Window.Maximized && window.visibility !== Window.FullScreen

    // Top
    MouseArea {
        height: 6; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.leftMargin: 6; anchors.rightMargin: 6
        cursorShape: Qt.SizeVerCursor
        onPressed: window.startSystemResize(Qt.TopEdge)
    }
    // Bottom
    MouseArea {
        height: 6; anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.leftMargin: 6; anchors.rightMargin: 6
        cursorShape: Qt.SizeVerCursor
        onPressed: window.startSystemResize(Qt.BottomEdge)
    }
    // Left
    MouseArea {
        width: 6; anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom; anchors.topMargin: 6; anchors.bottomMargin: 6
        cursorShape: Qt.SizeHorCursor
        onPressed: window.startSystemResize(Qt.LeftEdge)
    }
    // Right
    MouseArea {
        width: 6; anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom; anchors.topMargin: 6; anchors.bottomMargin: 6
        cursorShape: Qt.SizeHorCursor
        onPressed: window.startSystemResize(Qt.RightEdge)
    }
    // Top-Left
    MouseArea {
        width: 6; height: 6; anchors.left: parent.left; anchors.top: parent.top
        cursorShape: Qt.SizeFDiagCursor
        onPressed: window.startSystemResize(Qt.TopEdge | Qt.LeftEdge)
    }
    // Top-Right
    MouseArea {
        width: 6; height: 6; anchors.right: parent.right; anchors.top: parent.top
        cursorShape: Qt.SizeBDiagCursor
        onPressed: window.startSystemResize(Qt.TopEdge | Qt.RightEdge)
    }
    // Bottom-Left
    MouseArea {
        width: 6; height: 6; anchors.left: parent.left; anchors.bottom: parent.bottom
        cursorShape: Qt.SizeBDiagCursor
        onPressed: window.startSystemResize(Qt.BottomEdge | Qt.LeftEdge)
    }
    // Bottom-Right
    MouseArea {
        width: 6; height: 6; anchors.right: parent.right; anchors.bottom: parent.bottom
        cursorShape: Qt.SizeFDiagCursor
        onPressed: window.startSystemResize(Qt.BottomEdge | Qt.RightEdge)
    }
}
