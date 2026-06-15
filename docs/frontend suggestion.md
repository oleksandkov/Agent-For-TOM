# Frontend Suggestion — Agent-For-Labs (Combined v2)

## З яких файлів виведені вимоги

| Джерело | Що впливає на frontend |
|---|---|
| `apps_loogic.md` | 2-pass pipeline, прогрес-бари, матриця помилок, 5 input elements, параметри LLM |
| `the configurate own template.md` | Завантаження PDF/DOCX, rich text preview, анотації, Markdown редактор інструкцій, список шаблонів з бейджами |
| `database.md` | Структура сесій, restore/duplicate, input_snapshot, модальні помилки |

---

## Повний список UI компонентів

### Екран 1 — Головна / Список сесій
| Компонент | Джерело |
|---|---|
| Таблиця сесій (status, template, name, created_at) | `database.md: sessions` |
| Кнопки: Нова сесія, Дублювати, Видалити | `database.md` rules 3-4 |
| Фільтр по статусу (draft / completed / failed) | `sessions.status` |
| Пошук по назві | `sessions.name` |

### Екран 2 — Форма запуску (New Session / Restore)
| Компонент | Джерело |
|---|---|
| Select шаблону + бейджі (built-in, has_instructions) | `the configurate own template.md` бейджі |
| Text fields: name, theme, goal | `database.md` input_snapshot |
| Select: length (short/middle/long/large) | `apps_loogic.md` режими довжини |
| Select: hardness (school/university_1/university_2/bachelor) | `apps_loogic.md` рівні складності |
| Radio: image_mode (full / references / none) | `apps_loogic.md` Image Reference Protocol |
| Gap fields (якщо шаблон має gaps) | `the configurate own template.md`: gap_values |
| File upload (PDF/DOCX/PPTX/PNG/JPG/WEBP) | `apps_loogic.md` Step 0 |
| Preview завантажених файлів + статус конвертації | `apps_loogic.md` Step 0 |
| Кнопка "Generate" | pipeline start |

### Екран 3 — Pipeline прогрес
| Компонент | Джерело |
|---|---|
| Progress bar (0–100%) | `apps_loogic.md` 5 steps |
| Step labels: Convert → Text → Validate → Images → Execute → Compose | `apps_loogic.md` |
| Live log / status per step | `apps_loogic.md` |
| Cancel button | `sessions.status = cancelled` |
| HARD FAIL → STOP + error message + traceback | `apps_loogic.md` матриця помилок |
| WARN → жовтий банер (continue) | `apps_loogic.md` матриця помилок |

### Екран 4 — Результат
| Компонент | Джерело |
|---|---|
| Кнопки: Download DOCX, Download PDF | `sessions.docx_output` |
| Статистика: token_usage, duration_ms, image_count | `database.md: sessions` |
| Word count vs target | `validation_result` |
| Список згенерованих images | `image_count` |
| Show filled.py (read-only with syntax highlight) | `filled_py_path` |

### Екран 5 — Створення шаблону
| Компонент | Джерело |
|---|---|
| File upload (PDF/DOCX) | `the configurate own template.md` Step 1 |
| Left panel: rich text preview з оригінальним форматуванням | `the configurate own template.md` Step 1 |
| Right panel: tools (4 types annotations) | `the configurate own template.md` Step 2 |
| Color highlights per annotation type | Step 2 візуалізація |
| Save dialog: name, review annotations | Step 3 |
| Modal: "Add instructions?" | Step 3 |
| Warning: "Format may be inaccurate" для PDF | Step 1 |

### Екран 6 — Редактор інструкцій
| Компонент | Джерело |
|---|---|
| Markdown editor + preview | `the configurate own template.md` Step 4 |
| Auto-filled placeholder keys | `configurate own template.md` |
| Save → create version (`is_active=0` old) | `database.md: instructions` |
| Історія версій з diff | `database.md: instructions` |

