import re

_SPACER_RE = re.compile(r"\{:\s*[<>\^]?\s*(\d+)\s*\}")


def prepare_template(tmpl):
    """Превращает спейсер `{:<N}` / `{:>N}` / `{:^N}` в N пробелов."""
    return _SPACER_RE.sub(lambda m: " " * int(m.group(1)), tmpl or "")


def safe_format(tmpl, **values):
    """Безопасное форматирование: спейсеры + защита от неизвестных плейсхолдеров."""
    tmpl = prepare_template(tmpl)
    try:
        return tmpl.format(**values)
    except Exception:
        return tmpl