"""Сборка LoRa-нагрузки: префикс ``[тип:ник]`` + бюджет байтов (§4, AD-10/AD-11).

Чистые функции, без I/O — реализованы полностью (легко тестируются).
"""
from __future__ import annotations

from ..domain.models import LabelFormat, Message, Room


def utf8_len(s: str) -> int:
    """Длина строки в БАЙТАХ UTF-8 (лимит LoRa байтовый, не символьный — D2)."""
    return len(s.encode("utf-8"))


def clip_utf8(s: str, max_bytes: int) -> str:
    """Усечь строку так, чтобы её UTF-8 не превышал ``max_bytes``, не разрывая символ.

    Применяется ТОЛЬКО к нику/метаданным; пользовательский текст НЕ усекаем (AD-11).
    """
    if max_bytes <= 0:
        return ""
    encoded = s.encode("utf-8")
    if len(encoded) <= max_bytes:
        return s
    # обрезаем по границе символа: декодируем с игнором «хвоста» многобайтового символа
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def build_lora_text(msg: Message, room: Room, tag: str, fmt: LabelFormat) -> str:
    """Развернуть сообщение мессенджера в плоскую строку ``<префикс><текст>``.

    Тип опускается, если в комнату пишет ровно один мессенджер (AD-10).
    Текст пользователя НЕ трогаем (AD-11); проверку байтового бюджета делает вызывающий.
    """
    nick = clip_utf8(msg.sender.display_name, fmt.max_nick_bytes)  # ник усекаем — ок
    if room.writable_messenger_count > 1 and fmt.include_type:
        label = f"[{tag}:{nick}] "      # "[TG:Alex] "
    else:
        label = f"[{nick}] "            # "[Alex] "
    return label + msg.text             # ТЕКСТ не трогаем; байты считает вызывающий


def oversize_bytes(text: str, max_text_bytes: int) -> int:
    """На сколько байт строка превышает лимит (>0 → REJECTED(TOO_LONG), §6)."""
    return utf8_len(text) - max_text_bytes
