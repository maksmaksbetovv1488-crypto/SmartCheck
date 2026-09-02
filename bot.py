import os
import asyncio
from typing import Optional, List
from datetime import datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup, default_state
from aiogram.fsm.storage.memory import MemoryStorage

from database import (
    init_db,
    add_user,
    get_user,
    update_subscription,
    get_referral_count,
    set_tariff,
    add_premium_days,
    set_blocked,
    get_stats,
    get_all_user_ids,
    search_users,
)
from ai_service import ask_gemini, generate_recommendation_analysis


BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_USERNAME = "StudyGotgkk"
BOT_USERNAME = os.getenv("BOT_USERNAME", "SmartCheckBot")

# Админы — через переменную окружения ADMIN_IDS (через запятую)
# Пример: ADMIN_IDS=123456789,987654321
_admin_raw = os.getenv("ADMIN_IDS", "")
ADMIN_IDS: List[int] = []
for part in _admin_raw.replace(" ", "").split(","):
    if part.isdigit():
        ADMIN_IDS.append(int(part))

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is not set")

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
admin_router = Router()
# admin_router первым — чтобы FSM админки не перехватывался общим обработчиком текста
dp.include_router(admin_router)
dp.include_router(router)


class SelectionStates(StatesGroup):
    currency = State()
    budget = State()
    phone_class = State()
    priorities = State()
    storage = State()
    condition = State()
    size = State()
    waterproof = State()


class AdminStates(StatesGroup):
    waiting_user_id = State()
    waiting_broadcast = State()
    waiting_days = State()
    waiting_search = State()


# ==================== ХЕЛПЕРЫ ====================

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def is_subscribed(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(
            chat_id=f"@{CHANNEL_USERNAME}",
            user_id=user_id
        )
        return member.status in ("member", "administrator", "creator")
    except Exception:
        return False


def subscription_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📢 Подписаться на канал",
            url=f"https://t.me/{CHANNEL_USERNAME}"
        )],
        [InlineKeyboardButton(
            text="🔄 Проверить подписку",
            callback_data="check_sub"
        )],
    ])


async def check_subscription_or_ask(message_or_callback, user_id: int) -> bool:
    # Админы проходят без проверки подписки
    if is_admin(user_id):
        return True

    user = get_user(user_id)
    if user and user[6]:  # is_blocked
        text = "🚫 Ты заблокирован в боте."
        if isinstance(message_or_callback, Message):
            await message_or_callback.answer(text)
        else:
            await message_or_callback.message.edit_text(text)
        return False

    subscribed = await is_subscribed(user_id)
    if subscribed:
        update_subscription(user_id, 1)
        return True

    update_subscription(user_id, 0)
    text = (
        "⚠️ Чтобы пользоваться ботом <b>SmartCheck</b>, необходимо подписаться на канал.\n\n"
        f"Подпишись на @{CHANNEL_USERNAME} и нажми «Проверить подписку»."
    )
    kb = subscription_keyboard()

    if isinstance(message_or_callback, Message):
        await message_or_callback.answer(text, reply_markup=kb, parse_mode="HTML")
    else:
        await message_or_callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    return False


def main_menu_keyboard(user_id: int = 0) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="🎯 Подобрать телефон", callback_data="select_phone")],
        [
            InlineKeyboardButton(text="⚔️ Сравнить", callback_data="stub_compare"),
            InlineKeyboardButton(text="🏆 Рейтинги", callback_data="stub_ratings"),
        ],
        [
            InlineKeyboardButton(text="♻️ Проверить б/у", callback_data="stub_used"),
            InlineKeyboardButton(text="⭐ PRO / ULTRA", callback_data="stub_pro"),
        ],
        [InlineKeyboardButton(text="🎁 Рефералы и Бонусы", callback_data="referrals")],
        [InlineKeyboardButton(text="🤖 Задать вопрос ИИ", callback_data="ask_ai")],
    ]
    # Админ-панель скрыта — только через команду /admin
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="main_menu")],
    ])


