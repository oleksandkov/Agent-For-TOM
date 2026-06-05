// State Machine for Application Form
let currentStep = 1;
const apiBase = "";

document.addEventListener("DOMContentLoaded", () => {
    loadProfile();
    setupAutosave();
    setupKeyBadge();
    setupFormatBadge();
    updateGenerateButtonLabel();
    setupModelPicker();
});

// Load Profile metadata
async function loadProfile() {
    try {
        const res = await fetch(`${apiBase}/api/profile`);
        if (res.ok) {
            const data = await res.json();
            if (data.university) document.getElementById("university").value = data.university;
            if (data.department) document.getElementById("department").value = data.department;
            if (data.discipline) document.getElementById("discipline").value = data.discipline;
            if (data.authors) document.getElementById("authors").value = data.authors.join(", ");
            if (data.city) document.getElementById("city").value = data.city;
            if (data.year) document.getElementById("year").value = data.year;
            if (data.api_key) {
                document.getElementById("api_key").value = data.api_key;
                setupKeyBadge();
            }
            if (data.output_format) {
                const radio = document.querySelector(`input[name="output_format"][value="${data.output_format}"]`);
                if (radio) {
                    radio.checked = true;
                    updateGenerateButtonLabel();
                }
            }
            if (data.ai_provider) {
                const prov = document.getElementById("ai_provider");
                if (prov) prov.value = data.ai_provider;
            }
            if (data.ai_model) {
                window.__pending_model = data.ai_model;
            }
        }
    } catch (e) {
        showToast("Не вдалося завантажити локальний профіль", "error");
    }
}

// Setup Event listeners to auto-save profile details
function setupAutosave() {
    const fields = ["university", "department", "discipline", "authors", "city", "year", "api_key", "ai_provider"];
    fields.forEach(f => {
        const el = document.getElementById(f);
        if (el) el.addEventListener("change", saveProfile);
    });
    document.getElementById("api_key").addEventListener("input", setupKeyBadge);
    document.getElementById("ai_model").addEventListener("change", saveProfile);
    document.querySelectorAll('input[name="output_format"]').forEach(el => {
        el.addEventListener("change", () => {
            saveProfile();
            updateGenerateButtonLabel();
        });
    });
}

// ---------------------------------------------------------------------------
// Model picker: fetch the available chat models for a HuggingFace provider
// and populate the <select id="ai_model">.
// ---------------------------------------------------------------------------

let _modelFetchTimer = null;

function setupModelPicker() {
    const apiKeyEl = document.getElementById("api_key");
    const provEl = document.getElementById("ai_provider");
    const refreshBtn = document.getElementById("btn-refresh-models");

    if (refreshBtn) refreshBtn.addEventListener("click", () => fetchModels(true));
    if (provEl) provEl.addEventListener("change", () => fetchModels(false));
    if (apiKeyEl) {
        apiKeyEl.addEventListener("input", () => {
            clearTimeout(_modelFetchTimer);
            _modelFetchTimer = setTimeout(() => fetchModels(false), 700);
        });
    }

    // Initial fetch (will use cached or fallback for cerebras)
    fetchModels(false);
}

