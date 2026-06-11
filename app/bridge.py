"""
AppBridge — QObject that exposes Python slots and signals to QML.
Handles: session listing, template listing, navigation, mock generation.
"""
import json
import uuid
from datetime import datetime

# pyrefly: ignore [missing-import]
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot, pyqtProperty, QTimer, QUrl
# pyrefly: ignore [missing-import]
from PyQt6.QtWidgets import QFileDialog
import os
import shutil


# ─── Mock data ───────────────────────────────────────────────────────────────

MOCK_SESSIONS = [
    {
        "id": "s1", "name": "Лаба 1 — сортування масивів",
        "status": "completed", "template": "lab1", "hardness": "university_1",
        "duration": "14.3s", "created_at": "12 чер 2026, 14:23",
    },
    {
        "id": "s2", "name": "Звіт з практики — БД",
        "status": "completed", "template": "lab2", "hardness": "bachelor",
        "duration": "28.7s", "created_at": "11 чер 2026, 09:15",
    },
    {
        "id": "s3", "name": "Лаба 3 — графи, обхід у ширину",
        "status": "processing", "template": "lab1", "hardness": "university_1",
        "duration": "~10s", "created_at": "12 чер 2026, 14:28",
    },
    {
        "id": "s4", "name": "Тест — власний шаблон",
        "status": "warning", "template": "custom", "hardness": "school",
        "duration": "5.2s", "created_at": "10 чер 2026, 18:42",
    },
    {
        "id": "s5", "name": "Помилковий тест — невал. Python",
        "status": "failed", "template": "lab1", "hardness": "university_2",
        "duration": "3.1s", "created_at": "10 чер 2026, 18:30",
    },
    {
        "id": "s6", "name": "Чернетка — Лаба 2",
        "status": "draft", "template": "lab2", "hardness": "",
        "duration": "—", "created_at": "10 чер 2026, 15:11",
    },
]

MOCK_TEMPLATES = [
    {
        "id": "lab1", "name": "lab1", "display_name": "Лабораторна робота №1",
        "description": "Звіт про виконання лабораторної роботи з алгоритмів сортування",
        "is_builtin": True, "has_instructions": True,
    },
    {
        "id": "lab2", "name": "lab2", "display_name": "Лабораторна робота №2",
        "description": "Звіт з бази даних — проектування, нормалізація, SQL",
        "is_builtin": True, "has_instructions": True,
    },
    {
        "id": "custom1", "name": "custom1", "display_name": "Мій звіт",
        "description": "Користувацький шаблон без інструкцій",
        "is_builtin": False, "has_instructions": False,
    },
]

MOCK_INSTRUCTIONS = [
    {
        "id": "i1", "name": "Глобальні інструкції", "type": "global",
        "attached_to": None, "is_active": True,
        "created_at": "01 чер 2026",
    },
    {
        "id": "i2", "name": "Інструкції для Лаби 1", "type": "special",
        "attached_to": "Лабораторна робота №1", "is_active": True,
        "created_at": "02 чер 2026",
    },
    {
        "id": "i3", "name": "Інструкції для Лаби 2", "type": "special",
        "attached_to": "Лабораторна робота №2", "is_active": True,
        "created_at": "03 чер 2026",
    },
    {
        "id": "i4", "name": "Мій стиль написання", "type": "user_created",
        "attached_to": None, "is_active": True,
        "created_at": "05 чер 2026",
    },
]