def admin_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="🔍 Найти пользователя", callback_data="admin_search")],
        [InlineKeyboardButton(text="⭐ Выдать PRO (дни)", callback_data="admin_give_pro")],
        [InlineKeyboardButton(text="💎 Выдать ULTRA (дни)", callback_data="admin_give_ultra")],
        [InlineKeyboardButton(text="➕ Добавить дни", callback_data="admin_add_days")],
        [InlineKeyboardButton(text="🆓 Снять премиум", callback_data="admin_remove_prem")],
        [InlineKeyboardButton(text="🚫 Заблокировать", callback_data="admin_block")],
        [InlineKeyboardButton(text="✅ Разблокировать", callback_data="admin_unblock")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="◀️ В главное меню", callback_data="main_menu")],
    ])


# ==================== /start ====================

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    username = message.from_user.username

    referrer_id = None
    args = message.text.split(maxsplit=1)
    if len(args) > 1 and args[1].isdigit():
        potential_referrer = int(args[1])
        if potential_referrer != user_id:
            referrer_id = potential_referrer

    add_user(user_id, username, referrer_id)

    if not await check_subscription_or_ask(message, user_id):
        return

    await message.answer(
        "📱 <b>SMARTCHECK</b>\n\n"
        "«НЕ ХВАЛИМ ТЕЛЕФОН. ПРОВЕРЯЕМ ЕГО.»\n\n"
        "Я независимый аналитик смартфонов.\n"
        "Помогу подобрать телефон под твои задачи, бюджет и приоритеты — честно, без рекламы брендов.\n\n"
        "Выбери действие:",
        reply_markup=main_menu_keyboard(user_id),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "check_sub")
