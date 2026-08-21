import os
from typing import Optional


_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def env_flag(name: str, default: bool = True) -> bool:
    """Read a boolean environment flag and reject ambiguous values."""
    raw_value = os.environ.get(name)
    if raw_value is None or not raw_value.strip():
        return default

    normalized = raw_value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ValueError(
        f"{name} must be one of: true, false, 1, 0, yes, no, on, off."
    )


def env_value(*names: str) -> Optional[str]:
    """Return the first non-empty environment value from a list of aliases."""
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def provider_status(enabled_variable: str, *credential_variables: str) -> dict:
    try:
        enabled = env_flag(enabled_variable)
    except ValueError as error:
        return {"available": False, "reason": "invalid_configuration", "detail": str(error)}

    if not enabled:
        return {"available": False, "reason": "disabled"}
    if not env_value(*credential_variables):
        return {"available": False, "reason": "missing_credentials"}
    return {"available": True, "reason": "active"}
