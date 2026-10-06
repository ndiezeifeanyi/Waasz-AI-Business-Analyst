"""
WhatsApp Business Profile Picture Updater.
Uploads the specified logo image to Meta Graph API using the Resumable Upload API
and sets it as the active WhatsApp Business profile photo.
"""

import logging
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env", override=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def update_whatsapp_profile_picture(image_path: str | Path | None = None) -> bool:
    token = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()
    phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
    api_version = os.getenv("WHATSAPP_API_VERSION", "v21.0").strip().lstrip("/")

    if not token or not phone_id:
        logger.error("WHATSAPP_ACCESS_TOKEN or WHATSAPP_PHONE_NUMBER_ID missing from environment.")
        return False

    if not image_path:
        # Default logo paths
        candidates = [
            ROOT_DIR / "admin" / "waasz_logo.jpg",
            ROOT_DIR / "waasz_logo.jpg",
            ROOT_DIR / "docs" / "assets" / "waasz_logo.jpg",
        ]
        for c in candidates:
            if c.exists():
                image_path = c
                break

    if not image_path or not Path(image_path).exists():
        logger.error("Logo image file not found.")
        return False

    image_file = Path(image_path)
    file_size = image_file.stat().st_size
    mime_type = "image/jpeg" if image_file.suffix.lower() in [".jpg", ".jpeg"] else "image/png"

    logger.info("Found logo at %s (Size: %d bytes, MIME: %s)", image_file, file_size, mime_type)

    # 1. Discover Meta App ID
    app_id = "1397672365851427"
    try:
        r_app = httpx.get(
            f"https://graph.facebook.com/{api_version}/app",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15.0,
        )
        if r_app.status_code == 200:
            app_id = r_app.json().get("id", app_id)
            logger.info("Discovered Meta App ID: %s", app_id)
    except Exception as exc:
        logger.warning("Could not auto-detect App ID, using default: %s", exc)

    # 2. Initiate Resumable Upload Session
    logger.info("Creating Meta Resumable Upload Session...")
    r_session = httpx.post(
        f"https://graph.facebook.com/{api_version}/{app_id}/uploads",
        params={"file_length": file_size, "file_type": mime_type},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15.0,
    )
    if r_session.status_code != 200:
        logger.error("Failed to initiate upload session: %s", r_session.text)
        return False

    session_id = r_session.json().get("id")
    logger.info("Upload session initialized: %s", session_id)

    # 3. Upload Binary File Data
    with open(image_file, "rb") as f:
        img_bytes = f.read()

    logger.info("Uploading binary payload...")
    r_upload = httpx.post(
        f"https://graph.facebook.com/{api_version}/{session_id}",
        headers={
            "Authorization": f"OAuth {token}",
            "file_offset": "0",
            "Content-Type": mime_type,
        },
        content=img_bytes,
        timeout=30.0,
    )
    if r_upload.status_code != 200:
        logger.error("Failed to upload binary content: %s", r_upload.text)
        return False

    handle = r_upload.json().get("h")
    logger.info("Received profile picture handle: %s", handle)

    # 4. Set Profile Picture on WhatsApp Business Profile
    logger.info("Setting profile picture on phone ID: %s...", phone_id)
    r_profile = httpx.post(
        f"https://graph.facebook.com/{api_version}/{phone_id}/whatsapp_business_profile",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "messaging_product": "whatsapp",
            "profile_picture_handle": handle,
        },
        timeout=15.0,
    )
    if r_profile.status_code == 200 and r_profile.json().get("success"):
        logger.info("✅ SUCCESS! WhatsApp Business Profile Picture updated to Waasz logo!")
        return True
    else:
        logger.error("Failed to set profile picture: %s", r_profile.text)
        return False


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    success = update_whatsapp_profile_picture(target)
    sys.exit(0 if success else 1)
