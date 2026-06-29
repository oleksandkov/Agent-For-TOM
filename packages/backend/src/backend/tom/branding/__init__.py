"""TOM product identity — name, version, support links, copyright.

Single source of truth for branding shown by the CLI, the HTTP `/healthz`
endpoint, the `/v1/brand` response, and user-facing logs.
"""

from __future__ import annotations

PRODUCT_NAME: str = "TOM"
PRODUCT_TAGLINE: str = "local-first personal AI agent"
VERSION: str = "0.1.0"
API_VERSION: str = "v1"

SUPPORT_EMAIL: str = "support@tom.local"
DOCS_URL: str = "https://github.com/Agent-For-TOM"
REPO_URL: str = "https://github.com/Agent-For-TOM"
LICENSE_NAME: str = "MIT"
LICENSE_URL: str = "https://opensource.org/licenses/MIT"

COPYRIGHT_NOTICE: str = "Copyright (c) 2026 TOM contributors"

__all__: list[str] = [
    "API_VERSION",
    "COPYRIGHT_NOTICE",
    "DOCS_URL",
    "LICENSE_NAME",
    "LICENSE_URL",
    "PRODUCT_NAME",
    "PRODUCT_TAGLINE",
    "REPO_URL",
    "SUPPORT_EMAIL",
    "VERSION",
]
