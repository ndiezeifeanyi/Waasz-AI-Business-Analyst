import html


def sanitize_message(text: str, max_length: int) -> str:
    cleaned = html.escape(text.strip(), quote=False)
    return cleaned[:max_length]
