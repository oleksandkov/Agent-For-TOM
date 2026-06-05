"""
Agent_for_TOM — FastAPI backend.

Responsibilities:
  * Persist a local teacher profile (GET/PUT /api/profile)
  * Generate a document via either:
      - mock (no API key)         — deterministic stub content
      - HuggingFace (free tier)   — InferenceClient + validate-and-retry
      - Gemini (legacy)           — kept for backwards-compatibility
  * Build the document in the requested format(s): pdf | docx | both

Output filenames share a common base and differ by extension:
    lab_guidelines_{year}_{discipline}.pdf
    lab_guidelines_{year}_{discipline}.docx

Both files are kept in OUTPUT_DIR; the API response advertises one or
two download URLs depending on the requested format.
"""

from __future__ import annotations

import os
import re
import json
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from .models import GenerateRequest, LabGuidelinesContent, LabWork
from .docx_generator import DSTUDocxGenerator
from .pdf_generator import DSTUPdfGenerator

app = FastAPI(title="Agent_for_TOM Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
USER_DATA_DIR = os.path.join(BASE_DIR, "user_data")
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(USER_DATA_DIR, exist_ok=True)
PROFILE_PATH = os.path.join(USER_DATA_DIR, "profile.json")


# ---------------------------------------------------------------------------
# Profile persistence
# ---------------------------------------------------------------------------

def load_profile() -> dict:
    if os.path.exists(PROFILE_PATH):
        try:
            with open(PROFILE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_profile_data(data: dict) -> None:
    with open(PROFILE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@app.get("/api/profile")
def get_profile():
    return load_profile()


@app.put("/api/profile")
def update_profile(profile: dict):
    save_profile_data(profile)
    return {"status": "success"}


# ---------------------------------------------------------------------------
# Content generation — Mock
# ---------------------------------------------------------------------------

def generate_mock_guidelines(req: GenerateRequest) -> LabGuidelinesContent:
    """Deterministic structural mock. Mirrors the new schema (no tasks, no report_requirements)."""
    discipline = req.metadata.discipline

    lines = [line.strip() for line in req.content_requirements.split("\n") if len(line.strip()) > 5]
    topics: list[str] = []
    for l in lines:
        if any(kw in l.lower() for kw in ["лабораторна", "робота", "тема", "тема:", "лк", "№"]):
            topics.append(l)

    if not topics:
        topics = [
            "Ознайомлення з базовим інструментарієм та налаштування середовища розробника",
            "Створення архітектури першого прототипу та опис метаданих",
            "Розгортання рішень, інтеграційне тестування та налагодження коду",
            "Оптимізація швидкодії, аналіз продуктивності та захист розробленого рішення",
        ]

    labs: list[LabWork] = []
    for t in topics[:6]:
        clean_title = t
        for sep in (":", "—", " - "):
            if sep in t:
                clean_title = t.split(sep, 1)[1].strip()
                break

        labs.append(
            LabWork(
                topic=clean_title,
                objective=(
                    f"Набути стійких практичних навичок проектування систем і налаштування "
                    f"робочих інструментів при вивченні теми «{clean_title}»."
                ),
                theory=(
                    f"Теоретичний розділ присвячений глибокому аналізу принципів, що лежать в основі "
                    f"теми «{clean_title}». Досліджуються основні архітектурні шаблони проектування, "
                    f"які застосовуються для забезпечення гнучкості та модульності побудови систем. "
                    f"Наводяться приклади базових інтерфейсів, які підтримують функціонування даного "
                    f"технологічного стеку. Проводиться детальний огляд специфікацій і стандартів, які "
                    f"регламентують розробку в контексті предметної області «{discipline}»."
                ),
                procedure=[
                    "Підготувати та запустити локальне інтегроване середовище розробки (IDE).",
                    "Імпортувати необхідні класи з системних бібліотек згідно з темою роботи.",
                    "Написати програмний код та перевірити роботу основних функцій.",
                    "Провести тестування працездатності на контрольних прикладах.",
                    "Зафіксувати результати у вигляді знімків екрану (скріншотів) та логів виконання.",
                ],
                questions=[
                    f"Які ключові компоненти використовуються для реалізації концепції «{clean_title}»?",
                    "Поясніть призначення та логіку роботи створених вами методів.",
                    "Які потенційні помилки можуть виникнути при конфігуруванні середовища розробки?",
                    "Які методи відлагодження коду (дебагу) ви застосовували у даній лабораторній роботі?",
                    "Як можна підвищити продуктивність та читабельність створеного рішення?",
                ],
                tasks=[
                    f"Опрацювати теоретичні відомості з теми «{clean_title}» та підготувати конспект.",
                    "Налаштувати робоче середовище відповідно до вимог лабораторної роботи.",
                    "Реалізувати програмне рішення поставленої задачі.",
                    "Провести серію тестових запусків на контрольних прикладах.",
                    "Зафіксувати результати у звіті (скріншоти, лістинги коду, графіки).",
                    "Сформулювати висновки щодо отриманих результатів.",
                ],
                variants=["1, 4, 5", "2, 5, 6", "3, 6, 7", "4, 7, 8", "5, 8, 9", "1, 6, 8"],
                report_sections=[
                    "Титульний аркуш (тема, ПІБ студента, група, рік).",
                    "Мета роботи та короткі теоретичні відомості.",
                    "Хід роботи (лістинги коду, ключові рішення).",
                    "Результати тестування (скріншоти, графіки, таблиці).",
                    "Висновки щодо виконаної роботи.",
                    "Список використаних джерел.",
                ],
                references=[
                    f"Іванов І. І. Основи розробки програмних систем з дисципліни «{discipline}». "
                    f"Навчальний посібник. Київ: ВНЗ-Преса, 2024. 320 с.",
                    "ДСТУ 3008:2015. Інформація та документація. Звіти у сфері науки і техніки. "
                    "Структура та правила оформлення. Київ: ДП «УкрНДНЦ», 2016. 31 с.",
                    f"Петров П. П. Методичні вказівки до лабораторних практикумів з {discipline}. "
                    "Харків: НТУ «ХПІ», 2025. 112 с.",
                ],
            )
        )

    return LabGuidelinesContent(
        introduction=(
            f"Дані методичні вказівки призначені для методичного забезпечення викладання дисципліни "
            f"«{discipline}». Лабораторний практикум покликаний сформувати у здобувачів вищої освіти "
            f"систему практичних вмінь та навичок з розробки, налагодження та розгортання програмних "
            f"рішень. Кожна лабораторна робота містить необхідний набір теоретичних відомостей, "
            f"порядок виконання та контрольні запитання для самоперевірки."
        ),
        lab_works=labs,
        references=[],
    )


# ---------------------------------------------------------------------------
# Content generation — Gemini (legacy, kept for any old profile.api_key entries)
# ---------------------------------------------------------------------------

def generate_ai_guidelines_gemini(req: GenerateRequest) -> LabGuidelinesContent:
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="Модуль 'google-genai' не виявлено. Скористайтеся HuggingFace API або Mock-режимом.",
        )

    client = genai.Client(api_key=req.api_key)
    system_prompt = _build_system_prompt(req)
    user_prompt = _build_user_prompt(req)

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_schema=LabGuidelinesContent,
            ),
        )
        return response.parsed
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Помилка Gemini API: {e}")


