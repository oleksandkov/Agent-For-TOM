"""
Agent-For-Labs — Desktop Application Launcher
PyQt6 + QML, Single-user, SQLite, 2-pass AI pipeline
"""
import sys
import os
from pathlib import Path

# pyrefly: ignore [missing-import]
from PyQt6.QtWidgets import QApplication
# pyrefly: ignore [missing-import]
from PyQt6.QtQml import QQmlApplicationEngine
# pyrefly: ignore [missing-import]
from PyQt6.QtCore import Qt
# pyrefly: ignore [missing-import]
from PyQt6.QtGui import QIcon

# Suppress FFmpeg logs (like video metadata dumps) in the terminal
os.environ["QT_LOGGING_RULES"] = "qt.multimedia.ffmpeg.info=false;qt.multimedia.ffmpeg.debug=false;qt.qpa.mime=false"

# Add app directory to path
sys.path.insert(0, str(Path(__file__).parent))

from app.bridge import AppBridge


def main():
    import ctypes
    try:
        # Fix Windows taskbar icon (prevents grouping under Python logo)
        myappid = 'sych.agentfortom.app.1'
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    except Exception:
        pass

    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Material")
    os.environ["QT_LOGGING_RULES"] = "qt.qpa.mime=false"
    os.environ.setdefault("QT_QUICK_CONTROLS_MATERIAL_THEME", "Light")
    os.environ.setdefault("QT_QUICK_CONTROLS_MATERIAL_ACCENT", "#0F766E")

    app = QApplication(sys.argv)
    app.setApplicationName("Agent-For-Labs")
    app.setApplicationDisplayName("Agent-For-Labs")
    app.setOrganizationName("Agent-For-Labs")
    app.setApplicationVersion("1.0.0")

    # Set app icon
    icon_path = str(Path(__file__).parent / "app" / "assets" / "img" / "logo.png")
    if Path(icon_path).exists():
        app.setWindowIcon(QIcon(icon_path))

    engine = QQmlApplicationEngine()

    # Register the bridge as a context property so QML can use it
    bridge = AppBridge()
    app.bridge = bridge  # Keep a strong reference to prevent garbage collection!
    engine.rootContext().setContextProperty("bridge", bridge)

    # Add the ui directory as a QML import path so components resolve correctly
    ui_path = str(Path(__file__).parent / "app" / "ui")
    engine.addImportPath(ui_path)
    # Also add parent dir so "import ui 1.0" works
    engine.addImportPath(str(Path(__file__).parent / "app"))

    # Connect QML warnings to stderr for debugging
    def on_warnings(warnings):
        for w in warnings:
            print(f"QML WARNING: {w.url().toString()} line {w.line()}: {w.description()}", file=sys.stderr)

    engine.warnings.connect(on_warnings)

    # Load the root QML file
    qml_path = Path(__file__).parent / "app" / "ui" / "main.qml"
    print(f"Loading QML from: {qml_path}")
    engine.load(str(qml_path))

    if not engine.rootObjects():
        print("ERROR: Failed to load QML. Check the QML file path and syntax.", file=sys.stderr)
        sys.exit(1)

    # Force window to foreground on Windows
    if sys.platform == 'win32':
        import ctypes
        window = engine.rootObjects()[0]
        hwnd = int(window.winId())
        # The famous "ALT key hack" to bypass Windows focus stealing prevention
        ctypes.windll.user32.keybd_event(0x12, 0, 0, 0) # ALT down
        ctypes.windll.user32.keybd_event(0x12, 0, 2, 0) # ALT up
        ctypes.windll.user32.ShowWindow(hwnd, 9) # SW_RESTORE
        ctypes.windll.user32.SetForegroundWindow(hwnd)

    # Ensure transit data is cleaned up properly on exit
    app.aboutToQuit.connect(bridge.clearTransitFolder)
    app.aboutToQuit.connect(lambda: setattr(bridge, 'sessionPayloadJson', '{}'))

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
