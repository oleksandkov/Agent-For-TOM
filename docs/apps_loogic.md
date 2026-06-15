# Apps pipeline — Agent-For-Labs (Combined v2)

Архітектура: **2 проходи** (Text LLM → Image Processing Stage) + **5 вхідних елементів**.

---

## 5 вхідних елементів (порядок важливий — пізніші мають вищий пріоритет)

| # | Елемент | Тип | Джерело | Надсилається |
|---|---------|-----|---------|-------------|
| 1 | **Глобальні інструкції** | Статичний, завжди | `global_instructions.md` | Text LLM + Image Stage |
| 2 | **Спеціальні інструкції** | Статичний, на шаблон | `labN_fill.md` | Text LLM |
| 3 | **Файл стилю користувача** | Статичний, на користувача | `user_style.md` (порожній за дефолтом) | Text LLM (пропустити, якщо порожній) |
| 4 | **Додаткові файли** | Динамічний, опційно | Завантаження → конвертація | Text LLM |
| 5 | **Параметри користувача** | Динамічний, кожен запуск | UI форма | Text LLM | 

**Context overflow:** Якщо конвертовані файли > 4000 токенів — кожен файл стискається до 500 токенів. Модель отримує summary, не повний текст.

---

## Протокол Image References

Єдиний формат для зв'язку між Pass 1 (текст) і Pass 2 (зображення).

```
[[IMAGE|type:<type>|subject:<subject>|context:<context>|style:<style>]]
```

| Поле | Обов'язково | Значення | Макс |
|---|---|---|---|
| `type` | так | `diagram` / `chart` / `schema` / `illustration` | — |
| `subject` | так | Що зображено | 80 символів |
| `context` | так | Чому тут, що підтримує | 150 символів |
| `style` | ні | `simple` / `detailed` / `labeled` | — |

### Правила для Text LLM (Pass 1)
- Тільки де візуал дійсно допомагає — ніколи декоративно
- Один reference на рядок, з пустим рядком зверху і знизу
- Всередині `[[IMAGE|...]]` не допускати newlines
- Максимум 1 reference на 300 слів
- Якщо `image_mode=none` — нуль references
- Якщо `image_mode=references` — тільки текст «(див. Рис. 1)», без `[[IMAGE]]`

---

## Рівні складності

| Рівень | ID | Речення | Лексика | Цитування | Passive voice | Формули |
|---|---|---|---|---|---|---|
| Школа | `school` | ≤ 15 слів | без jargon | ні | уникати | ні |
| Університет 1 | `university_1` | ≤ 25 слів | стандартна | рекомендовано | помірно | стандартні |
| Університет 2 | `university_2` | ≤ 30 слів | повна | обов'язково | нормально | повні |
| Бакалавр | `bachelor` | без ліміту | розширена | обов'язково | нормально | без пояснень |

---

## Режими довжини

| Режим | ID | Слів | Нотатки |
|---|---|---|---|
| Короткий | `short` | 500–1000 | Тільки основне |
| Середній | `middle` | 1000–1700 | Стандартна глибина |
| Довгий | `long` | 1700–2500 | Повний опис з прикладами |
| Великий | `large` | 2500+ | Вичерпно, додавати підсекції |

---

## Пасивний кеш (3 рівні)

Кеш працює прозоро через `cache_manager.py`.

| Рівень | Ключ | Значення | Коли використовується |
|---|---|---|---|
| **LLM кеш** | SHA256(template_id + params + user_files_hash + style_hash) | Відповідь LLM | Pass 1: якщо точний збіг — пропустити виклик API |
| **Image кеш** | SHA256(render_spec) | PNG файл | Pass 2a: якщо точний збіг — не генерувати заново |
| **Document кеш** | SHA256(filled.py без anchor) | DOCX + PDF | Pass 2b: якщо filled.py не змінився — не запускати executor |

---

## Pipeline: Pass 1 — Текстова LLM

```
[Step 0] Конвертація файлів  ← детермінований код
  1. Хешувати кожен файл
  2. Перевірити кеш (library_file)
  3. Конвертувати: pdf/docx → txt, pptx → md, зображення → vision опис
  4. Якщо >4000 токенів → стиснути до 500 токенів на файл
  5. Зберегти в library_file
         │
         ▼
[Step 1] Text Model  ← LLM
  Контекст (в порядку):
    1. global_instructions.md        (prefix-cache)
    2. labN_fill.md                  (prefix-cache)
    3. user_style.md                 (prefix-cache, якщо не порожній)
    4. конвертовані файли / summaries
    5. параметри користувача + Python шаблон

  Завдання:
    - Заповнити всі [Вставте ...] плейсхолдери
    - Дотримуватись word count, hardness, style
    - Якщо image_mode=full: вставити [[IMAGE|...]] references
    - Якщо image_mode=references: тільки текст «(див. Рис. N)»
    - Якщо image_mode=none: без згадок про рисунки
    - НЕ змінювати Python-синтаксис поза плейсхолдерами
  Cache: llm_cache — збіг → повернути без API

  Output: filled.py (plain text)
         │
         ▼
[Step 2] ResponseParser  ← детермінований код
  1. Витягнути Python-код (зрізати ```python, ```, текст до/після)
  2. Перевірити синтаксис: ast.parse(filled.py) → HARD FAIL
  3. Пошук незаповнених [Вставте ...] → HARD FAIL
  4. Перевірити word count (±15%) → WARN
  5. Якщо image_mode=full:
     a. Знайти всі [[IMAGE|...]] → валідувати формат
     b. ManifestValidator: перевірити, що references в обох create_docx + create_pdf
     c. Малформовані → WARN, видалити
  HARD FAIL → зупинити, показати помилку
         │
         ▼
   Передати на Pass 2
```

