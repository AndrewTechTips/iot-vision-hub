# Alert channels: Telegram and webhooks

Besides email and web push, the hub can send each alert to **Telegram** and to a **webhook**.
Every channel goes through the same outbox: an alert is stored before it is sent, retried with
growing delays when the other side is down, and survives restarts. Turn on as many channels as
you like; a slow one never delays another.

Two settings shape the text of every channel:

```bash
VISION_HUB_NOTIFICATIONS__TIMEZONE=Europe/Bucharest   # times in alerts, as an IANA name
VISION_HUB_APP__PUBLIC_URL=https://hub.example.com    # alerts link to the event there
```

## Telegram

Each alert arrives as the snapshot with a caption, from a bot you own:

> **Someone lingering at Front door**
> In Doorstep since 11 Oct 2026, 14:05:23 EEST.
> [Open in Vision Hub](#)

1. In Telegram, talk to [@BotFather](https://t.me/BotFather), send `/newbot`, and keep the
   token it gives you.
2. Send your new bot any message, then open
   `https://api.telegram.org/bot<token>/getUpdates`: `"chat":{"id":...}` is your chat id. For a
   group, add the bot to it and send a message there (group ids are negative). For a channel,
   make the bot an admin and use `@channelname`.
3. Configure the hub and restart it:

```bash
VISION_HUB_TELEGRAM__ENABLED=true
VISION_HUB_TELEGRAM__BOT_TOKEN=123456789:AA...
VISION_HUB_TELEGRAM__CHAT_IDS=123456789,-1001234567890
```

A wrong token or an unknown chat is reported in the logs and not retried; Telegram being busy
(429, 5xx) or unreachable is retried. The token is part of every Bot API URL, so the hub never
logs URLs.

## Webhook

Each alert is POSTed as JSON to your URL. It suits Home Assistant, n8n, Node-RED or a script of
your own.

```bash
VISION_HUB_WEBHOOK__ENABLED=true
VISION_HUB_WEBHOOK__URL=https://home.example.com/api/webhook/vision-hub-7f3a9c
VISION_HUB_WEBHOOK__SECRET=a-long-random-string      # required in production
VISION_HUB_WEBHOOK__INCLUDE_IMAGE=false              # true adds the snapshot, base64
```

### Payload

```json
{
  "type": "alert",
  "version": 1,
  "title": "Someone lingering at Front door",
  "text": "In Doorstep since 11 Oct 2026, 14:05:23 EEST.",
  "url": "https://hub.example.com/events?event=01a12a0f-b087-717e-aba3-1a2170268849",
  "event": {
    "id": "01a12a0f-b087-717e-aba3-1a2170268849",
    "device_id": "front-door",
    "device_name": "Front door",
    "kind": "lingering",
    "zone": "Doorstep",
    "person": true,
    "person_confidence": null,
    "started_at": "2026-10-11T11:05:23+00:00",
    "ended_at": null,
    "duration_seconds": null
  },
  "image": { "content_type": "image/jpeg", "data_base64": "/9j/4AAQ..." }
}
```

- `title`, `text` and `url` are ready to show as they are. `url` is null without
  `VISION_HUB_APP__PUBLIC_URL`, and `image` is only there with `INCLUDE_IMAGE=true`.
- `event.kind` is `motion`, `covered`, `blurred`, `moved` (tampering), `lingering` or `crossed`
  (zone rules, with `zone` set). Motion alerts go out once the event ends; the others as soon
  as they start, so their `ended_at` is null.
- Every request also carries `X-Vision-Hub-Event` (the event id). An alert may arrive twice
  after a crash or a retry; deduplicate on it if that matters.

### Answers and retries

A `2xx` answer delivers the alert. `408`, `425`, `429` and `5xx`, or no answer at all, are
retried. Anything else, a redirect included (redirects are never followed), is logged and not
retried: the URL or the receiver needs fixing.

### Verifying the signature

With a secret set, every request is signed:

```
X-Vision-Hub-Timestamp: 1791000000
X-Vision-Hub-Signature: sha256=5c3e...   HMAC-SHA256(secret, "<timestamp>.<raw body>")
```

Recompute it over the raw body, compare in constant time, and refuse old timestamps so a
recorded request can't be replayed:

```python
import hashlib, hmac, time

def verify(secret: bytes, headers: dict[str, str], body: bytes, max_age: int = 300) -> bool:
    timestamp = headers["X-Vision-Hub-Timestamp"]
    if abs(time.time() - int(timestamp)) > max_age:
        return False
    expected = hmac.new(secret, timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(headers["X-Vision-Hub-Signature"], f"sha256={expected}")
```

```js
import { createHmac, timingSafeEqual } from "node:crypto";

function verify(secret, headers, rawBody, maxAge = 300) {
  const timestamp = headers["x-vision-hub-timestamp"];
  if (Math.abs(Date.now() / 1000 - Number(timestamp)) > maxAge) return false;
  const expected = createHmac("sha256", secret).update(`${timestamp}.`).update(rawBody).digest("hex");
  const given = Buffer.from(headers["x-vision-hub-signature"] ?? "");
  const wanted = Buffer.from(`sha256=${expected}`);
  return given.length === wanted.length && timingSafeEqual(given, wanted);
}
```

### Home Assistant

A webhook trigger runs an automation for each alert. Home Assistant does not check signatures,
so use a long random webhook id, which acts as the password:

```yaml
automation:
  - alias: "Vision Hub alert"
    triggers:
      - trigger: webhook
        webhook_id: vision-hub-7f3a9c
        allowed_methods: [POST]
        local_only: true
    actions:
      - action: notify.mobile_app_your_phone
        data:
          title: "{{ trigger.json.title }}"
          message: "{{ trigger.json.text }}"
          data:
            url: "{{ trigger.json.url }}"
```

The hub's URL is then `http://homeassistant.local:8123/api/webhook/vision-hub-7f3a9c`.
