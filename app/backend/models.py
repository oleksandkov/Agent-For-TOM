from pydantic import BaseModel, Field
from typing import List, Optional

class DocumentMetadata(BaseModel):
    university: str = Field(..., description="Назва університету")
    department: str = Field(..., description="Назва кафедри")
    discipline: str = Field(..., description="Назва навчальної дисципліни")
    authors: List[str] = Field(..., description="Список авторів (ПІБ)")
    city: str = Field(..., description="Місто")
    year: int = Field(..., description="Рік видання")

class GenerateRequest(BaseModel):
    api_key: Optional[str] = Field(None, description="API-ключ Gemini. Якщо порожній, запускається Mock-режим.")
    metadata: DocumentMetadata
    content_requirements: str = Field(..., description="Вимоги користувача до контенту лабораторних робіт")
    persona: str = Field("formal_academic", description="Стиль автора")

# Pydantic schema for AI Output Validation
class LabWork(BaseModel):
    topic: str = Field(..., description="Тема лабораторної роботи")
    objective: str = Field(..., description="Мета роботи, 1-2 речення")
    theory: str = Field(..., description="Короткі теоретичні відомості, 2-3 абзаци")
    tasks: List[str] = Field(..., description="Завдання для виконання (3-5 завдань)")
    procedure: List[str] = Field(..., description="Покроковий порядок виконання роботи")
    questions: List[str] = Field(..., description="Контрольні запитання (4-6 питань)")
    report_requirements: str = Field(..., description="Вимоги до звіту")

class LabGuidelinesContent(BaseModel):
    introduction: str = Field(..., description="Вступ до циклу лабораторних робіт (актуальність, цілі, 2-3 абзаци)")
    lab_works: List[LabWork] = Field(..., description="Список лабораторних робіт")
    references: List[str] = Field(..., description="Список рекомендованих джерел (3-5 джерел у форматі ДСТУ 8302:2015)")
