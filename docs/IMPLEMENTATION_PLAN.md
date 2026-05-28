# Agent_for_TOM: План імплементації

> **Дата створення:** 27.05.2026  
> **Версія:** 1.0  
> **Статус:** На розгляді

---

## 1. Мета проєкту

Створити **локальний веб-застосунок**, який генерує стандартизовані DOCX документи (наприклад, методичні вказівки до виконання лабораторних робіт) відповідно до **ДСТУ 3008:2015**, наповнюючи їх AI-згенерованим контентом на основі вимог користувача.

**Цільова аудиторія:** викладачі українських університетів.

---

## 2. Формат застосунку: Локальний веб-застосунок

### 2.1. Чому саме локальний веб-застосунок?

| Формат | Де працює | Потрібен хостинг? | Наш вибір? |
|--------|-----------|-------------------|------------|
| **Вебсайт** | Хостинг в інтернеті | ✅ Так | ❌ |
| **Вебсервіс / API** | Хмара, багато користувачів | ✅ Так, 24/7 | ❌ |
| **Десктоп-застосунок** | Локально, нативне вікно (Electron, Tauri) | ❌ | ❌ Зайва складність |
| **Локальний веб-застосунок** | Локально, UI у браузері | ❌ | ✅ **Наш варіант** |

### 2.2. Обґрунтування вибору

1. **Простота для користувача:** `pip install -r requirements.txt` → `python run.py` — і все працює. Не потрібно платити за хостинг, не потрібен домен, не потрібен деплой. Браузер є у кожного.

2. **Python-нативність:** Весь backend на Python — `python-docx`, `google-genai`, `FastAPI`. Не потрібно мости між мовами (як у Electron/Node.js або Tauri/Rust).

3. **Немає потреби в нативному вікні:** Electron додав би +150 MB до розміру. Tauri потребує Rust. Браузерний UI дає ті самі можливості.

4. **Приватність:** Дані не залишають машину користувача (крім запиту до Gemini API через його власний API-ключ).

5. **Розширюваність:** Той самий FastAPI-backend можна задеплоїти на хмарний сервер без змін коду.

### 2.3. Як це працює

```
Користувач запускає:  python run.py
                          │
                          ▼
              FastAPI сервер стартує на localhost:8000
                          │
                          ▼
              Автоматично відкривається браузер
              http://127.0.0.1:8000
                          │
                          ▼
              Користувач працює у браузері
              (все крутиться локально)
                          │
                          ▼
              Єдиний зовнішній запит — до Gemini API
              (за контентом, з API-ключем користувача)
```

---

## 3. Аналіз оригінальної архітектури

Початкова пропозиція: **Blank → Локальна LLM (Ollama) → обирає скрипт → API модель (Gemini) → заповнює Python-скрипт → локальна LLM → виконує скрипт → DOCX**.

### 3.1. Виявлені недоліки

| # | Проблема | Серйозність | Опис |
|---|----------|-------------|------|
| 1 | **Виконання ненадійного коду** | 🔴 Критична | AI-згенерований Python може містити шкідливі команди (видалення файлів, витік даних, backdoor) |
| 2 | **Prompt injection** | 🔴 Критична | Введення користувача може маніпулювати AI для генерації шкідливого коду |
| 3 | **Подвійна LLM** | 🟡 Середня | Складність підтримки двох систем одночасно (Ollama + Gemini API) |
| 4 | **Апаратні вимоги** | 🟡 Середня | Локальна LLM потребує 8-16 GB RAM та бажано GPU |
| 5 | **Нестабільне форматування** | 🟡 Середня | Кожна генерація Python-коду дає різне форматування DOCX |
| 6 | **Ненадійність коду** | 🟡 Середня | Згенерований код може мати помилки синтаксису, відсутні імпорти |

---

## 4. Рекомендована архітектура: "Schema-First"

