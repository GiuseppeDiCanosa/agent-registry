# GENERATED FROM SPEC — DO NOT EDIT DIRECTLY
# Source: openspec/specs/whatsapp-notifications/spec.md
"""Client minimale per il gateway WhatsApp open-wa.

Nessun segreto è hardcoded: URL del gateway, session id, API key e destinatario
arrivano da parametri o dall'ambiente. `build_send_request` è puro e testabile;
`send_text` esegue la POST HTTP.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any


def build_send_request(
    text: str,
    recipient: str,
    *,
    base_url: str | None = None,
    session_id: str | None = None,
    api_key: str | None = None,
) -> tuple[str, dict[str, str], dict[str, Any]]:
    """Costruisce (url, headers, body) per l'invio di un messaggio testuale.

    L'endpoint segue l'API open-wa: POST /api/sessions/{id}/messages/send-text.
    """
    base_url = base_url if base_url is not None else os.environ.get(
        "WA_GATEWAY_URL", "http://wa-gateway:2785"
    )
    session_id = session_id if session_id is not None else os.environ.get(
        "WA_SESSION_ID", "default"
    )
    api_key = api_key if api_key is not None else os.environ.get("WA_API_KEY", "")

    url = f"{base_url.rstrip('/')}/api/sessions/{session_id}/messages/send-text"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    # open-wa vuole il chatId nel formato WhatsApp: <numero>@c.us
    chat_id = recipient if "@" in recipient else f"{recipient}@c.us"
    body = {"chatId": chat_id, "text": text}
    return url, headers, body


def send_text(
    text: str,
    recipient: str,
    *,
    base_url: str | None = None,
    session_id: str | None = None,
    api_key: str | None = None,
    timeout: float = 10.0,
) -> tuple[int, str]:
    """Invia un messaggio testuale via gateway open-wa. Ritorna (status, corpo)."""
    url, headers, body = build_send_request(
        text, recipient, base_url=base_url, session_id=session_id, api_key=api_key
    )
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (host interno)
        return resp.status, resp.read().decode("utf-8", "replace")
