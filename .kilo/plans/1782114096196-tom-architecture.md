# План: Архітектура і реалізація TOM (Agent-For-TOM)

## Мета

Створити локального AI-агента "TOM" з desktop-інтерфейсом, який підтримує багато провайдерів (Ollama + cloud), MCP, має 3-рівневу пам'ять і авто-створення скілів. Побудований як глибокий форк Langflow з embedded Letta SDK і окремим Tauri-фронтендом. Повністю самодостатній інсталятор, офлайн-режим з локальним LLM.

## Прийняті рішення

| Рішення | Вибір | Обґрунтування |
|---|---|---|
| База агента | Форк Langflow | MIT, 150k★, MCP server/client, multi-provider, Python |
| Пам'ять | Letta SDK embedded як Python-модуль | Найкраща модель core/archival/recall |
| Інтеграція пам'яті | Embedded (import SDK у форк) | Один процес, тісніша інтеграція, менші затримки |
| Desktop UI | Гібрид: TOM chat-app (Tauri) + Langflow backend | Чистий брендинг, чат-перший UX |
| Самовдосконалення | 3 рівні пам'яті + авто-скіли (після підтвердження юзера) | Без неконтрольованої зміни ваг/промптів |
| БД | SQLite + SQLCipher | Single-file, embedded, шифрована at-rest |
| Вектори | sqlite-vec extension | Вектори в тому ж .db = один файл бекапу |
| Ключ шифрування | OS keyring (DPAPI/Keychain/libsecret) | Юзер не помічає, файл містить лише ID ключа |
| Шляхи даних | Windows: `%APPDATA%\tom\`; macOS: `~/Library/Application Support/tom/`; Linux: `~/.local/share/tom/` | Platform-стандарт, один каталог = бекап |
| Ліцензія | MIT | Як у репо, сумісно з Langflow і Letta |
| Single vs multi | Single-user, локально | Умова задачі |
| Тестування | Unit + E2E + CI з Phase 0 | Повний стек з самого початку |

## Архітектура (компоненти)

```
┌─────────────────────────────────────────────┐
│  TOM Desktop (Tauri 2 + React 18 + TS)      │
│  - Чат-сесії, sidebar                       │
│  - Вибір провайдера/моделі                  │
│  - Inbox самовдосконалення                  │
│  - Керування скілами                       │
│  - "Developer Mode" → Langflow UI           │
└────────────────┬────────────────────────────┘
                 │ HTTP/WebSocket (localhost:7878)
┌────────────────▼────────────────────────────┐
│  TOM Backend (форк Langflow, Python 3.12)   │
│  ┌─────────────────────────────────────┐    │
│  │ Langflow runtime                    │    │
│  │  - LLM-провайдери (Ollama, cloud)   │    │
│  │  - MCP client + MCP server          │    │
│  │  - REST API для TOM UI              │    │
│  └─────────────────────────────────────┘    │
│  ┌─────────────────────────────────────┐    │
│  │ TOM Memory Layer (embedded Letta)   │    │
│  │  - core / archival / recall tiers   │    │
│  └─────────────────────────────────────┘    │
│  ┌─────────────────────────────────────┐    │
│  │ TOM Self-Improvement Pipeline       │    │
│  │  - pattern detector (cron)          │    │
│  │  - skill scaffolder (LLM gen)       │    │
│  │  - sandbox (subprocess, no-net)     │    │
│  │  - approval queue                   │    │
│  └─────────────────────────────────────┘    │
└────────────────┬────────────────────────────┘
                 │ stdio