async def process_check_sub(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if await is_subscribed(user_id):
        update_subscription(user_id, 1)
        await callback.message.edit_text(
            "✅ Подписка подтверждена!\n\n"
            "📱 <b>SMARTCHECK</b>\n"
            "«НЕ ХВАЛИМ ТЕЛЕФОН. ПРОВЕРЯЕМ ЕГО.»\n\n"
            "Я независимый аналитик смартфонов.\n"
            "Помогу подобрать телефон под твои задачи и бюджет — честно, без рекламы брендов.\n\n"
            "Выбери действие:",
            reply_markup=main_menu_keyboard(user_id),
            parse_mode="HTML",
        )
    else:
        await callback.answer("Ты ещё не подписан. Подпишись и попробуй снова.", show_alert=True)
    await callback.answer()


@router.callback_query(F.data == "main_menu")
async def process_main_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback.from_user.id
    if not await check_subscription_or_ask(callback, user_id):
        await callback.answer()
        return

    await callback.message.edit_text(
        "📱 <b>SMARTCHECK</b>\n\n"
        "«НЕ ХВАЛИМ ТЕЛЕФОН. ПРОВЕРЯЕМ ЕГО.»\n\n"
        "Я независимый аналитик смартфонов.\n"
        "Помогу подобрать телефон под твои задачи и бюджет — честно, без рекламы брендов.\n\n"
        "Выбери действие:",
        reply_markup=main_menu_keyboard(user_id),
        parse_mode="HTML",
    )
    await callback.answer()


# ==================== ПОДБОР ТЕЛЕФОНА (FSM) ====================

@router.callback_query(F.data == "select_phone")
async def start_selection(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if not await check_subscription_or_ask(callback, user_id):
        await callback.answer()
        return

    await state.set_state(SelectionStates.currency)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇰🇿 KZT", callback_data="cur_KZT"),
            InlineKeyboardButton(text="🇷🇺 RUB", callback_data="cur_RUB"),
        ],
        [
            InlineKeyboardButton(text="🇺🇦 UAH", callback_data="cur_UAH"),
            InlineKeyboardButton(text="🇺🇸 USD", callback_data="cur_USD"),
        ],
        [
            InlineKeyboardButton(text="🇪🇺 EUR", callback_data="cur_EUR"),
            InlineKeyboardButton(text="🇬🇧 GBP", callback_data="cur_GBP"),
        ],
        [InlineKeyboardButton(text="🇹🇷 TRY", callback_data="cur_TRY")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")],
    ])
    await callback.message.edit_text(
        "💰 <b>Шаг 1/8 — Выбери валюту</b>",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cur_"), SelectionStates.currency)
async def process_currency(callback: CallbackQuery, state: FSMContext):
    currency = callback.data.split("_", 1)[1]
    await state.update_data(currency=currency)
    await state.set_state(SelectionStates.budget)

    await callback.message.edit_text(
        f"✅ Валюта: <b>{currency}</b>\n\n"
        "💵 <b>Шаг 2/8 — Введи бюджет</b>\n"
        "Напиши сумму только цифрами (например: 250000)",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(SelectionStates.budget)
async def process_budget(message: Message, state: FSMContext):
    budget_text = message.text.strip().replace(" ", "").replace(",", "").replace(".", "")
    if not budget_text.isdigit():
        await message.answer("Пожалуйста, введи бюджет только цифрами (например: 250000).")
        return

    await state.update_data(budget=budget_text)
    await state.set_state(SelectionStates.phone_class)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💵 Ультрабюджет", callback_data="class_Ультрабюджет")],
        [InlineKeyboardButton(text="💰 Бюджет", callback_data="class_Бюджет")],
        [InlineKeyboardButton(text="⚡ Субфлагман", callback_data="class_Субфлагман")],
        [InlineKeyboardButton(text="👑 Флагман", callback_data="class_Флагман")],
        [InlineKeyboardButton(text="💎 Ультрафлагман", callback_data="class_Ультрафлагман")],
        [InlineKeyboardButton(text="🤷 Не знаю", callback_data="class_Не знаю")],
        [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="main_menu")],
    ])
    await message.answer(
        f"✅ Бюджет: <b>{budget_text}</b>\n\n"
        "📱 <b>Шаг 3/8 — Выбери класс смартфона</b>",
        reply_markup=kb,
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("class_"), SelectionStates.phone_class)
async def process_class(callback: CallbackQuery, state: FSMContext):
    phone_class = callback.data.split("_", 1)[1]
    await state.update_data(phone_class=phone_class, priorities=[])
    await state.set_state(SelectionStates.priorities)

    kb = _build_priorities_keyboard([])
    await callback.message.edit_text(
        f"✅ Класс: <b>{phone_class}</b>\n\n"
        "🔥 <b>Шаг 4/8 — Выбери приоритеты</b>\n"
        "Можно выбрать несколько. «Баланс» нельзя сочетать с другими.\n"
        "После выбора нажми «✅ Готово».",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


def _build_priorities_keyboard(priorities: list) -> InlineKeyboardMarkup:
    def mark(name: str, emoji: str) -> str:
        return f"✅ {emoji} {name}" if name in priorities else f"{emoji} {name}"

    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=mark("Камера", "📸"), callback_data="prio_Камера"),
            InlineKeyboardButton(text=mark("Видео", "🎥"), callback_data="prio_Видео"),
        ],
        [
            InlineKeyboardButton(text=mark("Игры", "🎮"), callback_data="prio_Игры"),
            InlineKeyboardButton(text=mark("Батарея", "🔋"), callback_data="prio_Батарея"),
        ],
        [
            InlineKeyboardButton(text=mark("Защита", "💧"), callback_data="prio_Защита"),
            InlineKeyboardButton(text=mark("Охлаждение", "🧊"), callback_data="prio_Охлаждение"),
        ],
        [InlineKeyboardButton(text=mark("Баланс", "⚖️"), callback_data="prio_Баланс")],
        [InlineKeyboardButton(text="✅ Готово", callback_data="prio_done")],
        [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="main_menu")],
    ])


