from pydantic import BaseModel, Field
from typing import List, Optional, Literal


class DocumentMetadata(BaseModel):
    university: str
    department: str
    discipline: str
    authors: List[str]
    city: str
    year: int


class GenerateRequest(BaseModel):
    api_key: Optional[str] = Field(None)
    ai_provider: Optional[str] = Field(None, description="'huggingface' or 'gemini' (legacy). Auto-detected from token format if None.")
    ai_model: Optional[str] = Field(None, description="Override the default AI model id (HuggingFace only).")
    output_format: Literal["pdf", "docx", "both"] = Field("both", description="Output file format(s).")
    metadata: DocumentMetadata
    content_requirements: str
    persona: str = Field("formal_academic")


class LabWork(BaseModel):
    """Single lab work entry, aligned with the structure of LMV_LabRob.pdf sample."""
    topic: str = Field(..., description="Тема лабораторної роботи")
    objective: str = Field(..., description="Мета роботи — 1-2 речення")
    theory: str = Field(..., description="Методичні / теоретичні відомості — 2-3 розгорнуті абзаци")
    procedure: List[str] = Field(default_factory=list, description="Хід роботи — 4-7 кроків виконання")
    questions: List[str] = Field(default_factory=list, description="Контрольні запитання — 4-6 питань")
    tasks: List[str] = Field(default_factory=list, description="Завдання — 4-10 конкретних завдань для виконання")
    variants: List[str] = Field(default_factory=list, description="Варіанти — 3-10 варіантів завдань (зазвичай номери)")
    report_sections: List[str] = Field(default_factory=list, description="Зміст звіту — перелік розділів звіту (4-8 пунктів)")
    references: List[str] = Field(default_factory=list, description="Література — 2-5 джерел у форматі ДСТУ 8302:2015")


class LabGuidelinesContent(BaseModel):
    """Full document payload produced by the AI and validated by Pydantic before rendering."""
    introduction: Optional[str] = Field("", description="Необов'язковий вступ (одна чи більше абзаців). Пропускається, якщо порожній.")
    lab_works: List[LabWork]
    references: Optional[List[str]] = Field(default_factory=list, description="Необов'язковий глобальний список джерел наприкінці документа.")