┌────────────────▼────────────────────────────┐
│  Built-in MCP Servers                       │
│  - tom-docx, tom-pdf, tom-fmt               │
└─────────────────────────────────────────────┘
```

## Структура репозиторію

```
C:\Github\Agent-For-TOM\
├── README.md
├── LICENSE
├── ARCHITECTURE.md                  (копія плану після фіналізації)
├── .github/workflows/
│   ├── backend-tests.yml            (pytest + mypy + coverage)
│   └── desktop-tests.yml            (tsc + vitest + playwright + cargo check)
├── packages/
│   ├── backend/                     (форк Langflow + TOM-шар)
│   │   ├── pyproject.toml           (letta-ai, mcp, sqlalchemy, sqlcipher, sqlite-vec)
│   │   ├── src/backend/tom/
│   │   │   ├── memory/              (TomMemory wrapper над Letta)
│   │   │   ├── improvement/         (detector, scaffolder, sandbox)
│   │   │   ├── api/                 (REST: chat, sessions, memory, skills, improvement)
│   │   │   ├── db/                  (SQLAlchemy models, migrations, keyring)
│   │   │   └── branding/            (TOM strings, лого)
│   │   └── tests/
│   ├── desktop/                     (Tauri-додаток)
│   │   ├── src-tauri/               (Rust, sidecar launcher)
│   │   ├── src/                     (React + TS UI)
│   │   └── package.json
│   └── mcp-servers/
│       ├── builtin/tom-docx/
│       ├── builtin/tom-pdf/
│       └── builtin/tom-fmt/
├── docs/
│   ├── skills.md
│   ├── memory.md
│   └── api.md
└── scripts/
    ├── dev.sh                       (запуск backend + desktop + MCP локально)
    └── build.sh                     (PyInstaller + Tauri bundle)