@router.callback_query(F.data.startswith("prio_"), SelectionStates.priorities)
async def process_priority(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    priorities: list = list(data.get("priorities", []))

    action = callback.data.split("_", 1)[1]

    if action == "done":
        if not priorities:
            await callback.answer("Выбери хотя бы один приоритет или «Баланс».", show_alert=True)
            return

        await state.set_state(SelectionStates.storage)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="128 ГБ", callback_data="storage_128")],
            [InlineKeyboardButton(text="256 ГБ", callback_data="storage_256")],
            [InlineKeyboardButton(text="512 ГБ", callback_data="storage_512")],
            [InlineKeyboardButton(text="1 ТБ+", callback_data="storage_1024")],
            [InlineKeyboardButton(text="🤷 Без разницы", callback_data="storage_any")],
            [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="main_menu")],
        ])
        await callback.message.edit_text(
            "✅ Приоритеты сохранены.\n\n"
            "💾 <b>Шаг 5/8 — Минимальный объём памяти</b>",
            reply_markup=kb,
            parse_mode="HTML",
        )
        await callback.answer()
        return

    if action == "Баланс":
        priorities = ["Баланс"]
    else:
        if "Баланс" in priorities:
            priorities.remove("Баланс")
        if action in priorities:
            priorities.remove(action)
        else:
            priorities.append(action)

    await state.update_data(priorities=priorities)

    selected = ", ".join(priorities) if priorities else "ничего"
    kb = _build_priorities_keyboard(priorities)

    await callback.message.edit_text(
        f"🔥 <b>Шаг 4/8 — Приоритеты</b>\n"
        f"Выбрано: <b>{selected}</b>\n\n"
        "Можно выбрать несколько. «Баланс» нельзя сочетать с другими.\n"
        "После выбора нажми «✅ Готово».",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


# ==================== ПРОДОЛЖЕНИЕ ПОДБОРА ====================

@router.callback_query(F.data.startswith("storage_"), SelectionStates.storage)
async def process_storage(callback: CallbackQuery, state: FSMContext):
    storage = callback.data.split("_", 1)[1]
    labels = {"128": "128 ГБ", "256": "256 ГБ", "512": "512 ГБ", "1024": "1 ТБ+", "any": "Без разницы"}
    await state.update_data(storage=labels.get(storage, storage))
    await state.set_state(SelectionStates.condition)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆕 Только новый", callback_data="cond_new")],
        [InlineKeyboardButton(text="♻️ Можно б/у", callback_data="cond_used")],
        [InlineKeyboardButton(text="🤷 Без разницы", callback_data="cond_any")],
        [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="main_menu")],
    ])
    await callback.message.edit_text(
        f"✅ Память: <b>{labels.get(storage, storage)}</b>\n\n"
        "📦 <b>Шаг 6/8 — Новый или б/у?</b>",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cond_"), SelectionStates.condition)
async def process_condition(callback: CallbackQuery, state: FSMContext):
    cond = callback.data.split("_", 1)[1]
    labels = {"new": "Только новый", "used": "Можно б/у", "any": "Без разницы"}
    await state.update_data(condition=labels.get(cond, cond))
    await state.set_state(SelectionStates.size)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Компактный (до 6.3\")", callback_data="size_compact")],
        [InlineKeyboardButton(text="📱 Средний (6.3–6.7\")", callback_data="size_medium")],
        [InlineKeyboardButton(text="📱 Большой (6.7\"+)", callback_data="size_large")],
        [InlineKeyboardButton(text="🤷 Без разницы", callback_data="size_any")],
        [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="main_menu")],
    ])
    await callback.message.edit_text(
        f"✅ Состояние: <b>{labels.get(cond, cond)}</b>\n\n"
        "📐 <b>Шаг 7/8 — Предпочтительный размер</b>",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("size_"), SelectionStates.size)
async def process_size(callback: CallbackQuery, state: FSMContext):
    size = callback.data.split("_", 1)[1]
    labels = {
        "compact": "Компактный",
        "medium": "Средний",
        "large": "Большой",
        "any": "Без разницы",
    }
    await state.update_data(size=labels.get(size, size))
    await state.set_state(SelectionStates.waterproof)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💧 Обязательно IP67/IP68", callback_data="water_yes")],
        [InlineKeyboardButton(text="Нет, не обязательно", callback_data="water_no")],
        [InlineKeyboardButton(text="🤷 Без разницы", callback_data="water_any")],
        [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="main_menu")],
    ])
    await callback.message.edit_text(
        f"✅ Размер: <b>{labels.get(size, size)}</b>\n\n"
        "💧 <b>Шаг 8/8 — Нужна ли защита от воды?</b>",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("water_"), SelectionStates.waterproof)
