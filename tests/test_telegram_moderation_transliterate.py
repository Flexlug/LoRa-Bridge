import pytest

from lora_bridge.transports.telegram.moderation.transliterate import transliterate


@pytest.mark.parametrize(
    "src,expected",
    [
        ("привет", "privet"),             # строчная кириллица
        ("Привет", "Privet"),             # заглавная сохраняется
        ("hello 123!", "hello 123!"),     # не-кириллица проходит как есть
        ("Вася: hello", "Vasya: hello"),  # смешанный текст
        ("объект", "obekt"),              # ъ/ь выкидываются
        ("", ""),                         # пустая строка
    ],
    ids=["lowercase", "uppercase", "non_cyrillic", "mixed", "soft_hard_sign", "empty"],
)
def test_transliterate(src: str, expected: str) -> None:
    assert transliterate(src) == expected
