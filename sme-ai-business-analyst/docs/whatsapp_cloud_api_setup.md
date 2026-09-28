# WhatsApp Cloud API Setup

1. In Meta for Developers, create an app and add WhatsApp.
2. Copy the temporary or permanent WhatsApp access token.
3. Copy the phone number ID.
4. Set:

```env
WHATSAPP_ACCESS_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_API_VERSION=v20.0
WHATSAPP_VERIFY_TOKEN=
WHATSAPP_APP_SECRET=
```

5. Configure the webhook callback:

```text
https://YOUR_DOMAIN/webhooks/whatsapp
```

6. During verification, Meta sends:

```text
hub.mode=subscribe
hub.verify_token=<your token>
hub.challenge=<challenge>
```

The API returns the challenge only when the verify token matches.

7. Subscribe to `messages`.

8. Send test messages:

```text
Sold 5 bags rice for 250000
Bought fuel 12000
Received 20 cartons indomie
```

The bot must answer each extraction with a confirmation message containing:

```text
Reply 1=Yes, 2=Edit.
```
