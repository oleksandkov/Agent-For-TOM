# Agent-for-TOM — Схема бази даних (Combined v2)

SQLite — однокористувацький десктоп. Всі PK — UUID v4 (TEXT). JSON — TEXT.

---

## Сутність: `templates`

| Колонка | Тип | Опис |
|---|---|---|
| `id` | TEXT PK | UUID |
| `name` | TEXT UNIQUE NOT NULL | Машинна назва, slug |
| `display_name` | TEXT NOT NULL | Людська назва |
| `script_path` | TEXT NOT NULL | Шлях до .py |
| `instructions_path` | TEXT NULL | Шлях до інструкцій |
| `is_builtin` | INTEGER BOOL DEFAULT 1 | 1 — вбудований, 0 — створений користувачем |
| `has_instructions` | INTEGER BOOL DEFAULT 0 | 1 — є інструкції, 0 — показувати warning |
| `supports_images` | INTEGER BOOL DEFAULT 1 | Чи підтримує шаблон image references |
| `placeholder_schema` | JSON NULL | Масив плейсхолдерів (ключ, label, тип, max_words) |
| `gap_schema` | JSON NULL | Масив іменованих розривів для custom template |
| `source_file_id` | TEXT NULL FK → `library_file` | Оригінальний PDF/DOCX (для custom) |
| `created_at` | TEXT | ISO datetime |
| `updated_at` | TEXT | ISO datetime |

### placeholder_schema
```json
[
  {"key": "TITLE", "label": "Назва", "required": true, "max_words": 15, "type": "inline"},
  {"key": "INTRODUCTION", "label": "Вступ", "required": true, "max_words": null, "type": "block"}
]
```

### gap_schema
```json
[
  {"gap_id": "footer", "label": "Footer", "default_text": "Студент: [name]", "editable_by_model": false}
]
```

---

## Сутність: `instructions`

Версіонування інструкцій (глобальні + спеціальні + user-created).

| Колонка | Тип | Опис |
|---|---|---|
| `id` | TEXT PK | UUID |
| `template_id` | TEXT NULL FK → `templates` | NULL для глобальних |
| `type` | TEXT NOT NULL | `global` / `special` / `user_created` |
| `name` | TEXT NOT NULL | Відображувана назва |
| `content` | TEXT NOT NULL | Текст інструкції |
| `content_hash` | TEXT NOT NULL | SHA-256 контенту |
| `is_active` | INTEGER BOOL DEFAULT 1 | Тільки одна активна на (template_id, type) |
| `created_at` | TEXT | ISO datetime |

**Правило:** При збереженні нової версії стара встановлюється `is_active=0`.

---

## Сутність: `user_style`

Стиль/поведінка користувача. Порожній за замовчуванням.

| Колонка | Тип | Опис |
|---|---|---|
| `id` | TEXT PK | UUID |
| `content` | TEXT NOT NULL | Текст стилю |
| `content_hash` | TEXT NOT NULL | SHA-256 |
| `is_active` | INTEGER BOOL DEFAULT 1 | |
| `is_empty` | INTEGER BOOL | Пропускати відправку моделі |
| `created_at` | TEXT | ISO datetime |

---

## Сутність: `sessions`

Один запуск генерації — одна сесія.

| Колонка | Тип | Опис |
|---|---|---|
| `id` | TEXT PK | UUID |
| `template_id` | TEXT FK → `templates` | |
| `name` | TEXT | Назва сесії (редагується) |
| `status` | TEXT | `draft` / `processing` / `completed` / `failed` / `cancelled` |
| `input_snapshot` | JSON NOT NULL | Заморожена копія всіх вхідних даних |
| `filled_py_path` | TEXT NULL | Шлях до filled.py |
| `validation_result` | JSON NULL | Результат валідації |
| `docx_output` | TEXT NULL | Шлях до готового DOCX |
| `pdf_output` | TEXT NULL | Шлях до готового PDF |
| `image_count` | INTEGER DEFAULT 0 | Скільки згенеровано/вставлено |
| `error_message` | TEXT NULL | Текст помилки |
| `error_stage` | TEXT NULL | `file_convert` / `text_model` / `validate` / `image_gen` / `execute` / `compose` |
| `token_usage` | JSON NULL | Токени (input, output, cached) |
| `duration_ms` | INTEGER | Час виконання |
| `global_instructions_hash` | TEXT NULL | Хеш використаних глобальних інструкцій |
| `style_hash` | TEXT NULL | Хеш використаного стилю |
| `created_at` | TEXT | ISO datetime |
| `completed_at` | TEXT NULL | ISO datetime |

### input_snapshot
```json
{
  "name": "Іван Петренко",
  "theme": "Сортування масивів",
  "goal": "Порівняти bubble sort і quicksort",
  "length": "long",
  "hardness": "university_1",
  "image_mode": "full",
  "template_slug": "lab1",
  "gap_values": {"footer": "Студент: Іван Петренко"}
}
```

### validation_result
```json
{
  "syntax_ok": true,
  "unfilled_placeholders": [],
  "word_count": 1923,
  "word_count_target_min": 1700,
  "word_count_target_max": 2500,
  "image_refs_found": 2,
  "image_refs_valid": 2,
  "warnings": ["Word count 3% below target"]
}
```

### token_usage
```json
{
  "text_model": {"input_tokens": 4821, "output_tokens": 2103, "cached_tokens": 3200},
  "image_model": {"images_generated": 2}
}
```