**Ключова ідея:** Замість генерації Python-коду, AI генерує тільки **структурований контент (JSON)** через Pydantic-схеми. Перевірені Python-скрипти конвертують ці дані в DOCX із гарантованою відповідністю ДСТУ.

### 4.1. Схема архітектури

```
┌──────────────────────────────────────────────────────────┐
│                   🌐 БРАУЗЕР (Web UI)                     │
│  ┌────────────────────────────────────────────────────┐  │
│  │  Wizard (5 кроків):                                │  │
│  │  1. Тип документа (карточки)                       │  │
│  │  2. Метадані (автозаповнення з profile.json)       │  │
│  │  3. Вимоги до контенту                             │  │
│  │  4. Стиль/персона автора                           │  │
│  │  5. API ключ → Генерація → Завантаження            │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────┬───────────────────────────────┘
                           │ HTTP POST /api/generate
                           ▼
┌──────────────────────────────────────────────────────────┐
│                  ⚙️ FastAPI Backend                        │
│                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │ Маршрутизатор │→│ PromptBuilder│→│  Pydantic    │   │
│  │ (rule-based)  │  │ (промпт +    │  │  Validator   │   │
│  │              │  │  схема)      │  │              │   │
│  └──────────────┘  └──────┬───────┘  └──────┬───────┘   │
│                           │                  │           │
└───────────────────────────┼──────────────────┼───────────┘
                           │                  │
                    ┌──────▼───────┐          │
                    │ 🤖 Gemini API │          │
                    │ JSON mode +  │          │
                    │ Pydantic     │          │
                    │ schema       │          │
                    └──────┬───────┘          │
                           │ Structured JSON  │
                           ▼                  │
                    ┌──────────────┐          │
                    │ Pydantic     │◄─────────┘
                    │ Validation   │
                    └──────┬───────┘
                           │ Validated model
                           ▼
┌──────────────────────────────────────────────────────────┐
│              📄 DOCX Generator (python-docx)              │
│  ┌────────────────────────────────────────────────────┐  │
│  │  Перевірені Python-скрипти:                        │  │
│  │  • base_generator.py   (ДСТУ стилі)               │  │
│  │  • lab_guidelines.py   (методичні вказівки)        │  │
│  │  • syllabus.py         (силабуси) [майбутнє]      │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────┬───────────────────────────────┘
                           │
                           ▼
                    📥 Завантаження .docx
```

### 4.2. Порівняння архітектур

| Критерій | Оригінальна (LLM → код → виконання) | Рекомендована (Schema-First) |
|----------|-------------------------------------|-------------------------------|
| **Безпека** | ❌ Виконання AI-коду | ✅ Тільки дані від AI |
| **Апаратні вимоги** | ❌ 8-16 GB для локальної LLM | ✅ Тільки Python + браузер |
| **Стабільність DOCX** | ❌ Різна при кожній генерації | ✅ Детерміновано, pixel-perfect |
| **Складність** | ❌ 2 LLM + code execution | ✅ 1 API + trusted scripts |
| **Швидкість** | ❌ 2 inference кроки | ✅ 1 API виклик |
| **Розширюваність** | ❌ Нові промпти + скрипти | ✅ Нова Pydantic-схема + шаблон |
| **Вартість** | 🟡 Обчислення + API | ✅ Тільки API |
| **Офлайн** | ✅ Часткова | ⚠️ Потрібен API (опція Ollama) |

---

## 5. Структура проєкту та зберігання даних

### 5.1. Повна файлова структура

