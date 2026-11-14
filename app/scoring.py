import math
import re


class PlayfulAnswerError(ValueError):
    pass


MONTH_WORDS = {
    "ноль": 0, "один": 1, "одна": 1, "два": 2, "две": 2, "три": 3,
    "четыре": 4, "пять": 5, "шесть": 6, "семь": 7, "восемь": 8,
    "девять": 9, "десять": 10, "одиннадцать": 11, "двенадцать": 12,
}


def _first_number(text):
    match = re.search(r"[-+]?\d+(?:[.,]\d+)?", text)
    return float(match.group(0).replace(",", ".")) if match else None


def parse_numeric_answer(question, raw_value):
    text = " ".join(str(raw_value or "").strip().lower().replace("ё", "е").split())
    if not text:
        raise PlayfulAnswerError("Без догадки никак — напишите хотя бы примерное значение 🙂")

    kind = question["answer_kind"]
    if kind == "time":
        match = re.search(r"(?<!\d)([01]?\d|2[0-3])\s*[:.\- ]\s*([0-5]\d)(?!\d)", text)
        if not match:
            compact = re.sub(r"\D", "", text)
            if len(compact) in (3, 4):
                hours, minutes = int(compact[:-2]), int(compact[-2:])
                if 0 <= hours <= 23 and 0 <= minutes <= 59:
                    return float(hours * 60 + minutes)
            raise PlayfulAnswerError("Часики запутались. Попробуйте формат вроде 22:10 или 2210 ⏰")
        return float(int(match.group(1)) * 60 + int(match.group(2)))

    number = _first_number(text)
    if number is None and kind == "months":
        number = next((value for word, value in MONTH_WORDS.items() if re.search(rf"\b{word}\b", text)), None)
    if number is None:
        raise PlayfulAnswerError("Цифры куда-то спрятались. Добавьте число — можно вместе с единицами 🙂")

    if kind == "months":
        if not 0 <= number <= 36:
            raise PlayfulAnswerError("Кажется, зубик слишком долго ждал. Попробуйте число от 0 до 36 месяцев 🦷")
        return float(number)
    if kind == "weight_grams":
        if "кг" in text or number <= 20:
            number *= 1000
        if not 500 <= number <= 10000:
            raise PlayfulAnswerError("Весы удивились. Попробуйте значение от 500 г до 10 кг ⚖️")
        return float(number)
    if kind == "height_cm":
        if re.search(r"\bм\b|метр", text) or number <= 3:
            number *= 100
        if not 20 <= number <= 150:
            raise PlayfulAnswerError("Ростомер просит что-нибудь от 20 до 150 см 📏")
        return float(number)
    raise PlayfulAnswerError("Не получилось разобрать ответ. Попробуйте ещё раз.")


def answer_distance(question, numeric_value):
    if numeric_value is None:
        return math.inf
    difference = abs(float(numeric_value) - float(question["correct_value"]))
    if question["answer_kind"] == "time":
        return min(difference, 1440 - difference)
    return difference


def maximum_score(config, participant_count):
    return sum(
        participant_count if question.get("type", "choice") == "numeric" else 2
        for question in config["questions"]
    )


def recompute_scores(connection, round_row, config):
    participants = connection.execute(
        "SELECT id FROM participants WHERE round_id=? ORDER BY id", (round_row["id"],)
    ).fetchall()
    participant_ids = [row["id"] for row in participants]
    total = len(participant_ids)
    if not total:
        return
    scores = {participant_id: 0 for participant_id in participant_ids}
    answers = connection.execute(
        "SELECT participant_id, question_index, option_id, numeric_value FROM answers WHERE round_id=?",
        (round_row["id"],),
    ).fetchall()
    by_question = {}
    for answer in answers:
        by_question.setdefault(answer["question_index"], {})[answer["participant_id"]] = answer

    for index, question in enumerate(config["questions"]):
        question_answers = by_question.get(index, {})
        is_scored = (
            round_row["status"] == "finished"
            or index < round_row["current_question"]
            or (index == round_row["current_question"] and bool(question_answers))
        )
        if not is_scored:
            continue
        if question.get("type", "choice") == "numeric":
            distances = {
                participant_id: answer_distance(
                    question,
                    question_answers.get(participant_id)["numeric_value"]
                    if participant_id in question_answers else None,
                )
                for participant_id in participant_ids
            }
            for participant_id, distance in distances.items():
                if math.isinf(distance):
                    points = 1
                else:
                    closer_count = sum(1 for other in distances.values() if other < distance)
                    points = max(1, total - closer_count)
                scores[participant_id] += points
                if participant_id in question_answers:
                    connection.execute(
                        "UPDATE answers SET points=? WHERE participant_id=? AND question_index=?",
                        (points, participant_id, index),
                    )
        else:
            for participant_id in participant_ids:
                answer = question_answers.get(participant_id)
                points = 2 if answer and answer["option_id"] == question["correct_option_id"] else 0
                scores[participant_id] += points
                if answer:
                    connection.execute(
                        "UPDATE answers SET points=?, is_correct=? WHERE participant_id=? AND question_index=?",
                        (points, int(answer["option_id"] == question["correct_option_id"]), participant_id, index),
                    )

    for participant_id, score in scores.items():
        connection.execute("UPDATE participants SET score=? WHERE id=?", (score, participant_id))
