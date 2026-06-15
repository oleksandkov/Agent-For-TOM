# План реалізації бази даних Agent-For-TOM

> **Статус:** детальний план, готовий до виконання.
> **Джерело правди для схеми:** `docs/database.md` (9 таблиць + 3 кеш-таблиці).
> **Джерело правди для контрактів коду:** `app/backend/cache_manager.py`, `app/bridge.py`, `app/backend/manifest_validator.py`.
> **Поточний стан:** `app/db/data.txt` — порожній файл-заглушка, `app/config/user_preferences.json` — JSON замість БД, сесії зберігаються як JSON-файли в `app/db/<safe_name>/` (див. `bridge.saveSessionJson`).

---

## 0. Виправлення ідей з `database questoin.md`

Оригінальний документ пропонує таке:

| Ідея | Що не так | Що робити |
|---|---|---|
| БД у `app/db/` | Правильно задумка, але `app/db/` вже існує й містить `data.txt` — порожню заглушку, яка **вводить в оману** | Перейменувати `data.txt` → `README.md` з описом структури, додати підпапки |
| Зберігати лише сесії + config | Замало. Потрібно ще: бібліотека файлів із дедуплікацією за SHA-256, шаблони, версіоновані інструкції, user_style, кастомні анотації шаблонів, кеші LLM/зображень/документів, секрети (HF token), налаштування застосунку | Повна схема з 9 основних + 3 кеш-таблиць (див. `docs/database.md`) |
| `compactTime` — одне поле | Семантично не визначене, без контексту | Розділити на `compact_ms`, `llm_input_tokens`, `llm_output_tokens`, `llm_cached_tokens`, `image_count`, `image_gen_ms`, `validate_ms`, `execute_ms`, `compose_ms` |
| «config files» | Розмито. Config — це `user_preferences.json` і `secrets.json`? Інструкції? Стиль? | Розділити: `instructions` (версіоновані), `user_style` (версіонований), `app_settings` (key-value), `secrets` (зашифровані окремо) |
| README з поясненням кожної таблиці | Документ не визначає формат README і не прив'язує до жодної конкретної таблиці | Зробити `app/db/README.md` із структурою `schema/`, `migrations/`, `backups/`, `README.md` з повним описом усіх таблиць |
| Дедуплікація файлів | Не згадано, за чим дедуплікувати | За **SHA-256** (`library_file.file_hash` UNIQUE) — це вже зафіксовано в `docs/database.md` |
| `info` без таймінгів | Немає `processing_started_at`, `processing_finished_at` окремо від `created_at` і `completed_at` | Додати `started_at`, `completed_at`, `cancelled_at`, `failed_at` — потрібно для UX (тривалість у UI) |
| Шляхи | Не сказано, абсолютні чи відносні | Усі шляхи в БД — **відносні до program root** (`docs/database.md`, правило 6 у «Правила для UI») |

---

## 1. Цілі реалізації

### 1.1. Глобальні цілі
1. **Один файл SQLite** — `app/db/agent.db` — замість розкиданих JSON-файлів.
2. **Повна відповідність** `docs/database.md` (9 основних + 3 кеш-таблиці).
3. **Ідемпотентна міграція** — існуючий `cache.db` з `cache_manager.py` має бути перенесений у новий `agent.db` без втрати даних.
4. **Зворотна сумісність на 1 крок** — якщо схема БД зміниться, `db_version` дозволить оновити без ручного втручання.
5. **Потокобезпечність** — одночасно можуть писати UI (сесії, файли) і pipeline (кеші, лог-стадії). Використовуємо `WAL` режим SQLite + короткі транзакції.
6. **Тестабельність** — кожен репозиторій має публічний API, який можна викликати з unit-тестів без підняття Qt.

### 1.2. Не-цілі
- Не змінюємо `app/instructions/*.md` — вони залишаються файлами на диску, у БД тільки **метадані** версій.
- Не переносимо `cache_manager.py` в `agent.db` одразу — спочатку мігруємо, потім об'єднуємо.
- Не чіпаємо UI в цій фазі — тільки бекенд.

---

## 2. Структура файлів