```
Agent_for_TOM/
│
├── run.py                              # Точка входу (запуск сервера + браузер)
├── requirements.txt                    # Python залежності
├── README.md                           # Документація
├── .gitignore                          # Ігнорувати user_data/ та output/
│
├── frontend/                           # Web UI
│   ├── index.html                      # Wizard-інтерфейс (5 кроків)
│   ├── styles.css                      # Дизайн-система (dark mode, glassmorphism)
│   └── app.js                          # Логіка UI + HTTP-клієнт
│
├── backend/                            # FastAPI Backend (КОД ЗАСТОСУНКУ)
│   ├── __init__.py
│   ├── main.py                         # FastAPI app + маршрути API
│   ├── config.py                       # Конфігурація (шляхи, порт)
│   ├── models.py                       # Pydantic моделі (запити/відповіді)
│   │
│   ├── services/                       # Бізнес-логіка
│   │   ├── __init__.py
│   │   ├── ai_service.py              # Gemini API клієнт
│   │   ├── prompt_builder.py          # Конструктор промптів
│   │   ├── document_service.py        # Оркестрація генерації
│   │   └── profile_service.py         # Робота з profile.json
│   │
│   ├── generators/                     # 📜 DOCX ГЕНЕРАТОРИ (Python-скрипти)
│   │   ├── __init__.py
│   │   ├── base_generator.py          # Базовий клас (ДСТУ стилі)
│   │   ├── lab_guidelines.py          # Методичні вказівки до лаб. робіт
│   │   ├── syllabus.py                # [МАЙБУТНЄ] Силабус
│   │   └── course_program.py          # [МАЙБУТНЄ] Робоча програма
│   │
│   └── templates/                      # Шаблони та конфігурації
│       ├── schemas/                    # 📐 Pydantic-схеми (що AI генерує)
│       │   ├── __init__.py
│       │   ├── lab_guidelines.py      # Схема методичних вказівок
│       │   └── syllabus.py            # [МАЙБУТНЄ] Схема силабусу
│       ├── prompts/                    # 💬 Системні промпти для AI
│       │   ├── lab_guidelines.md      # Промпт для методичних вказівок
│       │   └── syllabus.md            # [МАЙБУТНЄ] Промпт для силабусу
│       └── personas/                   # 🎭 Стилі персони автора
│           └── personas.json          # Набір пресетів стилю
│
├── user_data/                          # 👤 ПЕРСОНАЛЬНІ ДАНІ (НЕ в git!)
│   ├── profile.json                   # Профіль користувача
│   ├── api_keys.json                  # Збережені API-ключі
│   └── history/                       # Історія генерацій
│       ├── 2026-05-27_lab_guidelines_001.json
│       └── ...
│
├── output/                             # 📥 ЗГЕНЕРОВАНІ DOCX (НЕ в git!)
│   ├── 2026-05-27_methodical_guidelines.docx
│   └── ...
│
├── tests/                              # Тести
│   ├── test_generators.py
│   ├── test_api.py
│   └── test_schemas.py
│
└── docs/                               # Документація
    └── IMPLEMENTATION_PLAN.md          # Цей файл
```

### 5.2. Де зберігаються Python-скрипти (генератори DOCX)

Python-скрипти є **частиною коду застосунку** і зберігаються в `backend/generators/`:

```
backend/generators/
├── base_generator.py          # Базові ДСТУ стилі (шрифт, поля, інтервал)
├── lab_guidelines.py          # Генератор: Методичні вказівки
├── syllabus.py                # Генератор: Силабус (майбутнє)
└── course_program.py          # Генератор: Робоча програма (майбутнє)
```

**Маршрутизація** — простий словник, без LLM:

```python
# backend/services/document_service.py

TEMPLATES = {
    "lab_guidelines": {
        "name": "Методичні вказівки до лабораторних робіт",
        "schema": LabGuidelinesContent,        # Pydantic-схема для AI
        "generator": LabGuidelinesGenerator,    # Python-скрипт генерації
        "prompt": "prompts/lab_guidelines.md",  # Системний промпт
    },
    "syllabus": {
        "name": "Силабус",
        "schema": SyllabusContent,
        "generator": SyllabusGenerator,
        "prompt": "prompts/syllabus.md",
    },
}
```

Користувач обирає тип у UI → система бере відповідний скрипт зі словника. Жодного AI для вибору не потрібно — це детерміновано, миттєво, не потребує GPU/RAM.

