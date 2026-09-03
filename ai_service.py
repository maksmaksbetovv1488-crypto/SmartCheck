import os
import time
from google import genai
from google.genai import types


SYSTEM_PROMPT = (
    "Ты — SmartCheck, независимый эксперт и аналитик смартфонов. "
    "Главный принцип: 'НЕ ХВАЛИМ ТЕЛЕФОН. ПРОВЕРЯЕМ ЕГО.' "
    "Никогда не выдумывай характеристики, цифры, FPS, температуры, тесты или результаты бенчмарков. "
    "Пиши честно, подробно, аргументировано. Всегда указывай на реальные минусы, компромиссы и подводные камни. "
    "Если данных недостаточно — прямо пиши «недостаточно подтверждённых данных». "
    "Samsung, Apple, Xiaomi, Vivo, OPPO, Huawei и любые другие бренды оцениваются по одним и тем же правилам."
)

# Список моделей по приоритету (если одна перегружена — пробуем следующую)
MODELS = [
    "gemini-3.6-flash",
    "gemini-2.0-flash",
    "gemini-2.5-flash",
    "gemini-1.5-flash",
]


def ask_gemini(prompt: str) -> str:
    """Отправляет запрос в Gemini с ретраями и запасными моделями."""
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return "Ошибка: переменная окружения GOOGLE_API_KEY не задана."

    client = genai.Client(api_key=api_key)
    last_error = None

    for model in MODELS:
        for attempt in range(3):  # до 3 попыток на каждую модель
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        temperature=0.3,
                    ),
                )
                text = response.text
                if text:
                    return text
                last_error = "Пустой ответ от модели"
            except Exception as e:
                last_error = str(e)
                err_lower = last_error.lower()
                # Если модель недоступна / не найдена — сразу пробуем следующую
                if "404" in last_error or "not_found" in err_lower or "no longer available" in err_lower:
                    break
                # При перегрузке (503) — ждём и повторяем
                if "503" in last_error or "unavailable" in err_lower or "high demand" in err_lower:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                # Другие ошибки — тоже пробуем ещё раз
                time.sleep(1)
                continue

    return (
        "Сейчас сервис Gemini перегружен или временно недоступен.\n"
        "Попробуй через 30–60 секунд ещё раз.\n\n"
        f"(Техническая информация: {last_error})"
    )


def generate_recommendation_analysis(
    currency: str,
    budget: str,
    phone_class: str,
    priorities: list[str],
) -> str:
    """Формирует детальный промпт для подбора топ-3 смартфонов и возвращает ответ Gemini."""
    priorities_str = ", ".join(priorities) if priorities else "баланс"

    prompt = f"""
Пользователь хочет подобрать смартфон через SmartCheck.

Параметры запроса:
• Валюта: {currency}
• Бюджет: {budget} {currency}
• Класс смартфона: {phone_class}
• Приоритеты: {priorities_str}

Твоя задача:
1. Подбери ровно 3 наиболее подходящих актуальных смартфона строго в рамках указанного бюджета и класса (если класс указан и это не «Не знаю»).
2. Для каждой модели укажи честный SmartScore в формате XX/100 (например 94/100). Оценка должна быть обоснованной, без завышения.
3. Подробно и честно объясни:
   - почему модель №1 лучше всего подходит именно этому пользователю;
   - какие требования пользователя она закрывает;
   - где она сильнее конкурентов;
   - где слабее и какие есть компромиссы;
   - кому она подходит, а кому лучше поискать другое;
   - почему модели №2 и №3 оказались ниже.
4. Категорически запрещено выдумывать характеристики, FPS, температуры, результаты тестов, объём памяти, ёмкость батареи и любые другие цифры.
5. Если точных подтверждённых данных нет — пиши «недостаточно подтверждённых данных».
6. Соблюдай главный принцип: «НЕ ХВАЛИМ ТЕЛЕФОН. ПРОВЕРЯЕМ ЕГО.»

Формат ответа строго такой:

🎯 ЛУЧШИЕ ВАРИАНТЫ

🥇 [Название модели] — SmartScore XX/100
[подробный честный разбор]

🥈 [Название модели] — SmartScore XX/100
[разбор]

🥉 [Название модели] — SmartScore XX/100
[разбор]

🧠 ИТОГОВЫЙ СОВЕТ
[чёткий вывод: стоит ли брать №1, на что обратить внимание, кому подойдёт]
"""
    return ask_gemini(prompt)
