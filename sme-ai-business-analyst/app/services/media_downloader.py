import httpx

from app.core.config import settings


class MediaDownloader:
    async def download(self, media_id: str) -> bytes:
        if not settings.whatsapp_access_token:
            return b""
        headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}
        metadata_url = f"https://graph.facebook.com/{settings.whatsapp_api_version}/{media_id}"
        async with httpx.AsyncClient(timeout=30) as client:
            metadata = await client.get(metadata_url, headers=headers)
            metadata.raise_for_status()
            media_url = metadata.json().get("url")
            if not media_url:
                return b""
            media = await client.get(media_url, headers=headers)
            media.raise_for_status()
            return media.content