### 5.3. Де зберігаються персональні дані користувача

Персональні дані зберігаються **локально** в `user_data/` (додано в `.gitignore`):

#### `user_data/profile.json`

```json
{
  "personal": {
    "full_name": "Іванов Іван Іванович",
    "academic_title": "к.т.н., доцент",
    "position": "доцент кафедри"
  },
  "institution": {
    "ministry": "Міністерство освіти і науки України",
    "university": "Національний технічний університет «ХПІ»",
    "faculty": "Навчально-науковий інститут комп'ютерних наук",
    "department": "Кафедра програмної інженерії та інформаційних технологій",
    "city": "Харків"
  },
  "preferences": {
    "default_persona": "formal_academic",
    "default_language": "uk",
    "custom_instructions": "Завжди використовувати приклади мовою Python"
  }
}
```

#### `user_data/api_keys.json`

```json
{
  "gemini": "AIza...",
  "openai": null
}
```

#### Що це дає:

| Без profile.json | З profile.json |
|------------------|----------------|
| Кожен раз вводити ВНЗ, кафедру, ім'я | ✅ Заповнюється автоматично |
| Кожен раз обирати стиль | ✅ Дефолтний стиль збережено |
| Кожен раз вводити API-ключ | ✅ Ключ збережено локально |
| Не пам'ятає попередні генерації | ✅ Історія доступна |

#### Логіка в UI:

1. **Перший запуск** → Wizard показує крок "Налаштування профілю" → користувач заповнює один раз → зберігається
2. **Наступні запуски** → Метадані підтягуються з `profile.json`, користувач одразу до контенту
3. **Редагування** → Кнопка ⚙️ у header UI

---

## 6. Детальний опис компонентів

### 6.1. Компонент: Web UI (Frontend)

**Файли:** `frontend/index.html`, `frontend/styles.css`, `frontend/app.js`

Premium веб-інтерфейс із wizard-подібною навігацією (5 кроків з анімованими переходами).

**Крок 1 — Тип документа:**
- Карточки з іконками (методичні вказівки, силабус тощо)
- Завантажуються з `GET /api/templates`

**Крок 2 — Метадані:**
- Форма: університет, кафедра, дисципліна, автори, рік, місто
- Автозаповнення з `profile.json` (якщо існує)

**Крок 3 — Контент:**
- Динамічна форма з полями для кожної лабораторної роботи
- Поля: тема, мета, ключові слова, кількість завдань
- Кнопки "Додати лабораторну" / "Видалити"

**Крок 4 — Стиль:**
- Вибір персони з пресетів
- Textarea для додаткових інструкцій

**Крок 5 — Генерація:**
- Введення/підтягування API ключа
- Кнопка "Згенерувати"
- Progress bar з етапами (генерація контенту → створення DOCX)
- Кнопка завантаження готового файлу

**Дизайн:** Dark mode, glassmorphism, Google Font (Inter), gradient акценти, smooth transitions.

---

### 6.2. Компонент: FastAPI Backend

**Файли:** `backend/main.py`, `backend/models.py`, `backend/config.py`

#### API Endpoints:

```
POST /api/generate           # Запуск генерації DOCX
GET  /api/templates          # Список доступних типів документів
GET  /api/personas           # Список стилів персон
GET  /api/status/{job_id}    # Статус генерації (SSE)
GET  /api/download/{job_id}  # Завантаження готового DOCX

GET  /api/profile            # Отримати профіль користувача
PUT  /api/profile            # Оновити профіль користувача
GET  /api/history            # Історія генерацій
```

#### Pydantic моделі:

```python
class GenerateRequest(BaseModel):
    template_type: str           # "lab_guidelines"
    api_key: str                 # Gemini API key
    persona: str                 # "formal_academic"
    metadata: DocumentMetadata   # university, department, ...
    content_requirements: dict   # опис бажаного контенту
    custom_instructions: str     # додаткові інструкції стилю

class DocumentMetadata(BaseModel):
    ministry: str                # "Міністерство освіти і науки України"
    university: str
    faculty: str
    department: str
    discipline: str
    authors: list[str]
    city: str
    year: int

class GenerateResponse(BaseModel):
    job_id: str
    status: str  # "pending" | "generating_content" | "building_docx" | "done" | "error"
    download_url: str | None
    error_message: str | None
```

---

### 6.3. Компонент: AI Service

**Файли:** `backend/services/ai_service.py`, `backend/services/prompt_builder.py`

#### GeminiService

```python
class GeminiService:
    async def generate_content(
        self,
        api_key: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: type[BaseModel]  # Pydantic model
    ) -> BaseModel:
        """Викликає Gemini API з JSON mode + Pydantic schema."""

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=user_prompt,
            config={
                "system_instruction": system_prompt,
                "response_mime_type": "application/json",
                "response_schema": response_schema,  # Pydantic!
            }
        )
        return response.parsed  # Validated Pydantic model!
```

**Ключова перевага:** Gemini API з `response_schema` **гарантує** валідний JSON, що відповідає Pydantic-схемі. Не потрібен regex-парсинг чи ручна обробка.

#### PromptBuilder

```python
class PromptBuilder:
    def build_system_prompt(self, template_type: str, persona: str) -> str:
        """Зібрати system prompt з шаблону + персони."""
        # Завантажує prompts/lab_guidelines.md
        # Додає інструкції персони з personas.json
        # Повертає повний system prompt

    def build_user_prompt(self, metadata: dict, requirements: dict) -> str:
        """Зібрати user prompt з метаданих + вимог користувача."""
        # Форматує запит з деталями дисципліни
        # Включає теми лабораторних, кількість завдань тощо
```

---

### 6.4. Компонент: DOCX Генератори (ДСТУ 3008:2015)

**Файли:** `backend/generators/base_generator.py`, `backend/generators/lab_guidelines.py`

#### BaseDocxGenerator — ДСТУ параметри

```python
class BaseDocxGenerator:
    # ДСТУ 3008:2015
    FONT_NAME = "Times New Roman"
    FONT_SIZE = Pt(14)
    LINE_SPACING = 1.5
    FIRST_LINE_INDENT = Cm(1.25)
    MARGIN_TOP = Cm(2)
    MARGIN_BOTTOM = Cm(2)
    MARGIN_LEFT = Cm(3)      # Для зшивання
    MARGIN_RIGHT = Cm(1)

    def setup_styles(self)           # Налаштування стилів документа
    def setup_page_layout(self)      # Поля сторінки
    def add_title_page(self, meta)   # Титульна сторінка
    def add_toc_field(self)          # Поле змісту (оновлюється в Word)
    def add_heading(self, text, level, number)  # Заголовки за ДСТУ
    def add_paragraph(self, text)    # Абзац з відступом 1.25 см
    def add_numbered_list(self, items)
    def add_bulleted_list(self, items)
    def add_table(self, headers, rows, caption)
    def add_page_numbers(self)       # Нумерація (XML injection)
    def add_references(self, refs)   # Список літератури
    def save(self, path) -> str
```

#### LabGuidelinesGenerator — Методичні вказівки

```python
class LabGuidelinesGenerator(BaseDocxGenerator):
    def generate(self, metadata: DocumentMetadata, content: LabGuidelinesContent) -> str:
        """Генерує 'Методичні вказівки до виконання лабораторних робіт'."""
        # 1. Титульний аркуш
        #    - Міністерство, ВНЗ, кафедра
        #    - "МЕТОДИЧНІ ВКАЗІВКИ до виконання лабораторних робіт
        #       з дисципліни «...»"
        #    - Місто, рік
        # 2. Зміст (TOC field code)
        # 3. ВСТУП
        # 4. Для кожної лабораторної роботи:
        #    - ЛАБОРАТОРНА РОБОТА №N (heading 1, великі літери)
        #    - Тема (heading 2)
        #    - Мета роботи
        #    - Теоретичні відомості (3-5 абзаців)
        #    - Завдання (нумерований список)
        #    - Порядок виконання (покрокова інструкція)
        #    - Контрольні запитання (нумерований список, 5-10)
        #    - Вимоги до звіту
        # 5. СПИСОК ВИКОРИСТАНИХ ДЖЕРЕЛ (ДСТУ 8302:2015)
        # 6. ДОДАТКИ (опціонально)
```

