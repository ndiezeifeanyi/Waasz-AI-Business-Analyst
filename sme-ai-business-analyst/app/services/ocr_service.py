from io import BytesIO

from PIL import Image

from app.core.config import settings


class OcrService:
    async def extract_text(self, image_bytes: bytes) -> str:
        if not image_bytes:
            return ""
        try:
            import pytesseract

            if settings.tesseract_cmd:
                pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd
            image = Image.open(BytesIO(image_bytes))
            text = pytesseract.image_to_string(image)
            if text.strip():
                return text.strip()
        except Exception:
            pass
        return await self._google_vision_fallback(image_bytes)

    async def _google_vision_fallback(self, image_bytes: bytes) -> str:
        if not settings.google_application_credentials:
            return ""
        try:
            from google.cloud import vision

            client = vision.ImageAnnotatorClient()
            image = vision.Image(content=image_bytes)
            response = client.text_detection(image=image)
            if response.text_annotations:
                return response.text_annotations[0].description.strip()
        except Exception:
            return ""
        return ""