```
app/db/
├── agent.db                 ← єдиний файл SQLite (створюється автоматично)
├── README.md                ← опис таблиць + операцій
├── schema/
│   ├── 001_init.sql         ← створення 9 основних таблиць
│   ├── 002_cache.sql        ← створення 3 кеш-таблиць
│   ├── 003_indexes.sql      ← всі індекси
│   ├── 004_seeds.sql        ← початкові дані (templates, global instructions)
│   └── migrations/          ← номеровані міграції
│       └── 002_add_started_at.sql
├── backups/                 ← автоматичні копії перед міграцією
└── migrations/              ← (порожньо, використовується лише schema/migrations)
```

**Зміни в `app/db/`:**
- Видалити `data.txt` (порожня заглушка, що вводить в оману).
- Додати всі файли з дерева вище.

**Зміни в `app/config/`:**
- `user_preferences.json` → перенести в `app_settings` (БД), залишити файл тільки як fallback для першого запуску.
- `secrets.json` (якщо існує) → перенести в таблицю `secrets` із шифруванням (див. крок 7).

**Зміни в `app/backend/`:**
- Додати `app/backend/db/__init__.py`, `app/backend/db/connection.py`, `app/backend/db/migrations.py`, `app/backend/db/repositories/*.py`.

---

## 3. Схема — що створюємо (деталі)

### 3.1. Базові таблиці (з `docs/database.md`)

Повні DDL — у `schema/001_init.sql`. Коротко по кожній:

#### `templates` (9 рядків у `docs/database.md`)
- PK: `id` (UUID TEXT)
- Унікальний: `name`
- Поля: `script_path`, `instructions_path`, `is_builtin`, `has_instructions`, `supports_images`, `placeholder_schema` (JSON), `gap_schema` (JSON), `source_file_id` (FK → `library_file`), `created_at`, `updated_at`
- **Зв'язки:** 1-N до `instructions`, 1-N до `custom_template_annotations`

#### `instructions` (версіонована)
- PK: `id` (UUID)
- FK: `template_id` (NULL = глобальна)
- `type` ∈ {`global`, `special`, `user_created`}
- Унікальна активна: один рядок з `is_active=1` на комбінацію `(template_id, type)`
- Індекси: `idx_instructions_template`, `idx_instructions_active`

#### `user_style`
- Версіонована, як `instructions`
- Додаткове поле `is_empty` (BOOL): якщо `is_empty=1` — pipeline пропускає її (зафіксовано в `docs/database.md` § user_style)

#### `sessions`
- PK: `id` (UUID)
- FK: `template_id` (NOT NULL)
- `status` ∈ {`draft`, `processing`, `completed`, `failed`, `cancelled`}
- `input_snapshot` (JSON, NOT NULL) — заморожена копія всіх вхідних даних
- `validation_result` (JSON NULL)
- `token_usage` (JSON NULL) — структура з `docs/database.md` § token_usage
- `error_stage` ∈ {`file_convert`, `text_model`, `validate`, `image_gen`, `execute`, `compose`} (NULL якщо успіх)
- `duration_ms` (INTEGER)
- `global_instructions_hash`, `style_hash` (TEXT NULL) — для трасування, які саме версії використано
- `image_count` (INTEGER DEFAULT 0)
- Індекси: `idx_sessions_created` (DESC), `idx_sessions_status`

**Покращення проти `docs/database.md`:**
- Додати `started_at TEXT NULL` — час початку processing (для точної тривалості).
- Додати `cancelled_at TEXT NULL` — щоб UI не перераховував «тривалість» для скасованих сесій.

#### `session_files` (join)
- PK: `id` (INTEGER AUTOINCREMENT)
- FK: `session_id` (CASCADE DELETE), `file_id`
- `was_summarized` (BOOL) — чи був файл стиснутий
- `token_count_used` (INTEGER)
- Індекси: `idx_session_files_session`, `idx_session_files_file`

#### `library_file`
- PK: `id` (UUID)
- Унікальний: `file_hash` (SHA-256, TEXT)
- `original_name`, `original_type` (MIME), `stored_path`, `converted_text` (TEXT NULL), `conversion_status` ∈ {`pending`, `done`, `failed`}
- `last_used_at` — оновлюється щоразу, коли файл додається до нової сесії
- Індекси: `idx_library_hash`, `idx_library_user`