async def process_waterproof(callback: CallbackQuery, state: FSMContext):
    water = callback.data.split("_", 1)[1]
    labels = {"yes": "Обязательно", "no": "Не обязательно", "any": "Без разницы"}
    await state.update_data(waterproof=labels.get(water, water))

    data = await state.get_data()
    await callback.message.edit_text(
        "⏳ Анализирую рынок и подбираю варианты...\n"
        "Это может занять 10–25 секунд."
    )
    await callback.answer()

    currency = data.get("currency", "USD")
    budget = data.get("budget", "0")
    phone_class = data.get("phone_class", "Не знаю")
    priorities = data.get("priorities", [])
    storage = data.get("storage", "Без разницы")
    condition = data.get("condition", "Без разницы")
    size = data.get("size", "Без разницы")
    waterproof = data.get("waterproof", "Без разницы")

    # Расширенный промпт
    extra = (
        f"Минимальная память: {storage}. "
        f"Состояние: {condition}. "
        f"Размер: {size}. "
        f"Защита от воды: {waterproof}."
    )
    priorities_with_extra = list(priorities) + [extra]

    result = generate_recommendation_analysis(currency, budget, phone_class, priorities_with_extra)

    if len(result) > 4000:
        parts = [result[i:i + 4000] for i in range(0, len(result), 4000)]
        for part in parts:
            await callback.message.answer(part)
    else:
        await callback.message.answer(result)

    await callback.message.answer(
        "Готово! Можешь вернуться в меню или задать вопрос ИИ.",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )
    await state.clear()


# ==================== РАЗДЕЛЫ МЕНЮ ====================

