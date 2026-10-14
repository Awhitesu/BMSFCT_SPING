"""Small process-wide holder for the authenticated operator name."""

_current_user = ""


def set_current_user(user_name: str) -> None:
    global _current_user
    _current_user = (user_name or "").strip()


def get_current_user() -> str:
    return _current_user