**Покращення:** додати `converted_at TEXT NULL` — коли саме текст було конвертовано (для TTL кешу конвертації).

#### `custom_template_annotations`
- PK: `id` (UUID)
- FK: `template_id` (CASCADE), `source_file_id` (CASCADE)
- `annotations` (JSON) — масив об'єктів `{type, text, format, gap_name}`

### 3.2. Кеш-таблиці (3 шт)

Уже реалізовані в `app/backend/cache_manager.py`, але в **окремому файлі** `cache.db`. Треба перенести в `agent.db` або створити аліас.

**Рішення:** створюємо в `agent.db` (3 кеш-таблиці), а `cache_manager.py` отримує новий параметр `db_path` (default = `app/db/agent.db`).

Схеми:
- `llm_cache` — `cache_key` (UNIQUE, SHA-256(template_id + params + user_files_hash + style_hash))
- `image_cache` — `prompt_hash` (UNIQUE, SHA-256(render_spec))
- `document_cache` — `content_hash` (UNIQUE, SHA-256(filled.py без anchor-маркерів))

**Важливе зауваження:** у `cache_manager.py` є помилка — `re` імпортується в кінці файлу (рядок 313: `import re  # noqa: E402`). Це працює, але це anti-pattern. У новій версії перенести на початок.

### 3.3. Нові таблиці (відсутні в `docs/database.md`, але потрібні)

#### `app_settings` (key-value)
- PK: `key` (TEXT)
- `value_json` (TEXT) — серіалізований JSON
- `updated_at` (TEXT)

**Призначення:** заміна `app/config/user_preferences.json`. Зберігає:
```json
{
  "ui.isDarkTheme": true,
  "ui.isSidebarCollapsed": true,
  "ui.isBigFont": false,
  "cache.llm_ttl_days": 30,
  "cache.image_ttl_days": 60,
  "cache.document_ttl_days": 365,
  "session.retention_days": 90,
  "pipeline.cancellation_timeout_ms": 5000
}
```

#### `secrets`
- PK: `key` (TEXT)
- `value_encrypted` (BLOB) — зашифрований токен
- `updated_at` (TEXT)

**Шифрування:** через `cryptography.fernet` (або Windows DPAPI через `keyring`). Ключ — у `app/config/.db_key` з обмеженим доступом (0600). Якщо ключ зникає — секрети треба ввести повторно.

**Початкові секрети:**
- `hf.token` — HuggingFace API токен (єдиний секрет, згаданий у `docs/...`)

#### `pipeline_runs` (child of `sessions`)
- PK: `id` (UUID)
- FK: `session_id` (CASCADE)
- `stage` TEXT — `file_convert` | `text_model` | `validate` | `image_gen` | `execute` | `compose`
- `status` TEXT — `started` | `ok` | `warn` | `error`
- `started_at`, `ended_at` (TEXT)
- `duration_ms` (INTEGER)
- `error_message` (TEXT NULL)
- `log_excerpt` (TEXT NULL) — перші 2 КБ логів стадії

**Призначення:** детальний таймінг pipeline + матриця помилок. `sessions.error_stage` посилається на цю таблицю. `docs/...` згадує «error_stage колонка» — без деталей, де зберігати повідомлення про помилку.

#### `audit_log` (опційно, але рекомендовано)
- PK: `id` (INTEGER AUTOINCREMENT)
- `at` (TEXT) — ISO datetime
- `actor` TEXT — `ui` | `pipeline` | `cli`
- `action` TEXT — `session.create`, `session.cancel`, `template.edit`, `cache.invalidate` тощо
- `target_id` TEXT NULL — UUID цільової сутності
- `details` JSON NULL

**Призначення:** дебаг, аудит, частково GDPR/безпека. Без неї неможливо відповісти на «хто видалив сесію s3 12 чер о 14:28?».

---

## 4. Індекси (повний список)