---

## Сутність: `session_files` (join table)

| Колонка | Тип | Опис |
|---|---|---|
| `id` | INTEGER PK | |
| `session_id` | TEXT FK → `sessions` | |
| `file_id` | TEXT FK → `library_file` | |
| `was_summarized` | INTEGER BOOL DEFAULT 0 | Чи було перевищено ліміт токенів |
| `token_count_used` | INTEGER | Скільки токенів реально відправлено |
| `added_at` | TEXT | ISO datetime |

---

## Сутність: `library_file`

Єдине сховище файлів. Дедуплікація по SHA-256.

| Колонка | Тип | Опис |
|---|---|---|
| `id` | TEXT PK | UUID |
| `original_name` | TEXT NOT NULL | Оригінальне ім'я файлу |
| `original_type` | TEXT NOT NULL | MIME тип |
| `file_hash` | TEXT UNIQUE NOT NULL | SHA-256 |
| `stored_path` | TEXT NOT NULL | Шлях у сховищі програми |
| `converted_text` | TEXT NULL | Конвертований текст |
| `conversion_status` | TEXT DEFAULT 'pending' | `pending` / `done` / `failed` |
| `file_size_bytes` | INTEGER | |
| `created_at` | TEXT | ISO datetime |
| `last_used_at` | TEXT | ISO datetime |

---

## Сутність: `custom_template_annotations`

Збережені анотації для кастомного шаблону.

| Колонка | Тип | Опис |
|---|---|---|
| `id` | TEXT PK | UUID |
| `template_id` | TEXT FK → `templates` | |
| `source_file_id` | TEXT FK → `library_file` | |
| `annotations` | JSON NOT NULL | Масив анотацій (тип, текст, формат, gap_name) |
| `created_at` | TEXT | ISO datetime |
| `updated_at` | TEXT | ISO datetime |

---

## Кеш-таблиці (автоматично створюються cache_manager.py)

### `llm_cache`

| Колонка | Тип | Опис |
|---|---|---|
| `id` | INTEGER PK | |
| `cache_key` | TEXT UNIQUE NOT NULL | SHA256(template_id + params + user_hash + style_hash) |
| `template_id` | TEXT | |
| `params` | TEXT | JSON параметрів |
| `user_files_hash` | TEXT | SHA256 конвертованих файлів |
| `style_hash` | TEXT | SHA256 user_style |
| `response_text` | TEXT NOT NULL | Відповідь LLM |
| `prompt_tokens` | INTEGER | |
| `output_tokens` | INTEGER | |
| `created_at` | TEXT | ISO datetime |
| `hit_count` | INTEGER | Лічильник використань |

### `image_cache`

| Колонка | Тип | Опис |
|---|---|---|
| `id` | INTEGER PK | |
| `prompt_hash` | TEXT UNIQUE NOT NULL | SHA256(render_spec) |
| `prompt` | TEXT | Повний render_spec JSON |
| `engine` | TEXT | matplotlib / huggingface |
| `png_path` | TEXT NOT NULL | Шлях до PNG |
| `file_size_bytes` | INTEGER | |
| `created_at` | TEXT | ISO datetime |
| `last_hit_at` | TEXT | ISO datetime |

### `document_cache`

| Колонка | Тип | Опис |
|---|---|---|
| `id` | INTEGER PK | |
| `content_hash` | TEXT UNIQUE NOT NULL | SHA256(filled.py без anchor-маркерів) |
| `docx_path` | TEXT | |
| `pdf_path` | TEXT | |
| `created_at` | TEXT | ISO datetime |

---

## Індекси

```sql
CREATE INDEX idx_sessions_created ON sessions(created_at DESC);
CREATE INDEX idx_sessions_status ON sessions(status);
CREATE INDEX idx_session_files_session ON session_files(session_id);
CREATE INDEX idx_session_files_file ON session_files(file_id);
CREATE INDEX idx_library_hash ON library_file(file_hash);
CREATE INDEX idx_library_user ON library_file(created_at);
CREATE INDEX idx_instructions_template ON instructions(template_id);
CREATE INDEX idx_instructions_active ON instructions(is_active);
CREATE UNIQUE INDEX idx_templates_name ON templates(name);
CREATE INDEX idx_llm_cache_key ON llm_cache(cache_key);
CREATE INDEX idx_image_cache_hash ON image_cache(prompt_hash);
CREATE INDEX idx_document_cache_hash ON document_cache(content_hash);
```

---

## Зв'язки

```
library_file ──< session_files >── sessions ──> templates
                                        │           │
                                        │           ├── instructions
                                        │           └── custom_template_annotations
                                        │
                                  user_style (активний)

instructions (global, template_id=NULL) → всі запуски
```

---

## Правила для UI

1. **Видалити сесію** — каскад на `session_files`. Файли в `library_file` НЕ видаляти.
2. **Видалити library_file** — тільки якщо 0 `session_files` посилаються на нього.
3. **Відновити сесію** — читати `input_snapshot` + `session_files JOIN library_file`, заповнити форму. Не запускати автоматично.
4. **Дублювати** — нова `sessions` з `status='draft'`, тим самим `input_snapshot`, копія `session_files`. Ім'я + " — копія".
5. **Очищення файлів** — при видаленні сесії видаляти filled.py, DOCX, PDF з диску.
6. **Шляхи** — всі шляхи відносні до кореня програми, не абсолютні.
