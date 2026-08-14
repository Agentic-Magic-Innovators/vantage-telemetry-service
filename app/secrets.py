"""Fail-closed resolution for security-critical secrets.

Both TELEMETRY_INTERNAL_KEY and TELEMETRY_BRIDGE_JWT_SECRET used to fall back
to a hardcoded placeholder string when their env var was unset -- silently, so
a deployment that forgot to override them just ran with a signing key anyone
reading this repo already knows. require_secret() makes that fail loud at
import time instead, with a single documented escape hatch for local dev.
"""
from __future__ import annotations

import os


def require_secret(var_name: str, placeholder: str) -> str:
    value = os.environ.get(var_name, "")
    if value and value != placeholder:
        return value

    if os.environ.get("VANTAGE_ALLOW_DEV_SECRETS", "").strip().lower() in {"1", "true", "yes"}:
        return value or placeholder

    raise RuntimeError(
        f"{var_name} is not set (or still the '{placeholder}' placeholder). "
        f"Generate a real value -- e.g. "
        f'`python -c "import secrets; print(secrets.token_hex(32))"` -- and set '
        f"{var_name} in your environment before starting this service. To run "
        f"locally without one, set VANTAGE_ALLOW_DEV_SECRETS=true -- NEVER in a "
        f"shared or networked deployment, since the placeholder is public."
    )
