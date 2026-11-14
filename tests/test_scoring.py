import pytest

from app.scoring import PlayfulAnswerError, parse_numeric_answer


@pytest.mark.parametrize(
    ("kind", "raw", "expected"),
    [
        ("time", "22:10", 1330),
        ("time", "2210", 1330),
        ("time", "22.10", 1330),
        ("months", "восемь месяцев", 8),
        ("months", "8 мес.", 8),
        ("weight_grams", "3150 г", 3150),
        ("weight_grams", "3,15 кг", 3150),
        ("weight_grams", "3.15", 3150),
        ("height_cm", "51 см", 51),
        ("height_cm", "0,51 м", 51),
    ],
)
def test_loose_numeric_formats(kind, raw, expected):
    question = {"answer_kind": kind}
    assert parse_numeric_answer(question, raw) == expected


def test_invalid_numeric_answer_has_playful_message():
    with pytest.raises(PlayfulAnswerError, match="Часики"):
        parse_numeric_answer({"answer_kind": "time"}, "после мультиков")

