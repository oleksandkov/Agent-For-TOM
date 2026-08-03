"""
Text primitives for the learning system.

Deliberately owned by this package rather than imported from self_improve.py:
the keyword taxonomy in that module is being deleted, and retrieval must not
go down with it. Same behaviour, no dependency.
"""

from __future__ import annotations

import re

_STOP_WORDS_EN = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "as", "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "again", "further", "then",
    "once", "here", "there", "when", "where", "why", "how", "all", "each",
    "every", "both", "few", "more", "most", "other", "some", "such", "no",
    "nor", "not", "only", "own", "same", "so", "than", "too", "very",
    "just", "because", "but", "and", "or", "if", "while", "that", "this",
    "these", "those", "it", "its", "what", "which", "who", "whom",
    "about", "up", "down", "i", "you", "my", "your", "me",
}

# Ukrainian and Russian function words. Without these, making the tokeniser
# Unicode-aware would be a net loss: "що", "це", "как", "это" are the most
# frequent tokens in any Cyrillic message and would dominate every keyword
# set. The two changes only make sense together.
_STOP_WORDS_UK = {
    "і", "й", "та", "а", "але", "або", "чи", "що", "щоб", "як", "які", "який",
    "яка", "яке", "це", "цей", "ця", "то", "той", "так", "там", "тут", "де",
    "коли", "тому", "бо", "для", "від", "до", "на", "у", "в", "з", "із", "зі",
    "за", "по", "про", "при", "над", "під", "перед", "після", "між", "через",
    "без", "не", "ні", "ще", "вже", "тільки", "лише", "дуже", "más", "теж",
    "також", "весь", "вся", "все", "всі", "мій", "моя", "моє", "мої", "твій",
    "наш", "ваш", "його", "її", "їх", "хто", "кого", "кому", "чий", "скільки",
    "є", "був", "була", "було", "були", "буде", "будуть", "бути", "маю",
    "має", "мати", "можна", "треба", "потрібно", "можу", "може", "можуть",
    "я", "ти", "ви", "ми", "він", "вона", "воно", "вони", "себе", "собі",
    "би", "б", "же", "ж", "ось", "он", "оце", "нехай", "хай", "теж",
}
_STOP_WORDS_RU = {
    "и", "а", "но", "или", "либо", "что", "чтобы", "как", "какой", "какая",
    "какое", "какие", "это", "этот", "эта", "эти", "то", "тот", "так", "там",
    "тут", "здесь", "где", "когда", "потому", "поэтому", "для", "от", "до",
    "на", "в", "во", "с", "со", "за", "по", "про", "при", "над", "под",
    "перед", "после", "между", "через", "без", "не", "ни", "еще", "ещё",
    "уже", "только", "лишь", "очень", "также", "тоже", "весь", "вся", "все",
    "всё", "мой", "моя", "моё", "мои", "твой", "наш", "ваш", "его", "её",
    "их", "кто", "кого", "кому", "чей", "сколько", "есть", "был", "была",
    "было", "были", "будет", "будут", "быть", "имеет", "можно", "нужно",
    "надо", "могу", "может", "могут", "я", "ты", "вы", "мы", "он", "она",
    "оно", "они", "себя", "себе", "бы", "же", "ли", "вот", "нет", "да",
}

STOP_WORDS = _STOP_WORDS_EN | _STOP_WORDS_UK | _STOP_WORDS_RU

# Any alphabetic character in any script, then word characters.
# `[^\W\d_]` reads as "a word character that is not a digit or underscore",
# which under Unicode semantics (the default for str patterns) covers
# Cyrillic, Greek and accented Latin. The previous `[a-zA-Z]` silently
# dropped every non-Latin word, so a Ukrainian message tokenised to [] and
# retrieval, tool selection and similarity all quietly stopped working.
_WORD_RE = re.compile(r"[^\W\d_][\w\-']*")

