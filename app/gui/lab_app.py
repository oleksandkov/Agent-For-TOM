"""
Agent-For-TOM — CustomTkinter desktop app.

Goals of this UI:
    * Show every important field at once (no hidden tabs).
    * Use a real MODEL PICKER, not a free-text box. The dropdown is
      populated from HuggingFace (or a curated fallback if no token is set
      or the live query fails). A refresh button re-queries the API.
    * Show a real SAVE-LOCATION picker (path + Browse button), so the user
      knows where the files will land before clicking Generate.
    * Make the primary "Згенерувати" action visually dominant.
    * Status bar at the bottom: progress + last message + "open folder" link.

Layout (1400x900 default):
    ┌──────────────────────────────────────────────────────────────┐
    │ Header: title + subtitle                                      │
    ├──────────────────┬───────────────────────────────────────────┤
    │ LEFT  (settings) │ RIGHT (preset form)                       │
    │  - Реквізити     │  Preset title + description               │
    │  - Пресет        │  Dynamic fields (rebuilt on change)       │
    │  - AI            │  Action panel: save location + Generate   │
    │  - Збереження    │  Log / preview                            │
    ├──────────────────┴───────────────────────────────────────────┤
    │ Status bar: progress + status text                            │
    └──────────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import os
import sys
import threading
import webbrowser
from datetime import datetime
from typing import Any

# Ensure repo root is on sys.path so the absolute `app.*` imports work
# when the user launches the app via `python app/run_app.py`.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import customtkinter as ctk
from tkinter import filedialog, messagebox

from app.backend.models import DocumentMetadata, GenerateRequest, LabGuidelinesContent
from app.backend.docx_generator import DSTUDocxGenerator
from app.backend.pdf_generator import DSTUPdfGenerator
from app.gui.presets import PRESETS, Field, Preset, get_preset

# Force UTF-8 on stdout/stderr so Ukrainian text in logs doesn't break
# on legacy Windows consoles.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PERSONAS = [
    ("formal_academic", "Академічний (рекомендовано)"),
    ("practical_oriented", "Практичний (з прикладами)"),
    ("detailed_explanatory", "Пояснювальний (для початківців)"),
    ("concise_technical", "Стислий (короткі списки)"),
]

PROVIDERS = [
    ("cerebras", "Cerebras (рекомендовано)"),
    ("novita", "Novita"),
    ("together", "Together"),
    ("hf-inference", "HF Inference (офіційний)"),
]

DEFAULT_METADATA = {
    "university": "Національний технічний університет України «Київський політехнічний інститут імені Ігоря Сікорського»",
    "department": "автоматики та управління в технічних системах",
    "discipline": "Людино-машинна взаємодія",
    "authors": "Іванов І. І.",
    "city": "Київ",
    "year": str(datetime.now().year),
}

DEFAULT_SAVE_DIR = os.path.join(os.path.expanduser("~"), "Documents", "Agent-For-TOM")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_filename(discipline: str, year: int, ext: str) -> str:
    import re
    base = f"lab_guidelines_{year}_{discipline.replace(' ', '_')}"
    base = re.sub(r'[\\/*?:"<>|]', "", base)
    return f"{base}.{ext}"


def _apply_structure_filters(content: LabGuidelinesContent, values: dict) -> LabGuidelinesContent:
    """Drop or trim sections according to the user's structure toggles.

    Even if the AI returns tasks/variants, we honour the toggle by
    clearing the lists. We also trim `variants` to the requested count.
    """
    include_tv = bool(values.get("_include_tasks_variants", True))
    try:
        n_variants = max(0, int(values.get("_variant_count", 6) or 6))
    except (TypeError, ValueError):
        n_variants = 6

    for lab in content.lab_works:
        if not include_tv:
            lab.tasks = []
            lab.variants = []
        else:
            lab.variants = (lab.variants or [])[:n_variants]
    return content


# ---------------------------------------------------------------------------
# Dynamic list widget (one-line entries + add/remove buttons)
# ---------------------------------------------------------------------------

class ListFieldWidget(ctk.CTkFrame):
    """A vertically-scrolling list of single-line Entry widgets."""

    def __init__(self, master, default: str = "", **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._entries: list[ctk.CTkEntry] = []

        self._scroll = ctk.CTkScrollableFrame(self, fg_color="transparent", height=170)
        self._scroll.pack(fill="x", expand=False, padx=2, pady=(2, 4))

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=2, pady=(0, 2))
        ctk.CTkButton(btn_row, text="+ Додати рядок", width=160, height=28,
                      command=self._add_row).pack(side="left")
        ctk.CTkButton(btn_row, text="− Видалити останній", width=180, height=28,
                      command=self._remove_last).pack(side="left", padx=8)
        ctk.CTkButton(btn_row, text="Очистити все", width=130, height=28,
                      fg_color="gray40", hover_color="gray30",
                      command=self._clear).pack(side="left", padx=8)

        for line in (default or "").splitlines():
            line = line.strip()
            if line:
                self._add_row(line)

        if not self._entries:
            self._add_row("")

    def _add_row(self, value: str = "") -> None:
        entry = ctk.CTkEntry(self._scroll, placeholder_text="...")
        entry.pack(fill="x", padx=2, pady=2)
        if value:
            entry.insert(0, value)
        self._entries.append(entry)

    def _remove_last(self) -> None:
        if not self._entries:
            return
        last = self._entries.pop()
        last.destroy()

    def _clear(self) -> None:
        for e in self._entries:
            e.destroy()
        self._entries.clear()

    def get_values(self) -> list[str]:
        out: list[str] = []
        for e in self._entries:
            v = e.get().strip()
            if v:
                out.append(v)
        return out


# ---------------------------------------------------------------------------
# Section header (icon + title)
# ---------------------------------------------------------------------------

class SectionHeader(ctk.CTkFrame):
    """A subtle section divider with an icon, a bold title, and a hint."""

    def __init__(self, master, icon: str, title: str, hint: str = "",
                 fg_color=None, **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(self, text=icon, font=ctk.CTkFont(size=18),
                     width=32, anchor="w").grid(row=0, column=0, padx=(0, 6), sticky="w")
        ctk.CTkLabel(self, text=title, font=ctk.CTkFont(size=14, weight="bold"),
                     anchor="w").grid(row=0, column=1, sticky="w")
        if hint:
            ctk.CTkLabel(self, text=hint, font=ctk.CTkFont(size=11),
                         text_color=("gray50", "gray70"),
                         anchor="w").grid(row=1, column=1, sticky="w", pady=(0, 4))


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------

class LabApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title("Agent-For-TOM — Генератор методичних вказівок")
        self.geometry("1400x900")
        self.minsize(1200, 720)

        self._field_widgets: dict[str, Any] = {}
        self._current_preset: Preset = PRESETS[0]
        self._model_options: list[str] = []
        self._last_save_dir: str = DEFAULT_SAVE_DIR

        self._build_layout()
        self._populate_defaults()
        self._switch_preset(PRESETS[0].key)
        self._refresh_models()
        self.after(100, self._update_window_title)

    def _update_window_title(self) -> None:
        try:
            self.iconbitmap()
        except Exception:
            pass

    # ----- layout ---------------------------------------------------------

    def _build_layout(self) -> None:
        self.grid_columnconfigure(0, weight=0, minsize=460)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # === Header strip ===
        header = ctk.CTkFrame(self, height=72, corner_radius=0)
        header.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=0, pady=0)
        # header is a strip; the main split-frame goes below
        self.grid_rowconfigure(0, weight=0)

        ctk.CTkLabel(
            header, text="📚  Agent-For-TOM",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(side="left", padx=(20, 12), pady=16)
        ctk.CTkLabel(
            header, text="Методичні вказівки за ДСТУ 3008:2015  •  PDF + DOCX",
            font=ctk.CTkFont(size=13),
            text_color=("gray60", "gray70"),
        ).pack(side="left", pady=20)

        # Theme toggle (right side of header)
        theme_row = ctk.CTkFrame(header, fg_color="transparent")
        theme_row.pack(side="right", padx=20, pady=20)
        self._theme_var = ctk.StringVar(value="dark")
        ctk.CTkSegmentedButton(
            theme_row, values=["☀ Light", "🌙 Dark"],
            command=self._on_theme_changed, width=160, height=28,
        ).pack(side="right")

        # === Main split: left settings, right form ===
        main = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        main.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=0, pady=0)
        main.grid_columnconfigure(0, weight=0, minsize=460)
        main.grid_columnconfigure(1, weight=1)
        main.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        left = ctk.CTkScrollableFrame(main, label_text="")
        left.grid(row=0, column=0, sticky="nsew", padx=(8, 4), pady=(8, 4))
        left.grid_columnconfigure(0, weight=1)

        right = ctk.CTkFrame(main, corner_radius=0, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(4, 8), pady=(8, 4))
        right.grid_rowconfigure(2, weight=1)
        right.grid_columnconfigure(0, weight=1)

        # ---------- LEFT side ----------
        # 1) Document metadata
        SectionHeader(left, "📄", "Реквізити документа",
                      "Те, що друкується на титульній сторінці").grid(
            row=0, column=0, padx=12, pady=(12, 4), sticky="ew",
        )
        meta_box = ctk.CTkFrame(left)
        meta_box.grid(row=1, column=0, padx=12, pady=4, sticky="ew")
        meta_box.grid_columnconfigure(1, weight=1)
        self._meta_widgets: dict[str, ctk.CTkEntry] = {}
        meta_fields = [
            ("university", "Університет"),
            ("department", "Кафедра"),
            ("discipline", "Дисципліна"),
            ("authors", "Автори (через кому)"),
            ("city", "Місто"),
            ("year", "Рік"),
        ]
        for i, (k, label) in enumerate(meta_fields):
            ctk.CTkLabel(meta_box, text=label, anchor="w",
                         font=ctk.CTkFont(size=12)).grid(
                row=i, column=0, padx=10, pady=5, sticky="w",
            )
            entry = ctk.CTkEntry(meta_box, height=30)
            entry.grid(row=i, column=1, padx=10, pady=5, sticky="ew")
            self._meta_widgets[k] = entry

        # 2) Preset
        SectionHeader(left, "📋", "Пресет документа",
                      "Кожен пресет має власний набір полів").grid(
            row=2, column=0, padx=12, pady=(14, 4), sticky="ew",
        )
        preset_box = ctk.CTkFrame(left)
        preset_box.grid(row=3, column=0, padx=12, pady=4, sticky="ew")
        preset_box.grid_columnconfigure(0, weight=1)
        # Map key -> display name for the dropdown
        self._preset_display_keys = [p.key for p in PRESETS]
        self._preset_display_map = {p.name: p.key for p in PRESETS}
        self._preset_key_to_name = {p.key: p.name for p in PRESETS}
        self._preset_var = ctk.StringVar(value=PRESETS[0].name)
        ctk.CTkLabel(preset_box, text="Оберіть пресет", anchor="w").grid(
            row=0, column=0, padx=10, pady=(8, 2), sticky="w",
        )
        self._preset_menu = ctk.CTkOptionMenu(
            preset_box, variable=self._preset_var,
            values=[p.name for p in PRESETS],
            command=self._on_preset_changed_display, height=32,
        )
        self._preset_menu.grid(row=1, column=0, padx=10, pady=(0, 6), sticky="ew")
        self._preset_desc = ctk.CTkLabel(preset_box, text="", wraplength=380,
                                          justify="left",
                                          text_color=("gray50", "gray70"),
                                          font=ctk.CTkFont(size=11))
        self._preset_desc.grid(row=2, column=0, padx=10, pady=(0, 10), sticky="w")

        # 3) AI settings
        SectionHeader(left, "🤖", "AI-налаштування",
                      "Без API-ключа буде використано вбудований шаблон (mock)").grid(
            row=4, column=0, padx=12, pady=(14, 4), sticky="ew",
        )
        ai_box = ctk.CTkFrame(left)
        ai_box.grid(row=5, column=0, padx=12, pady=4, sticky="ew")
        ai_box.grid_columnconfigure(1, weight=1)
        ai_box.grid_columnconfigure(2, weight=0)

        ctk.CTkLabel(ai_box, text="Стиль викладу", anchor="w").grid(
            row=0, column=0, padx=10, pady=5, sticky="w",
        )
        self._persona_var = ctk.StringVar(value=PERSONAS[0][0])
        ctk.CTkOptionMenu(
            ai_box, variable=self._persona_var,
            values=[p[1] for p in PERSONAS],
            height=30,
        ).grid(row=0, column=1, columnspan=2, padx=10, pady=5, sticky="ew")

        ctk.CTkLabel(ai_box, text="Провайдер", anchor="w").grid(
            row=1, column=0, padx=10, pady=5, sticky="w",
        )
        self._provider_var = ctk.StringVar(value=PROVIDERS[0][0])
        self._provider_menu = ctk.CTkOptionMenu(
            ai_box, variable=self._provider_var,
            values=[p[1] for p in PROVIDERS],
            command=self._on_provider_changed, height=30,
        )
        self._provider_menu.grid(row=1, column=1, padx=(10, 4), pady=5, sticky="ew")

        ctk.CTkLabel(ai_box, text="Модель", anchor="w").grid(
            row=2, column=0, padx=10, pady=5, sticky="w",
        )
        self._model_var = ctk.StringVar(value="(завантаження…)")
        self._model_menu = ctk.CTkOptionMenu(
            ai_box, variable=self._model_var, values=["(завантаження…)"],
            height=30,
        )
        self._model_menu.grid(row=2, column=1, padx=(10, 4), pady=5, sticky="ew")
        self._refresh_models_btn = ctk.CTkButton(
            ai_box, text="↻", width=36, height=30,
            command=self._refresh_models, fg_color="gray40", hover_color="gray30",
        )
        self._refresh_models_btn.grid(row=2, column=2, padx=(0, 10), pady=5)
        self._models_hint = ctk.CTkLabel(
            ai_box, text="", font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray70"), anchor="w",
        )
        self._models_hint.grid(row=3, column=0, columnspan=3, padx=10, pady=(0, 6), sticky="w")

        ctk.CTkLabel(ai_box, text="API-ключ HF", anchor="w").grid(
            row=4, column=0, padx=10, pady=5, sticky="w",
        )
        self._api_entry = ctk.CTkEntry(
            ai_box, placeholder_text="hf_… (залиште порожнім для mock)",
            show="•", height=30,
        )
        self._api_entry.grid(row=4, column=1, padx=(10, 4), pady=5, sticky="ew")
        ctk.CTkButton(
            ai_box, text="Тест", width=56, height=30,
            command=self._test_api_key, fg_color="gray40", hover_color="gray30",
        ).grid(row=4, column=2, padx=(0, 10), pady=5)

        # 4) Document structure: which optional sections to include
        SectionHeader(left, "📑", "Структура документа",
                      "Увімкніть або вимкніть додаткові секції").grid(
            row=6, column=0, padx=12, pady=(14, 4), sticky="ew",
        )
        struct_box = ctk.CTkFrame(left)
        struct_box.grid(row=7, column=0, padx=12, pady=4, sticky="ew")
        struct_box.grid_columnconfigure(1, weight=1)
        struct_box.grid_columnconfigure(2, weight=0)

        self._include_tasks_variants_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            struct_box,
            text="Додати «Завдання» та «Варіанти»",
            variable=self._include_tasks_variants_var,
            command=self._on_tasks_variants_toggle,
        ).grid(row=0, column=0, columnspan=3, padx=10, pady=(10, 4), sticky="w")

        ctk.CTkLabel(struct_box, text="Кількість варіантів", anchor="w").grid(
            row=1, column=0, padx=10, pady=(2, 10), sticky="w",
        )
        self._variant_count_var = ctk.StringVar(value="6")
        self._variant_count_menu = ctk.CTkOptionMenu(
            struct_box, variable=self._variant_count_var,
            values=[str(n) for n in (3, 4, 5, 6, 7, 8, 10, 12, 15)],
            width=80, height=28,
        )
        self._variant_count_menu.grid(
            row=1, column=1, padx=(10, 4), pady=(2, 10), sticky="w",
        )
        ctk.CTkLabel(struct_box, text="(активно лише коли увімкнено «Завдання»/«Варіанти»)",
                     font=ctk.CTkFont(size=11),
                     text_color=("gray50", "gray70"), anchor="w").grid(
            row=1, column=2, padx=(0, 10), pady=(2, 10), sticky="w",
        )

        # 5) Output format checkboxes
        SectionHeader(left, "💾", "Збереження",
                      "Куди і в яких форматах").grid(
            row=8, column=0, padx=12, pady=(14, 4), sticky="ew",
        )
        out_box = ctk.CTkFrame(left)
        out_box.grid(row=9, column=0, padx=12, pady=4, sticky="ew")
        out_box.grid_columnconfigure(0, weight=1)
        out_box.grid_columnconfigure(1, weight=0)
        self._save_pdf_var = ctk.BooleanVar(value=True)
        self._save_docx_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(out_box, text="PDF", variable=self._save_pdf_var).grid(
            row=0, column=0, padx=10, pady=10, sticky="w",
        )
        ctk.CTkCheckBox(out_box, text="DOCX", variable=self._save_docx_var).grid(
            row=0, column=0, padx=80, pady=10, sticky="w",
        )

        # ---------- RIGHT side ----------
        # Preset title bar
        self._preset_title_label = ctk.CTkLabel(
            right, text="",
            font=ctk.CTkFont(size=20, weight="bold"),
            anchor="w",
        )
        self._preset_title_label.grid(row=0, column=0, padx=20, pady=(16, 2), sticky="w")
        self._preset_subtitle_label = ctk.CTkLabel(
            right, text="", wraplength=820, justify="left",
            font=ctk.CTkFont(size=12),
            text_color=("gray50", "gray70"), anchor="w",
        )
        self._preset_subtitle_label.grid(row=1, column=0, padx=20, pady=(0, 8), sticky="w")

        # Form
        self._form_frame = ctk.CTkScrollableFrame(right, label_text="")
        self._form_frame.grid(row=2, column=0, padx=12, pady=4, sticky="nsew")
        self._form_frame.grid_columnconfigure(0, weight=1)

        # Action panel
        action = ctk.CTkFrame(right, fg_color=("gray90", "gray17"), corner_radius=8)
        action.grid(row=3, column=0, padx=12, pady=(8, 4), sticky="ew")
        action.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(action, text="📁  Папка для збереження",
                     font=ctk.CTkFont(weight="bold"), anchor="w").grid(
            row=0, column=0, columnspan=3, padx=14, pady=(10, 2), sticky="w",
        )
        self._save_dir_var = ctk.StringVar(value=self._last_save_dir)
        self._save_dir_entry = ctk.CTkEntry(
            action, textvariable=self._save_dir_var, height=32,
            font=ctk.CTkFont(size=12),
        )
        self._save_dir_entry.grid(row=1, column=0, columnspan=2, padx=(14, 4), pady=(0, 10), sticky="ew")
        ctk.CTkButton(action, text="Огляд…", width=100, height=32,
                      command=self._browse_save_dir).grid(
            row=1, column=2, padx=(0, 14), pady=(0, 10),
        )

        self._generate_btn = ctk.CTkButton(
            action, text="  ⚡  Згенерувати документ  ",
            height=50, font=ctk.CTkFont(size=17, weight="bold"),
            command=self._on_generate_clicked,
        )
        self._generate_btn.grid(row=2, column=0, columnspan=3, padx=14, pady=(0, 12), sticky="ew")

        # Log
        self._log_box = ctk.CTkTextbox(
            right, height=130, wrap="word",
            font=ctk.CTkFont(family="Consolas", size=11),
        )
        self._log_box.grid(row=4, column=0, padx=12, pady=(4, 10), sticky="nsew")
        self._log_box.insert("1.0", "Готово. Оберіть пресет і заповніть поля.\n")
        self._log_box.configure(state="disabled")

        # === Status bar (full width) ===
        self._status_bar = ctk.CTkFrame(self, height=44, corner_radius=0)
        self._status_bar.grid(row=2, column=0, columnspan=2, sticky="ew")
        self._status_bar.grid_columnconfigure(0, weight=1)
        self._status_label = ctk.CTkLabel(
            self._status_bar, text="Готовий до роботи.",
            anchor="w", font=ctk.CTkFont(size=12),
        )
        self._status_label.grid(row=0, column=0, padx=20, pady=10, sticky="ew")
        self._open_folder_btn = ctk.CTkButton(
            self._status_bar, text="📂 Відкрити папку результатів",
            command=self._open_last_output_dir, height=30,
            state="disabled",
        )
        self._open_folder_btn.grid(row=0, column=1, padx=20, pady=6, sticky="e")
        self._progress = ctk.CTkProgressBar(self._status_bar, width=240, height=12)
        self._progress.set(0)
        self._progress.grid(row=0, column=2, padx=(0, 20), pady=16, sticky="e")

    # ----- defaults & state ---------------------------------------------

    def _populate_defaults(self) -> None:
        for k, v in DEFAULT_METADATA.items():
            if k in self._meta_widgets:
                self._meta_widgets[k].insert(0, v)
        os.makedirs(self._last_save_dir, exist_ok=True)
        self._save_dir_var.set(self._last_save_dir)
        # Apply initial state to the variant-count picker
        self._on_tasks_variants_toggle()

    def _on_theme_changed(self, value: str) -> None:
        if "Light" in value:
            ctk.set_appearance_mode("light")
        else:
            ctk.set_appearance_mode("dark")

    def _on_tasks_variants_toggle(self) -> None:
        """Enable / disable the variant-count picker based on the toggle."""
        enabled = self._include_tasks_variants_var.get()
        try:
            if enabled:
                self._variant_count_menu.configure(state="normal", fg_color=None)
            else:
                self._variant_count_menu.configure(state="disabled", fg_color="gray25")
        except Exception:
            pass

    # ----- preset switching ----------------------------------------------

    def _on_preset_changed_display(self, display_name: str) -> None:
        key = self._preset_display_map.get(display_name)
        if key is None:
            return
        self._switch_preset(key)

    def _switch_preset(self, key: str) -> None:
        self._current_preset = get_preset(key)
        # Keep the menu's display value in sync (it was set by name)
        self._preset_title_label.configure(text=self._current_preset.name)
        self._preset_subtitle_label.configure(text=self._current_preset.description)
        self._preset_desc.configure(text=self._current_preset.description)
        # Rebuild form
        for child in self._form_frame.winfo_children():
            child.destroy()
        self._field_widgets.clear()
        for i, fld in enumerate(self._current_preset.fields):
            label_text = fld.label + ("  *" if fld.required else "")
            ctk.CTkLabel(self._form_frame, text=label_text, anchor="w",
                         font=ctk.CTkFont(size=13, weight="bold")).grid(
                row=i * 3, column=0, padx=8, pady=(12, 0), sticky="w",
            )
            if fld.help:
                ctk.CTkLabel(self._form_frame, text=fld.help, wraplength=800,
                             justify="left", text_color=("gray50", "gray70"),
                             font=ctk.CTkFont(size=11)).grid(
                    row=i * 3 + 1, column=0, padx=8, pady=(0, 2), sticky="w",
                )
                widget_row = i * 3 + 2
            else:
                widget_row = i * 3 + 1
            if fld.type == "multiline":
                w = ctk.CTkTextbox(self._form_frame, height=130, wrap="word",
                                   font=ctk.CTkFont(size=12))
                if fld.default:
                    w.insert("1.0", fld.default)
                w.grid(row=widget_row, column=0, padx=8, pady=(0, 6), sticky="ew")
            elif fld.type == "list":
                w = ListFieldWidget(self._form_frame, default=fld.default)
                w.grid(row=widget_row, column=0, padx=8, pady=(0, 6), sticky="ew")
            else:
                w = ctk.CTkEntry(self._form_frame, height=34,
                                 placeholder_text=fld.default or "",
                                 font=ctk.CTkFont(size=12))
                if fld.default:
                    w.insert(0, fld.default)
                w.grid(row=widget_row, column=0, padx=8, pady=(0, 6), sticky="ew")
            self._field_widgets[fld.key] = w

    # ----- model picker --------------------------------------------------

    def _provider_key(self) -> str:
        """Convert the display name back to the provider key."""
        for key, name in PROVIDERS:
            if name == self._provider_var.get():
                return key
        return PROVIDERS[0][0]

    def _on_provider_changed(self, _value: str) -> None:
        self._refresh_models()

    def _refresh_models(self) -> None:
        provider = self._provider_key()
        api_key = self._api_entry.get().strip() or None
        self._models_hint.configure(text="Завантаження списку моделей…")
        thread = threading.Thread(
            target=self._refresh_models_worker, args=(provider, api_key), daemon=True,
        )
        thread.start()

    def _refresh_models_worker(self, provider: str, api_key: str | None) -> None:
        from app.backend.ai.hf_models import (
            list_available_models, get_default_for_provider,
        )
        try:
            data = list_available_models(
                api_key=api_key, provider=provider, limit=30, chat_only=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.after(0, lambda: self._on_models_loaded(
                [], get_default_for_provider(provider), "error", str(exc),
            ))
            return
        ids = [m["id"] for m in data.get("models", [])]
        default = data.get("default") or get_default_for_provider(provider)
        if default and default not in ids:
            ids.insert(0, default)
        self.after(0, lambda: self._on_models_loaded(
            ids, default, data.get("source", "fallback"), data.get("error"),
        ))

    def _on_models_loaded(self, ids: list[str], default: str, source: str, error: str | None) -> None:
        self._model_options = ids or [default or "(немає моделей)"]
        self._model_menu.configure(values=self._model_options)
        if default and default in self._model_options:
            self._model_var.set(default)
        else:
            self._model_var.set(self._model_options[0])
        # Hint text
        if source == "api":
            hint = f"✓ {len(self._model_options)} моделей (live HuggingFace)"
        elif source == "cache":
            hint = f"✓ {len(self._model_options)} моделей (кеш 5 хв)"
        elif source == "fallback":
            if error == "no_token":
                hint = f"⚠ {len(self._model_options)} моделей (fallback — потрібен API-ключ для live-списку)"
            else:
                hint = f"⚠ {len(self._model_options)} моделей (fallback)"
        else:
            hint = f"⚠ {len(self._model_options)} моделей"
        self._models_hint.configure(text=hint)

    def _test_api_key(self) -> None:
        api_key = self._api_entry.get().strip()
        if not api_key:
            messagebox.showinfo("Тест API-ключа", "Введіть API-ключ для перевірки.")
            return
        self._status("Перевіряю API-ключ…")
        thread = threading.Thread(target=self._test_api_key_worker, args=(api_key,), daemon=True)
        thread.start()

    def _test_api_key_worker(self, api_key: str) -> None:
        try:
            from huggingface_hub import HfApi
            who = HfApi(token=api_key).whoami(token=api_key)
            name = who.get("name") or who.get("fullname") or "(невідомо)"
            self.after(0, lambda: self._on_api_key_test(True, f"Токен валідний. Користувач: {name}"))
        except Exception as exc:  # noqa: BLE001
            self.after(0, lambda: self._on_api_key_test(False, str(exc)))

    def _on_api_key_test(self, ok: bool, msg: str) -> None:
        if ok:
            self._status("API-ключ валідний ✓")
            messagebox.showinfo("API-ключ", msg)
            # Refetch models with the new key
            self._refresh_models()
        else:
            self._status("API-ключ невалідний ✗")
            messagebox.showerror("API-ключ", f"Не вдалося перевірити токен:\n{msg}")

    # ----- save location -------------------------------------------------

    def _browse_save_dir(self) -> None:
        path = filedialog.askdirectory(
            title="Оберіть папку для збереження",
            initialdir=self._save_dir_var.get() or DEFAULT_SAVE_DIR,
            mustexist=True,
        )
        if path:
            self._save_dir_var.set(path)
            self._last_save_dir = path

    def _open_last_output_dir(self) -> None:
        path = self._save_dir_var.get().strip()
        if not path or not os.path.isdir(path):
            messagebox.showwarning("Папка недоступна", f"Папка не існує: {path}")
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":  # pragma: no cover
                os.system(f'open "{path}"')
            else:  # pragma: no cover
                os.system(f'xdg-open "{path}"')
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Помилка", f"Не вдалося відкрити папку:\n{exc}")

    # ----- field collection ---------------------------------------------

    def _collect_field_values(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for fld in self._current_preset.fields:
            w = self._field_widgets.get(fld.key)
            if w is None:
                continue
            if fld.type == "list":
                values[fld.key] = "\n".join(w.get_values())
            elif fld.type == "multiline":
                values[fld.key] = w.get("1.0", "end").strip()
            else:
                values[fld.key] = w.get().strip()
            if fld.required and not values[fld.key]:
                raise ValueError(f"Поле «{fld.label}» є обов'язковим.")
        return values

    def _collect_metadata(self) -> DocumentMetadata:
        year_raw = self._meta_widgets["year"].get().strip()
        try:
            year = int(year_raw) if year_raw else datetime.now().year
        except ValueError:
            raise ValueError("Рік має бути цілим числом.")
        authors_raw = self._meta_widgets["authors"].get().strip()
        authors = [a.strip() for a in authors_raw.split(",") if a.strip()]
        if not authors:
            raise ValueError("Потрібен хоча б один автор.")
        return DocumentMetadata(
            university=self._meta_widgets["university"].get().strip(),
            department=self._meta_widgets["department"].get().strip(),
            discipline=self._meta_widgets["discipline"].get().strip(),
            authors=authors,
            city=self._meta_widgets["city"].get().strip(),
            year=year,
        )

    def _collect_request(self) -> tuple[GenerateRequest, dict[str, Any]]:
        meta = self._collect_metadata()
        values = self._collect_field_values()
        values["_persona"] = self._persona_var.get()
        # Document-structure flags
        values["_include_tasks_variants"] = bool(self._include_tasks_variants_var.get())
        try:
            values["_variant_count"] = int(self._variant_count_var.get())
        except (TypeError, ValueError):
            values["_variant_count"] = 6
        return GenerateRequest(
            api_key=self._api_entry.get().strip() or None,
            ai_provider=self._provider_key(),
            ai_model=self._model_var.get().strip() or None,
            output_format="both",
            metadata=meta,
            content_requirements=values.get("topic") or values.get("title") or "",
            persona=self._persona_var.get(),
        ), values

    # ----- generation flow -----------------------------------------------

    def _on_generate_clicked(self) -> None:
        if self._generate_btn.cget("state") == "disabled":
            return
        try:
            req, values = self._collect_request()
        except ValueError as exc:
            messagebox.showwarning("Перевірте поля", str(exc))
            return
        save_dir = self._save_dir_var.get().strip()
        if not save_dir:
            messagebox.showwarning("Перевірте поля", "Оберіть папку для збереження.")
            return
        if not (self._save_pdf_var.get() or self._save_docx_var.get()):
            messagebox.showwarning("Перевірте поля", "Оберіть хоча б один формат (PDF або DOCX).")
            return
        os.makedirs(save_dir, exist_ok=True)

        self._generate_btn.configure(state="disabled", text="  ⏳  Генерую…  ")
        self._progress.set(0.05)
        self._status("Запуск AI-генерації…")
        self._open_folder_btn.configure(state="disabled")
        thread = threading.Thread(
            target=self._generate_worker, args=(req, values, save_dir), daemon=True,
        )
        thread.start()

    def _generate_worker(self, req: GenerateRequest, values: dict[str, Any], save_dir: str) -> None:
        try:
            content, mode = self._generate_content(req, values)
        except Exception as exc:  # noqa: BLE001
            self.after(0, lambda: self._generation_failed(exc))
            return
        try:
            pdf_path, docx_path = self._render_artifacts(req.metadata, content, save_dir)
        except Exception as exc:  # noqa: BLE001
            self.after(0, lambda: self._generation_failed(exc))
            return
        self.after(0, lambda: self._generation_succeeded(content, mode, req.metadata, pdf_path, docx_path))

    def _generate_content(self, req: GenerateRequest, values: dict[str, Any]) -> tuple[LabGuidelinesContent, str]:
        if req.api_key and req.api_key.strip():
            self._log("Надсилаю запит до HuggingFace Inference…")
            from app.backend.ai.hf_service import generate_validated
            provider = req.ai_provider or "cerebras"
            model = req.ai_model or "(default)"
            system_prompt = self._current_preset.build_system_prompt(
                {**values, "_persona": req.persona}, req.metadata.discipline,
            )
            user_prompt = self._current_preset.build_user_prompt(values, req.metadata.discipline)
            content = generate_validated(
                api_key=req.api_key,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=LabGuidelinesContent,
                model=model,
                provider=provider,
            )
            # Defensive post-filter: respect the user's structure toggles.
            content = _apply_structure_filters(content, values)
            self._log(f"AI-відповідь отримано через {provider}/{model}.")
            return content, f"AI ({provider}/{model})"
        self._log("API-ключ не надано — використовую вбудований шаблон (mock).")
        content = self._current_preset.fallback_content(values, req.metadata.discipline)
        content = _apply_structure_filters(content, values)
        return content, "Mock"

    def _render_artifacts(self, meta: DocumentMetadata, content: LabGuidelinesContent, save_dir: str) -> tuple[str, str]:
        pdf_path = ""
        docx_path = ""
        if self._save_pdf_var.get():
            pdf_path = os.path.join(save_dir, _safe_filename(meta.discipline, meta.year, "pdf"))
            self._log(f"Рендерю PDF: {pdf_path}")
            DSTUPdfGenerator().generate(meta, content, pdf_path)
        if self._save_docx_var.get():
            docx_path = os.path.join(save_dir, _safe_filename(meta.discipline, meta.year, "docx"))
            self._log(f"Рендерю DOCX: {docx_path}")
            DSTUDocxGenerator().generate(meta, content, docx_path)
        return pdf_path, docx_path

    # ----- callbacks (run on UI thread) ----------------------------------

    def _generation_succeeded(self, _content, mode, _meta, pdf_path, docx_path) -> None:
        self._progress.set(1.0)
        paths: list[str] = []
        if pdf_path and os.path.exists(pdf_path):
            paths.append(f"PDF:  {pdf_path}  ({os.path.getsize(pdf_path)/1024:.1f} KB)")
        if docx_path and os.path.exists(docx_path):
            paths.append(f"DOCX: {docx_path}  ({os.path.getsize(docx_path)/1024:.1f} KB)")
        if paths:
            self._log("\n".join(paths))
        self._status(f"Готово. Режим: {mode}. Файлів: {len(paths)}.")
        self._open_folder_btn.configure(state="normal")
        if paths:
            messagebox.showinfo("Готово", "Документ(и) збережено:\n\n" + "\n".join(paths))
        self._reset_actions()

    def _generation_failed(self, exc: Exception) -> None:
        self._progress.set(0)
        self._status("Помилка генерації.")
        self._log(f"ПОМИЛКА: {exc}")
        messagebox.showerror("Помилка генерації", str(exc))
        self._reset_actions()

    def _reset_actions(self) -> None:
        self._generate_btn.configure(state="normal", text="  ⚡  Згенерувати документ  ")

    # ----- small UI helpers ----------------------------------------------

    def _status(self, text: str) -> None:
        self._status_label.configure(text=text)

    def _log(self, text: str) -> None:
        self._log_box.configure(state="normal")
        self._log_box.insert("end", text + "\n")
        self._log_box.see("end")
        self._log_box.configure(state="disabled")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> int:
    app = LabApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
