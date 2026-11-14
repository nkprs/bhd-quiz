import hashlib
import json
from pathlib import Path


class QuizConfigError(ValueError):
    pass


def validate_quiz_config(data):
    required_text = ("child_name", "title", "subtitle", "welcome", "final_title", "final_text")
    for key in required_text:
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise QuizConfigError(f"Поле {key!r} должно быть непустой строкой")

    seconds = data.get("question_seconds", 60)
    if not isinstance(seconds, int) or not 10 <= seconds <= 600:
        raise QuizConfigError("question_seconds должен быть целым числом от 10 до 600")

    questions = data.get("questions")
    if not isinstance(questions, list) or len(questions) != 10:
        raise QuizConfigError("В конфигурации должно быть ровно 10 вопросов")

    seen_question_ids = set()
    for number, question in enumerate(questions, 1):
        question_id = question.get("id")
        if not isinstance(question_id, str) or not question_id.strip() or question_id in seen_question_ids:
            raise QuizConfigError(f"У вопроса {number} отсутствует уникальный строковый id")
        seen_question_ids.add(question_id)
        if not isinstance(question.get("text"), str) or not question["text"].strip():
            raise QuizConfigError(f"У вопроса {number} отсутствует текст")
        question_type = question.get("type", "choice")
        if question_type not in {"choice", "numeric"}:
            raise QuizConfigError(f"У вопроса {number} неизвестный тип {question_type!r}")
        if question_type == "numeric":
            if question.get("answer_kind") not in {"time", "months", "weight_grams", "height_cm"}:
                raise QuizConfigError(f"У числового вопроса {number} неизвестный answer_kind")
            if not isinstance(question.get("correct_value"), (int, float)):
                raise QuizConfigError(f"У числового вопроса {number} отсутствует correct_value")
            continue
        options = question.get("options")
        if not isinstance(options, list) or len(options) < 2:
            raise QuizConfigError(f"У вопроса {number} должно быть минимум два варианта")
        option_ids = []
        for option in options:
            if not isinstance(option, dict):
                raise QuizConfigError(f"Некорректный вариант в вопросе {number}")
            option_id, text = option.get("id"), option.get("text")
            if not isinstance(option_id, str) or not option_id.strip() or option_id in option_ids:
                raise QuizConfigError(f"Варианты вопроса {number} должны иметь уникальные id")
            if not isinstance(text, str) or not text.strip():
                raise QuizConfigError(f"У варианта вопроса {number} отсутствует текст")
            option_ids.append(option_id)
        if question.get("correct_option_id") not in option_ids:
            raise QuizConfigError(f"У вопроса {number} должен быть ровно один корректный ответ")

    return data


def load_quiz_config(path):
    config_path = Path(path)
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QuizConfigError(f"Не удалось прочитать конфигурацию {config_path}: {exc}") from exc
    validate_quiz_config(data)
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return data, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def public_question(question):
    result = {
        "id": question["id"],
        "type": question.get("type", "choice"),
        "text": question["text"],
        "caption": question.get("caption", ""),
        "image": question.get("image", ""),
    }
    if result["type"] == "numeric":
        result["placeholder"] = question.get("placeholder", "Введите вашу догадку")
        result["answer_kind"] = question["answer_kind"]
    else:
        result["options"] = question["options"]
    return result