```sql
-- sessions
CREATE INDEX idx_sessions_created      ON sessions(created_at DESC);
CREATE INDEX idx_sessions_status       ON sessions(status);
CREATE INDEX idx_sessions_template     ON sessions(template_id);
CREATE INDEX idx_sessions_completed    ON sessions(completed_at) WHERE completed_at IS NOT NULL;

-- session_files
CREATE INDEX idx_session_files_session ON session_files(session_id);
CREATE INDEX idx_session_files_file    ON session_files(file_id);

-- library_file
CREATE INDEX idx_library_hash          ON library_file(file_hash);
CREATE INDEX idx_library_user          ON library_file(created_at);
CREATE INDEX idx_library_last_used     ON library_file(last_used_at);

-- instructions
CREATE INDEX idx_instructions_template ON instructions(template_id);
CREATE INDEX idx_instructions_active   ON instructions(is_active);

-- templates
CREATE UNIQUE INDEX idx_templates_name ON templates(name);

-- custom_template_annotations
CREATE INDEX idx_annotations_template  ON custom_template_annotations(template_id);

-- cache tables (вже є в cache_manager.py)
CREATE INDEX idx_llm_cache_key         ON llm_cache(cache_key);
CREATE INDEX idx_image_cache_hash      ON image_cache(prompt_hash);
CREATE INDEX idx_document_cache_hash   ON document_cache(content_hash);

-- pipeline_runs
CREATE INDEX idx_pipeline_session      ON pipeline_runs(session_id);
CREATE INDEX idx_pipeline_stage        ON pipeline_runs(stage);

-- audit_log
CREATE INDEX idx_audit_at              ON audit_log(at DESC);
CREATE INDEX idx_audit_action          ON audit_log(action);
```

---

## 5. Зв'язки та cascade-правила

```
library_file ──< session_files >── sessions ──> templates
                                          │           │
                                          │           ├── instructions (1-N)
                                          │           └── custom_template_annotations (1-N)
                                          │
                                    pipeline_runs (1-N)
                                    audit_log (target_id, без FK)
```

**Cascade-правила (оновлені, частково відрізняються від `docs/database.md`):**

| Дія | Що каскадує | Що НЕ каскадує |
|---|---|---|
| `DELETE session` | `session_files`, `pipeline_runs`, файли `filled.py/docx/pdf` з диску | `library_file` (файли лишаються для reuse) |
| `DELETE library_file` | — | `session_files` (ЗАБОРОНЕНО видаляти, якщо є посилання) — див. правило 2 у `docs/database.md` |
| `DELETE template` | `instructions` (для `template_id` цього шаблону), `custom_template_annotations`, `sessions` → cascade blocked (або встановлює `template_id=NULL`, що порушує FK) | — |
| `DELETE instruction` | — | `sessions.input_snapshot` (там копія, не FK) |

**Зміна проти `docs/database.md`:** для `sessions.template_id` додати `ON DELETE RESTRICT` — не можна видалити шаблон, поки є сесії, що його використовують. Це захищає історію.

---

## 6. Шляхи — правила (оновлення)

**Правило з `docs/database.md`:** усі шляхи в БД — відносні до program root.

**Що додаємо:**
- Шляхи зберігаються в **нормалізованому вигляді** — `forward slashes` (`/`), без `..` і без початкового `/`.
- У Python-коді є єдина функція `db_path.to_absolute(root) -> Path` для конвертації.
- Жоден шлях у БД не може бути абсолютним — на старті валідація.

```python
# app/backend/db/path_utils.py
def normalize(path: str) -> str:
    """Convert OS path to normalized relative path with forward slashes."""
    p = Path(path).as_posix()
    if Path(p).is_absolute():
        raise ValueError(f"DB paths must be relative, got absolute: {path}")
    if ".." in Path(p).parts:
        raise ValueError(f"DB paths must not contain '..': {path}")
    return p

def to_absolute(rel: str, root: Path) -> Path:
    return (root / rel).resolve()
```

---

## 7. Секрети — детальний план

### 7.1. Поточний стан
У `app/config/` є `user_preferences.json` (UI). Файлу `secrets.json` зараз немає, але в `bridge.py` ніде не згадується HF token. У `docs/apps_loogic.md` згадується `HUGGY_FACE_TOKEN` — отже, він має десь зберігатися.