# ---------------------------------------------------------------------------
# Content generation — HuggingFace
# ---------------------------------------------------------------------------

def generate_ai_guidelines_hf(
    req: GenerateRequest,
    model_override: str | None = None,
    provider_override: str | None = None,
) -> LabGuidelinesContent:
    from .ai.hf_service import generate_validated
    from .ai.hf_models import DEFAULT_PROVIDER, get_default_for_provider

    system_prompt = _build_system_prompt(req)
    user_prompt = _build_user_prompt(req)

    provider = (provider_override or DEFAULT_PROVIDER).lower()
    model = model_override or get_default_for_provider(provider)

    try:
        return generate_validated(
            api_key=req.api_key,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=LabGuidelinesContent,
            model=model,
            provider=provider,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Shared prompt builders
# ---------------------------------------------------------------------------

def _build_system_prompt(req: GenerateRequest) -> str:
    persona = req.persona
    return (
        "Ви — досвідчений викладач українського університету (професор/доцент) "
        "з великим стажем нормоконтролю та методичної роботи.\n"
        f"Ваше завдання — написати детальний професійний контент для збірника методичних вказівок "
        f"з дисципліни «{req.metadata.discipline}».\n\n"
        "Мова: виключно українська (академічний, науково-технічний стиль).\n"
        f"Стиль викладу: {persona}.\n"
        "  - formal_academic — строгий, безособовий, пасивний стан.\n"
        "  - practical_oriented — багато прикладів, фрагментів коду, практичних кроків.\n"
        "  - detailed_explanatory — для початківців, з аналогіями та розгорнутими поясненнями.\n"
        "  - concise_technical — короткі списки, мінімум опису, максимум фактів.\n\n"
        "Жодних плейсхолдерів типу «...», «TODO», «Lorem ipsum».\n"
        "Теоретична частина кожної роботи — 2-3 розгорнуті абзаци, пов'язані між собою.\n"
        "Контрольні питання — конкретні, без загальних «що таке?».\n"
        "Кожне посилання у полі references — у форматі ДСТУ 8302:2015 (Прізвище І. Б. Назва. "
        "Місто: Видавництво, Рік. Кількість с.).\n\n"
        "ВИ ВІДПОВІДАЄТЕ ВИКЛЮЧНО ВАЛІДНИМ JSON-ОБ'ЄКТОМ, без жодних пояснень поза JSON."
    )


def _build_user_prompt(req: GenerateRequest) -> str:
    return (
        "Згенеруй повний контент методичних вказівок з такими реквізитами:\n\n"
        f"Університет: {req.metadata.university}\n"
        f"Кафедра: {req.metadata.department}\n"
        f"Дисципліна: {req.metadata.discipline}\n"
        f"Автори: {', '.join(req.metadata.authors)}\n"
        f"Місто: {req.metadata.city}\n"
        f"Рік: {req.metadata.year}\n\n"
        "ВИМОГИ ДО ЛАБОРАТОРНИХ РОБІТ (теми, побажання, кількість):\n"
        f"{req.content_requirements}\n\n"
        "Структура JSON:\n"
        "{\n"
        '  "introduction": "Вступ до практикуму — 2-3 абзаци. Якщо непотрібен, поверни порожній рядок.",\n'
        '  "lab_works": [\n'
        "    {\n"
        '      "topic": "Тема лабораторної роботи",\n'
        '      "objective": "Мета роботи — 1-2 речення",\n'
        '      "theory": "Методичні відомості — 2-3 розгорнуті абзаци",\n'
        '      "procedure": ["Крок 1", "Крок 2", "Крок 3", "Крок 4"],\n'
        '      "questions": ["Питання 1", "Питання 2", "Питання 3", "Питання 4"],\n'
        '      "references": ["Джерело 1 у форматі ДСТУ 8302:2015", "Джерело 2"]\n'
        "    }\n"
        "  ],\n"
        '  "references": []  // залиш порожнім, глобальний список не потрібен\n'
        "}\n\n"
        "Поверни ТІЛЬКИ JSON. Жодного тексту до чи після. Жодних ```json``` огороджень."
    )


@app.get("/api/models")
def list_models(
    provider: str = "cerebras",
    api_key: Optional[str] = None,
    limit: int = 30,
    chat_only: bool = True,
    refresh: bool = False,
):
    """Return the catalog of chat models available for a HuggingFace inference provider.

    `api_key` is optional — without it we serve a curated FALLBACK list so the
    UI is never empty. With a valid token we hit the live HF API and cache for
    5 minutes.
    """
    from .ai.hf_models import list_available_models
    try:
        data = list_available_models(
            api_key=api_key,
            provider=provider,
            limit=limit,
            chat_only=chat_only,
            force_refresh=refresh,
        )
        return {"status": "success", **data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Не вдалося отримати список моделей: {e}")


# ---------------------------------------------------------------------------
# Filename + render pipeline
# ---------------------------------------------------------------------------

def _safe_filename(discipline: str, year: int, ext: str) -> str:
    base = f"lab_guidelines_{year}_{discipline.replace(' ', '_')}"
    base = re.sub(r'[\\/*?:"<>|]', "", base)
    return f"{base}.{ext}"


def _build_artifacts(
    req: GenerateRequest,
    content: LabGuidelinesContent,
    output_format: str,
) -> list[dict]:
    """Render the document in the requested format(s) and return a list of file descriptors."""
    artifacts: list[dict] = []
    formats = [output_format] if output_format in ("pdf", "docx") else ["pdf", "docx"]

    for fmt in formats:
        filename = _safe_filename(req.metadata.discipline, req.metadata.year, fmt)
        filepath = os.path.join(OUTPUT_DIR, filename)
        try:
            if fmt == "pdf":
                DSTUPdfGenerator().generate(req.metadata, content, filepath)
                media_type = "application/pdf"
            else:
                DSTUDocxGenerator().generate(req.metadata, content, filepath)
                media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Не вдалося згенерувати {fmt.upper()}: {e}")

        artifacts.append({
            "format": fmt,
            "filename": filename,
            "filepath": filepath,
            "media_type": media_type,
            "download_url": f"/api/download/{filename}",
        })
    return artifacts


# ---------------------------------------------------------------------------
# Main /api/generate endpoint
# ---------------------------------------------------------------------------

@app.post("/api/generate")
def generate_document(req: GenerateRequest):
    use_ai = bool(req.api_key and req.api_key.strip())
    provider_label = "mock"
    if use_ai:
        from .ai.hf_service import detect_provider
        provider = (req.ai_provider or detect_provider(req.api_key)).lower()
        # Anything other than "gemini" goes through the HF Inference router
        # (cerebras, novita, together, hf-inference, or generic "huggingface").
        is_hf = provider != "gemini"
        provider_label = provider
        try:
            if is_hf:
                model_name = req.ai_model or "(default)"
                print(f"[generate] HF via {provider}/{model_name}...")
                content = generate_ai_guidelines_hf(
                    req,
                    model_override=req.ai_model,
                    provider_override=req.ai_provider,
                )
                mode = "AI"
            else:
                print("[generate] Gemini 2.5 Flash...")
                content = generate_ai_guidelines_gemini(req)
                mode = "AI"
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"AI-генерація не вдалася: {e}")
    else:
        print("[generate] Mock (no API key)...")
        content = generate_mock_guidelines(req)
        mode = "Mock"

    artifacts = _build_artifacts(req, content, req.output_format)
    primary = artifacts[0]
    return {
        "status": "success",
        "mode": mode,
        "provider": provider_label,
        "filename": primary["filename"],
        "format": primary["format"],
        "media_type": primary["media_type"],
        "download_url": primary["download_url"],
        "artifacts": [
            {"format": a["format"], "filename": a["filename"], "download_url": a["download_url"]}
            for a in artifacts
        ],
    }


# ---------------------------------------------------------------------------
# Download route — figures out the right Content-Type per extension
# ---------------------------------------------------------------------------

_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


@app.get("/api/download/{filename}")
def download_file(filename: str):
    filepath = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Файл не знайдено на сервері.")
    ext = os.path.splitext(filename)[1].lower()
    media_type = _MEDIA_TYPES.get(ext, "application/octet-stream")
    return FileResponse(path=filepath, filename=filename, media_type=media_type)


# ---------------------------------------------------------------------------
# Static frontend (must be last)
# ---------------------------------------------------------------------------

app.mount(
    "/",
    StaticFiles(directory=os.path.join(BASE_DIR, "frontend"), html=True),
    name="frontend",
)