async function fetchModels(forceRefresh) {
    const provEl = document.getElementById("ai_provider");
    const apiKeyEl = document.getElementById("api_key");
    const modelEl = document.getElementById("ai_model");
    const statusEl = document.getElementById("model-list-status");
    const refreshBtn = document.getElementById("btn-refresh-models");

    const provider = provEl.value;
    const apiKey = apiKeyEl.value.trim();

    if (refreshBtn) {
        refreshBtn.classList.add("spinning");
        refreshBtn.disabled = true;
    }
    if (statusEl) statusEl.innerText = "Завантаження списку моделей…";

    const params = new URLSearchParams({ provider, limit: "30", chat_only: "true" });
    if (apiKey) params.set("api_key", apiKey);
    if (forceRefresh) params.set("refresh", "true");

    let data = null;
    try {
        const res = await fetch(`${apiBase}/api/models?${params.toString()}`);
        data = await res.json();
    } catch (e) {
        data = { status: "error", error: e.message };
    } finally {
        if (refreshBtn) {
            refreshBtn.classList.remove("spinning");
            refreshBtn.disabled = false;
        }
    }

    if (!data || data.status !== "success") {
        if (statusEl) statusEl.innerText = "⚠️ Не вдалося отримати список моделей.";
        if (modelEl) {
            modelEl.innerHTML = '<option value="">— помилка завантаження —</option>';
            modelEl.disabled = true;
        }
        return;
    }

    const models = data.models || [];
    const preferred = window.__pending_model;
    window.__pending_model = null;

    // Populate the <select>
    if (modelEl) {
        modelEl.innerHTML = "";
        if (models.length === 0) {
            const opt = document.createElement("option");
            opt.value = "";
            opt.innerText = "— немає доступних моделей —";
            modelEl.appendChild(opt);
            modelEl.disabled = true;
        } else {
            models.forEach(m => {
                const opt = document.createElement("option");
                opt.value = m.id;
                const note = m.note ? ` — ${m.note}` : "";
                const dl = m.downloads ? ` (${formatNumber(m.downloads)} ⬇)` : "";
                opt.innerText = `${m.id}${dl}${note}`;
                if (m.is_default) opt.innerText = `⭐ ${opt.innerText}`;
                modelEl.appendChild(opt);
            });
            modelEl.disabled = false;
        }

        // Restore previously saved model if it still exists
        const target = preferred || data.default;
        if (target && models.some(m => m.id === target)) {
            modelEl.value = target;
        } else if (data.default && models.some(m => m.id === data.default)) {
            modelEl.value = data.default;
        }
    }

    if (statusEl) {
        const sourceLabel = data.source === "api"
            ? "актуальний список HuggingFace"
            : data.source === "fallback"
                ? "резервний список (API недоступний)"
                : "кешований список";
        statusEl.innerText = `✅ Знайдено ${models.length} моделей · ${sourceLabel}`;
    }
}

function formatNumber(n) {
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
    if (n >= 1_000) return (n / 1_000).toFixed(1) + "K";
    return String(n);
}

// Update UI Badge depending on API Key presence
function setupKeyBadge() {
    const apiKey = document.getElementById("api_key").value.trim();
    const badge = document.getElementById("mode-badge");
    if (apiKey) {
        badge.innerText = "🚀 AI Mode (HuggingFace)";
        badge.classList.add("active-ai");
    } else {
        badge.innerText = "🤖 Mock Mode (Без API-ключа)";
        badge.classList.remove("active-ai");
    }
}

// Update the main generate button label based on the selected output format
function updateGenerateButtonLabel() {
    const fmt = getSelectedOutputFormat();
    const map = {
        pdf: "ЗГЕНЕРУВАТИ PDF",
        docx: "ЗГЕНЕРУВАТИ DOCX",
        both: "ЗГЕНЕРУВАТИ PDF + DOCX",
    };
    const el = document.getElementById("btn-generate-text");
    if (el) el.innerText = map[fmt] || "ЗГЕНЕРУВАТИ ДОКУМЕНТ";
}

// Mirror the radio selection into the step-3 summary card
function setupFormatBadge() {
    document.querySelectorAll('input[name="output_format"]').forEach(el => {
        el.addEventListener("change", updateFormatSummary);
    });
    updateFormatSummary();
}

function updateFormatSummary() {
    const fmt = getSelectedOutputFormat();
    const map = {
        pdf: "📕 PDF",
        docx: "📄 DOCX",
        both: "📄📕 PDF + DOCX",
    };
    const sum = document.getElementById("sum-format");
    if (sum) {
        sum.innerText = map[fmt] || fmt;
        sum.style.color = "#a5b4fc";
    }
}

function getSelectedOutputFormat() {
    const checked = document.querySelector('input[name="output_format"]:checked');
    return checked ? checked.value : "both";
}

// Save Profile metadata
async function saveProfile() {
    const authorsStr = document.getElementById("authors").value;
    const authorsList = authorsStr ? authorsStr.split(",").map(a => a.trim()).filter(a => a.length > 0) : [];

    const profile = {
        university: document.getElementById("university").value.trim(),
        department: document.getElementById("department").value.trim(),
        discipline: document.getElementById("discipline").value.trim(),
        authors: authorsList,
        city: document.getElementById("city").value.trim(),
        year: parseInt(document.getElementById("year").value) || 2026,
        api_key: document.getElementById("api_key").value.trim(),
        output_format: getSelectedOutputFormat(),
        ai_provider: document.getElementById("ai_provider").value,
        ai_model: document.getElementById("ai_model").value,
    };

    try {
        await fetch(`${apiBase}/api/profile`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(profile)
        });
    } catch (e) {
        console.error("Profile save failed:", e);
    }
}