### 7.2. Рішення
- Створити таблицю `secrets` (див. § 3.3).
- При першому запуску: якщо в БД немає `hf.token`, показати UI-діалог "Введіть HuggingFace API токен" і зберегти зашифровано.
- Ключ шифрування — у `app/config/.db_key`, генерується при першому запуску (`os.urandom(32)`), права 0600 (на Windows — обмеження через ACL, або через `keyring` модуль).
- Бібліотека: `cryptography` (Fernet).

### 7.3. Аварійне відновлення
- Якщо `.db_key` зник — усі секрети треба ввести повторно. Показуємо це в SettingsScreen.
- Якщо ж `.db_key` є, а `secrets` порожні — це нормальний стан, просто запитуємо токен.

---

## 8. Кодова структура (нові файли)

```
app/backend/db/
├── __init__.py
├── connection.py            # SQLite connection pool / single connection
├── migrations.py            # version table, apply_pending_migrations()
├── path_utils.py            # normalize(), to_absolute()
├── crypto.py                # Fernet wrapper для secrets
├── seed.py                  # initial data: 2 templates, 1 global instruction
├── repositories/
│   ├── __init__.py
│   ├── base.py              # BaseRepository — спільні методи (commit, fetchone)
│   ├── sessions.py          # SessionRepository
│   ├── templates.py         # TemplateRepository
│   ├── instructions.py      # InstructionRepository
│   ├── user_style.py
│   ├── library_file.py
│   ├── session_files.py
│   ├── custom_annotations.py
│   ├── settings.py          # AppSettingsRepository
│   ├── secrets.py
│   ├── pipeline_runs.py
│   ├── audit.py
│   └── cache.py             # об'єднаний для llm_cache, image_cache, document_cache
└── exceptions.py            # DbError, NotFoundError, VersionConflictError
```

### 8.1. `connection.py` — ключові рішення

- **WAL режим** для паралельних читань: `PRAGMA journal_mode=WAL`
- **Foreign keys ON**: `PRAGMA foreign_keys=ON`
- **Single connection** для desktop-застосунку, але `check_same_thread=False` + threading.Lock для потокобезпеки.
- **Альтернатива:** thread-local connections. Для desktop-застосунку з 1-2 активними потоками — single connection + Lock простіше.

```python
class Database:
    def __init__(self, db_path: Path):
        self._path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(db_path),
            check_same_thread=False,
            isolation_level=None,  # autocommit, керуємо транзакціями явно
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")

    def transaction(self) -> ContextManager[sqlite3.Connection]:
        @contextmanager
        def _tx():
            with self._lock:
                self._conn.execute("BEGIN IMMEDIATE")
                try:
                    yield self._conn
                    self._conn.execute("COMMIT")
                except Exception:
                    self._conn.execute("ROLLBACK")
                    raise
        return _tx()
```

### 8.2. `migrations.py` — версіонування схеми

```sql
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now')),
    description TEXT
);
```

**Стратегія:**
- Кожна міграція — файл `schema/migrations/00N_description.sql`.
- При старті: знайти останню версію, застосувати всі нові в транзакції.
- Перед міграцією — backup у `app/db/backups/agent_<timestamp>.db`.
- Після міграції — запис у `schema_version`.

**Поточна версія:** `001_init.sql + 002_cache.sql + 003_indexes.sql` = version 1.

### 8.3. Репозиторії — приклад `SessionRepository`

```python
class SessionRepository(BaseRepository):
    def create(self, *, template_id: str, name: str,
               input_snapshot: dict) -> Session:
        sid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO sessions
                   (id, template_id, name, status, input_snapshot, created_at)
                   VALUES (?, ?, ?, 'draft', ?, ?)""",
                (sid, template_id, name,
                 json.dumps(input_snapshot, ensure_ascii=False), now),
            )
        return Session(id=sid, template_id=template_id, name=name,
                       status="draft", input_snapshot=input_snapshot,
                       created_at=now)

    def get(self, session_id: str) -> Session | None: ...
    def list_recent(self, limit: int = 50) -> list[Session]: ...
    def update_status(self, session_id: str, status: str,
                      *, error_stage: str | None = None,
                      error_message: str | None = None) -> None: ...
    def set_started(self, session_id: str) -> None: ...
    def set_completed(self, session_id: str, *,
                      docx_output: str, pdf_output: str,
                      image_count: int, token_usage: dict,
                      duration_ms: int) -> None: ...
```

