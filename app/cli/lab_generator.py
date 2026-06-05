"""
Agent-For-TOM — Interactive CLI (single-user program).

Prompts the teacher for the few facts that *only* they know
(university, department, discipline, lab topic, what to include, etc.) and
delegates the academic writing to a HuggingFace chat model. The result is
rendered into a PDF and a DOCX using the same ДСТУ 3008:2015 generators the
web app uses.

Why a CLI in addition to the FastAPI app?
-----------------------------------------
* The web app expects an API key + model picker + browser session.
  The CLI is a zero-friction, "press Enter to accept a sensible default"
  flow that fits a teacher's keyboard-only workflow.
* The user can run it from the terminal, get the two files in
  `app/output/`, and walk away. No web server to start.
* The same AI + rendering pipeline is reused, so quality is identical.

Usage
-----
    python app/run_cli.py                  # interactive
    python app/run_cli.py --no-ai          # mock generator (no API key needed)
    python app/run_cli.py --topic "..."    # pre-fill the lab topic

The program is intentionally bilingual-friendly: all messages to the user
are in Ukrainian (the target audience), but the source code and comments
are in English.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from typing import Optional

# Allow `python app/run_cli.py` from the repo root: ensure the `app/` parent
# (the repo root) is on sys.path so `app.cli.*` and `app.backend.*` resolve.
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from app.backend.models import (  # noqa: E402  (path tweak above)
    DocumentMetadata,
    GenerateRequest,
    LabGuidelinesContent,
)
from app.backend.docx_generator import DSTUDocxGenerator  # noqa: E402
from app.backend.pdf_generator import DSTUPdfGenerator  # noqa: E402

# Where the final files are written. Mirrors the FastAPI app's choice.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Small UI helpers — keep the terminal output calm and readable
# ---------------------------------------------------------------------------

CYAN = "\033[96m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
RED = "\033[91m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

# Windows consoles (cp1251 / cp866) can't encode the box-drawing and
# check-mark glyphs. We force UTF-8 on stdout/stderr where possible, and
# fall back to ASCII if even that fails.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass


def _supports_color() -> bool:
    return sys.stdout.isatty() and os.name != "nt" or os.environ.get("FORCE_COLOR") == "1"


def _paint(text: str, color: str) -> str:
    if not _supports_color():
        return text
    return f"{color}{text}{RESET}"


def _safe_print(text: str) -> None:
    """Print a Unicode string, falling back to ASCII if the terminal cannot
    encode it. Prevents UnicodeEncodeError on legacy Windows consoles."""
    try:
        print(text)
    except UnicodeEncodeError:
        ascii_fallback = text.encode("ascii", errors="replace").decode("ascii")
        print(ascii_fallback)


def header() -> None:
    _safe_print(_paint("\n+======================================================+", CYAN))
    _safe_print(_paint("|        Agent-For-TOM  *  Generator of lab work       |", CYAN))
    _safe_print(_paint("|        DSTU 3008:2015 style  *  PDF + DOCX           |", CYAN))
    _safe_print(_paint("+======================================================+\n", CYAN))


def step(text: str) -> None:
    _safe_print(_paint(f"  > {text}", DIM))


def info(text: str) -> None:
    _safe_print(_paint(f"  {text}", ""))


def ok(text: str) -> None:
    _safe_print(_paint(f"  [OK] {text}", GREEN))


def warn(text: str) -> None:
    _safe_print(_paint(f"  [!] {text}", YELLOW))


def err(text: str) -> None:
    _safe_print(_paint(f"  [X] {text}", RED))


def ask(prompt: str, default: Optional[str] = None, required: bool = True) -> str:
    """Prompt the user; return the stripped answer or the default."""
    suffix = f" [{default}]" if default else ""
    while True:
        try:
            raw = input(_paint(f"  ? {prompt}{suffix}: ", BOLD)).strip()
        except (EOFError, UnicodeDecodeError):
            raw = ""
        except UnicodeEncodeError:
            # Fall back to ASCII-only prompt if the terminal cannot encode it.
            raw = input(f"  ? {prompt}{suffix}: ").strip()
        if raw:
            return raw
        if default is not None:
            return default
        if not required:
            return ""
        warn("  This field is required. Press Ctrl+C to abort.")


def ask_choice(prompt: str, options: list[str], default_index: int = 0) -> str:
    """Show a numbered menu and return the chosen option text."""
    _safe_print(_paint(f"  ? {prompt}", BOLD))
    for i, opt in enumerate(options, 1):
        marker = "*" if i - 1 == default_index else " "
        _safe_print(f"     {marker} {i}. {opt}")
    while True:
        try:
            raw = input(_paint(f"     Choose 1-{len(options)} (Enter = {default_index + 1}): ", BOLD)).strip()
        except (EOFError, UnicodeDecodeError):
            raw = ""
        if not raw:
            return options[default_index]
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        warn("  Enter a number from the list.")


def ask_multiline(prompt: str, default: str = "") -> str:
    """Collect multiple lines until the user enters a single '.' or empty
    line twice. Returns the joined text.
    """
    _safe_print(_paint(f"  ? {prompt}", BOLD))
    _safe_print(_paint("    (type lines; two empty lines in a row, or a single '.', to finish)", DIM))
    lines: list[str] = []
    if default:
        for d in default.splitlines():
            _safe_print(_paint(f"    | {d}", DIM))
        lines = default.splitlines()
    blanks = 0
    while True:
        try:
            raw = input(_paint("    > ", BOLD))
        except (EOFError, UnicodeDecodeError):
            break
        if raw.strip() == ".":
            break
        if raw == "":
            blanks += 1
            if blanks >= 2 and lines:
                break
            continue
        blanks = 0
        lines.append(raw)
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Defaults — used so an experienced user can just press Enter repeatedly
# ---------------------------------------------------------------------------

DEFAULTS = {
    "university": "Національний технічний університет України «Київський політехнічний інститут імені Ігоря Сікорського»",
    "department": "автоматики та управління в технічних системах",
    "discipline": "Людино-машинна взаємодія",
    "city": "Київ",
    "year": str(time.gmtime().tm_year),
    "authors": "Іванов І. І.",
    "topic": "Програмна реалізація структур діалогу командного типу, меню і екранних форм",
}

PERSONAS = [
    ("formal_academic", "formal_academic — строгий академічний стиль (рекомендовано)"),
    ("practical_oriented", "practical_oriented — багато прикладів та коду"),
    ("detailed_explanatory", "detailed_explanatory — для початківців, з аналогіями"),
    ("concise_technical", "concise_technical — короткі списки, максимум фактів"),
]

PROVIDERS = [
    "huggingface (cerebras) — рекомендовано, швидко",
    "huggingface (novita)",
    "huggingface (together)",
    "huggingface (hf-inference)",
    "mock — без API-ключа, шаблонний текст для перевірки",
]


# ---------------------------------------------------------------------------
# Prompt builders — the same logic the FastAPI endpoint uses, kept locally
# so the CLI has no dependency on `app.backend.main` (avoids pulling in
# FastAPI just to ask the user a few questions).
# ---------------------------------------------------------------------------

def _build_system_prompt(persona: str, discipline: str) -> str:
    return (
        "Ви — досвідчений викладач українського університету (професор/доцент) "
        "з великим стажем нормоконтролю та методичної роботи.\n"
        f"Ваше завдання — написати детальний професійний контент для методичних "
        f"вказівок з дисципліни «{discipline}».\n\n"
        "Мова: виключно українська (академічний, науково-технічний стиль).\n"
        f"Стиль викладу: {persona}.\n"
        "  - formal_academic — строгий, безособовий, пасивний стан.\n"
        "  - practical_oriented — багато прикладів, фрагментів коду, практичних кроків.\n"
        "  - detailed_explanatory — для початківців, з аналогіями та розгорнутими поясненнями.\n"
        "  - concise_technical — короткі списки, мінімум опису, максимум фактів.\n\n"
        "Жодних плейсхолдерів типу «...», «TODO», «Lorem ipsum».\n"
        "Теоретична частина кожної роботи — 2-3 розгорнуті абзаци, пов'язані між собою.\n"
        "Контрольні питання — конкретні, без загальних «що таке?».\n"
        "Кожне посилання у полі references — у форматі ДСТУ 8302:2015 "
        "(Прізвище І. Б. Назва. Місто: Видавництво, Рік. Кількість с.).\n\n"
        "ВИ ВІДПОВІДАЄТЕ ВИКЛЮЧНО ВАЛІДНИМ JSON-ОБ'ЄКТОМ, без жодних пояснень поза JSON."
    )


def _build_user_prompt(req: GenerateRequest) -> str:
    return (
        "Згенеруй повний контент однієї лабораторної роботи з такими реквізитами:\n\n"
        f"Університет: {req.metadata.university}\n"
        f"Кафедра: {req.metadata.department}\n"
        f"Дисципліна: {req.metadata.discipline}\n"
        f"Автори: {', '.join(req.metadata.authors)}\n"
        f"Місто: {req.metadata.city}\n"
        f"Рік: {req.metadata.year}\n\n"
        "ВИМОГИ ДО ЛАБОРАТОРНОЇ РОБОТИ (тема, побажання, обов'язкові розділи):\n"
        f"{req.content_requirements}\n\n"
        "Структура JSON (поверни САМЕ такий об'єкт, масив lab_works містить ОДИН елемент):\n"
        "{\n"
        '  "introduction": "Короткий вступ (1-2 абзаци), що мотивує роботу. Поверни порожній рядок, якщо не потрібен.",\n'
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
        '  "references": []\n'
        "}\n\n"
        "Поверни ТІЛЬКИ JSON. Жодного тексту до чи після. Жодних ```json``` огороджень."
    )


# ---------------------------------------------------------------------------
# Mock generator — a deterministic stand-in for the AI, used when the user
# has no API key. Mirrors the LMV_LabRob.pdf structure exactly.
# ---------------------------------------------------------------------------

def _generate_mock(req: GenerateRequest, flags: dict | None = None) -> LabGuidelinesContent:
    flags = flags or {}
    include_tv = bool(flags.get("_include_tasks_variants", True))
    try:
        n_variants = max(0, int(flags.get("_variant_count", 6) or 6))
    except (TypeError, ValueError):
        n_variants = 6

    discipline = req.metadata.discipline
    topic = req.content_requirements.splitlines()[0] if req.content_requirements else DEFAULTS["topic"]
    topic = re.sub(r"^(тема|topic|лабораторна|робота|лк)\s*[:№\-]?\s*\d*\s*", "", topic, flags=re.I).strip(" .:")
    if not topic:
        topic = DEFAULTS["topic"]

    intro = (
        f"Дана лабораторна робота входить до складу практикуму з дисципліни "
        f"«{discipline}» і спрямована на формування у здобувачів вищої освіти "
        f"стійких практичних навичок, передбачених робочою програмою курсу. "
        f"Під час виконання роботи закріплюються теоретичні положення, "
        f"вивчені в лекційному матеріалі, та формуються компетентності, "
        f"необхідні для подальшої професійної діяльності."
    )

    theory = (
        f"Теоретичні відомості, необхідні для виконання роботи, базуються на "
        f"фундаментальних положеннях дисципліни «{discipline}». Розглядаються "
        f"основні концепції, що лежать в основі теми «{topic}», аналізуються "
        f"типові архітектурні рішення та наводяться приклади їх практичного "
        f"застосування. Особлива увага приділяється інструментальним засобам і "
        f"методам, які дозволяють ефективно вирішувати поставлену задачу в "
        f"умовах сучасного виробництва та науково-дослідної роботи.\n\n"
        f"Додатково розглядаються нормативні документи та стандарти, що "
        f"регламентують процес розробки, тестування та документування "
        f"програмних рішень. Аналізуються типові помилки, які виникають під "
        f"час виконання подібних завдань, та наводяться рекомендації щодо їх "
        f"уникнення. Вивчення теоретичного матеріалу передує практичній "
        f"частині та є обов'язковим етапом підготовки здобувача до виконання "
        f"роботи.\n\n"
        f"Перед початком практичної частини рекомендується повторити "
        f"лекційний матеріал, ознайомитися з рекомендованими джерелами та "
        f"підготувати робоче середовище відповідно до встановлених вимог."
    )

    procedure = [
        "Підготувати робоче середовище, перевірити наявність необхідного програмного забезпечення та бібліотек.",
        "Опрацювати теоретичні відомості, викладені в методичних вказівках, законспектувати ключові положення.",
        "Розробити програмне рішення відповідно до поставленої задачі, дотримуючись вимог до структури та стилю коду.",
        "Провести тестування розробленого рішення на контрольних прикладах, зафіксувати результати у вигляді скріншотів.",
        "Оформити звіт про виконання лабораторної роботи згідно з вимогами ДСТУ 3008:2015 та завантажити його у систему.",
    ]

    questions = [
        f"Які ключові концепції лежать в основі теми «{topic}»?",
        "Поясніть призначення та логіку роботи розробленого вами програмного рішення.",
        "Які нормативні документи регламентують розробку подібних систем?",
        "Які типові помилки можуть виникнути під час виконання даної роботи та як їх уникнути?",
        "Як можна підвищити ефективність та читабельність розробленого програмного коду?",
    ]

    tasks = ([
        f"Опрацювати теоретичні відомості з теми «{topic}» та підготувати конспект.",
        "Налаштувати робоче середовище відповідно до вимог лабораторної роботи.",
        "Реалізувати програмне рішення поставленої задачі.",
        "Провести серію тестових запусків на контрольних прикладах.",
        "Зафіксувати результати у звіті (скріншоти, лістинги коду, графіки).",
        "Сформулювати висновки щодо отриманих результатів.",
    ] if include_tv else [])

    variants = ([
        "1, 4, 5", "2, 5, 6", "3, 6, 7",
        "4, 7, 8", "5, 8, 9", "1, 6, 8",
    ][:max(0, n_variants)] if include_tv else [])

    report_sections = [
        "Титульний аркуш (тема, ПІБ студента, група, рік).",
        "Мета роботи та короткі теоретичні відомості.",
        "Хід роботи (лістинги коду, ключові рішення).",
        "Результати тестування (скріншоти, графіки, таблиці).",
        "Висновки щодо виконаної роботи.",
        "Список використаних джерел.",
    ]

    references = [
        f"Іванов І. І. Основи розробки програмних систем з дисципліни «{discipline}»: навч. посіб. Київ: ВНЗ-Преса, 2024. 320 с.",
        "ДСТУ 3008:2015. Інформація та документація. Звіти у сфері науки і техніки. Структура та правила оформлення. Київ: ДП «УкрНДНЦ», 2016. 31 с.",
        f"Петров П. П. Методичні вказівки до лабораторних практикумів з {discipline}. Харків: НТУ «ХПІ», 2025. 112 с.",
    ]

    return LabGuidelinesContent(
        introduction=intro,
        lab_works=[{
            "topic": topic,
            "objective": (
                f"Набути стійких практичних навичок з теми «{topic}» в межах "
                f"дисципліни «{discipline}», навчитися застосовувати теоретичні "
                f"положення курсу для розв'язання прикладних задач."
            ),
            "theory": theory,
            "procedure": procedure,
            "questions": questions,
            "tasks": tasks,
            "variants": variants,
            "report_sections": report_sections,
            "references": references,
        }],
        references=[],
    )


# ---------------------------------------------------------------------------
# AI generator — thin wrapper around the existing HF service
# ---------------------------------------------------------------------------

def _generate_with_ai(req: GenerateRequest, model: str, provider: str) -> LabGuidelinesContent:
    from app.backend.ai.hf_service import generate_validated
    from app.backend.ai.hf_models import DEFAULT_PROVIDER, get_default_for_provider

    provider = provider or DEFAULT_PROVIDER
    model = model or get_default_for_provider(provider)
    system_prompt = _build_system_prompt(req.persona, req.metadata.discipline)
    user_prompt = _build_user_prompt(req)
    return generate_validated(
        api_key=req.api_key,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        schema=LabGuidelinesContent,
        model=model,
        provider=provider,
    )


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

_SAFE = re.compile(r"[\\/*?:\"<>|]")


def _safe_filename(discipline: str, year: int, ext: str) -> str:
    base = f"lab_guidelines_{year}_{discipline.replace(' ', '_')}"
    base = _SAFE.sub("", base)
    return f"{base}.{ext}"


def _render_both(meta: DocumentMetadata, content: LabGuidelinesContent) -> tuple[str, str]:
    pdf_path = os.path.join(OUTPUT_DIR, _safe_filename(meta.discipline, meta.year, "pdf"))
    docx_path = os.path.join(OUTPUT_DIR, _safe_filename(meta.discipline, meta.year, "docx"))
    DSTUPdfGenerator().generate(meta, content, pdf_path)
    DSTUDocxGenerator().generate(meta, content, docx_path)
    return pdf_path, docx_path


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------

def collect_input(args: argparse.Namespace) -> GenerateRequest:
    """Walk the user through every required field."""
    header()
    info(_paint("Кілька запитань — і програма згенерує готову методичку.", DIM))
    info(_paint("Більшість полів мають значення за замовчуванням: просто натискайте Enter.", DIM))
    print()

    university = ask("Повна назва університету", default=args.university or DEFAULTS["university"])
    department = ask("Кафедра", default=args.department or DEFAULTS["department"])
    discipline = ask("Дисципліна", default=args.discipline or DEFAULTS["discipline"])
    city = ask("Місто", default=args.city or DEFAULTS["city"])
    year_raw = ask("Рік видання", default=args.year or DEFAULTS["year"])
    year = int(year_raw) if year_raw.isdigit() else int(DEFAULTS["year"])

    authors_raw = ask(
        "Автори (через кому, у форматі 'Прізвище І. Б.')",
        default=args.authors or DEFAULTS["authors"],
    )
    authors = [a.strip() for a in authors_raw.split(",") if a.strip()] or [DEFAULTS["authors"]]

    print()
    persona_label = ask_choice("Стиль викладу", [p[1] for p in PERSONAS], default_index=0)
    persona = next(code for code, label in PERSONAS if label == persona_label)
    print()

    include_tv_default = bool(getattr(args, "include_tasks_variants", True))
    include_tv = ask_choice(
        "Секції «Завдання» і «Варіанти»",
        ["Так — додати обидві", "Ні — без них"],
        default_index=0 if include_tv_default else 1,
    ).startswith("Так")
    print()
    variant_count = 6
    if include_tv:
        variant_count_raw = ask("Кількість варіантів (3-15)", default=str(getattr(args, "variant_count", 6) or 6))
        try:
            variant_count = max(1, min(15, int(variant_count_raw)))
        except (TypeError, ValueError):
            variant_count = 6
    print()

    topic_default = args.topic or DEFAULTS["topic"]
    info(_paint("Опишіть лабораторну роботу: тема, ціль, які розділи обов'язкові,", DIM))
    info(_paint("рівень складності, мова програмування тощо.", DIM))
    requirements = ask_multiline(
        "Вимоги до лабораторної роботи",
        default=topic_default,
    )
    if not requirements:
        requirements = topic_default

    return GenerateRequest(
        api_key=args.api_key,
        ai_provider=args.ai_provider,
        ai_model=args.ai_model,
        output_format="both",
        metadata=DocumentMetadata(
            university=university,
            department=department,
            discipline=discipline,
            authors=authors,
            city=city,
            year=year,
        ),
        content_requirements=requirements,
        persona=persona,
    ), {
        "_persona": persona,
        "_include_tasks_variants": include_tv,
        "_variant_count": variant_count,
    }


def _apply_structure_filters(content: LabGuidelinesContent, flags: dict) -> LabGuidelinesContent:
    """Drop or trim tasks/variants according to user flags (CLI / GUI)."""
    include_tv = bool(flags.get("_include_tasks_variants", True))
    try:
        n_variants = max(0, int(flags.get("_variant_count", 6) or 6))
    except (TypeError, ValueError):
        n_variants = 6
    for lab in content.lab_works:
        if not include_tv:
            lab.tasks = []
            lab.variants = []
        else:
            lab.variants = (lab.variants or [])[:n_variants]
    return content


def run_generation(req: GenerateRequest, flags: dict | None = None) -> tuple[LabGuidelinesContent, str, list[tuple[str, str]]]:
    """Generate the content and render to PDF+DOCX. Returns
    (content, mode_label, [(format, path), ...]).
    """
    flags = flags or {"_include_tasks_variants": True, "_variant_count": 6}
    use_ai = bool(req.api_key and req.api_key.strip())
    if not use_ai:
        warn("API-ключ не надано — використовую вбудований шаблон (mock).")
        info(_paint("Щоб увімкнути справжню генерацію, передайте --api-key hf_…", DIM))
        content = _generate_mock(req, flags)
        mode = "Mock"
    else:
        info(_paint("Надсилаю запит до HuggingFace Inference…", DIM))
        t0 = time.time()
        try:
            content = _generate_with_ai(req, req.ai_model or "", req.ai_provider or "cerebras")
        except Exception as exc:  # noqa: BLE001 — surface to user with friendly msg
            err(f"AI-генерація не вдалася: {exc}")
            raise SystemExit(1) from exc
        content = _apply_structure_filters(content, flags)
        ok(f"AI-генерація завершена за {time.time() - t0:.1f} c.")
        mode = f"AI ({req.ai_provider or 'cerebras'}/{req.ai_model or 'default'})"

    info(_paint("Рендерю PDF та DOCX (ДСТУ 3008:2015)…", DIM))
    pdf_path, docx_path = _render_both(req.metadata, content)
    ok(f"PDF : {pdf_path}")
    ok(f"DOCX: {docx_path}")
    return content, mode, [("PDF", pdf_path), ("DOCX", docx_path)]


def maybe_open(paths: list[tuple[str, str]]) -> None:
    try:
        ans = input(_paint("\n  ? Відкрити папку з результатами? [y/N]: ", BOLD)).strip().lower()
    except EOFError:
        ans = "n"
    if ans not in ("y", "yes", "т", "так"):
        return
    target = os.path.dirname(paths[0][1])
    if sys.platform.startswith("win"):
        os.startfile(target)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":  # pragma: no cover
        os.system(f'open "{target}"')
    else:  # pragma: no cover
        os.system(f'xdg-open "{target}"')


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agent-for-tom-cli",
        description="Генератор методичних вказівок до лабораторних робіт (ДСТУ 3008:2015).",
    )
    p.add_argument("--api-key", help="HuggingFace API-токен (hf_…). Якщо не передано — mock-режим.")
    p.add_argument("--ai-provider", default="cerebras",
                   help="Провайдер HuggingFace (cerebras, novita, together, hf-inference).")
    p.add_argument("--ai-model", default="",
                   help="Конкретна модель (наприклад, meta-llama/Llama-3.1-8B-Instruct).")
    p.add_argument("--university", help="Повна назва університету.")
    p.add_argument("--department", help="Кафедра.")
    p.add_argument("--discipline", help="Дисципліна.")
    p.add_argument("--city", help="Місто видання.")
    p.add_argument("--year", help="Рік видання.")
    p.add_argument("--authors", help="Автори через кому.")
    p.add_argument("--topic", help="Тема/вимоги до лабораторної (еквівалент інтерактивного вводу).")
    p.add_argument("--no-ai", action="store_true", help="Примусово використати mock-генератор.")
    p.add_argument("--yes", "-y", action="store_true",
                   help="Використати всі дефолти без інтерактивних запитань.")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--include-tasks-variants", dest="include_tasks_variants",
                   action="store_true", default=True,
                   help="Додати секції «Завдання» та «Варіанти» (за замовчуванням).")
    g.add_argument("--no-tasks-variants", dest="include_tasks_variants",
                   action="store_false",
                   help="Не додавати секції «Завдання» та «Варіанти».")
    p.add_argument("--variants", type=int, default=6, dest="variant_count",
                   help="Кількість варіантів (3-15). За замовчуванням 6.")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_argparser().parse_args(argv)
    if args.no_ai:
        args.api_key = ""

    if args.yes:
        # Non-interactive path: synthesize a default-filled request.
        from app.backend.models import DocumentMetadata
        req = GenerateRequest(
            api_key=args.api_key or None,
            ai_provider=args.ai_provider,
            ai_model=args.ai_model or None,
            output_format="both",
            metadata=DocumentMetadata(
                university=args.university or DEFAULTS["university"],
                department=args.department or DEFAULTS["department"],
                discipline=args.discipline or DEFAULTS["discipline"],
                authors=[a.strip() for a in (args.authors or DEFAULTS["authors"]).split(",") if a.strip()],
                city=args.city or DEFAULTS["city"],
                year=int(args.year) if (args.year and args.year.isdigit()) else int(DEFAULTS["year"]),
            ),
            content_requirements=args.topic or DEFAULTS["topic"],
            persona="formal_academic",
        )
    else:
        req = collect_input(args)

    _, mode, paths = run_generation(req)
    info(_paint(f"\nРежим генерації: {mode}", BOLD))
    for fmt, path in paths:
        size = os.path.getsize(path) if os.path.exists(path) else 0
        info(f"   {fmt:<4} • {path}  ({size/1024:.1f} KB)")

    maybe_open(paths)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