#### Таблиця параметрів ДСТУ 3008:2015

| Параметр | Значення |
|----------|----------|
| Папір | A4, книжкова орієнтація |
| Поля: верхнє / нижнє | 20 мм |
| Поле: ліве | 30 мм (для зшивання) |
| Поле: праве | 10 мм |
| Шрифт | Times New Roman, 14 pt |
| Міжрядковий інтервал | 1.5 (полуторний) |
| Абзацний відступ | 1.25 см |
| Вирівнювання тексту | По ширині (Justified) |
| Заголовки розділів | Великі літери, жирний, по центру, без крапки |
| Підзаголовки | З абзацним відступом, жирний, з великої літери |
| Нумерація сторінок | Арабські, верхній правий кут (або нижній центр) |
| Титульна сторінка | Рахується, але номер НЕ проставляється |
| Таблиці | "Таблиця 1.1 — Назва" |
| Рисунки | "Рисунок 1.1 — Назва" |
| Список літератури | За ДСТУ 8302:2015 |

---

### 6.5. Компонент: Pydantic-схеми (AI output)

**Файли:** `backend/templates/schemas/lab_guidelines.py`

```python
from pydantic import BaseModel, Field

class LabWork(BaseModel):
    """Одна лабораторна робота."""
    topic: str = Field(description="Тема лабораторної роботи")
    objective: str = Field(description="Мета роботи, 1-2 речення")
    theory: str = Field(
        description="Теоретичні відомості, 3-5 абзаців, формальний академічний стиль"
    )
    tasks: list[str] = Field(
        description="Список завдань для виконання (3-5 завдань)",
        min_length=3
    )
    procedure: str = Field(
        description="Покроковий порядок виконання лабораторної роботи"
    )
    questions: list[str] = Field(
        description="Контрольні запитання для самоперевірки (5-10 питань)",
        min_length=5
    )
    report_requirements: str = Field(
        description="Вимоги до оформлення звіту з лабораторної роботи"
    )

class LabGuidelinesContent(BaseModel):
    """Повний контент методичних вказівок."""
    introduction: str = Field(
        description="Вступ: мета дисципліни, актуальність, структура вказівок, 2-3 абзаци"
    )
    lab_works: list[LabWork] = Field(min_length=1)
    references: list[str] = Field(
        description="Список використаних джерел у форматі ДСТУ 8302:2015",
        min_length=3
    )
```

**Як це працює з Gemini API:**
- `Field(description=...)` виступає інструкцією для AI щодо формату та обсягу контенту
- Gemini API з `response_schema=LabGuidelinesContent` **гарантовано** повертає JSON цієї структури
- `response.parsed` повертає вже валідований Pydantic-об'єкт

---

### 6.6. Компонент: Стилі персони автора

**Файл:** `backend/templates/personas/personas.json`