---

## 9. План міграції даних (з поточного стану)

### 9.1. З `cache.db` → `agent.db`
- `cache_manager.py` вже використовує SQLite.
- Крок: відкрити обидва файли, скопіювати рядки з `llm_cache`, `image_cache`, `document_cache`.
- Після успіху — видалити `cache.db` (або залишити як backup на 1 реліз, видалити в релізі N+1).
- Оновити `cache_manager.py`: `db_path` за замовчуванням = `app/db/agent.db`.

### 9.2. З `app/db/<safe_name>/*.json` → `sessions` + `session_files`
- `bridge.saveSessionJson` зараз пише JSON у підпапку `app/db/<safe_name>/`.
- Крок: при першому запуску нової БД — пройтись по всіх підпапках `app/db/*/`, розпарсити `*.json`, створити `sessions` + `session_files` + `library_file` записи.
- Файли в `transit/` і `app/db/<safe_name>/` перемістити в стандартне сховище (`app/storage/library/<sha256[:2]>/<sha256>.<ext>`).
- Після міграції — залишити старі файли, але більше туди не писати. Видалити в релізі N+1.

### 9.3. З `app/config/user_preferences.json` → `app_settings`
- Простий маппінг: 3 ключі → 3 рядки в `app_settings`.
- Після міграції — залишити файл як fallback (на випадок, якщо БД зламається і користувач хоче скинути налаштування).

### 9.4. З файлів в `app/instructions/*.md` → `instructions`
- При першому запуску — для кожного `app/instructions/*.md` створити запис в `instructions` з `type='global'` (або `type='special'` для `template-ins/lab1_fill.md`).
- `content_hash` = SHA-256 вмісту.
- Сам файл залишити на диску (pipeline читає файл напряму, БД — для версіонування).
- При оновленні через UI — створюється новий рядок з `is_active=1`, старий → `is_active=0`.

### 9.5. З файлів у `app/instructions/template-ins/*.md` → `instructions` (special)

### 9.6. З `app/backend/templates/*.py` (майбутніх) → `templates`
- Коли з'являться шаблони як файли — при старті БД засканувати `app/backend/templates/*.py` і створити записи в `templates`.

---

## 10. Конкретні кроки реалізації (чеклист)

### Фаза 1 — каркас (1–2 дні)
- [ ] Видалити `app/db/data.txt`.
- [ ] Створити `app/db/README.md` з повним описом таблиць (копія § 3 з посиланням на `docs/database.md` як джерело правди).
- [ ] Створити `app/backend/db/connection.py` (Database class + transaction context).
- [ ] Створити `app/backend/db/path_utils.py`.
- [ ] Створити `app/backend/db/exceptions.py`.
- [ ] Створити `app/db/schema/001_init.sql` з усіма 9 таблицями + `schema_version` + 4 новими.
- [ ] Створити `app/db/schema/002_cache.sql` з 3 кеш-таблицями.
- [ ] Створити `app/db/schema/003_indexes.sql` з усіма індексами.
- [ ] Створити `app/db/schema/004_seeds.sql` з початковими даними.
- [ ] Створити `app/backend/db/migrations.py` (apply_pending_migrations).

### Фаза 2 — репозиторії (2–3 дні)
- [ ] `BaseRepository` (спільне: commit, fetchone, fetchall).
- [ ] `TemplateRepository`.
- [ ] `InstructionRepository` (з версіонуванням).
- [ ] `UserStyleRepository`.
- [ ] `SessionRepository` (найскладніший — багато методів).
- [ ] `SessionFilesRepository`.
- [ ] `LibraryFileRepository` (з дедуплікацією за SHA-256).
- [ ] `CustomAnnotationsRepository`.
- [ ] `AppSettingsRepository` (key-value).
- [ ] `SecretsRepository` (через crypto.py).
- [ ] `PipelineRunsRepository`.
- [ ] `AuditLogRepository`.
- [ ] `CacheRepository` (3 таблиці разом).