```

## Зберігання даних

### Шляхи

| Платформа | Дані | Кеш/Темп | Логи |
|---|---|---|---|
| Windows | `%APPDATA%\tom\` | `%LOCALAPPDATA%\tom\cache\` | `%LOCALAPPDATA%\tom\logs\` |
| macOS | `~/Library/Application Support/tom/` | `~/.cache/tom/` | `~/Library/Logs/tom/` |
| Linux | `~/.local/share/tom/` | `~/.cache/tom/` | `~/.local/state/tom/logs/` |

### Структура каталогу даних

```
%APPDATA%\tom\
├── tom.db                # SQLite (SQLCipher): всі таблиці
├── core_memory.json      # human-readable дублікат core memory
├── uploads/              # файли для docx/pdf-скілів
├── skills/               # MCP-сервери
│   ├── builtin/
│   └── generated/        # авто-скіли після approval
└── keyring.id            # ID ключа шифрування в OS keyring
```

### Таблиці БД

- `sessions(id, created_at, updated_at, provider, model, summary, total_tokens)`
- `messages(id, session_id, role, content, ts, tool_calls_json)`
- `memory_records(id, tier ENUM[core|archival|recall], content, embedding BLOB, source_session_id, created_at, confidence)`
- `skills(id, name, description, mcp_server_path, version, status, created_from_pattern_id)`
- `patterns(id, fingerprint, occurrence_count, last_seen, suggested_skill_id, status)`
- `provider_configs(id, type, endpoint, api_key_ref, default_model, fallback_chain_json)`
- `audit_log(id, ts, actor, action, target, payload_hash)`

## API-контракти (REST, localhost:7878)

| Метод | Шлях | Призначення |
|---|---|---|
| GET | `/healthz` | Healthcheck для Tauri sidecar |
| POST | `/v1/chat` | Надіслати повідомлення, streaming відповідь |
| GET/POST | `/v1/sessions` | Список / створення сесій |
| GET/PATCH | `/v1/memory/core` | Core memory (юзер може редагувати) |
| GET/POST | `/v1/skills` | Список / активація скілів |
| POST | `/v1/skills/{id}/approve` | Схвалити авто-скіл |
| POST | `/v1/skills/{id}/reject` | Відхилити авто-скіл |
| GET | `/v1/improvement/inbox` | Pending patterns + draft skills |

Контракти фіксимо перед кожною фазою (див. нижче "Sync-точка").

## Пайплайн самовдосконалення

1. **Збір**: після закриття сесії — summary + embedding → `memory_records` (archival)
2. **Детекція патернів** (cron): кластеризація embeddings; ≥3 схожих → `Pattern`
3. **Генерація скіла**: LLM-агент на прикладах патерну → код MCP-сервера → статус `draft`
4. **Sandbox**: код запускається в subprocess з no-net, timeout, read-only fs
5. **Схвалення**: юзер бачить у `/v1/improvement/inbox` → approve → `active`, MCP підключається
6. **Відхилення**: `rejected`, патерн архівується

## Безпека / ізоляція

- Backend слухає тільки `127.0.0.1:7878`
- API-ключі — в OS keyring, ніколи в plain config
- Auto-скіли — sandbox (subprocess + no-net + timeout + read-only fs)
- Core memory — тільки юзер редагує, або з явним підтвердженням
- Жодної телеметрії, авто-update, CDN-залежностей

## Самодостатня установка

- Tauri-бандл: MSI/EXE (Win), DMG (macOS), deb/AppImage (Linux)
- В інсталятор упаковано: Tauri-UI + Python backend як PyInstaller-sidecar + MCP-сервери
- Перший запуск: Tauri піднімає sidecar як child-process, чекає /healthz, відкриває UI
- Жодних зовнішніх запитів без opt-in (cloud-провайдери)

## Бекап/перенесення

Скопіювати `%APPDATA%\tom\` = повний стан. На новій машині: встановити TOM + покласти каталог назад = повне відновлення.

## Тестування (повний стек з Phase 0)

- **Unit**: pytest (backend), vitest (frontend), моки зовнішніх залежностей
- **E2E**: Playwright (UI flow) + bash+curl (API); запускаються в CI
- **CI**: GitHub Actions — `backend-tests.yml` і `desktop-tests.yml` на кожен PR
- **Smoke**: manual-чеклист 3-5 кроків наприкінці кожної фази

## Інструменти

- Python 3.12, uv (пакети), PyInstaller (sidecar)
- Rust + Tauri 2.x, React 18 + TypeScript, Vite
- SQLite + SQLCipher + sqlite-vec, SQLAlchemy 2.x, Alembic
- Letta SDK, MCP (офіційний Python SDK)
- pytest, vitest, Playwright, mypy, eslint, GitHub Actions

## Фази реалізації

Паттерн кожної фази:
```
[SYNC]    Contract freeze (обидва)  — S
Phase N-B <backend-робота>          — <size>
Phase N-F <frontend-робота>         — <size>
[INTEG]   Glue + e2e                — S
```

| # | Phase | Розмір | B-substep | F-substep | Інтеграція (e2e) |
|---|---|---|---|---|---|
| 0 | Bootstrap + CI | S | pyproject + deps, CI: pytest+mypy | Tauri+React init, CI: tsc+vitest+playwright | обидва CI зелені на пустому репо |
| 1 | Foundation | M | Форк Langflow, ребрендинг→TOM, SQLite+SQLCipher+sqlite-vec, keyring, schema, /healthz | Tauri запускає sidecar, порожнє чат-вікно показує health | Tauri→backend: health видно |
| 2 | Memory Layer | M | TomMemory (Letta wrapper, 3 tiers), embed-on-close, GET/PATCH /v1/memory/core | Панель Memory (read/edit core) | закрив сесію → memory оновилась → видно в UI |
| 3 | Chat MVP | M | Provider abstraction, POST /v1/chat (streaming), sessions CRUD | Sidebar, чат зі streaming, picker провайдера/моделі | чат → історія → reload |
| 4 | MCP Built-in | M | 4-B-infra: MCP client lifecycle + loader; 4-B-mcp: tom-docx, tom-pdf, tom-fmt | Панель Skills (list/enable/disable) | виклик скіла з чату → результат |
| 5 | Self-Improvement | **L** | 5-B-a detector, 5-B-b scaffolder, 5-B-c sandbox, 5-B-d approval API | 5-F-a inbox list, 5-F-b preview, 5-F-c approve/reject, 5-F-d real-time | reject→нема, approve→в списку |
| 6 | UX polish | M | Усі strings→TOM, error format | Theme, icon, splash, settings, toasts, empty/loading | повний flow виглядає "як TOM" |
| 7 | Packaging | M | PyInstaller sidecar (Win/macOS/Linux) | Tauri bundle (MSI/EXE, DMG, deb/AppImage) | installer на чисту Windows-VM, cold-start <5s |
| 8 | Release prep | S | Backup/restore з %APPDATA%\tom\ | README, skills.md, memory.md, screenshots | license audit |

## Phase 5 деталізовано

**5-B-a Pattern detector** (M)
- Cron scheduler (asyncio task, кожну годину)
- Embedding-based clustering останніх N сесій
- Threshold: ≥3 сесії зі схожістю >X за 7 днів → Pattern
- Тести: unit (detector logic, threshold), e2e (synthetic sessions → Pattern створено)

**5-B-b Skill scaffolder** (M)
- Шаблон MCP-сервера на основі Python SDK
- LLM-агент на прикладах патерну → код
- py_compile + security-review prompt
- Тести: unit (scaffolder), e2e (mock pattern → валідний MCP-сервер)

**5-B-c Sandbox** (M)
- subprocess runner з timeout (30s default)
- Network egress blocking (Linux: namespaces; macOS: pf; Windows: firewall rule)
- Read-only fs для непотрібних шляхів
- Тести: unit (sandbox config), e2e ("no network" guarantee)

**5-B-d Approval queue API** (S)
- GET /v1/improvement/inbox
- POST /v1/skills/{id}/approve
- POST /v1/skills/{id}/reject
- Тести: unit (API), e2e (lifecycle)

**5-F-a Inbox list** (S)
- Sidebar-таб "Improvements"
- List pending items з preview

**5-F-b Skill preview** (M)
- Diff-view згенерованого коду
- "Test call" — симульований виклик, показати що поверне
- Risk indicators (network access, fs access)

**5-F-c Approve/Reject actions** (S)
- Кнопки + confirmation modal
- Toast notifications

**5-F-d Real-time updates** (S)
- WebSocket або polling для нових patterns
- Badge count у sidebar

**Паралелізація всередині Phase 5:**

```
5-B-a (detector) ─┐
5-B-b (scaffold)  ├─► 5-B-d (approval API) ─┐
5-B-c (sandbox)   ┘                        │
                                            ├─► [INTEG]