// Navigation between steps
function goToStep(step) {
    if (step > currentStep) {
        if (currentStep === 1) {
            const reqs = ["university", "department", "discipline", "authors", "city", "year"];
            let valid = true;
            reqs.forEach(id => {
                const el = document.getElementById(id);
                if (!el.value || el.value.trim() === "") {
                    el.style.borderColor = "var(--danger)";
                    valid = false;
                } else {
                    el.style.borderColor = "var(--bg-card-border)";
                }
            });
            if (!valid) {
                showToast("Будь ласка, заповніть обов'язкові реквізити профілю", "error");
                return;
            }
        }
        if (currentStep === 2) {
            const reqText = document.getElementById("content_requirements").value.trim();
            if (!reqText) {
                document.getElementById("content_requirements").style.borderColor = "var(--danger)";
                showToast("Вкажіть вимоги до контенту або теми лабораторних робіт", "error");
                return;
            } else {
                document.getElementById("content_requirements").style.borderColor = "var(--bg-card-border)";
            }
        }
    }

    saveProfile();

    document.querySelectorAll(".wizard-step").forEach(el => el.classList.remove("active"));
    document.getElementById(`step-${step}-content`).classList.add("active");

    document.querySelectorAll(".step-indicator").forEach(el => el.classList.remove("active"));
    document.querySelector(`.step-indicator[data-step="${step}"]`).classList.add("active");

    currentStep = step;

    if (step === 3) {
        document.getElementById("sum-discipline").innerText = document.getElementById("discipline").value.trim();
        document.getElementById("sum-authors").innerText = document.getElementById("authors").value.trim();
        document.getElementById("sum-university").innerText = document.getElementById("university").value.trim();
        updateFormatSummary();

        const apiKey = document.getElementById("api_key").value.trim();
        const provider = document.getElementById("ai_provider").value;
        const model = document.getElementById("ai_model").value || "—";
        const modeVal = document.getElementById("sum-mode");
        if (apiKey) {
            const provLabel = { cerebras: "Cerebras", novita: "Novita", together: "Together", "hf-inference": "HF Inference" }[provider] || provider;
            modeVal.innerText = `Генерація AI · ${provLabel} · ${model}`;
            modeVal.style.color = "#6ee7b7";
        } else {
            modeVal.innerText = "Імітація (Mock Mode)";
            modeVal.style.color = "#a5b4fc";
        }
    }
}

// Start Document Generation
async function startGeneration() {
    const btn = document.getElementById("btn-generate");
    const spinner = document.getElementById("btn-spinner");
    const consoleBox = document.getElementById("console-box");
    const downloadCard = document.getElementById("download-card");
    const stepNav = document.getElementById("step-3-navigation");

    downloadCard.style.display = "none";
    consoleBox.innerHTML = "";
    btn.disabled = true;
    spinner.style.display = "block";
    stepNav.style.opacity = 0.5;
    stepNav.style.pointerEvents = "none";

    appendConsole("Ініціалізація запиту...", "info");

    const authorsStr = document.getElementById("authors").value;
    const authorsList = authorsStr ? authorsStr.split(",").map(a => a.trim()).filter(a => a.length > 0) : [];
    const apiKey = document.getElementById("api_key").value.trim();
    const outputFormat = getSelectedOutputFormat();

    const payload = {
        api_key: apiKey || null,
        output_format: outputFormat,
        ai_provider: document.getElementById("ai_provider").value || null,
        ai_model: document.getElementById("ai_model").value || null,
        metadata: {
            university: document.getElementById("university").value.trim(),
            department: document.getElementById("department").value.trim(),
            discipline: document.getElementById("discipline").value.trim(),
            authors: authorsList,
            city: document.getElementById("city").value.trim(),
            year: parseInt(document.getElementById("year").value) || 2026
        },
        content_requirements: document.getElementById("content_requirements").value.trim(),
        persona: document.getElementById("persona").value
    };

    if (apiKey) {
        appendConsole("Зв'язок із HuggingFace Inference API... (це може зайняти до 60 секунд)", "info");
    } else {
        appendConsole("Запущено імітаційний режим (без API ключа). Синтез тестових даних...", "info");
    }
    appendConsole(`Обраний формат: ${formatLabel(outputFormat)}`, "info");

    try {
        const res = await fetch(`${apiBase}/api/generate`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });

        const data = await res.json();

        if (res.ok && data.status === "success") {
            appendConsole("AI-генерацію контенту завершено успішно!", "success");
            appendConsole(`Збірка документів: ${data.artifacts.map(a => a.format.toUpperCase()).join(", ")}...`, "info");
            appendConsole("Застосування стилістичних шаблонів ДСТУ 3008:2015...", "info");

            data.artifacts.forEach(a => {
                appendConsole(`Створено файл: ${a.filename}`, "success");
            });

            renderDownloadCard(data);
            downloadCard.style.display = "flex";
            showToast("Документ успішно згенеровано!", "success");
        } else {
            const errMsg = data.detail || "Невідома помилка під час генерації";
            appendConsole(`Помилка: ${errMsg}`, "error");
            showToast(errMsg, "error");
        }
    } catch (e) {
        appendConsole(`Мережева помилка: ${e.message}`, "error");
        showToast("Помилка підключення до сервера", "error");
    } finally {
        btn.disabled = false;
        spinner.style.display = "none";
        stepNav.style.opacity = 1;
        stepNav.style.pointerEvents = "auto";
    }
}