### Фаза 3 — крипто і безпека (0.5 дня)
- [ ] `app/backend/db/crypto.py` (Fernet wrapper).
- [ ] Генерація `app/config/.db_key` при першому запуску (з перевіркою прав).
- [ ] Інтеграція `SecretsRepository` з `bridge.py` (замінити mock-hf-token на справжнє зчитування).

### Фаза 4 — міграція даних (1–2 дні)
- [ ] `app/backend/db/migrate_from_json.py` — скрипт міграції з існуючих JSON.
- [ ] `app/backend/db/migrate_from_cache.py` — копіювання `cache.db` → `agent.db`.
- [ ] `app/backend/db/migrate_from_instructions.py` — сканування `app/instructions/*.md`.
- [ ] Backup у `app/db/backups/agent_<timestamp>.db` перед кожною міграцією.
- [ ] CLI: `python -m app.backend.db.migrate --dry-run`, `--apply`.

### Фаза 5 — інтеграція з `bridge.py` (1 день)
- [ ] Створити `BridgeRepository` фасад, який інкапсулює доступ до кількох репозиторіїв.
- [ ] Замінити `MOCK_SESSIONS` / `MOCK_TEMPLATES` / `MOCK_INSTRUCTIONS` на виклики репозиторіїв.
- [ ] Замінити `_load_preferences` / `_save_preferences` на `AppSettingsRepository`.
- [ ] Замінити `saveSessionJson` (JSON-файли) на `SessionRepository.create` + `SessionFilesRepository.attach`.
- [ ] Замінити дедуплікацію за іменем у `uploadFilesUrls` на дедуплікацію за SHA-256 через `LibraryFileRepository`.

### Фаза 6 — інтеграція з `cache_manager.py` (0.5 дня)
- [ ] Перенести `re` на початок файлу (виправити `# noqa: E402`).
- [ ] Змінити `__init__(db_path="app/db/agent.db")`.
- [ ] Додати поле `style_hash` в `llm_cache` (відсутнє зараз, але в `docs/database.md` воно є).
- [ ] Перенести TTL логіку (зараз відсутня) у `CacheRepository.invalidate_older_than(days)`.

### Фаза 7 — тести (1 день)
- [ ] `tests/db/test_sessions.py` — CRUD, cascade.
- [ ] `tests/db/test_library_dedup.py` — дедуплікація за SHA-256.
- [ ] `tests/db/test_instructions_versioning.py` — активна/неактивна версія.
- [ ] `tests/db/test_migrations.py` — upgrade/downgrade (downgrade — best effort).
- [ ] `tests/db/test_secrets_encryption.py` — перевірка, що у БД лежать не plaintext токени.
- [ ] `tests/db/test_concurrent_writes.py` — WAL + Lock.

### Фаза 8 — документація (0.5 дня)
- [ ] `app/db/README.md` (повний, посилається на `docs/database.md`).
- [ ] ADR (Architecture Decision Records) у `docs/adr/`:
  - `0001-sqlite-vs-json.md` — чому SQLite, а не JSON-файли
  - `0002-single-connection.md` — чому single connection + Lock, а не pool
  - `0003-secrets-encryption.md` — чому Fernet + локальний ключ
  - `0004-cache-in-agent-db.md` — об'єднання кешу з основною БД

---

## 11. Конвенції та правила коду

### 11.1. Іменування
- **Таблиці:** `snake_case`, множина (`sessions`, `library_file`).
- **Колонки:** `snake_case`, однина (`name`, `created_at`).
- **FK:** `<table_singular>_id` (`template_id`, `session_id`).
- **JSON-поля:** `*_snapshot`, `*_schema`, `*_result` — завжди серіалізовані як TEXT.

### 11.2. Типи
- PK: `TEXT` (UUID v4) для всіх бізнес-сутностей.
- PK: `INTEGER AUTOINCREMENT` тільки для join-таблиць та audit_log.
- BOOL: `INTEGER 0/1` (SQLite не має нативного BOOL).
- DATETIME: `TEXT` ISO 8601 з `timezone.utc` (`datetime.now(timezone.utc).isoformat()`).
- JSON: `TEXT` (серіалізований). Альтернатива SQLite JSON1 ext — не використовуємо (зайва складність).

