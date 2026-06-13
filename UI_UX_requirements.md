# UI/UX Requirements for Backend Integration

> **Purpose:** Document all frontend changes required to integrate with the new backend architecture.  
> **Dependency:** Must be read alongside `Plan/backendPlan/10_integration_with_bridge.md`.

---

## 1. Session Management

### SessionsScreen.qml
- **Replace:** `MOCK_SESSIONS` array with live query from `AppBridge.getRecentSessions()`.
- **Add:** Status badges for `draft`, `processing`, `completed`, `failed`, `cancelled`.
- **Add:** Context menu per session: Restore, Duplicate, Delete (calls `AppBridge` methods).
- **Add:** Search/filter by name/theme.

---

## 2. New Session Form

### NewDocumentScreen.qml
- **On Generate click:**
  1. Call `AppBridge.createSession(template_id, name, params)` → returns `session_id`.
  2. Call `AppBridge.startPipeline(session_id)` → starts background pipeline thread.
  3. Navigate to ProgressScreen.
- **Handle:** File upload → `AppBridge.uploadFile(path)` → returns `file_id` (UUID).

---

## 3. Pipeline Progress

### ProgressScreen.qml
- **Live updates:** Listen to `AppBridge.pipelineProgress(session_id)` signal.
- **Stages to show (in order):**
  1. File Convert
  2. Compact (local Qwen)
  3. Text Model (HF Llama)
  4. Validate
  5. Images (if enabled)
  6. Execute
  7. Compose
- **Add:** Cancel button → calls `AppBridge.cancelSession(session_id)`.

---

## 4. Result Screen

### ResultScreen.qml
- **Show on completion:**
  - Token usage (prompt / output / cached)
  - Image count
  - Duration
- **Add buttons:**
  - Download DOCX
  - Download PDF
  - View filled.py (syntax-highlighted)
- **If failed:** Show `error_stage` + `error_message` from `sessions` table.

---

## 5. Settings

### SettingsScreen.qml
- **Add fields:**
  - HuggingFace Token (password field, stored in `secrets` table)
  - User Style (multiline Markdown editor, saves to `user_style` table)
  - Cache TTL (LLM / Image / Document days)
  - Session Retention Days
- **Add:** "Save" button → calls `AppBridge.saveSettings(settings_dict)`.

---

## 6. Instructions Manager

### InstructionsScreen.qml
- **Replace:** Static list with `AppBridge.listInstructions(template_id)`.
- **Add:** Toggle for `is_active`.
- **Add:** Edit button → opens editor, saves via `AppBridge.updateInstruction()`.
- **Global instructions** — pinned at top, non-deletable, always shown.

---

## 7. Custom Template Builder

### TemplateCreatorScreen.qml
- **Workflow:**
  1. Upload PDF/DOCX → `AppBridge.uploadTemplateFile(path)`.
  2. Show converted text in left panel.
  3. Click-drag selection boxes for regions.
  4. Assign annotation type (A/B/C/D) to each selection.
  5. "Save Template" → `AppBridge.createCustomTemplate(selections, name)`.
- **Note:** This is a **new screen** — not in current UI.

---

## 8. Bridge API Contract (what frontend calls)

| Method | Purpose | Signal |
|---|---|---|
| `createSession(template_id, name, params)` | Create session row | `sessionCreated(session_id)` |
| `startPipeline(session_id)` | Begin background pipeline | `pipelineProgress(stage, percent)` |
| `cancelSession(session_id)` | Cancel processing | `sessionCancelled(session_id)` |
| `getRecentSessions()` | List sessions | — |
| `uploadFile(path)` | Upload attached file | `fileUploaded(file_id)` |
| `listInstructions(template_id)` | List instructions | — |
| `listTemplates()` | List templates | — |
| `saveSettings(dict)` | Save settings | — |

---

## 9. Local model indicator

If Qwen is loading (Step 2), show a **toast notification**:
- "Loading local model Qwen 2.5-3B... (one-time, ~10s)"
- Show memory usage if available.

---

## 10. Error handling

- **Network error (HF timeout):** Retry 3×, then show "Check HuggingFace token".
- **LLM syntax error:** Show "AI response invalid, try again" with raw text in expandable panel.
- **Python execution error:** Show `filled.py` traceback in modal.

---

## 11. Non-breaking changes

The following existing UI behavior remains unchanged:
- Dark/light theme toggle
- Sidebar navigation
- Language switch (UA/EN)
- About dialog