5-F-a (list) ─┐                             │
5-F-b (preview)├─► 5-F-c (actions) ─► 5-F-d (real-time) ─┘
```

Контракт API фікситься перед стартом 5-B-d / 5-F-c.

## Ризики по фазах

| Фаза | Ризик | Мітигація |
|---|---|---|
| 1 | Глибокий форк Langflow → складні мерджі | TOM-шар у `backend/tom/`, не чіпати ядро |
| 2 | Letta як embedded → конфлікти залежностей | Ізольований Python sub-package, моніторинг pip conflicts |
| 4 | MCP-сервери як зовнішні процеси | subprocess-ізоляція, чіткий контракт manifest |
| 5 | LLM може згенерувати небезпечний скіл | sandbox (no-net, timeout, read-only) + юзер завжди підтверджує |
| 7 | PyInstaller + Tauri sidecar — складнощі пакування | ранній прототип у Phase 1, поступова стабілізація |

## Валідація перед merge кожної фази

- Backend: `pytest` зелені, `mypy` зелений, coverage не падає
- Frontend: `tsc --noEmit` + `cargo check` + `vitest` зелені
- E2E: Playwright + curl-сценарій проходить
- Smoke: manual-чеклист фази пройдено
- CI: 2 workflows зелені на PR

## Out of scope (MVP)

- Мультиюзер / авторизація
- Хмарна синхронізація
- Мобільний клієнт
- Тренування / файнтюн моделей
- Голосовий інтерфейс
- Авто-оновлення TOM

## Відкриті питання (для наступних ітерацій)

- Точний список MCP-скілів v1 (окрім docx/pdf/fmt)
- UX approval-inbox: модалка vs sidebar-таб vs нотифікації
- Чи потрібен вбудований auto-update у MVP
- White-label vs повний ребрендинг Langflow UI для "Developer Mode"
- Sandbox на Windows: NSG/firewall rule vs WSL — що простіше в PyInstaller-sidecar