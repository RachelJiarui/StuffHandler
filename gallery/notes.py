"""Rendering of the notes field's stored markup."""

import re

from markupsafe import Markup, escape

MENTION_RE = re.compile(r"@\[([^\]]+)\]\(([^)]+)\)")
LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def notes_to_html(text: str) -> Markup:
    """Render stored notes markup (`@[Name](file)`, `[text](url)`) as HTML.

    Registered as the `notes_html` Jinja filter."""
    escaped = str(escape(text))

    def mention_sub(m):
        name, file = m.group(1), m.group(2)
        return (f'<span class="mention" contenteditable="false" '
                f'data-file="{file}" data-name="{name}">@{name}</span>')

    def link_sub(m):
        text_, url = m.group(1), m.group(2)
        return f'<a href="{url}" target="_blank" rel="noopener">{text_}</a>'

    out = MENTION_RE.sub(mention_sub, escaped)
    out = LINK_RE.sub(link_sub, out)
    return Markup(out)
