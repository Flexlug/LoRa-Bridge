from lora_bridge.transports.telegram.moderation.transliterate import transliterate


def test_basic_lowercase() -> None:
    assert transliterate("привет") == "privet"


def test_basic_uppercase() -> None:
    assert transliterate("Привет") == "Privet"


def test_non_cyrillic_passthrough() -> None:
    assert transliterate("hello 123!") == "hello 123!"


def test_mixed() -> None:
    result = transliterate("Вася: hello")
    assert result == "Vasya: hello"


def test_soft_hard_sign_removed() -> None:
    assert transliterate("объект") == "obekt"


def test_empty_string() -> None:
    assert transliterate("") == ""
