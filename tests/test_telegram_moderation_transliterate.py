import pytest

from lora_bridge.transports.telegram.moderation.transliterate import transliterate


@pytest.mark.parametrize(
    "src,expected",
    [
        ("сорока", "copoka"),       # все строчные маппятся
        ("привет", "npuвeт"),       # в/т остаются кириллицей
        ("гора", "ropa"),           # г→r
        ("шуба", "wyбa"),           # ш→w, б остаётся
        ("ёжик", "eжuk"),           # ё→e, ж остаётся
        ("ь", "b"),                 # мягкий знак
        ("hello 123!", "hello 123!"),  # не-кириллица проходит как есть
        ("", ""),                   # пустая строка
    ],
    ids=["all_lower", "kept_v_t", "g", "sh_kept_b", "yo_kept_zh", "soft_sign",
         "non_cyrillic", "empty"],
)
def test_transliterate(src: str, expected: str) -> None:
    assert transliterate(src) == expected


@pytest.mark.parametrize(
    "src,expected",
    [
        ("ВНТ", "BHT"),     # заглавные В/Н/Т совпадают с латиницей
        ("внт", "внт"),     # строчные в/н/т аналога в том же регистре не имеют
        ("Папа", "Пana"),   # п→n и а→a, но заглавная П остаётся
        ("ПРИВЕТ", "ПPИBET"),  # П/И остаются, Р/В/Е/Т маппятся
    ],
    ids=["upper_VNT", "lower_vnt_kept", "p_vs_P", "mixed_caps"],
)
def test_case_asymmetry(src: str, expected: str) -> None:
    """Регистровая асимметрия — ключевой инвариант таблицы (легко сломать при правке)."""
    assert transliterate(src) == expected


def test_mapped_text_shrinks_payload() -> None:
    """На тексте только из маппящихся букв вывод по байтам меньше входа (цель — payload)."""
    src = "сорока"
    out = transliterate(src)
    assert len(out.encode("utf-8")) < len(src.encode("utf-8"))
