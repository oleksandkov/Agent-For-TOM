# Implementation Plan: PyQt6 + QML Desktop Interface for Agent-For-TOM

This plan details how to build the desktop interface for **Agent-For-TOM** using **PyQt6** and **QML (Qt Quick)**. It outlines the application architecture, directory structure, visual styling, and provides the starting boilerplate files to run a premium, modern prototype.

---

## Technical Choices & Answers

1. **GPU Acceleration:** No GPU acceleration needed. The application will use simple static layouts, clean grids, and standard transitions without heavy visual animations, ensuring compatibility with standard CPU rendering.
2. **Icons:** Drawn dynamically using SVG paths/Canvas, removing the need for external icon font assets or image packages.
3. **Styling Concept:** A premium **Dark Mode Design** using **Qt Quick Controls 2 Material style** tailored with clean Slate and Accent colors.

---

## Architecture & Visual Style

### Visual Look and Feel
- **Color Palette:**
  - Background: Deep Slate `#121218`
  - Cards/Containers: Glassmorphic Navy `#1c1c28` (with border `#2c2c3e` and subtle shadow)
  - Primary Accent: Neon Cyan `#00f5ff` or Electric Violet `#8a2be2`
  - Text: High-contrast Off-White `#e4e4eb` and muted gray `#8c8c9e` for subtitles
- **Typography:** Outfit or Inter font (fallback to System Sans-serif).
- **Navigation:** Left sidebar drawer with 5 menu items (Sessions, New Session, Templates, Instructions, Settings).

### Backend-to-Frontend Architecture
```
  ┌─────────────────────────────────────────────────────────────┐
  │                        PYTHON BACKEND                       │
  │                                                             │
  │                     main.py (App Launcher)                 │
  │                              │                              │
  │                              ▼                              │
  │                    bridge.py (QObject Bridge) ◄────────────┐│
  │                    ├── Slots: run_generation(), etc.       ││
  │                    └── Signals: progress_changed, etc.     ││
  │                              │                             ││
  │                              ▼                             ││
  │                worker.py (QThread Pipeline Executor) ──────┘│
  │                └── Runs Step 0 to 5 asynchronously          │
  └──────────────────────────────┬──────────────────────────────┘
                                 │ PySide6/PyQt6 Context Property
                                 ▼
  ┌─────────────────────────────────────────────────────────────┐
  │                         QML FRONTEND                        │
  │                                                             │
  │                     main.qml (Main Window Layout)           │
  │                              │                              │
  │                              ▼                              │
  │                   StackView (Screen Navigation)             │
  │    ┌───────────────────┬───────────────┬────────────────┐   │
  │    ▼                   ▼               ▼                ▼   │
  │ Sessions.qml       Form.qml      Progress.qml      Result.qml│
  └─────────────────────────────────────────────────────────────┘
```

---

## Proposed Directory Structure

We will create a structured and modular directory under the workspace root:

```
Agent-For-TOM/
├── app/
│   ├── __init__.py
│   ├── bridge.py             # QObject subclass to communicate with QML
│   ├── worker.py             # QThread for running the pipeline in background
│   └── ui/
│       ├── main.qml          # Root window and navigation container
│       ├── screens/
│       │   ├── Dashboard.qml  # Screen 1: List of past sessions
│       │   ├── SessionForm.qml# Screen 2: Launch options & parameters
│       │   ├── Progress.qml   # Screen 3: Live progress log
│       │   ├── Result.qml     # Screen 4: Results & code viewer
│       │   └── TemplateBuilder.qml # Screen 5: Custom PDF/DOCX annotator
│       └── components/
│           ├── Sidebar.qml    # Side menu component
│           └── Card.qml       # Styled reusable container
├── main.py                   # Desktop Application Launcher
└── requirements.txt          # Python dependencies
```

---

## Starter Code Implementation

Below is the proposed implementation of the core boilerplate files.

### 1. [NEW] [main.py](file:///e:/Programming/Agent-For-TOM/main.py)
Entry point to load the QML engine, register the Python Bridge, and style the Material theme.

### 2. [NEW] [bridge.py](file:///e:/Programming/Agent-For-TOM/app/bridge.py)
The Python-to-QML bridge implementing slots for configuration loading, session listing, and launching threads.

### 3. [NEW] [worker.py](file:///e:/Programming/Agent-For-TOM/app/worker.py)
The background worker subclassing `QThread` executing pipeline stages sequentially without locking the GUI.

### 4. [NEW] [main.qml](file:///e:/Programming/Agent-For-TOM/app/ui/main.qml)
The parent QML container managing layout, dark theme settings, sidebar navigation, and StackView routing.

### 5. [NEW] [Dashboard.qml](file:///e:/Programming/Agent-For-TOM/app/ui/screens/Dashboard.qml)
The Session list screen displaying active, draft, and completed jobs with badge statuses.

### 6. [NEW] [SessionForm.qml](file:///e:/Programming/Agent-For-TOM/app/ui/screens/SessionForm.qml)
The parameter and document generation launch form.

---

## Verification Plan

### Automated Verification
- We will verify the Python structure runs by executing `python main.py` in the workspace context.
- Validate QML components compile cleanly through QML engine logs.

### Manual Verification
1. Launch application and observe the dark-mode layout and side navigation.
2. Toggle screens (Dashboard, New Session, Templates).
3. Test mock session generation: input dummy details in Form screen, press Generate, watch the mock progress bar fill, and view the final dummy result screen.