### 11.3. Обробка помилок
- `sqlite3.IntegrityError` → `DbError` з повідомленням.
- `sqlite3.OperationalError` (database locked) → retry 3 рази з backoff.
- `NotFoundError` для `get_or_404` стилів.
- Жоден репозиторій не логує в `print` — тільки через `logging`.

### 11.4. Транзакції
- Завжди `BEGIN IMMEDIATE` (а не `BEGIN`) — запобігає deadlock при читанні-записі.
- Жоден виклик API репозиторію не робить auto-commit — тільки через `db.transaction()`.
- Pipeline може мати одну велику транзакцію на всю сесію або дрібні на кожну стадію — це архітектурне рішення (див. ADR).

---

## 12. Відкриті питання (потребують рішення перед реалізацією)

1. **Чи перейменовувати `data.txt`?** Так — див. Фаза 1. Видалення безпечне, файл порожній.
2. **Чи об'єднувати `cache.db` з `agent.db`?** Так — Фаза 6. Альтернатива (залишити окремо) ускладнює резервне копіювання.
3. **Чи шифрувати `secrets` зараз чи в релізі N+1?** Зараз — інакше HF token у відкритому вигляді.
4. **Чи робити `audit_log` у цій фазі?** Рекомендую так — інакше його додавання пізніше потребуватиме нової міграції.
5. **Чи потрібен `pipeline_runs` окремо від `sessions.error_stage`?** Так — `sessions` тільки фінальний статус, `pipeline_runs` — детальний таймінг.
6. **TTL для кешів:** 30 днів для LLM, 60 для зображень, 365 для документів? (потребує підтвердження)
7. **Retention для сесій:** 90 днів за замовчуванням, налаштовується в `app_settings`?
8. **Шифрування БД цілком (SQLCipher) vs тільки secrets?** Поки що — тільки secrets. SQLCipher ускладнює розробку й додає native dependency.

---

## 13. Ризики

| Ризик | Імовірність | Вплив | Мітигація |
|---|---|---|---|
| Міграція з JSON-файлів втрачає дані | Середня | Високий | Backup перед міграцією + dry-run режим + ручне підтвердження |
| WAL файл `agent.db-wal` не видаляється при crash | Низька | Середній | Документація для користувача, auto-checkpoint при старті |
| Потокобезпека при concurrent UI + pipeline | Середня | Високий | `BEGIN IMMEDIATE` + `Lock` + busy_timeout=5000 + unit-тести |
| Криптоключ `.db_key` втрачено | Низька | Високий (секрети) | Діалог "Введіть HF token знову" у SettingsScreen |
| Схема БД розростається без міграцій | Середня | Середній | Обов'язковий code review для змін `schema/*.sql` |
| Великий `agent.db` через кеш-таблиці | Низька | Низький | TTL + ручне очищення через SettingsScreen |
| SQLite single-writer обмеження при багатьох сесіях | Низька (desktop) | Низький | WAL + black_timeout — для desktop з 1 користувачем достатньо |

---

## 14. Definition of Done (для всієї фази)

- [ ] Усі 12 репозиторіїв реалізовані та мають unit-тести.
- [ ] `cache_manager.py` використовує `agent.db`.
- [ ] `bridge.py` використовує репозиторії замість mocks/JSON.
- [ ] Існуючі JSON-файли в `app/db/<safe_name>/` зчитано й перенесено в БД.
- [ ] Існуючий `cache.db` злито з `agent.db` (або видалено, якщо порожній).
- [ ] `app/db/README.md` повний і точний.
- [ ] 3 ADR-файли в `docs/adr/`.
- [ ] `python -m app.backend.db.migrate --apply` працює з резервним копіюванням.
- [ ] Усі тести проходять (`pytest tests/db/`).
- [ ] Жоден `print(...)` у новому коді, тільки `logging`.
- [ ] Жоден абсолютний шлях у БД (валідація при записі).
- [ ] `docs/database.md` оновлено з новими таблицями (`app_settings`, `secrets`, `pipeline_runs`, `audit_log`).
- [ ] Стара версія `database.md` (Combined v2) синхронізована або явно позначена як superseded новою версією.
