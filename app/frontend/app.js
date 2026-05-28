// State Machine for Application Form
let currentStep = 1;
const apiBase = "";

document.addEventListener("DOMContentLoaded", () => {
    loadProfile();
    setupAutosave();
    setupKeyBadge();
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
        }
    } catch (e) {
        showToast("Не вдалося завантажити локальний профіль", "error");
    }
}

// Setup Event listeners to auto-save profile details
function setupAutosave() {
    const fields = ["university", "department", "discipline", "authors", "city", "year", "api_key"];
    fields.forEach(f => {
        document.getElementById(f).addEventListener("change", saveProfile);
    });
    document.getElementById("api_key").addEventListener("input", setupKeyBadge);
}

// Update UI Badge depending on API Key presence
function setupKeyBadge() {
    const apiKey = document.getElementById("api_key").value.strip ? document.getElementById("api_key").value.trim() : document.getElementById("api_key").value;
    const badge = document.getElementById("mode-badge");
    if (apiKey) {
        badge.innerText = "🚀 AI Mode (Gemini API)";
        badge.classList.add("active-ai");
    } else {
        badge.innerText = "🤖 Mock Mode (Без API-ключа)";
        badge.classList.remove("active-ai");
    }
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
        api_key: document.getElementById("api_key").value.trim()
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
    // Basic Form validation
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

    // Save profile metadata
    saveProfile();

    // Toggle Content Views
    document.querySelectorAll(".wizard-step").forEach(el => el.classList.remove("active"));
    document.getElementById(`step-${step}-content`).classList.add("active");

    // Toggle Sidebar Indicators
    document.querySelectorAll(".step-indicator").forEach(el => el.classList.remove("active"));
    document.querySelector(`.step-indicator[data-step="${step}"]`).classList.add("active");

    currentStep = step;

    // Populate review details in Step 3
    if (step === 3) {
        document.getElementById("sum-discipline").innerText = document.getElementById("discipline").value.trim();
        document.getElementById("sum-authors").innerText = document.getElementById("authors").value.trim();
        document.getElementById("sum-university").innerText = document.getElementById("university").value.trim();
        
        const apiKey = document.getElementById("api_key").value.trim();
        const modeVal = document.getElementById("sum-mode");
        if (apiKey) {
            modeVal.innerText = "Генерація AI (Gemini 2.5 Flash)";
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
    
    // Reset UI state
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

    const payload = {
        api_key: apiKey || null,
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
        appendConsole("Зв'язок із сервісом Gemini API... (це може зайняти до 15 секунд)", "info");
    } else {
        appendConsole("Запущено імітаційний режим (без API ключа). Синтез тестових даних...", "info");
    }

    try {
        const res = await fetch(`${apiBase}/api/generate`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });

        const data = await res.json();
        
        if (res.ok && data.status === "success") {
            appendConsole("AI-генерацію контенту завершено успішно!", "success");
            appendConsole("Локальний генератор почав збірку DOCX документа...", "info");
            appendConsole("Застосування стилістичних шаблонів ДСТУ 3008:2015...", "info");
            appendConsole(`Створено файл: ${data.filename}`, "success");
            
            // Set up download card
            document.getElementById("download-filename").innerText = data.filename;
            document.getElementById("download-mode-badge").innerText = data.mode === "AI" ? "AI Generated" : "Mock Generated";
            
            const dlLink = document.getElementById("download-link");
            dlLink.href = `${apiBase}${data.download_url}`;
            
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
