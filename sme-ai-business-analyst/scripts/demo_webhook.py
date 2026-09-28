import argparse
import asyncio
import time

import httpx


def sample_payload(text: str, from_phone: str) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "demo-entry",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550000000",
                                "phone_number_id": "demo-phone-number-id",
                            },
                            "contacts": [
                                {
                                    "profile": {"name": "Demo Owner"},
                                    "wa_id": from_phone,
                                }
                            ],
                            "messages": [
                                {
                                    "from": from_phone,
                                    "id": f"wamid.demo.{int(time.time())}",
                                    "timestamp": str(int(time.time())),
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


async def _run() -> None:
    parser = argparse.ArgumentParser(description="Send a sample WhatsApp webhook payload.")
    parser.add_argument("--url", default="http://localhost:8000/webhooks/whatsapp")
    parser.add_argument("--from-phone", default="2348000000000")
    parser.add_argument("--text", default="Sold 5 bags rice for 250000")
    args = parser.parse_args()

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(args.url, json=sample_payload(args.text, args.from_phone))
        print(response.status_code)
        print(response.text)


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
