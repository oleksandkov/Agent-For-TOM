import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Controls.Material 2.15
import QtQuick.Layouts 1.15
import QtQuick.Window 2.15

ApplicationWindow {
    id: root
    width: 1024
    height: 768
    minimumWidth: 900
    minimumHeight: 600
    // Constraints to prevent covering the taskbar on Windows when frameless
    maximumWidth: root.screen.desktopAvailableWidth
    maximumHeight: root.screen.desktopAvailableHeight

    onClosing: {
        clearSessionPayload()
        bridge.clearTransitFolder()
    }
    title: "Agent-For-TOM"
    flags: Qt.Window | Qt.FramelessWindowHint | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint
    
    // ─── Resize Handles ──────────────────────────────────────────────────────────
    MouseArea { width: 5; anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom; cursorShape: Qt.SizeHorCursor; onPressed: root.startSystemResize(Qt.LeftEdge) }
    MouseArea { width: 5; anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom; cursorShape: Qt.SizeHorCursor; onPressed: root.startSystemResize(Qt.RightEdge) }
    MouseArea { height: 5; anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right; cursorShape: Qt.SizeVerCursor; onPressed: root.startSystemResize(Qt.TopEdge) }
    MouseArea { height: 5; anchors.bottom: parent.bottom; anchors.left: parent.left; anchors.right: parent.right; cursorShape: Qt.SizeVerCursor; onPressed: root.startSystemResize(Qt.BottomEdge) }
    MouseArea { width: 5; height: 5; anchors.top: parent.top; anchors.left: parent.left; cursorShape: Qt.SizeFDiagCursor; onPressed: root.startSystemResize(Qt.TopEdge | Qt.LeftEdge) }
    MouseArea { width: 5; height: 5; anchors.top: parent.top; anchors.right: parent.right; cursorShape: Qt.SizeBDiagCursor; onPressed: root.startSystemResize(Qt.TopEdge | Qt.RightEdge) }
    MouseArea { width: 5; height: 5; anchors.bottom: parent.bottom; anchors.left: parent.left; cursorShape: Qt.SizeBDiagCursor; onPressed: root.startSystemResize(Qt.BottomEdge | Qt.LeftEdge) }
    MouseArea { width: 5; height: 5; anchors.bottom: parent.bottom; anchors.right: parent.right; cursorShape: Qt.SizeFDiagCursor; onPressed: root.startSystemResize(Qt.BottomEdge | Qt.RightEdge) }

    property int lastVis: Window.Maximized
    property int visBeforeMin: Window.Maximized

    // Track safe windowed size to fix Qt restore bugs
    property real safeNormalWidth: 1024
    property real safeNormalHeight: 768
    property real safeNormalX: 100
    property real safeNormalY: 100
    
    Timer {
        id: saveGeometryTimer
        interval: 500
        onTriggered: {
            if (root.visibility === Window.Windowed && root.x > -10000 && root.y > -10000) {
                safeNormalWidth = root.width
                safeNormalHeight = root.height
                safeNormalX = root.x
                safeNormalY = root.y
            }
        }
    }
    
    onWidthChanged: saveGeometryTimer.restart()
    onHeightChanged: saveGeometryTimer.restart()
    onXChanged: saveGeometryTimer.restart()
    onYChanged: saveGeometryTimer.restart()
    
    // Shared state between New Session and Confirm Session
    property var sessionPayload: ({})

    Timer {
        id: restoreMaximizedTimer
        interval: 10
        onTriggered: root.showMaximized()
    }

    ParallelAnimation {
        id: stateTransitionAnim
        NumberAnimation { target: root.contentItem; property: "scale"; from: 0.97; to: 1.0; duration: 150; easing.type: Easing.OutCubic }
        NumberAnimation { target: root.contentItem; property: "opacity"; from: 0.0; to: 1.0; duration: 150; easing.type: Easing.OutCubic }
    }
    
    Timer {
        id: fixGeometryTimer
        interval: 10
        onTriggered: {
            if (root.visibility === Window.Windowed && (root.width >= root.screen.desktopAvailableWidth - 50 || root.height >= root.screen.desktopAvailableHeight - 50)) {
                root.width = safeNormalWidth > 0 ? safeNormalWidth : 1024
                root.height = safeNormalHeight > 0 ? safeNormalHeight : 768
                root.x = safeNormalX > 0 ? safeNormalX : 100
                root.y = safeNormalY > 0 ? safeNormalY : 100
            }
        }
    }
    


    onVisibilityChanged: function() {
        if (root.visibility !== Window.Minimized) {
            root.contentItem.y = 0
            
            if (lastVis !== Window.Minimized && root.visibility !== lastVis) {
                stateTransitionAnim.restart()
            } else if (lastVis === Window.Minimized) {
                root.contentItem.scale = 1.0
                root.contentItem.opacity = 1.0
            }

            if (root.visibility === Window.Windowed && lastVis === Window.Minimized && visBeforeMin === Window.Maximized) {
                restoreMaximizedTimer.start()
            }
            visBeforeMin = root.visibility
        }
        lastVis = root.visibility
    }

    // ─── Design Tokens ─────────────────────────────────────────────────────────
    QtObject {
        id: appTheme
        // Surfaces
        property color surfaceBase:  "#F3F4F6"
        property color surface1:     "#FFFFFF"
        property color surface2:     "#D1D5DB"
        property color surface3:     "#9CA3AF"
        // Borders
        property color borderSubtle: "#9CA3AF"
        property color borderStrong: "#6B7280"
        // Text
        property color textPrimary:   "#1C1917"
        property color textSecondary: "#57534E"
        property color textTertiary:  "#A8A29E"
        // Accent (Teal 700)
        property color accent:        "#047857"
        property color accentHover:   "#065F46"
        property color accentSoft:    "#6EE7B7"
        property color accentSoft2:   "#A7F3D0"
        // Semantic
        readonly property color success:       "#15803D"
        property color successSoft:   "#89ffb2ff"
        readonly property color warning:       "#B45309"
        property color warningSoft:   "#FEF3C7"
        readonly property color danger:        "#B91C1C"
        property color dangerSoft:    "#FEE2E2"
        // Typography
        property int   fontSizeXS:   11
        property int   fontSizeSM:   12
        property int   fontSizeMD:   13
        property int   fontSizeLG:   14
        property int   fontSizeXL:   16
        property int   fontSizeH1:   24
        // Spacing
        readonly property int   sp1:  4
        readonly property int   sp2:  8
        readonly property int   sp3:  12
        readonly property int   sp4:  16
        readonly property int   sp5:  20
        readonly property int   sp6:  24
        readonly property int   sp8:  32
        readonly property int   sp10: 40
        // Radius
        readonly property int   radiusSM:  4
        readonly property int   radiusMD:  6
        readonly property int   radiusLG:  8
        readonly property int   radiusXL:  12
    }

    // ─── State ─────────────────────────────────────────────────────────────────
    property string currentScreen: "documents"
    property bool isDarkTheme: bridge ? bridge.isDarkTheme : false
    property bool isBigFont: bridge ? bridge.isBigFont : false

    onIsDarkThemeChanged: applyTheme()
    onIsBigFontChanged: applyFontSizes()

    function applyFontSizes() {
        var m = isBigFont ? 1.4 : 1.0
        appTheme.fontSizeXS = 11 * m
        appTheme.fontSizeSM = 12 * m
        appTheme.fontSizeMD = 13 * m
        appTheme.fontSizeLG = 14 * m
        appTheme.fontSizeXL = 16 * m
        appTheme.fontSizeH1 = 24 * m
    }

    function applyTheme() {
        if (isDarkTheme) {
            appTheme.surfaceBase = "#0F172A"
            appTheme.surface1 = "#1E293B"
            appTheme.surface2 = "#334155"
            appTheme.surface3 = "#475569"
            appTheme.borderSubtle = "#334155"
            appTheme.borderStrong = "#475569"
            appTheme.textPrimary = "#F8FAFC"
            appTheme.textSecondary = "#CBD5E1"
            appTheme.textTertiary = "#94A3B8"
            appTheme.accent = "#0EA5E9"
            appTheme.accentHover = "#38BDF8"
            appTheme.accentSoft = "#082F49"
            appTheme.accentSoft2 = "#0C4A6E"
            appTheme.warningSoft = "#422006"
            appTheme.successSoft = "#052E16"
            appTheme.dangerSoft  = "#3B0764"
        } else {
            appTheme.surfaceBase = "#F3F4F6"
            appTheme.surface1 = "#FFFFFF"
            appTheme.surface2 = "#D1D5DB"
            appTheme.surface3 = "#9CA3AF"
            appTheme.borderSubtle = "#9CA3AF"
            appTheme.borderStrong = "#6B7280"
            appTheme.textPrimary = "#1C1917"
            appTheme.textSecondary = "#292524"
            appTheme.textTertiary = "#44403C"
            appTheme.accent = "#047857"
            appTheme.accentHover = "#065F46"
            appTheme.accentSoft = "#6EE7B7"
            appTheme.accentSoft2 = "#A7F3D0"
        }
    }

    function toggleTheme() {
        bridge.isDarkTheme = !bridge.isDarkTheme
    }

    function toggleBigFont() {
        bridge.isBigFont = !bridge.isBigFont
    }

    function clearSessionPayload() {
        root.sessionPayload = {}
        bridge.sessionPayloadJson = "{}"
    }

    Connections {
        target: bridge
        function onFilesUpdated(jsonStr) {
            var newFiles = JSON.parse(jsonStr);
            var sp = Object.assign({}, root.sessionPayload);
            sp.uploadedFiles = newFiles;
            root.sessionPayload = sp;
            // Don't override bridge.sessionPayloadJson here, let NewDocumentScreen handle saving
            // or we could, but the user hasn't clicked "Створити". Actually we should update it so it survives restarts.
            bridge.sessionPayloadJson = JSON.stringify(sp);
        }
    }

    Component.onCompleted: {
        applyTheme()
        applyFontSizes()
        
        try {
            root.sessionPayload = JSON.parse(bridge.sessionPayloadJson || "{}")
        } catch(e) {
            root.sessionPayload = {}
        }
        
        root.width = 1024
        root.height = 768
        
        // Show windowed first to register normal geometry
        root.visible = true
        
        // Then maximize after a brief delay
        restoreMaximizedTimer.start()
    }

    function toggleMaximized() {
        if (root.visibility === Window.Maximized || root.visibility === Window.FullScreen) {
            root.showNormal()
            root.width = safeNormalWidth > 0 ? safeNormalWidth : 1024
            root.height = safeNormalHeight > 0 ? safeNormalHeight : 768
            root.x = safeNormalX > -10000 ? safeNormalX : 100
            root.y = safeNormalY > -10000 ? safeNormalY : 100
            fixGeometryTimer.start()
        } else {
            root.showMaximized()
        }
    }

    // ─── Window Management Shortcuts ───────────────────────────────────────────
    Shortcut {
        sequence: "Alt+F4"
        onActivated: root.close()
    }
    Shortcut {
        sequence: "F11"
        onActivated: root.toggleMaximized()
    }
    Shortcut {
        sequence: "Esc"
        enabled: root.visibility === Window.FullScreen || root.visibility === Window.Maximized
        onActivated: {
            if (root.visibility === Window.Maximized || root.visibility === Window.FullScreen) {
                root.toggleMaximized()
            }
        }
    }

    Connections {
        target: bridge
        function onNavigationRequest(screen, param) {
            navigateTo(screen)
        }
    }

    function navigateTo(screen) {
        currentScreen = screen
        switch(screen) {
            case "documents":    stack.replace(documentsComp); break
            case "new_document": stack.replace(newDocumentComp); break
            case "confirm_document": stack.replace(confirmDocumentComp); break
            case "progress":    stack.replace(progressComp); break
            case "result":      stack.replace(resultComp); break
            case "templates":   stack.replace(templatesComp); break
            case "instructions":stack.replace(instructionsComp); break
            case "settings":    stack.replace(settingsComp); break
            case "about":       stack.replace(aboutComp); break
        }
    }

    // ─── Root Layout ───────────────────────────────────────────────────────────
    color: appTheme.surfaceBase
    Material.theme: root.isDarkTheme ? Material.Dark : Material.Light
    Material.accent: appTheme.accent
    Material.background: root.isDarkTheme ? appTheme.surfaceBase : "#E5E7EB"

    ColumnLayout {
        id: mainRootLayout
        anchors.fill: parent
        spacing: 0
        opacity: splashScreen.visible ? 0.0 : 1.0
        Behavior on opacity { NumberAnimation { duration: 400; easing.type: Easing.InOutQuad } }

        CustomTitleBar {
            Layout.fillWidth: true
            theme: appTheme
            window: root
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            // ── Sidebar ──────────────────────────────────────────────────────────
            Sidebar {
                id: sidebar
                z: 10
                Layout.fillHeight: true
                Layout.preferredWidth: isCollapsed ? 68 : 220
                Behavior on Layout.preferredWidth { NumberAnimation { duration: 250; easing.type: Easing.OutCubic } }
                theme: appTheme
                currentScreen: root.currentScreen
                isCollapsed: bridge ? bridge.isSidebarCollapsed : false
                onIsCollapsedChanged: { if (bridge) bridge.isSidebarCollapsed = isCollapsed }
                onNavigate: (screen) => root.navigateTo(screen)
            }

            // ── Main Content ─────────────────────────────────────────────────────
            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 0

                // Topbar
                Topbar {
                    id: topbar
                    Layout.fillWidth: true
                    theme: appTheme
                    currentScreen: root.currentScreen
                    onNavigate: (screen) => root.navigateTo(screen)
                    onToggleThemeRequested: root.toggleTheme()
                    onToggleBigFontRequested: root.toggleBigFont()
                }

                // Screen stack
                StackView {
                    id: stack
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    initialItem: documentsComp

                    pushEnter: Transition {
                        PropertyAnimation { property: "opacity"; from: 0; to: 1; duration: 180 }
                        PropertyAnimation { property: "x"; from: 20; to: 0; duration: 180; easing.type: Easing.OutCubic }
                    }
                    pushExit: Transition {
                        PropertyAnimation { property: "opacity"; from: 1; to: 0; duration: 120 }
                    }
                    replaceEnter: Transition {
                        PropertyAnimation { property: "opacity"; from: 0; to: 1; duration: 180 }
                        PropertyAnimation { property: "y"; from: 8; to: 0; duration: 180; easing.type: Easing.OutCubic }
                    }
                    replaceExit: Transition {
                        PropertyAnimation { property: "opacity"; from: 1; to: 0; duration: 100 }
                    }
                }
            } // end ColumnLayout
        } // end RowLayout
    } // end initial ColumnLayout

    // Floating continue button
    Rectangle {
        id: floatingContinueBtn
        width: floatingRow.implicitWidth + appTheme.sp6 * 2
        height: Math.max(60, floatingRow.implicitHeight + appTheme.sp4 * 2)
        property bool hasData: {
            var sp = root.sessionPayload
            if (!sp) return false;
            var hasName = (sp.documentName !== undefined && sp.documentName !== "")
            var hasTheme = (sp.documentTheme !== undefined && sp.documentTheme !== "")
            var hasGoal = (sp.documentGoal !== undefined && sp.documentGoal !== "")
            var hasLab = (sp.labNumber !== undefined && sp.labNumber !== "")
            var hasHints = (sp.sessionHints !== undefined && sp.sessionHints !== "")
            var hasFiles = (sp.uploadedFiles !== undefined && sp.uploadedFiles.length > 0)
            return hasName || hasTheme || hasGoal || hasLab || hasHints || hasFiles
        }
        visible: hasData && root.currentScreen !== "new_document" && root.currentScreen !== "confirm_document" && root.currentScreen !== "progress"
        color: appTheme.accent
        radius: appTheme.radiusMD
        anchors.left: parent.left
        anchors.bottom: parent.bottom
        anchors.leftMargin: 240 + appTheme.sp6 // 220 is sidebar width approx
        anchors.bottomMargin: appTheme.sp6
        border.color: appTheme.borderSubtle
        border.width: 1
        z: 9000

        RowLayout {
            id: floatingRow
            anchors.fill: parent
            anchors.margins: appTheme.sp3
            spacing: appTheme.sp3
            Text {
                text: "⚠️"
                font.pixelSize: 20
                Layout.alignment: Qt.AlignVCenter
            }
            Text {
                text: root.isBigFont ? "Ой, здається ви не закінчили роботу над\nстворенням файлу!\nНатисніть сюди, щоб перейти." : "Ой, здається ви не закінчили роботу над створенням файлу!\nНатисніть сюди, щоб перейти."
                color: "white"
                font.pixelSize: appTheme.fontSizeSM
                font.weight: Font.Medium
                wrapMode: Text.Wrap
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignVCenter
            }
        }
        MouseArea {
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onEntered: floatingContinueBtn.opacity = 0.9
            onExited: floatingContinueBtn.opacity = 1.0
            onClicked: root.navigateTo("new_document")
        }
        Behavior on opacity { NumberAnimation { duration: 150 } }
    }

    WindowResizer { window: root }

    // ─── Screen Components ─────────────────────────────────────────────────────
    Component { id: documentsComp;     DocumentsScreen     { theme: appTheme; onNavigate: (s) => root.navigateTo(s) } }
    Component { id: newDocumentComp; NewDocumentScreen { theme: appTheme; initialPayload: root.sessionPayload; onNavigate: (screen) => root.navigateTo(screen); onSessionPayloadUpdated: (payload) => { root.sessionPayload = payload; bridge.sessionPayloadJson = JSON.stringify(payload); } } }
    Component { id: confirmDocumentComp; ConfirmDocumentScreen { theme: appTheme; onNavigate: (screen) => root.navigateTo(screen) } }
    Component { id: progressComp; ProgressScreen { theme: appTheme; onNavigate: (screen) => root.navigateTo(screen) } }
    Component { id: resultComp;       ResultScreen       { theme: appTheme; onNavigate: (s) => root.navigateTo(s) } }
    Component { id: templatesComp;    TemplatesScreen    { theme: appTheme; onNavigate: (s) => root.navigateTo(s) } }
    Component { id: instructionsComp; InstructionsScreen { theme: appTheme; onNavigate: (s) => root.navigateTo(s) } }
    Component { id: settingsComp;     SettingsScreen     { theme: appTheme; onNavigate: (s) => root.navigateTo(s) } }
    Component { id: aboutComp;        AboutScreen        { theme: appTheme; onNavigate: (s) => root.navigateTo(s) } }

    // ── Window Border ────────────────────────────────────────────────────────────
    Rectangle {
        anchors.fill: parent
        color: "transparent"
        border.color: appTheme.borderSubtle
        border.width: root.visibility === Window.Windowed && root.width < Screen.desktopAvailableWidth - 20 ? 1 : 0
        z: 9998
    }

    // ── Splash Screen ────────────────────────────────────────────────────────────
    Rectangle {
        id: splashScreen
        anchors.fill: parent
        color: appTheme.surface1
        z: 9999

        Image {
            id: splashLogo
            source: "../assets/img/logo.png"
            anchors.centerIn: parent
            width: 240
            height: 240
            fillMode: Image.PreserveAspectFit
            opacity: 0
            scale: 0.8
            antialiasing: true
        }

        SequentialAnimation {
            id: splashAnim
            running: true
            PauseAnimation { duration: 50 }
            ParallelAnimation {
                NumberAnimation { target: splashLogo; property: "opacity"; to: 1.0; duration: 400; easing.type: Easing.OutCubic }
                NumberAnimation { target: splashLogo; property: "scale"; to: 1.05; duration: 400; easing.type: Easing.OutBack }
            }
            PauseAnimation { duration: 400 }
            ParallelAnimation {
                NumberAnimation { target: splashScreen; property: "opacity"; to: 0.0; duration: 400; easing.type: Easing.InOutQuad }
                NumberAnimation { target: splashLogo; property: "scale"; to: 1.15; duration: 400; easing.type: Easing.InQuad }
            }
            ScriptAction { script: splashScreen.visible = false }
        }
    }
}
