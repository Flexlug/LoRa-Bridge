"""Классификация ответа `meshcore_py` в доменный ``SendResult`` (§5.1, AD-5).

Ядро трактует ``SendResult.ok`` единообразно; здесь — единственное место, где
коды/строки ошибок устройства переводятся в busy (повтор) vs failed.
"""

from __future__ import annotations

from typing import Any

from ...domain.models import SendResult


def classify(res: Any) -> SendResult:
    if res.is_error():
        payload = res.payload if isinstance(res.payload, dict) else {}
        if payload.get("error_code") == 3:  # ERR_CODE_TABLE_FULL
            return SendResult.overloaded()
        reason = payload.get("reason", "")
        if reason == "no_event_received":  # устройство занято флудом — повтор, не FAILED
            return SendResult.overloaded(reason)
        detail = payload.get("code_string") or reason or str(res.payload)
        return SendResult.failure(detail)
    return SendResult.success()