@router.callback_query(F.data == "stub_compare")
async def process_compare(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if not await check_subscription_or_ask(callback, user_id):
        await callback.answer()
        return
    await state.clear()
    await callback.message.edit_text(
        "⚔️ <b>Сравнение смартфонов</b>\n\n"
        "Напиши две (или больше) модели через «vs» или запятую.\n\n"
        "Примеры:\n"
        "• Vivo X200 vs Xiaomi 15\n"
        "• Galaxy S25, iPhone 16 Pro, Pixel 9 Pro\n\n"
        "Я сравню их честно по ключевым параметрам.",
        reply_markup=back_to_menu_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "stub_ratings")
async def process_ratings(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if not await check_subscription_or_ask(callback, user_id):
        await callback.answer()
        return
    await state.clear()
    await callback.message.edit_text(
        "🏆 <b>Рейтинги</b>\n\n"
        "Напиши, какой рейтинг тебе нужен. Примеры:\n"
        "• Лучшие камерофоны 2025–2026\n"
        "• Лучшие для игр до 200к\n"
        "• Лучшая автономность в среднем сегменте\n"
        "• Топ флагманов по цене/качеству\n\n"
        "Я составлю честный рейтинг с пояснениями.",
        reply_markup=back_to_menu_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "stub_used")
async def process_used(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if not await check_subscription_or_ask(callback, user_id):
        await callback.answer()
        return
    await state.clear()
    await callback.message.edit_text(
        "♻️ <b>Проверка б/у смартфона</b>\n\n"
        "Чек-лист перед покупкой:\n\n"
        "✅ IMEI (проверка на чёрный список)\n"
        "✅ Экран (битые пиксели, шлейфы, оригинальность)\n"
        "✅ Камеры (все модули, автофокус, пятна)\n"
        "✅ Динамики и микрофоны\n"
        "✅ Разъёмы (зарядка, USB-режим)\n"
        "✅ Батарея (здоровье, циклы если доступно)\n"
        "✅ Связь, Wi-Fi, Bluetooth, GPS\n"
        "✅ Биометрия (отпечаток / Face ID)\n"
        "✅ Следы ремонта и влаги\n"
        "✅ Комплектация и коробка\n\n"
        "Напиши модель — дам специфические советы именно по ней.",
        reply_markup=back_to_menu_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "stub_pro")
async def process_pro(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if not await check_subscription_or_ask(callback, user_id):
        await callback.answer()
        return
    await state.clear()
    await callback.message.edit_text(
        "⭐ <b>PRO / ULTRA</b>\n\n"
        "<b>⭐ PRO — 149 ⭐ / месяц</b>\n"
        "• Безлимитный подбор и сравнения\n"
        "• История цен и уведомления\n"
        "• Персональные веса критериев\n"
        "• Smart Upgrade (сравнение с твоим текущим телефоном)\n"
        "• Без рекламы\n\n"
        "<b>💎 ULTRA — 299 ⭐ / месяц</b>\n"
        "• Всё из PRO\n"
        "• SmartCheck Lab (глубокий разбор железа)\n"
        "• Thermal / Gaming / Camera / Video Lab\n"
        "• Hidden Downsides\n"
        "• ULTRA Rankings\n\n"
        "Оплата через Telegram Stars появится в ближайшем обновлении.\n"
        "Пока премиум можно получить через рефералов или у админа.",
        reply_markup=back_to_menu_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


# ==================== РЕФЕРАЛЫ ====================

@router.callback_query(F.data == "referrals")
async def process_referrals(callback: CallbackQuery):
    user_id = callback.from_user.id
    if not await check_subscription_or_ask(callback, user_id):
        await callback.answer()
        return

    count = get_referral_count(user_id)
    link = f"https://t.me/{BOT_USERNAME}?start={user_id}"

    text = (
        "🎁 <b>Рефералы и Бонусы</b>\n\n"
        f"Твоя персональная ссылка:\n<code>{link}</code>\n\n"
        f"Приглашено и подписано: <b>{count}</b>\n\n"
        "Система наград (PRO-дни за приглашения) будет активирована в ближайшем обновлении."
    )
    await callback.message.edit_text(
        text,
        reply_markup=back_to_menu_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


# ==================== ЗАДАТЬ ВОПРОС ИИ ====================

@router.callback_query(F.data == "ask_ai")
async def process_ask_ai(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if not await check_subscription_or_ask(callback, user_id):
        await callback.answer()
        return

    await state.clear()
    await callback.message.edit_text(
        "🤖 <b>Задать вопрос ИИ</b>\n\n"
        "Напиши любой вопрос про смартфоны.\n"
        "Я отвечу честно и без прикрас.\n\n"
        "Примеры:\n"
        "• «Стоит ли брать Vivo X200 за 250к?»\n"
        "• «Чем Galaxy S25 отличается от S24?»\n"
        "• «Какой телефон лучше для игр до 150 000 ₸?»",
        reply_markup=back_to_menu_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(F.text, StateFilter(None))
async def handle_text_question(message: Message, state: FSMContext):
    """Обрабатывает свободный текст только когда пользователь НЕ в FSM."""
    user_id = message.from_user.id
    if not await check_subscription_or_ask(message, user_id):
        return

    wait_msg = await message.answer("⏳ Думаю...")
    answer = ask_gemini(message.text)

    try:
        await wait_msg.delete()
    except Exception:
        pass

    if len(answer) > 4000:
        parts = [answer[i:i + 4000] for i in range(0, len(answer), 4000)]
        for part in parts:
            await message.answer(part)
    else:
        await message.answer(answer)

    await message.answer(
        "Можешь задать ещё вопрос или вернуться в меню.",
        reply_markup=main_menu_keyboard(user_id),
    )


# ==================== АДМИН-ПАНЕЛЬ ====================

@admin_router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return
    await state.clear()
    await message.answer(
        "👑 <b>SMARTCHECK ADMIN</b>\n\nВыбери действие:",
        reply_markup=admin_menu_keyboard(),
        parse_mode="HTML",
    )


@admin_router.callback_query(F.data == "admin_panel")
async def admin_panel(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        "👑 <b>SMARTCHECK ADMIN</b>\n\nВыбери действие:",
        reply_markup=admin_menu_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@admin_router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    stats = get_stats()
    text = (
        "📊 <b>СТАТИСТИКА SMARTCHECK</b>\n\n"
        f"👥 Всего пользователей: <b>{stats['total']}</b>\n"
        f"✅ Подписаны на канал: <b>{stats['subscribed']}</b>\n"
        f"⭐ PRO: <b>{stats['pro']}</b>\n"
        f"💎 ULTRA: <b>{stats['ultra']}</b>\n"
        f"🚫 Заблокированы: <b>{stats['blocked']}</b>\n"
        f"🎁 Пришли по рефералу: <b>{stats['with_referrer']}</b>"
    )
    await callback.message.edit_text(
        text,
        reply_markup=admin_menu_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@admin_router.callback_query(F.data == "admin_search")
async def admin_search_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_search)
    await callback.message.edit_text(
        "🔍 Введи <b>Telegram ID</b> или <b>username</b> пользователя:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Отмена", callback_data="admin_panel")]
        ]),
        parse_mode="HTML",
    )
    await callback.answer()


@admin_router.message(AdminStates.waiting_search)
async def admin_search_process(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    query = message.text.strip().lstrip("@")
    results = search_users(query)

    if not results:
        await message.answer("Никого не найдено.", reply_markup=admin_menu_keyboard())
        await state.clear()
        return

    lines = []
    for row in results:
        uid, uname, tariff, until, sub, blocked = row
        status = "🚫" if blocked else ("✅" if sub else "❌")
        prem = f" до {until}" if until else ""
        lines.append(
            f"{status} <code>{uid}</code> @{uname or '—'} | {tariff}{prem}"
        )

    text = "🔍 <b>Результаты поиска:</b>\n\n" + "\n".join(lines)
    await message.answer(text, reply_markup=admin_menu_keyboard(), parse_mode="HTML")
    await state.clear()


async def _ask_user_id(callback: CallbackQuery, state: FSMContext, action: str, title: str):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_user_id)
    await state.update_data(admin_action=action)
    await callback.message.edit_text(
        f"{title}\n\nВведи <b>Telegram ID</b> пользователя:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Отмена", callback_data="admin_panel")]
        ]),
        parse_mode="HTML",
    )
    await callback.answer()


@admin_router.callback_query(F.data == "admin_give_pro")
async def admin_give_pro(callback: CallbackQuery, state: FSMContext):
    await _ask_user_id(callback, state, "give_pro", "⭐ Выдать PRO\n\nСначала укажи пользователя, потом количество дней.")


@admin_router.callback_query(F.data == "admin_give_ultra")
async def admin_give_ultra(callback: CallbackQuery, state: FSMContext):
    await _ask_user_id(callback, state, "give_ultra", "💎 Выдать ULTRA\n\nСначала укажи пользователя, потом количество дней.")


@admin_router.callback_query(F.data == "admin_remove_prem")
async def admin_remove_prem(callback: CallbackQuery, state: FSMContext):
    await _ask_user_id(callback, state, "remove_prem", "🆓 Снять премиум")


@admin_router.callback_query(F.data == "admin_block")
async def admin_block(callback: CallbackQuery, state: FSMContext):
    await _ask_user_id(callback, state, "block", "🚫 Заблокировать пользователя")


@admin_router.callback_query(F.data == "admin_unblock")
async def admin_unblock(callback: CallbackQuery, state: FSMContext):
    await _ask_user_id(callback, state, "unblock", "✅ Разблокировать пользователя")


@admin_router.callback_query(F.data == "admin_add_days")
async def admin_add_days_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_user_id)
    await state.update_data(admin_action="add_days")
    await callback.message.edit_text(
        "➕ Добавить дни премиума\n\nВведи <b>Telegram ID</b> пользователя:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Отмена", callback_data="admin_panel")]
        ]),
        parse_mode="HTML",
    )
    await callback.answer()