### Екран 7 — Управління інструкціями
| Компонент | Джерело |
|---|---|
| Список всіх інструкцій (name, type, status, attached_to) | `the configurate own template.md` |
| Фільтри: global / per template / unattached | |
| Дії: Edit, Attach, Detach, Create | |

---

## Порівняння frontend варіантів

| Критерій | **PyQt6/PySide6** | **Electron + React** | **Tkinter + ttkbootstrap** | **PyWebView + Vue** |
|---|---|---|---|---|
| **Rich text preview** (шрифт, розмір, bold) | ✅ QTextEdit / QTextBrowser з HTML | ✅ ContentEditable / Draft.js / ProseMirror | ❌ Tk text — limited | ✅ Vue + contenteditable |
| **Syntax highlight** (Python код) | ✅ QSyntaxHighlighter | ✅ Many libs (CodeMirror, Prism) | ❌ No | ✅ CodeMirror |
| **Annotation overlay** (виділення → 4 типи) | ❌ Складно — власний QGraphicsView | ✅ ProseMirror / Slate.js (ідеально) | ❌ Дуже складно | ✅ Slate.js / TipTap |
| **File drag-drop + preview** | ✅ QDrag + QLabel | ✅ HTML5 drag | ❌ Базово | ✅ HTML5 drag |
| **QSyntaxHighlighter для .py** | ✅ Native (швидко, 0 зусиль) | ❌ Потрібна бібліотека | ❌ | ✅ CodeMirror |
| **Віджети прогресу** | ✅ QProgressBar | ✅ Progress шаблони | ❌ ttk.Progressbar | ✅ Vue компоненти |
| **Threading** (pipeline без зависання) | ✅ QThread (native, надійно) | ✅ Web Workers / IPC | ❌ threading + tk.update() | ❌ Web Workers |
| **Native file dialogs** | ✅ QFileDialog (native OS) | ❌ Electron dialog (браузер) | ✅ filedialog | ✅ Electron dialog |
| **Розмір бандлу** | ~50 MB (Python + Qt) | ~200 MB (Node + Electron) | ~10 MB (вбудований) | ~80 MB |
| **Сучасний UI вигляд** | ⭐⭐⭐⭐ (Qt Styles / QML) | ⭐⭐⭐⭐⭐ (будь-який CSS) | ⭐⭐ | ⭐⭐⭐⭐ |
| **Швидкість розробки** | ⭐⭐⭐ (багато коду) | ⭐⭐⭐ (росіяни) | ⭐⭐⭐⭐⭐ (швидко) | ⭐⭐⭐ |
| **Інтеграція з Python** | ⭐⭐⭐⭐⭐ (native) | ⭐⭐ (child process) | ⭐⭐⭐⭐⭐ (native) | ⭐⭐ (child process) |
| **Desktop-native feel** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ |
| **Готові бібліотеки для text annotation** | ❌ Мало | ✅ Багато (Draft.js, Slate, TipTap, Quill) | ❌ | ✅ Багато |
| **Learning curve команди** | Medium | High | Low | Medium |

---

## Рекомендація: PyQt6 + QML / Qt Quick

**Чому PyQt6:**

1. **Native threading** — QThread для 2-pass pipeline. Жоден web-варіант не дасть такої простоти: запустити QThread, emit signal на кожному step, UI не зависає.

2. **QSyntaxHighlighter** — готовий клас для підсвітки Python-коду в `filled.py`.

3. **Native file operations** — QFileDialog, QDrag, QFileSystemWatcher.

4. **QTextEdit + HTML subset** — достатньо для rich text preview (шрифт, розмір, bold, italic, alignment). Не ідеально, але працює.

5. **QML/Qt Quick** для Canvas — annotation overlays можна реалізувати через Qt Quick Canvas + JavaScript.

**Де PyQt6 слабкий (і як виправити):**