class AppBridge(QObject):
    """
    Python ↔ QML bridge.
    Exposes mock data and pipeline simulation via Qt signals/slots.
    """

    # ─── Signals (Python → QML) ───────────────────────────────────────────────
    sessionsChanged = pyqtSignal()
    templatesChanged = pyqtSignal()
    instructionsChanged = pyqtSignal()
    currentScreenChanged = pyqtSignal()
    filesUpdated = pyqtSignal(str)
    fileWarning = pyqtSignal(str)   # warning message for the user
    isDarkThemeChanged = pyqtSignal()
    isSidebarCollapsedChanged = pyqtSignal()
    isBigFontChanged = pyqtSignal()
    sessionPayloadJsonChanged = pyqtSignal()

    # Pipeline signals
    pipelineStarted = pyqtSignal()
    pipelineProgress = pyqtSignal(int, str)        # percent, step_name
    pipelineLog = pyqtSignal(str, str)              # timestamp, message
    pipelineStepDone = pyqtSignal(int, str, str)   # step_index, name, detail
    pipelineStepActive = pyqtSignal(int)            # step_index
    pipelineFinished = pyqtSignal(str)              # session_id
    pipelineError = pyqtSignal(str, str)            # stage, message

    navigationRequest = pyqtSignal(str, str)        # screen, param

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sessions = list(MOCK_SESSIONS)
        self._templates = list(MOCK_TEMPLATES)
        self._instructions = list(MOCK_INSTRUCTIONS)
        self._current_screen = "documents"
        self._pipeline_timer = None
        self._pipeline_step = 0
        self._uploaded_files = []
        self._prefs_file = os.path.join(os.path.dirname(__file__), "config", "user_preferences.json")
        self._transit_file = os.path.join(os.path.dirname(__file__), "transit", "userInput.json")
        self._is_dark_theme = False
        self._is_sidebar_collapsed = False
        self._is_big_font = False
        self._session_payload_json = "{}"
        self._load_preferences()
        self._load_user_input()

    def _load_user_input(self):
        if os.path.exists(self._transit_file):
            try:
                with open(self._transit_file, "r", encoding="utf-8") as f:
                    self._session_payload_json = f.read()
                    if self._session_payload_json:
                        payload = json.loads(self._session_payload_json)
                        if "uploadedFiles" in payload and isinstance(payload["uploadedFiles"], list):
                            self._uploaded_files = payload["uploadedFiles"]
            except Exception as e:
                print(f"Error loading user input: {e}")

    @pyqtSlot()
    def clearTransitFolder(self):
        """Delete all files in transit folder except userInput.json."""
        transit_dir = os.path.join(os.path.dirname(__file__), "transit")
        if not os.path.exists(transit_dir):
            return
        for file in os.listdir(transit_dir):
            if file == "userInput.json":
                continue
            file_path = os.path.join(transit_dir, file)
            try:
                if os.path.isfile(file_path):
                    os.remove(file_path)
            except Exception as e:
                print(f"Error deleting file {file_path}: {e}")
        
        # Also clear the bridge's internal uploaded files list
        self._uploaded_files = []
        self.filesUpdated.emit("[]")

    def _save_user_input(self):
        try:
            os.makedirs(os.path.dirname(self._transit_file), exist_ok=True)
            if self._session_payload_json:
                payload = json.loads(self._session_payload_json)
                if "uploadedFiles" in payload and isinstance(payload["uploadedFiles"], list):
                    payload["uploadedFiles"] = [f for f in payload["uploadedFiles"] if f.get("status") == "done"]
                filtered_json = json.dumps(payload, ensure_ascii=False)
            else:
                filtered_json = "{}"
            with open(self._transit_file, "w", encoding="utf-8") as f:
                f.write(filtered_json)
        except Exception as e:
            print(f"Error saving user input: {e}")

    def _load_preferences(self):
        if os.path.exists(self._prefs_file):
            try:
                with open(self._prefs_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._is_dark_theme = data.get("isDarkTheme", False)
                    self._is_sidebar_collapsed = data.get("isSidebarCollapsed", False)
                    self._is_big_font = data.get("isBigFont", False)
            except Exception as e:
                print(f"Error loading preferences: {e}")

    def _save_preferences(self):
        try:
            data = {
                "isDarkTheme": self._is_dark_theme,
                "isSidebarCollapsed": self._is_sidebar_collapsed,
                "isBigFont": self._is_big_font
            }
            with open(self._prefs_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            print(f"Error saving preferences: {e}")

    # ─── Properties ───────────────────────────────────────────────────────────

    @pyqtProperty(bool, notify=isDarkThemeChanged)
    def isDarkTheme(self):
        return self._is_dark_theme

    @isDarkTheme.setter
    def isDarkTheme(self, val):
        if self._is_dark_theme != val:
            self._is_dark_theme = val
            self.isDarkThemeChanged.emit()
            self._save_preferences()

    @pyqtProperty(bool, notify=isSidebarCollapsedChanged)
    def isSidebarCollapsed(self):
        return self._is_sidebar_collapsed

    @isSidebarCollapsed.setter
    def isSidebarCollapsed(self, val):
        if self._is_sidebar_collapsed != val:
            self._is_sidebar_collapsed = val
            self.isSidebarCollapsedChanged.emit()
            self._save_preferences()

    @pyqtProperty(bool, notify=isBigFontChanged)
    def isBigFont(self):
        return self._is_big_font

    @isBigFont.setter
    def isBigFont(self, val):
        if self._is_big_font != val:
            self._is_big_font = val
            self.isBigFontChanged.emit()
            self._save_preferences()

    @pyqtProperty(str, notify=sessionPayloadJsonChanged)
    def sessionPayloadJson(self):
        return self._session_payload_json

    @sessionPayloadJson.setter
    def sessionPayloadJson(self, val):
        if self._session_payload_json != val:
            self._session_payload_json = val
            self.sessionPayloadJsonChanged.emit()
            self._save_user_input()
            if val:
                try:
                    payload = json.loads(val)
                    if "uploadedFiles" in payload and isinstance(payload["uploadedFiles"], list):
                        self._uploaded_files = payload["uploadedFiles"]
                except Exception:
                    pass

    @pyqtProperty(str, notify=currentScreenChanged)
    def currentScreen(self):
        return self._current_screen

    # ─── Slots (QML → Python) ─────────────────────────────────────────────────

    @pyqtSlot(result=str)
    def getSessions(self):
        """Return all sessions as JSON string."""
        return json.dumps(self._sessions, ensure_ascii=False)

    @pyqtSlot(str, result=str)
    def getSessionsFiltered(self, status_filter: str):
        """Return sessions filtered by status ('all', 'completed', 'failed', 'draft')."""
        if status_filter == "all":
            data = self._sessions
        else:
            data = [s for s in self._sessions if s["status"] == status_filter]
        return json.dumps(data, ensure_ascii=False)

    @pyqtSlot(str, result=str)
    def getSessionById(self, session_id: str):
        for s in self._sessions:
            if s["id"] == session_id:
                return json.dumps(s, ensure_ascii=False)
        return "{}"

    @pyqtSlot(str)
    def deleteSession(self, session_id: str):
        self._sessions = [s for s in self._sessions if s["id"] != session_id]
        self.sessionsChanged.emit()

    @pyqtSlot(str)
    def duplicateSession(self, session_id: str):
        for s in self._sessions:
            if s["id"] == session_id:
                new_s = dict(s)
                new_s["id"] = str(uuid.uuid4())[:8]
                new_s["name"] = s["name"] + " — копія"
                new_s["status"] = "draft"
                new_s["duration"] = "—"
                new_s["created_at"] = datetime.now().strftime("%d %b %Y, %H:%M")
                self._sessions.insert(0, new_s)
                self.sessionsChanged.emit()
                return

    @pyqtSlot(result=str)
    def getTemplates(self):
        return json.dumps(self._templates, ensure_ascii=False)

    @pyqtSlot(result=str)
    def getInstructions(self):
        return json.dumps(self._instructions, ensure_ascii=False)

    @pyqtSlot(str, result=str)
    def getInstructionsFiltered(self, type_filter: str):
        if type_filter == "all":
            data = self._instructions
        elif type_filter == "global":
            data = [i for i in self._instructions if i["type"] == "global"]
        elif type_filter == "special":
            data = [i for i in self._instructions if i["type"] == "special"]
        elif type_filter == "user_created":
            data = [i for i in self._instructions if i["type"] == "user_created"]
        elif type_filter == "unattached":
            data = [i for i in self._instructions if i["attached_to"] is None]
        else:
            data = self._instructions
        return json.dumps(data, ensure_ascii=False)

    @pyqtSlot(str)
    def navigate(self, screen: str):
        """Navigate to a screen by name."""
        self._current_screen = screen
        self.currentScreenChanged.emit()
        self.navigationRequest.emit(screen, "")

    @pyqtSlot(str, str)
    def navigateWithParam(self, screen: str, param: str):
        self._current_screen = screen
        self.currentScreenChanged.emit()
        self.navigationRequest.emit(screen, param)

    @pyqtSlot()
    def openFileDialog(self):
        # pyrefly: ignore [missing-import]
        from PyQt6.QtCore import QStandardPaths
        default_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        if not default_dir:
            default_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.HomeLocation)
            
        files, _ = QFileDialog.getOpenFileNames(
            None, 
            "Оберіть файли", 
            default_dir, 
            "Supported Files (*.pdf *.docx *.pptx *.png *.jpg *.jpeg)"
        )
        if files:
            urls = [QUrl.fromLocalFile(f).toString() for f in files]
            self.uploadFilesUrls(urls)

    @pyqtSlot('QVariantList')
    def uploadFilesUrls(self, urls):
        import threading
        thread = threading.Thread(target=self._process_files_sequentially, args=(urls,), daemon=True)
        thread.start()

    def _process_files_sequentially(self, urls):
        import time
        time.sleep(1)  # Delay after dialog closes
        
        app_dir = os.path.dirname(__file__)
        transit_dir = os.path.join(app_dir, "transit")
        os.makedirs(transit_dir, exist_ok=True)

        loaded_names = {f["name"] for f in self._uploaded_files}
        duplicates = []
        invalid_types = []

        for url in urls:
            if isinstance(url, QUrl):
                local_path = url.toLocalFile()
            else:
                local_path = QUrl(str(url)).toLocalFile()

            if not local_path or not os.path.exists(local_path):
                continue

            filename = os.path.basename(local_path)
            ext = os.path.splitext(filename)[1].lower()

            # Duplicate check
            if filename in loaded_names:
                duplicates.append(filename)
                continue

            # Type check
            if ext not in ['.pdf', '.docx', '.pptx', '.png', '.jpg', '.jpeg']:
                invalid_types.append(filename)
                continue

            try:
                size_bytes = os.path.getsize(local_path)
            except Exception as e:
                print(f"Error getting size for {filename}: {e}")
                continue

            # Human-readable size
            if size_bytes < 1024:
                meta = f"{size_bytes} Б"
            elif size_bytes < 1024 * 1024:
                meta = f"{size_bytes / 1024:.1f} КБ"
            else:
                meta = f"{size_bytes / (1024 * 1024):.1f} МБ"

            # Dummy symbol calculation
            if ext in ['.txt', '.md', '.json', '.xml', '.csv']:
                symbols = size_bytes
            else:
                symbols = min(size_bytes // 10, 180000)

            # Add to UI as processing
            file_record = {
                "name": filename,
                "meta": meta,
                "path": f"transit/{filename}",
                "symbols": symbols,
                "status": "processing"
            }
            self._uploaded_files.append(file_record)
            self.filesUpdated.emit(json.dumps(self._uploaded_files, ensure_ascii=False))

            # Simulate processing delay
            time.sleep(2)

            def update_status(status_str):
                for f in self._uploaded_files:
                    if f["name"] == filename:
                        f["status"] = status_str
                        break

            # 50MB limit check
            if size_bytes > 50 * 1024 * 1024:
                update_status("error")
                self.filesUpdated.emit(json.dumps(self._uploaded_files, ensure_ascii=False))
                self.fileWarning.emit(f"Файл перевищує ліміт у 50 МБ: {filename}")
                continue

            # Readability check
            try:
                with open(local_path, 'rb') as f:
                    f.read(1024)
            except Exception as e:
                print(f"File {filename} is not readable: {e}")
                update_status("error")
                self.filesUpdated.emit(json.dumps(self._uploaded_files, ensure_ascii=False))
                self.fileWarning.emit(f"Неможливо прочитати файл (пошкоджений або заблокований): {filename}")
                continue

            dest_path = os.path.join(transit_dir, filename)
            try:
                shutil.copy2(local_path, dest_path)
                update_status("done")
            except Exception as e:
                print(f"Error copying file {filename}: {e}")
                self._uploaded_files = [f for f in self._uploaded_files if f["name"] != filename]
                self.filesUpdated.emit(json.dumps(self._uploaded_files, ensure_ascii=False))
                self.fileWarning.emit(f"Помилка копіювання файлу: {filename}")
                continue

            loaded_names.add(filename)
            self.filesUpdated.emit(json.dumps(self._uploaded_files, ensure_ascii=False))

        warnings = []
        if duplicates:
            warnings.append(f"Файл(и) вже завантажені: {', '.join(duplicates)}")
        if invalid_types:
            warnings.append(f"Непідтримуваний формат: {', '.join(invalid_types)}")
        
        if warnings:
            self.fileWarning.emit(" | ".join(warnings))

        self.filesUpdated.emit(json.dumps(self._uploaded_files, ensure_ascii=False))

    @pyqtSlot(str)
    def deleteFile(self, filename: str):
        """Remove a file from the uploaded list and delete from transit."""
        self._uploaded_files = [f for f in self._uploaded_files if f["name"] != filename]
        app_dir = os.path.dirname(__file__)
        transit_path = os.path.join(app_dir, "transit", filename)
        if os.path.exists(transit_path):
            try:
                os.remove(transit_path)
            except Exception as e:
                print(f"Could not delete transit file {filename}: {e}")
        self.filesUpdated.emit(json.dumps(self._uploaded_files, ensure_ascii=False))

    # ─── Pipeline simulation ──────────────────────────────────────────────────

    _PIPELINE_STEPS = [
        (0,  16, "Конвертація файлів",    "2 файли, хешування, кешування"),
        (16, 33, "Text LLM (Pass 1)",     "5 вхідних елементів → filled.py"),
        (33, 50, "Валідація",             "ast.parse + плейсхолдери + word count"),
        (50, 75, "Генерація зображень",   "Matplotlib + HuggingFace FLUX"),
        (75, 90, "Виконання + Compose",   "subprocess filled.py → DOCX + PNG"),
        (90, 100, "PDF компіляція",       "DOCX → PDF через reportlab"),
    ]

    _LOG_MESSAGES = [
        (0,  "[convert] Перевірка хешів файлів…"),
        (1,  "[convert] Сортування_методичка.pdf → 2 314 tokens"),
        (2,  "[convert] lecture_sorting.pptx → 2 507 tokens"),
        (3,  "[llm] Збірка контексту: global + special + user files"),
        (4,  "[llm] POST /v1/chat/completions — input=4821"),
        (5,  "[cache] Hit: 3 200 tokens (67% prefix match)"),
        (6,  "[llm] output=2103 tokens, 12 плейсхолдерів заповнено"),
        (7,  "[validate] ast.parse(filled.py) → OK"),
        (8,  "[validate] ManifestValidator: 2 refs ✓"),
        (9,  "[validate] Word count: 1 923 (target 1700–2500) ✓"),
        (10, "[image] Generating fig1 (matplotlib, bubble sort diagram)"),
        (11, "[image] fig1.png saved (24 KB)"),
        (12, "[image] Generating fig2 (huggingface, quicksort schema)"),
        (13, "[image] fig2.png saved (187 KB)"),
        (14, "[execute] AST validation OK — no forbidden calls"),
        (15, "[execute] subprocess.run(filled.py) → exit 0"),
        (16, "[compose] Inserting fig1.png @ anchor [[IMAGE|diagram|…]]"),
        (17, "[compose] Inserting fig2.png @ anchor [[IMAGE|schema|…]]"),
        (18, "[pdf] reportlab → output.pdf (18 pages)"),
        (19, "[done] Session completed successfully ✓"),
    ]

    @pyqtSlot(str, str)
    def saveSessionJson(self, payload_str, file_name):
        import json
        import os
        import shutil
        from datetime import datetime
        
        try:
            payload = json.loads(payload_str)
            payload["saved_at"] = datetime.now().isoformat()
            
            sessions_dir = os.path.join(os.path.dirname(__file__), "db")
            
            safe_name = "".join(c for c in file_name if c.isalnum() or c in " -_").strip()
            if not safe_name:
                safe_name = "session"
                
            session_folder = os.path.join(sessions_dir, safe_name)
            os.makedirs(session_folder, exist_ok=True)
            
            transit_dir = os.path.join(os.path.dirname(__file__), "transit")
            local_file_paths = []
            
            if "uploadedFiles" in payload and isinstance(payload["uploadedFiles"], list):
                for f_info in payload["uploadedFiles"]:
                    fname = f_info.get("name")
                    if fname:
                        src_path = os.path.join(transit_dir, fname)
                        if os.path.exists(src_path):
                            dst_path = os.path.join(session_folder, fname)
                            try:
                                shutil.move(src_path, dst_path)
                                local_file_paths.append(os.path.abspath(dst_path).replace("\\\\", "/"))
                            except Exception as e:
                                print(f"[bridge] Error moving file {fname}: {e}")
            
            payload["localFilePaths"] = local_file_paths
            
            file_path = os.path.join(session_folder, f"{safe_name}.json")
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=4, ensure_ascii=False)
                
            print(f"[bridge] Saved session to folder: {session_folder}")
        except Exception as e:
            print(f"[bridge] Error saving session JSON: {e}")

    @pyqtSlot(str, str, str, str, str, str)
    def startGeneration(self, session_name, template_id, length, hardness, image_mode, goal):
        """Start a mock pipeline generation."""
        # Create a new session in processing state
        new_id = str(uuid.uuid4())[:8]
        new_session = {
            "id": new_id,
            "name": session_name or "Нова сесія",
            "status": "processing",
            "template": template_id or "lab1",
            "hardness": hardness or "university_1",
            "duration": "—",
            "created_at": datetime.now().strftime("%d %b %Y, %H:%M"),
        }
        self._sessions.insert(0, new_session)
        self.sessionsChanged.emit()

        self._active_session_id = new_id
        self._pipeline_step = 0
        self._pipeline_log_index = 0
        self.pipelineStarted.emit()

        # Start the step-by-step simulation
        self._run_step(0)

    def _run_step(self, step_index: int):
        if step_index >= len(self._PIPELINE_STEPS):
            # Done!
            for s in self._sessions:
                if s["id"] == self._active_session_id:
                    s["status"] = "completed"
                    s["duration"] = "16.4s"
                    break
            self.sessionsChanged.emit()
            self.pipelineProgress.emit(100, "Готово")
            self.pipelineFinished.emit(self._active_session_id)
            return

        idx, pct_start, pct_end, name, detail = (
            step_index,
            *self._PIPELINE_STEPS[step_index]
        )
        self.pipelineStepActive.emit(step_index)
        self.pipelineProgress.emit(pct_start, name)

        # Emit some log lines during this step
        for log_i, (log_step, log_msg) in enumerate(self._LOG_MESSAGES):
            if log_step == step_index * 3 // 1 or log_step == step_index * 3 + 1:
                ts = datetime.now().strftime("%H:%M:%S")
                QTimer.singleShot(
                    log_i * 60,
                    lambda msg=log_msg, t=ts: self.pipelineLog.emit(t, msg)
                )

        # Schedule step completion
        QTimer.singleShot(
            1800,
            lambda si=step_index, n=name, d=detail, pe=pct_end: self._complete_step(si, n, d, pe)
        )

    def _complete_step(self, step_index, name, detail, pct_end):
        self.pipelineStepDone.emit(step_index, name, detail)
        self.pipelineProgress.emit(pct_end, name)
        # Move to next step
        QTimer.singleShot(300, lambda: self._run_step(step_index + 1))

    @pyqtSlot()
    def cancelPipeline(self):
        if hasattr(self, '_active_session_id'):
            for s in self._sessions:
                if s["id"] == self._active_session_id:
                    s["status"] = "cancelled"
                    break
            self.sessionsChanged.emit()

    @pyqtSlot(result=str)
    def getMockResult(self):
        """Return mock result data for the result screen."""
        return json.dumps({
            "session_name": "Лаба 1 — сортування масивів",
            "status": "completed",
            "token_usage": {
                "text_model": {"input_tokens": 4821, "output_tokens": 2103, "cached_tokens": 3200},
                "image_model": {"images_generated": 2}
            },
            "duration_ms": 16400,
            "image_count": 2,
            "word_count": 1923,
            "word_count_min": 1700,
            "word_count_max": 2500,
            "warnings": ["Word count 3% нижче від мети"],
        }, ensure_ascii=False)

    @pyqtSlot(result=str)
    def getMockFilledPy(self):
        """Return mock filled.py content for syntax-highlighted preview."""
        return '''# custom_template_lab1.py
# Template: Лабораторна робота №1
# Auto-generated by Agent-for-TOM

from docx import Document
from docx.shared import Pt, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate

def create_docx(filename):
    doc = Document()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("Міністерство освіти і науки України")
    run.font.name = "Times New Roman"
    run.font.size = Pt(14)

    doc.add_heading("Лабораторна робота №1", level=1)
    doc.add_paragraph("""
Мета роботи: порівняти ефективність бульбашкового сортування
та quicksort на масивах різного розміру. Провести часові
характеристики для масивів розміром N = 100, 1000, 10000.
""")

    [[IMAGE|type:diagram|subject:Порівняння часу сортування|context:Графік O(n²) vs O(n log n)|style:labeled]]

    doc.add_heading("Висновки", level=2)
    doc.add_paragraph("""
Quicksort демонструє значно кращу продуктивність на великих
масивах завдяки середній складності O(n log n) порівняно з
O(n²) у bubble sort.
""")
    doc.save(filename)

def create_pdf(filename):
    doc = SimpleDocTemplate(filename, pagesize=A4)
    # ... (аналогічний вміст)
    doc.build([])
'''