function formatLabel(fmt) {
    return { pdf: "📕 PDF", docx: "📄 DOCX", both: "📄📕 PDF + DOCX" }[fmt] || fmt;
}

function renderDownloadCard(data) {
    const artifacts = data.artifacts && data.artifacts.length ? data.artifacts : [data];
    const providerLabel = {
        cerebras: "Cerebras",
        novita: "Novita",
        together: "Together",
        "hf-inference": "HF Inference",
        gemini: "Gemini",
        huggingface: "HuggingFace",
        mock: "Mock",
    }[data.provider] || data.provider || "HuggingFace";
    const selectedModel = document.getElementById("ai_model").value;
    const modelShort = selectedModel ? selectedModel.split("/").pop() : "";
    const modeLabel = data.mode === "AI"
        ? `AI Generated · ${providerLabel}${modelShort ? " · " + modelShort : ""}`
        : "Mock Generated";

    document.getElementById("download-filename").innerText = artifacts.map(a => a.filename).join("\n");
    document.getElementById("download-mode-badge").innerText = modeLabel;

    const actions = document.getElementById("download-actions");
    actions.innerHTML = "";
    artifacts.forEach((a, idx) => {
        const link = document.createElement("a");
        link.href = `${apiBase}${a.download_url}`;
        link.className = idx === 0 ? "btn btn-download" : "btn btn-secondary";
        link.innerText = a.format.toUpperCase();
        link.setAttribute("download", a.filename);
        actions.appendChild(link);
    });
}

// Console output helpers
function appendConsole(text, type = "info") {
    const consoleBox = document.getElementById("console-box");
    const line = document.createElement("div");
    line.className = "console-line";

    if (type === "success") {
        line.style.color = "#34d399";
        line.innerText = `[OK] ${text}`;
    } else if (type === "error") {
        line.style.color = "#f87171";
        line.innerText = `[ПОМИЛКА] ${text}`;
    } else {
        line.style.color = "#93c5fd";
        line.innerText = `[ІНФО] ${text}`;
    }

    consoleBox.appendChild(line);
    consoleBox.scrollTop = consoleBox.scrollHeight;
}

// Toast notification helper
function showToast(message, type = "info") {
    const container = document.getElementById("toast-container");
    const toast = document.createElement("div");
    toast.className = `toast ${type === "error" ? "error" : ""}`;

    const textSpan = document.createElement("span");
    textSpan.innerText = message;

    const closeBtn = document.createElement("button");
    closeBtn.className = "toast-close";
    closeBtn.innerText = "×";
    closeBtn.onclick = () => toast.remove();

    toast.appendChild(textSpan);
    toast.appendChild(closeBtn);
    container.appendChild(toast);

    setTimeout(() => {
        if (toast.parentNode) {
            toast.style.animation = "slideIn 0.3s reverse forwards";
            setTimeout(() => toast.remove(), 300);
        }
    }, 4000);
}