---

## Pipeline: Pass 2 — Image Processing Stage

```
[Step 3a] Image Generator  ← детермінований код + опційно модель
  Для кожного [[IMAGE|...]] з Pass 1:
    1. Перевірити image_cache (SHA256 render_spec)
    2. Якщо diagram/chart → matplotlib, згенерувати PNG
    3. Якщо illustration/schema → HuggingFace API, згенерувати PNG
    4. Якщо помилка → маркер _FAILED замість PNG
  Output: PNG файли, image_manifest (список: ref → png_path)
         │
         ▼
[Step 3b] ManifestValidator  ← детермінований код
  1. Перевірити кожен anchor з manifest у create_docx() та create_pdf()
  2. Якщо anchor є в DOCX але немає в PDF → WARN
  3. Якщо зайві anchor (в коді але не в manifest) → WARN
         │
         ▼
[Step 4] Script Executor  ← ізольований subprocess
  1. AST-валідація: заборонити os.system, eval, exec, import subprocess
  2. subprocess.run(["python", "filled.py"], timeout=30, cwd=temp_dir)
  3. Помилка → STOP, показати traceback
  Output: DOCX + PDF (з [[IMAGE|...]] як текст)
         │
         ▼
[Step 5] Image Composer  ← детермінований код
  Для кожного запису manifest:
    1. Знайти [[IMAGE|...]] у вихідному DOCX/PDF
    2. Вставити PNG + підпис «Рис. N — <subject>»
    3. Вирізати маркер
  Якщо _FAILED: вставити "(рисунок не вдалося згенерувати)"
  Output: фінальні DOCX + PDF → користувачу
```

**Якщо `image_mode=none/references`:**
- Step 3a–5 пропускаються
- Script Executor запускається одразу після Pass 1
- Output: DOCX + PDF без зображень

---

## Матриця помилок

| Етап | Помилка | Серйозність | Дія |
|---|---|---|---|
| File Converter | Невідомий тип файлу | WARN | Пропустити, продовжити |
| File Converter | Конвертація порожня | WARN | Пропустити, продовжити |
| File Converter | Файл завеликий | WARN | Стиснути |
| ResponseParser | Код не проходить ast.parse | HARD | STOP, показати рядок помилки |
| ResponseParser | Незаповнені плейсхолдери | HARD | STOP, список ключів |
| ResponseParser | Word count поза діапазоном | WARN | Продовжити |
| ResponseParser | Малформований [[IMAGE]] | WARN | Видалити, продовжити |
| Image Generator | Помилка генерації | WARN | _FAILED маркер |
| ManifestValidator | Anchor в DOCX але не в PDF | WARN | Продовжити |
| Script Executor | Exception при виконанні | HARD | STOP, показати traceback |
| Script Executor | Вихідний файл не створено | HARD | STOP |
| Image Composer | Anchor не знайдено в документі | WARN | Пропустити |

---

## Матриця відповідальності

| Завдання | Text LLM | Image Stage | Детермін. код |
|---|---|---|---|
| Заповнити плейсхолдери | ✅ | ❌ | ❌ |
| Застосувати user style | ✅ | ❌ | ❌ |
| Вставити [[IMAGE]] references | ✅ (якщо image_mode=full) | ❌ | ❌ |
| Парсинг відповіді LLM | ❌ | ❌ | ✅ Step 2 |
| Валідація синтаксису | ❌ | ❌ | ✅ Step 2 |
| Валідація manifest | ❌ | ❌ | ✅ Step 3b |
| Генерація зображень | ❌ | ✅ | ❌ |
| Вставка зображень у документ | ❌ | ❌ | ✅ Step 5 |
| Виконання Python | ❌ | ❌ | ✅ Step 4 |
| Конвертація файлів | ❌ | ❌ | ✅ Step 0 |
| Кешування | ❌ | ❌ | ✅ CacheManager |

---

## Component Map — файли для реалізації

| Файл | Призначення |
|---|---|
| `app/pipeline/orchestrator.py` | Координатор Pass 1 + Pass 2 |
| `app/pipeline/llm_client.py` | API клієнт з ретраями та prefix-caching |
| `app/pipeline/response_parser.py` | Парсинг відповіді LLM → код + manifest |
| `app/pipeline/template_processor.py` | Збірка контексту з 5 елементів |
| `app/pipeline/file_converter.py` | Конвертація завантажених файлів |
| `app/pipeline/manifest_validator.py` | Валідація references vs код |
| `app/pipeline/image_dispatcher.py` | Генерація PNG (matplotlib / HF) |
| `app/pipeline/template_executor.py` | Безпечний запуск filled.py |
| `app/pipeline/composer.py` | Вставка PNG у DOCX/PDF |
| `app/cache_manager.py` | 3-рівневий SQLite кеш |
| `app/history.py` | CRUD для сесій |
| `app/config.py` | .env + параметри |
| `app/models.py` | Pydantic схеми |
