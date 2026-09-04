import os
import time
import threading
import requests
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

GEMINI_MODELS = [
    "gemini-3.6-flash",
    "gemini-2.0-flash",
    "gemini-2.5-flash",
    "gemini-1.5-flash",
]

GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "gemma2-9b-it",
]

MAX_CONCURRENT = 6
_semaphore = threading.Semaphore(MAX_CONCURRENT)

USER_COOLDOWN_SEC = 6
_user_last: dict[int, float] = {}
_user_lock = threading.Lock()

_active = 0
_active_lock = threading.Lock()

FRIENDLY_BUSY = "⏳ Сейчас много запросов. Напиши ещё раз через 20–30 секунд."
FRIENDLY_COOLDOWN = "⏳ Подожди ещё {wait} сек. и напиши снова."
FRIENDLY_FAIL = "⏳ ИИ сейчас недоступен. Попробуй через минуту."


def can_user_request(user_id: int) -> tuple[bool, int]:
    now = time.time()
    with _user_lock:
        last = _user_last.get(user_id, 0)
        wait = USER_COOLDOWN_SEC - (now - last)
        if wait > 0:
            return False, int(wait) + 1
        _user_last[user_id] = now
        return True, 0


def get_load_info() -> str:
    with _active_lock:
        active = _active
    return f"Активных запросов к ИИ: {active}/{MAX_CONCURRENT}"


def _ask_gemini(prompt: str) -> str | None:
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return None
    try:
        client = genai.Client(api_key=api_key)
    except Exception:
        return None

    for model in GEMINI_MODELS:
        for attempt in range(2):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        temperature=0.3,
                    ),
                )
                if response.text:
                    return response.text.strip()
            except Exception as e:
                err = str(e).lower()
                if "404" in err or "not_found" in err or "no longer available" in err:
                    break
                if "503" in err or "unavailable" in err or "high demand" in err or "resource" in err:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                time.sleep(0.4)
    return None


def _ask_groq(prompt: str) -> str | None:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None

    for model in GROQ_MODELS:
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 4096,
                },
                timeout=55,
            )
            if r.status_code == 200:
                data = r.json()
                text = data.get("choices", [{}])[0].get("message", {}).get("content")
                if text:
                    return text.strip()
            if r.status_code in (429, 503, 500):
                time.sleep(1.2)
                continue
        except Exception:
            continue
    return None


def ask_gemini(prompt: str, user_id: int | None = None) -> str:
    if user_id is not None:
        ok, wait = can_user_request(user_id)
        if not ok:
            return FRIENDLY_COOLDOWN.format(wait=wait)

    acquired = _semaphore.acquire(timeout=35)
    if not acquired:
        return FRIENDLY_BUSY

    global _active
    with _active_lock:
        _active += 1

    try:
        result = _ask_gemini(prompt)
        if result:
            return result

        result = _ask_groq(prompt)
        if result:
            return result

        return FRIENDLY_FAIL
    finally:
        with _active_lock:
            _active -= 1
        _semaphore.release()


def generate_recommendation_analysis(
    currency: str,
    budget: str,
    phone_class: str,
    priorities: list[str],
    user_id: int | None = None,
) -> str:
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
    return ask_gemini(prompt, user_id=user_id)