@admin_router.message(AdminStates.waiting_user_id)
async def admin_user_id_process(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    text = message.text.strip()
    if not text.isdigit():
        await message.answer("Нужен числовой Telegram ID.")
        return

    target_id = int(text)
    data = await state.get_data()
    action = data.get("admin_action")

    user = get_user(target_id)
    if not user and action not in ("give_pro", "give_ultra"):
        await message.answer("Пользователь не найден в базе.", reply_markup=admin_menu_keyboard())
        await state.clear()
        return

    if action in ("give_pro", "give_ultra", "add_days"):
        if action in ("give_pro", "give_ultra") and not user:
            add_user(target_id, None)
        await state.update_data(target_id=target_id)
        await state.set_state(AdminStates.waiting_days)

        if action == "give_pro":
            prompt = f"Пользователь: <code>{target_id}</code>\n\n⭐ Сколько дней выдать <b>PRO</b>? (число)"
        elif action == "give_ultra":
            prompt = f"Пользователь: <code>{target_id}</code>\n\n💎 Сколько дней выдать <b>ULTRA</b>? (число)"
        else:
            prompt = f"Пользователь: <code>{target_id}</code>\n\n➕ Сколько дней добавить к текущему премиуму? (число)"

        await message.answer(prompt, parse_mode="HTML")
        return

    elif action == "remove_prem":
        set_tariff(target_id, "FREE")
        await message.answer(
            f"✅ У пользователя <code>{target_id}</code> снят премиум.",
            parse_mode="HTML",
            reply_markup=admin_menu_keyboard()
        )

    elif action == "block":
        set_blocked(target_id, True)
        await message.answer(
            f"🚫 Пользователь <code>{target_id}</code> заблокирован.",
            parse_mode="HTML",
            reply_markup=admin_menu_keyboard()
        )

    elif action == "unblock":
        set_blocked(target_id, False)
        await message.answer(
            f"✅ Пользователь <code>{target_id}</code> разблокирован.",
            parse_mode="HTML",
            reply_markup=admin_menu_keyboard()
        )

    await state.clear()


@admin_router.message(AdminStates.waiting_days)
async def admin_days_process(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    text = message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer("Введи положительное число дней (например: 7, 30, 90).")
        return

    days = int(text)
    data = await state.get_data()
    target_id = data.get("target_id")
    action = data.get("admin_action")

    if action == "give_pro":
        set_tariff(target_id, "PRO", days)
        await message.answer(
            f"✅ Пользователю <code>{target_id}</code> выдан <b>PRO</b> на <b>{days}</b> дн.",
            parse_mode="HTML",
            reply_markup=admin_menu_keyboard()
        )
        try:
            await bot.send_message(
                target_id,
                f"⭐ Тебе выдан тариф <b>PRO</b> на <b>{days}</b> дней!",
                parse_mode="HTML"
            )
        except Exception:
            pass

    elif action == "give_ultra":
        set_tariff(target_id, "ULTRA", days)
        await message.answer(
            f"✅ Пользователю <code>{target_id}</code> выдан <b>ULTRA</b> на <b>{days}</b> дн.",
            parse_mode="HTML",
            reply_markup=admin_menu_keyboard()
        )
        try:
            await bot.send_message(
                target_id,
                f"💎 Тебе выдан тариф <b>ULTRA</b> на <b>{days}</b> дней!",
                parse_mode="HTML"
            )
        except Exception:
            pass

    elif action == "add_days":
        add_premium_days(target_id, days)
        await message.answer(
            f"✅ Пользователю <code>{target_id}</code> добавлено <b>{days}</b> дней премиума.",
            parse_mode="HTML",
            reply_markup=admin_menu_keyboard()
        )
        try:
            await bot.send_message(
                target_id,
                f"⭐ Тебе добавлено <b>{days}</b> дней премиума!",
                parse_mode="HTML"
            )
        except Exception:
            pass

    else:
        await message.answer("Неизвестное действие.", reply_markup=admin_menu_keyboard())

    await state.clear()


@admin_router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_broadcast)
    await callback.message.edit_text(
        "📢 <b>Рассылка</b>\n\nОтправь сообщение, которое нужно разослать всем пользователям.\n"
        "Поддерживается HTML.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Отмена", callback_data="admin_panel")]
        ]),
        parse_mode="HTML",
    )
    await callback.answer()


@admin_router.message(AdminStates.waiting_broadcast)
async def admin_broadcast_process(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    user_ids = get_all_user_ids()
    total = len(user_ids)
    success = 0
    fail = 0

    status_msg = await message.answer(f"⏳ Рассылка начата... 0/{total}")

    for i, uid in enumerate(user_ids, 1):
        try:
            await bot.send_message(uid, message.text, parse_mode="HTML")
            success += 1
        except Exception:
            fail += 1

        if i % 25 == 0:
            try:
                await status_msg.edit_text(f"⏳ Рассылка... {i}/{total}")
            except Exception:
                pass
            await asyncio.sleep(0.05)

    await status_msg.edit_text(
        f"✅ Рассылка завершена!\n\n"
        f"Всего: {total}\n"
        f"Успешно: {success}\n"
        f"Ошибок: {fail}"
    )
    await message.answer("Готово.", reply_markup=admin_menu_keyboard())
    await state.clear()


# ==================== ЗАПУСК ====================

async def run_bot():
    init_db()
    print(f"SmartCheck bot starting... Admins: {ADMIN_IDS}")
    await dp.start_polling(bot)