```json
{
  "formal_academic": {
    "name": "Формальний академічний",
    "description": "Строгий, офіційний стиль. Пасивний стан, третя особа.",
    "instructions": "Використовуйте пасивний стан ('розглядається', 'аналізується'). Уникайте розмовних конструкцій. Кожне твердження має бути обґрунтованим. Стиль викладу — безособовий."
  },
  "practical_oriented": {
    "name": "Практично-орієнтований",
    "description": "Акцент на практичних прикладах та застосуванні.",
    "instructions": "Включайте конкретні приклади коду, розрахунків або алгоритмів. Описуйте реальні сценарії використання. Кожну тему пов'язуйте з практичним застосуванням."
  },
  "detailed_explanatory": {
    "name": "Детальний пояснювальний",
    "description": "Докладні пояснення, орієнтовані на початківців.",
    "instructions": "Пояснюйте кожен термін при першому вживанні. Використовуйте аналогії та порівняння. Поступово збільшуйте складність матеріалу. Включайте приклади для кращого розуміння."
  },
  "concise_technical": {
    "name": "Стислий технічний",
    "description": "Мінімум тексту, максимум інформації.",
    "instructions": "Використовуйте списки та таблиці замість довгих абзаців. Тільки ключова технічна інформація. Уникайте вступних фраз та повторень."
  }
}
```

---

## 7. Pipeline генерації (End-to-End)

```
Крок 1: Користувач заповнює Wizard у браузері
    │
    ▼
Крок 2: Frontend відправляє POST /api/generate
    │   {template_type, api_key, persona, metadata, content_requirements}
    │
    ▼
Крок 3: Backend обирає шаблон з TEMPLATES словника (rule-based)
    │   → LabGuidelinesContent (Pydantic schema)
    │   → LabGuidelinesGenerator (Python script)
    │   → prompts/lab_guidelines.md (system prompt)
    │
    ▼
Крок 4: PromptBuilder збирає промпт
    │   system_prompt = шаблон промпту + інструкції персони
    │   user_prompt = метадані + вимоги користувача
    │
    ▼
Крок 5: GeminiService відправляє запит
    │   → Gemini API (JSON mode + response_schema)
    │   → Отримує LabGuidelinesContent (validated JSON)
    │
    ▼
Крок 6: LabGuidelinesGenerator створює DOCX
    │   → python-docx з ДСТУ стилями
    │   → Титульний аркуш → Зміст → Вступ → Лаб. роботи → Список літератури
    │   → Зберігає в output/
    │
    ▼
Крок 7: Backend повертає download URL
    │
    ▼
Крок 8: Користувач завантажує .docx файл
```

---

## 8. Ключові технічні рішення

### 8.1. Чому Schema-First, а не Code Generation?

| Аспект | Code Generation | Schema-First |
|--------|----------------|--------------|
| AI генерує | Python-код (python-docx) | Структурований JSON |
| Виконання | Небезпечне (arbitrary code) | Безпечне (тільки дані) |
| Форматування | Непередбачуване | Детерміноване (ДСТУ) |
| Валідація | Складна (AST аналіз) | Проста (Pydantic) |
| Помилки | Syntax errors, runtime crashes | JSON validation errors (retryable) |

### 8.2. Чому rule-based маршрутизація?

Для вибору шаблону за типом документа достатньо простого dictionary lookup. Це **детерміновано**, **миттєво** і **не потребує GPU/RAM**.

### 8.3. Обмеження: Зміст (TOC)

`python-docx` **не може** автоматично оновити зміст. Після генерації документа користувач мусить відкрити його у MS Word і натиснути **Ctrl+A → F9** для оновлення змісту. Альтернатива — використання `win32com` для автоматизації Word (тільки Windows, потребує встановлений MS Word).

---

## 9. Технологічний стек

| Компонент | Технологія | Версія |
|-----------|-----------|--------|
| **Backend** | FastAPI + Uvicorn | ≥0.115 |
| **AI API** | Google Gemini (google-genai) | ≥1.0 |
| **DOCX генерація** | python-docx + docxtpl | ≥1.1 / ≥0.18 |
| **Валідація даних** | Pydantic | ≥2.7 |
| **Frontend** | HTML + CSS + JavaScript | Vanilla |
| **Шрифти** | Google Fonts (Inter) | — |
| **Python** | CPython | ≥3.11 |

### Залежності (requirements.txt):