# Cyrillic → English for the technical vocabulary that decides tool
# selection. A correct tokeniser is necessary but not sufficient: tool names
# and descriptions are English, so "зроби скріншот браузера" tokenises fine
# and still overlaps nothing in `take_screenshot — screenshot the browser`.
# These are added *alongside* the original words, never in place of them, so
# nothing is lost and a Ukrainian query can match an English tool.
TERM_ALIASES = {
    # files and paths
    "файл": "file", "файла": "file", "файлу": "file", "файли": "file",
    "файлы": "file", "файлов": "file", "тека": "directory",
    "папка": "directory", "папку": "directory", "каталог": "directory",
    "директорія": "directory", "директория": "directory",
    "шлях": "path", "путь": "path", "текст": "text",
    "рядок": "line", "строка": "line", "рядки": "line", "строки": "line",
    # actions
    "прочитай": "read", "читати": "read", "прочитать": "read",
    "читання": "read", "чтение": "read",
    "запиши": "write", "записати": "write", "написать": "write",
    "створи": "create", "створити": "create", "создай": "create",
    "створення": "create", "создать": "create",
    "видали": "delete", "видалити": "delete", "удали": "delete",
    "удалить": "delete", "видалення": "delete", "удаление": "delete",
    "зміни": "edit", "змінити": "edit", "измени": "edit", "изменить": "edit",
    "редагувати": "edit", "редактировать": "edit", "правка": "edit",
    "знайди": "search", "знайти": "search", "найди": "search",
    "найти": "search", "пошук": "search", "поиск": "search",
    "шукати": "search", "искать": "search",
    "запусти": "run", "запустити": "run", "запустить": "run",
    "виконай": "run", "виконати": "run", "выполни": "run",
    "выполнить": "run", "команда": "command", "команду": "command",
    "покажи": "list", "показати": "list", "показать": "list",
    "список": "list", "перелік": "list",
    # web and browser
    "браузер": "browser", "браузера": "browser", "браузері": "browser",
    "браузере": "browser", "сторінка": "page", "страница": "page",
    "сторінку": "page", "страницу": "page", "сайт": "site",
    "скріншот": "screenshot", "скриншот": "screenshot",
    "знімок": "screenshot", "снимок": "screenshot",
    "мережа": "network", "сеть": "network", "посилання": "url",
    "ссылка": "url", "інтернет": "web", "интернет": "web",
    # data
    "база": "database", "бази": "database", "базі": "database",
    "базы": "database", "базе": "database", "запит": "query",
    "запрос": "query", "таблиця": "table", "таблица": "table",
    "дані": "data", "данные": "data",
    # dev
    "код": "code", "коду": "code", "коді": "code", "кода": "code",
    "функція": "function", "функцію": "function", "функция": "function",
    "функцию": "function", "клас": "class", "класс": "class",
    "модуль": "module", "тест": "test", "тести": "test", "тесты": "test",
    "тестування": "test", "тестирование": "test",
    "помилка": "error", "помилку": "error", "помилки": "error",
    "ошибка": "error", "ошибку": "error", "ошибки": "error",
    "виправ": "fix", "виправити": "fix", "исправь": "fix",
    "исправить": "fix", "налагодження": "debug", "отладка": "debug",
    "пам'ять": "memory", "память": "memory", "пам'яті": "memory",
    "сесія": "session", "сессия": "session", "сесію": "session",
    "проєкт": "project", "проект": "project", "репозиторій": "repository",
    "репозиторий": "repository", "гілка": "branch", "ветка": "branch",
    "коміт": "commit", "коммит": "commit", "звіт": "report",
    "отчёт": "report", "отчет": "report", "документація": "documentation",
    "документация": "documentation", "налаштування": "settings",
    "настройки": "settings", "конфігурація": "config",
    "конфігурації": "config", "конфигурация": "config",
    "конфигурации": "config",
}


def expand_aliases(words: list[str]) -> list[str]:
    """Add English equivalents for known Cyrillic technical terms."""
    extra = []
    for w in words:
        alias = TERM_ALIASES.get(w)
        if alias and alias not in words:
            extra.append(alias)
    return words + extra


def extract_keywords(text: str, max_keywords: int = 12,
                     aliases: bool = True) -> list[str]:
    """Frequency-ranked keywords, stop words and code blocks removed.

    With `aliases`, English equivalents of known Cyrillic technical terms are
    appended, so a Ukrainian request can match English tool descriptions and
    English learned facts. Pass `aliases=False` for pure tokenisation.
    """
    text = (text or "").lower()
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"https?://\S+", "", text)
    words = _WORD_RE.findall(text)
    words = [w for w in words if w not in STOP_WORDS and len(w) > 2]
    freq: dict[str, int] = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    ranked = [w for w, _ in sorted(freq.items(), key=lambda x: -x[1])[:max_keywords]]
    if aliases:
        # Aliases ride along after the ranked words so they never displace a
        # word the user actually typed.
        for extra in expand_aliases(ranked)[len(ranked):]:
            if extra not in ranked:
                ranked.append(extra)
    return ranked


def similarity(a: str, b: str) -> float:
    """Jaccard overlap of the two texts' keyword sets, 0.0-1.0."""
    ka = set(extract_keywords(a, max_keywords=15))
    kb = set(extract_keywords(b, max_keywords=15))
    if not ka or not kb:
        return 0.0
    return len(ka & kb) / len(ka | kb)
