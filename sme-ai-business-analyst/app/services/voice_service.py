import logging

from app.core.config import settings
from app.core.exceptions import VoiceTranscriptionError
from app.core.model_resolver import is_model_not_found_error, model_resolver

logger = logging.getLogger(__name__)


class VoiceService:
    """
    Audio transcription service with two-provider fallback chain:
    Primary: Groq Whisper (high speed, active credits)
    Secondary: OpenAI Whisper
    """

    async def transcribe(self, audio_bytes: bytes) -> str:
        if not audio_bytes:
            raise VoiceTranscriptionError("Empty audio payload received.")

        errors: list[str] = []

        # 1. Primary: OpenAI Whisper
        if settings.openai_api_key and not settings.openai_api_key.startswith("placeholder"):
            openai_model = model_resolver.get_model("openai", "audio")
            try:
                from openai import AsyncOpenAI

                client = AsyncOpenAI(api_key=settings.openai_api_key, max_retries=1)
                response = await client.audio.transcriptions.create(
                    model=openai_model,
                    file=("whatsapp_voice.ogg", audio_bytes),
                    response_format="text",
                )
                text = str(response).strip()
                if text:
                    return text
            except Exception as exc:
                if is_model_not_found_error(exc):
                    did_heal, new_model = await model_resolver.handle_mid_run_failure("openai", "audio", exc)
                    if did_heal:
                        try:
                            from openai import AsyncOpenAI

                            client = AsyncOpenAI(api_key=settings.openai_api_key, max_retries=1)
                            response = await client.audio.transcriptions.create(
                                model=new_model,
                                file=("whatsapp_voice.ogg", audio_bytes),
                                response_format="text",
                            )
                            text = str(response).strip()
                            if text:
                                return text
                        except Exception as retry_exc:
                            exc = retry_exc
                logger.warning("OpenAI Whisper transcription failed: %s. Falling back to Groq.", exc)
                errors.append(f"OpenAI: {exc}")

        # 2. Secondary Fallback: Groq Whisper
        if settings.groq_api_key and not settings.groq_api_key.startswith("placeholder"):
            groq_model = model_resolver.get_model("groq", "audio")
            try:
                from groq import AsyncGroq

                groq_client = AsyncGroq(api_key=settings.groq_api_key, max_retries=1)
                response = await groq_client.audio.transcriptions.create(
                    model=groq_model,
                    file=("whatsapp_voice.ogg", audio_bytes),
                    response_format="text",
                )
                text = str(response).strip()
                if text:
                    return text
            except Exception as exc:
                if is_model_not_found_error(exc):
                    did_heal, new_model = await model_resolver.handle_mid_run_failure("groq", "audio", exc)
                    if did_heal:
                        try:
                            from groq import AsyncGroq

                            groq_client = AsyncGroq(api_key=settings.groq_api_key)
                            response = await groq_client.audio.transcriptions.create(
                                model=new_model,
                                file=("whatsapp_voice.ogg", audio_bytes),
                                response_format="text",
                            )
                            text = str(response).strip()
                            if text:
                                return text
                        except Exception as retry_exc:
                            exc = retry_exc
                logger.warning("Groq Whisper transcription fallback failed: %s.", exc)
                errors.append(f"Groq: {exc}")

        # Both providers failed or unconfigured
        groq_configured = bool(settings.groq_api_key and not settings.groq_api_key.startswith("placeholder"))
        openai_configured = bool(settings.openai_api_key and not settings.openai_api_key.startswith("placeholder"))
        if not groq_configured and not openai_configured:
            raise VoiceTranscriptionError(
                "Voice note transcription is unavailable because transcription services are unconfigured. "
                "Please type your message instead."
            )

        # Check if errors were billing/quota related
        err_text = " ".join(errors).lower()
        if any(k in err_text for k in ["quota", "429", "credit_balance", "billing", "insufficient_quota"]):
            raise VoiceTranscriptionError(
                "Voice note transcription is currently unavailable (API credits required). "
                "Please type your message instead."
            )

        raise VoiceTranscriptionError(
            "Could not transcribe voice note at this time. Please send your message as text."
        )
