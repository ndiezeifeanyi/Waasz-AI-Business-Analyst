"""
URL Helper utility for generating shortlinks and clean domain URLs.
"""

import urllib.parse
from app.core.config import settings


def get_base_origin() -> str:
    """
    Extracts the root web origin (e.g., https://waasz-sme-api.duckdns.org or https://waaszai.com)
    from settings.app_base_url without trailing paths or slashes.
    """
    raw = (settings.app_base_url or "http://localhost:8000").strip()
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return raw.rstrip("/")


def get_short_iou_url(ref_code: str) -> str:
    """
    Constructs an ultra-short, reliable, clickable IOU link.
    Example: https://waasz-sme-api.duckdns.org/i/WZ-1A2B3C
    """
    clean_ref = str(ref_code).strip()
    origin = get_base_origin()
    return f"{origin}/i/{clean_ref}"