```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
python-docx>=1.1.0
docxtpl>=0.18.0
google-genai>=1.0.0
pydantic>=2.7.0
aiofiles>=24.1.0
python-multipart>=0.0.9
```

---

## 10. Запуск застосунку

### Встановлення:

```bash
cd Agent_for_TOM
pip install -r requirements.txt
```

### Запуск:

```bash
python run.py
```

Автоматично відкриється браузер на `http://127.0.0.1:8000`.

### run.py:

```python
"""Точка входу: запускає FastAPI сервер і відкриває браузер."""
import uvicorn
import webbrowser
import threading

def open_browser():
    webbrowser.open("http://127.0.0.1:8000")

if __name__ == "__main__":
    threading.Timer(1.5, open_browser).start()
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=False)
```

---

## 11. План верифікації

### 11.1. Автоматизовані тести

**Тести генератора DOCX** (`tests/test_generators.py`):
```bash
python -m pytest tests/test_generators.py -v
```
- Поля сторінки = 20/20/30/10 мм
- Шрифт Normal = Times New Roman 14pt
- Інтервал = 1.5
- Абзацний відступ = 1.25 см
- Титульна сторінка містить всі обов'язкові елементи
- Нумерація сторінок присутня
- Заголовки форматовані за ДСТУ

**Тести API** (`tests/test_api.py`):
```bash
python -m pytest tests/test_api.py -v
```
- `GET /api/templates` → 200, список шаблонів
- `POST /api/generate` з валідними даними → 200, job_id
- `POST /api/generate` з невалідними → 422, error details
- `GET /api/profile` → 200, профіль
- `PUT /api/profile` → 200, оновлений профіль

**Тести Pydantic-схем** (`tests/test_schemas.py`):
- Валідний JSON → модель створюється
- Відсутні обов'язкові поля → ValidationError
- Неправильні типи → ValidationError

### 11.2. Ручна верифікація

1. **Візуальна перевірка DOCX:** відкрити у MS Word, перевірити відповідність ДСТУ, перевірити якість AI-контенту, оновити зміст (Ctrl+A, F9)

2. **End-to-End тест:** `python run.py` → заповнити wizard → генерувати → завантажити DOCX

3. **UI/UX перевірка:** Chrome + Firefox, анімації, адаптивність, error states

---

## 12. Відкриті питання

1. **Типи документів на першому етапі:** Підтримувати тільки методичні вказівки, чи ще й силабуси, робочі програми?

2. **Зразок документа:** Чи є конкретний DOCX-зразок вашого ВНЗ? Різні університети мають свої відхилення від ДСТУ.

3. **Мова:** Контент тільки українською, чи потрібна підтримка інших мов?

4. **API провайдери:** Тільки Gemini, чи також OpenAI / Anthropic / локальний Ollama?

5. **Стилі персон:** Чи достатньо 4 запропонованих пресетів, чи потрібні додаткові?

---

## 13. Дорожня карта

### Фаза 1: MVP (поточна реалізація)
- [  ] Базова архітектура (FastAPI + Frontend)
- [  ] Профіль користувача (profile.json)
- [  ] DOCX генератор: методичні вказівки (ДСТУ 3008:2015)
- [  ] Інтеграція Gemini API (structured JSON output)
- [  ] Web UI з wizard-інтерфейсом

### Фаза 2: Розширення
- [  ] Додаткові типи документів (силабус, робоча програма)
- [  ] Підтримка кількох AI провайдерів (OpenAI, Anthropic)
- [  ] Опція Ollama для офлайн-режиму
- [  ] Автоматичне оновлення TOC через win32com (Windows)

### Фаза 3: Полірування
- [  ] Пакування в .exe (PyInstaller)
- [  ] Редагування згенерованого контенту перед створенням DOCX
- [  ] Збереження та завантаження "проєктів" (набори налаштувань)
- [  ] Підтримка кастомних шаблонів (.docx templates через docxtpl)
