# Статус реалізації бази даних

> **Це — НЕ специфікація.** Специфікація схеми — у [`docs/database.md`](../../../docs/database.md) та [`None ai/working/backend3/database/database implementation plan.md`](database implementation plan.md).
> Цей файл — лише трекер прогресу реалізації.

## Загальний прогрес
**Поточна фаза:** 0 (підготовка) — `app/db/data.txt` ще існує, файл БД не створено, репозиторії не написані.

## Чек-ліст за фазами плану

### Фаза 0 — підготовка
- [ ] Видалити `app/db/data.txt`
- [ ] Створити `app/db/README.md`

### Фаза 1 — каркас
- [ ] `app/backend/db/connection.py`
- [ ] `app/backend/db/path_utils.py`
- [ ] `app/backend/db/exceptions.py`
- [ ] `app/db/schema/001_init.sql`
- [ ] `app/db/schema/002_cache.sql`
- [ ] `app/db/schema/003_indexes.sql`
- [ ] `app/db/schema/004_seeds.sql`
- [ ] `app/backend/db/migrations.py`

### Фаза 2 — репозиторії (12 шт)
- [ ] `BaseRepository`
- [ ] `TemplateRepository`
- [ ] `InstructionRepository`
- [ ] `UserStyleRepository`
- [ ] `SessionRepository`
- [ ] `SessionFilesRepository`
- [ ] `LibraryFileRepository`
- [ ] `CustomAnnotationsRepository`
- [ ] `AppSettingsRepository`
- [ ] `SecretsRepository`
- [ ] `PipelineRunsRepository`
- [ ] `AuditLogRepository`
- [ ] `CacheRepository`

### Фаза 3 — крипто
- [ ] `app/backend/db/crypto.py` (Fernet)
- [ ] Генерація `app/config/.db_key`
- [ ] Інтеграція `SecretsRepository` у `bridge.py`

### Фаза 4 — міграція даних
- [ ] `migrate_from_json.py`
- [ ] `migrate_from_cache.py`
- [ ] `migrate_from_instructions.py`
- [ ] Backup перед міграцією
- [ ] CLI: `--dry-run` / `--apply`

### Фаза 5 — інтеграція з `bridge.py`
- [ ] `BridgeRepository` фасад
- [ ] Заміна `MOCK_*` даних
- [ ] Заміна `_load_preferences` / `_save_preferences`
- [ ] Заміна `saveSessionJson` (JSON → БД)
- [ ] Дедуплікація за SHA-256 у `uploadFilesUrls`

### Фаза 6 — `cache_manager.py`
- [ ] Виправити `import re` position
- [ ] Змінити `db_path` на `app/db/agent.db`
- [ ] Додати `style_hash` у `llm_cache`
- [ ] Додати TTL-логіку

### Фаза 7 — тести
- [ ] `test_sessions.py`
- [ ] `test_library_dedup.py`
- [ ] `test_instructions_versioning.py`
- [ ] `test_migrations.py`
- [ ] `test_secrets_encryption.py`
- [ ] `test_concurrent_writes.py`

### Фаза 8 — документація
- [ ] `app/db/README.md`
- [ ] ADR `0001-sqlite-vs-json.md`
- [ ] ADR `0002-single-connection.md`
- [ ] ADR `0003-secrets-encryption.md`
- [ ] ADR `0004-cache-in-agent-db.md`

## Специфічні для `docs/database.md` таблиці — статус

| Таблиця | У `docs/database.md` | Реалізовано в коді | План у документі |
|---|---|---|---|
| `templates` | ✓ | ✗ (тільки mock у bridge.py) | Фаза 2 |
| `instructions` | ✓ | ✗ (тільки mock) | Фаза 2 + 4.5 |
| `user_style` | ✓ | ✗ | Фаза 2 |
| `sessions` | ✓ | △ (JSON-файли) | Фаза 5 |
| `session_files` | ✓ | ✗ | Фаза 2 + 5 |
| `library_file` | ✓ | △ (filename-dedup у bridge.py) | Фаза 2 + 5 |
| `custom_template_annotations` | ✓ | ✗ | Фаза 2 |
| `llm_cache` | ✓ | ✓ (окремий `cache.db`) | Фаза 6 |
| `image_cache` | ✓ | ✓ (окремий `cache.db`) | Фаза 6 |
| `document_cache` | ✓ | ✓ (окремий `cache.db`) | Фаза 6 |

## Нові таблиці (не в `docs/database.md`)

| Таблиця | Потреба | План |
|---|---|---|
| `app_settings` | key-value для UI prefs, TTL, retention | Фаза 2 |
| `secrets` | зашифровані секрети (HF token) | Фаза 3 |
| `pipeline_runs` | детальний таймінг + матриця помилок | Фаза 2 |
| `audit_log` | хто/що/коли | Фаза 2 |
| `schema_version` | версіонування міграцій | Фаза 1 |

## Позначки
- ✓ — готово
- △ — частково (є в коді, але не в новій архітектурі)
- ✗ — не реалізовано