| Проблема | Рішення |
|---|---|
| Annotation selection (перетин текст + overlay) | QML Canvas + MouseArea — емітувати сигнали в Python |
| Сучасний UI | Qt Quick Controls 2 + Material theme |
| Syntax highlight для інструкцій | QML TextEdit + власний highlight (або передати HTML) |

**Альтернатива якщо annotation критичний:**

Якщо annotation overlay (текст + візуальне виділення + спливаючі меню) — *найважливіший* екран, і потрібна максимальна якість → **Electron + Slate.js / ProseMirror**. Але за це платити double runtime + ~150MB зайвого.

---

## Архітектура PyQt6

```
┌─────────────────────────────────────┐
│  PyQt6 App (QApplication)           │
│  ┌───────────────────────────────┐  │
│  │  QStackedWidget (7 screens)   │  │
│  │  ┌─── Screen 1: Sessions    ─┐│  │
│  │  │ QTableView (sessions)     ││  │
│  │  │ Filter bar                ││  │
│  │  └───────────────────────────┘│  │
│  │  ┌─── Screen 2: Form        ─┐│  │
│  │  │ QComboBox (template)      ││  │
│  │  │ QLineEdit (name/theme)    ││  │
│  │  │ QComboBox (length/etc)    ││  │
│  │  │ FileListWidget (uploads)  ││  │
│  │  └───────────────────────────┘│  │
│  │  ┌─── Screen 3: Progress    ─┐│  │
│  │  │ QProgressBar              ││  │
│  │  │ QListWidget (step log)    ││  │
│  │  └───────────────────────────┘│  │
│  │  ┌─── Screen 4: Result      ─┐│  │
│  │  │ QTextEdit (filled.py)     ││  │
│  │  │ Download buttons          ││  │
│  │  └───────────────────────────┘│  │
│  │  ┌─── Screen 5: Template     ─┐│  │
│  │  │ QSplitter                  ││  │
│  │  │   Left: QTextBrowser       ││  │
│  │  │   Right: Annotation panel  ││  │
│  │  └───────────────────────────┘│  │
│  │  ┌─── Screen 6: Instructions ─┐│  │
│  │  │ QPlainTextEdit (markdown)  ││  │
│  │  │ Preview panel              ││  │
│  │  └───────────────────────────┘│  │
│  └───────────────────────────────┘  │
│                                      │
│  ┌─ Pipeline Thread ──────────────┐  │
│  │  QThread                       │  │
│  │  Orchestrator.run()            │  │
│  │  │  signal step_changed(name)  │  │
│  │  │  signal progress(pct)       │  │
│  │  │  signal log(msg)            │  │
│  │  │  signal error(stage, msg)   │  │
│  │  │  signal done(result)        │  │
│  │  └─────────────────────────────┘  │
│                                      │
│  ┌─ SQLite ───────────────────────┐  │
│  │  sqlite3 module                │  │
│  │  Models: ORM-like helpers      │  │
│  │  CacheManager (write-through)  │  │
│  └─────────────────────────────────┘  │
└─────────────────────────────────────┘
```

---

## Підсумкова таблиця

| Варіант | Рейтинг | Annotation | Rich text | Threading | Native |
|---|---|---|---|---|---|
| **PyQt6 + QML** | ⭐⭐⭐⭐⭐ | добре (QML Canvas) | добре | ідеально | ідеально |
| Electron + React | ⭐⭐⭐⭐ | ідеально | ідеально | складно | посередньо |
| Tkinter + ttkbootstrap | ⭐⭐ | погано | погано | посередньо | добре |
| PyWebView + Vue | ⭐⭐⭐ | добре | добре | складно | посередньо |

**Висновок:** PyQt6 — найкращий вибір для Python-centric десктоп додатку з threading, syntax highlight і native OS інтеграцією. Якщо annotation overlay виявиться занадто складним у QML — можна використати `QWebEngineView` тільки для екрану створення шаблону (через Vue/Slate.js), залишивши решту в PyQt6.
