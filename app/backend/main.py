import os
import re
import json
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional

from .models import GenerateRequest, LabGuidelinesContent, LabWork, DocumentMetadata
from .docx_generator import DSTUDocxGenerator

app = FastAPI(title="Agent_for_TOM Backend")

# CORS middleware to allow calls from different origins if needed
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

def load_profile():
    if os.path.exists(PROFILE_PATH):
        try:
            with open(PROFILE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_profile_data(data):
    with open(PROFILE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

@app.get("/api/profile")
def get_profile():
    return load_profile()

@app.put("/api/profile")
def update_profile(profile: dict):
    save_profile_data(profile)
    return {"status": "success"}

def generate_mock_guidelines(req: GenerateRequest) -> LabGuidelinesContent:
    """Generates standard structural mock data when API key is not supplied."""
    discipline = req.metadata.discipline
    req_text = req.content_requirements.lower()
    
    # Try parsing user requirements to separate potential topics
    lines = [line.strip() for line in req.content_requirements.split('\n') if len(line.strip()) > 5]
    topics = []
    
    for l in lines:
        if any(keyword in l.lower() for keyword in ["лабораторна", "робота", "тема", "тема:", "лк"]):
            topics.append(l)
            
    if not topics:
        topics = [
            "Ознайомлення з базовим інструментарієм та налаштування середовища розробника",
            "Створення архітектури першого прототипу та опис метаданих",
            "Розгортання рішень, інтеграційне тестування та налагодження коду",
            "Оптимізація швидкодії, аналіз продуктивності та захист розробленого рішення"
        ]
        
    labs = []
    for i, t in enumerate(topics[:6]): # Limit to max 6 lab works
        clean_title = t
        if ":" in t:
            clean_title = t.split(":", 1)[1].strip()
        elif "—" in t:
            clean_title = t.split("—", 1)[1].strip()
            
        labs.append(
            LabWork(
                topic=clean_title,
                objective=f"Набути стійких практичних навичок проектування систем і налаштування робочих інструментів при вивченні теми '{clean_title}'.",
                theory=f"Теоретичний розділ присвячений глибокому аналізу принципів, що лежать в основі теми '{clean_title}'. Досліджуються основні архітектурні шаблони проектування, які застосовуються для забезпечення гнучкості та модульності побудови систем. Наводяться приклади базових інтерфейсів, які підтримують функціонування даного технологічного стеку. Проводиться детальний огляд специфікацій і стандартів, які регламентують розробку в контексті предметної області {discipline}.",
                tasks=[
                    f"Вивчити теоретичні засади та інженерні підходи щодо теми '{clean_title}'.",
                    f"Розробити працездатний програмний код відповідно до індивідуального варіанту завдання.",
                    "Провести модульне тестування та інтеграційне тестування створених модулів.",
                    "Оформити результати роботи у вигляді електронного звіту за встановленими вимогами."
                ],
                procedure=[
                    "Підготувати та запустити локальне інтегроване середовище розробки (IDE).",
                    "Імпортувати необхідні класи з системних бібліотек.",
                    "Написати програмний код та перевірити роботу основних функцій.",
                    "Зафіксувати результати у вигляді знімків екрану (скріншотів) та логів виконання."
                ],
                questions=[
                    f"Які ключові компоненти використовуються для реалізації концепції '{clean_title}'?",
                    "Поясніть призначення та логіку роботи створених вами методів.",
                    "Які потенційні помилки можуть виникнути при конфігуруванні середовища розробки?",
                    "Які методи відлагодження коду (дебагу) ви застосовували у даній лабораторній роботі?"
                ],
                report_requirements="Звіт виконується на аркушах формату А4 та повинен містити: титульну сторінку встановленого зразка, тему, мету роботи, вихідні тексти програм із коментарями, графічні результати виконання (скріншоти роботи) та обґрунтовані висновки."
            )
        )
        
    return LabGuidelinesContent(
        introduction=f"Дані методичні вказівки призначені для методичного забезпечення викладання дисципліни «{discipline}». "
                     f"Лабораторний практикум покликаний сформувати у здобувачів вищої освіти систему практичних вмінь та навичок "
                     f"з розробки, налагодження та розгортання програмних рішень. Кожна лабораторна робота містить необхідний "
                     f"набір теоретичних відомостей, завдання для самостійного виконання, алгоритм проведення експериментів та контроль знань.",
        lab_works=labs,
        references=[
            f"Іванов І. І. Програмування та архітектура систем з дисципліни «{discipline}». Навчальний посібник. Київ: ВНЗ-Преса, 2024. 320 с.",
            "ДСТУ 3008:2015. Інформація та документація. Звіти у сфері науки і техніки. Структура та правила оформлення. Київ: ДП «УкрНДНЦ», 2016. 31 с.",
            f"Петров П. П. Методичні вказівки до лабораторних практикумів з {discipline}. Харків: НТУ «ХПІ», 2025. 112 с."
        ]
    )

def generate_ai_guidelines(req: GenerateRequest) -> LabGuidelinesContent:
    """Invokes the Gemini 2.5 Flash model requesting validated structured outputs."""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise HTTPException(
            status_code=500, 
            detail="Модуль 'google-genai' не виявлено. Будь ласка, запустіть застосунок без API-ключа у Mock-режимі."
        )
        
    client = genai.Client(api_key=req.api_key)
    
    system_prompt = f"""
    Ви — досвідчений викладач українського університету (професор/доцент) з великим стажем нормоконтролю та методичної роботи.
    Ваше завдання — написати детальний та професійний контент для збірника методичних вказівок з дисципліни «{req.metadata.discipline}».
    
    Мова: виключно українська (академічний, науково-технічний стиль).
    Стиль викладу: {req.persona}. 
    Якщо стиль formal_academic — пишіть строго, безособово, вживаючи пасивний стан ("проводиться аналіз", "досліджуються властивості").
    Якщо style = practical_oriented — зосередьтеся на прикладах коду, практичних технологіях, кроках налаштування.
    
    Уникайте будь-яких плейсхолдерів чи скорочень. Теоретична частина кожної роботи має складатися з 2-3 зв'язаних та розгорнутих абзаців.
    Контрольні питання мають бути реальними, а не загальними.
    """
    
    user_prompt = f"""
    Згенеруй повний навчальний контент з наступними вимогами:
    
    Університет: {req.metadata.university}
    Кафедра: {req.metadata.department}
    Дисципліна: {req.metadata.discipline}
    Автори: {", ".join(req.metadata.authors)}
    
    Вимоги до лабораторних робіт:
    {req.content_requirements}
    
    Вихідні дані мають відповідати схемі:
    1. introduction: вступ, актуальність, мета практикуму (2-3 абзаци).
    2. lab_works: список робіт. Кожна робота містить:
       - topic: тема
       - objective: мета (1-2 речення)
       - theory: детальна теорія (2-3 абзаци)
       - tasks: 3-5 завдань
       - procedure: 4-6 кроків виконання
       - questions: 4-6 контрольних запитань
       - report_requirements: вимоги до звіту з цієї роботи
    3. references: 3-5 джерел українською та англійською мовами у форматі ДСТУ 8302:2015.
    """
    
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
        raise HTTPException(
            status_code=500, 
            detail=f"Помилка підключення або генерації через Gemini API: {str(e)}"
        )

@app.post("/api/generate")
def generate_document(req: GenerateRequest):
    # Route based on api_key presence
    if not req.api_key or req.api_key.strip() == "":
        print("No API Key. Generating Mock Data...")
        content = generate_mock_guidelines(req)
        mode = "Mock"
    else:
        print("API Key provided. Calling Gemini...")
        content = generate_ai_guidelines(req)
        mode = "AI"
        
    filename = f"lab_guidelines_{req.metadata.year}_{req.metadata.discipline.replace(' ', '_')}.docx"
    filename = re.sub(r'[\\/*?:"<>|]', "", filename)
    filepath = os.path.join(OUTPUT_DIR, filename)
    
    try:
        generator = DSTUDocxGenerator()
        generator.generate(req.metadata, content, filepath)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Не вдалося згенерувати DOCX файл: {str(e)}")
        
    return {
        "status": "success",
        "mode": mode,
        "filename": filename,
        "download_url": f"/api/download/{filename}"
    }

@app.get("/api/download/{filename}")
def download_docx(filename: str):
    filepath = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Файл не знайдено на сервері.")
    return FileResponse(
        path=filepath, 
        filename=filename, 
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

app.mount("/", StaticFiles(directory=os.path.join(BASE_DIR, "frontend"), html=True), name="frontend")
