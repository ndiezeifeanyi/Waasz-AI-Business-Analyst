#!/usr/bin/env python3
"""
Empirical Quota Test for Dedicated Gemini Image API Key.
Tests sequential image generation calls against GEMINI_IMAGE_API_KEY.
Stops at 25 successes or on first 429/failure.
"""
import asyncio
import logging
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings
from app.core.model_resolver import model_resolver
from app.services.image_service import ImageService

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


async def main():
    print("=" * 70)
    print("EMPIRICAL TEST: GEMINI_IMAGE_API_KEY QUOTA CEILING")
    print("=" * 70)
    print(f"Main Shared Key (prefix):    {settings.gemini_api_key[:10]}...")
    print(f"Dedicated Image Key (prefix): {settings.gemini_image_api_key[:10]}...")
    print(f"Is using dedicated key:       {settings.is_using_dedicated_gemini_image_key}")
    
    model = model_resolver.get_model("gemini", "image_gen") or "gemini-3.1-flash-image-preview"
    print(f"Target image_gen model:       {model}")
    print("-" * 70)

    image_service = ImageService()
    target_count = 25
    success_count = 0
    failure_reason = None

    for i in range(1, target_count + 1):
        prompt = f"Minimal clean geometric vector icon of a coffee cup #{i}, high contrast, flat style"
        print(f"\n[Call {i}/{target_count}] Initiating image generation...")
        start_t = time.perf_counter()
        try:
            # Call _call_gemini_generate directly to test raw Gemini image endpoint without touching DB cost events
            img_bytes, mime = await image_service._call_gemini_generate(prompt, model)
            elapsed = time.perf_counter() - start_t
            success_count += 1
            print(f"  -> SUCCESS ({elapsed:.2f}s): {len(img_bytes)} bytes, MIME={mime}")
        except Exception as exc:
            elapsed = time.perf_counter() - start_t
            failure_reason = str(exc)
            print(f"  -> FAILED ({elapsed:.2f}s): {exc}")
            print(f"\nStopped early at call #{i} due to error.")
            break

        if i < target_count:
            # 3 second sleep to avoid transient per-minute burst rate limits
            await asyncio.sleep(3.0)

    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    print(f"Total Successful Calls: {success_count}/{target_count}")
    if failure_reason:
        print(f"Failure Reason: {failure_reason}")
        if "429" in failure_reason:
            print("Outcome: Hit 429 quota exhaustion.")
    else:
        print("Outcome: Reached 25 successful calls without hitting quota wall!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
