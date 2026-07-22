# TOMAS Repository Features

## Multi-Provider Configuration System (providers.json)
- **File**: `providers.json` in project root, managed by `agent_cli.py`
- **Functions**: `_load_providers_config()`, `_save_providers_config()`, `_save_provider_config()`, `_activate_provider()`
- **Provider types**: openrouter, anthropic, openai, google, zen, custom
- **Zen**: type="zen" — activates by starting the proxy daemon and setting env vars
- **Regular**: stores env vars dict, restores them on activation
- **Active tracking**: `config["active"]` keeps the currently active provider name

## Model Search/Filter
- Added `_show_filtered_model_menu()` function
- "🔍 Search models by name" option at top of model picker
- Prompts for search text, filters both display and value fields (case-insensitive)
- Shows filtered results in a sub-menu

## Main Menu
- Added "Switch active provider" menu item (index 3)
- Header shows active provider name
- `EXIT_INDEX` = 11 (12 menu items, 0-indexed)
