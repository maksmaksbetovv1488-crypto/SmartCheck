import sqlite3
from typing import Optional, Tuple, Any, List
from datetime import datetime, timedelta


DB_NAME = "smartcheck.db"


def init_db() -> None:
    """Создаёт/обновляет таблицы."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            referrer_id INTEGER,
            is_subscribed INTEGER DEFAULT 0,
            tariff TEXT DEFAULT 'FREE',
            premium_until TEXT,
            is_blocked INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # На случай, если таблица уже существовала без новых полей
    for column, typedef in [
        ("tariff", "TEXT DEFAULT 'FREE'"),
        ("premium_until", "TEXT"),
        ("is_blocked", "INTEGER DEFAULT 0"),
        ("created_at", "TEXT DEFAULT CURRENT_TIMESTAMP"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE users ADD COLUMN {column} {typedef}")
        except sqlite3.OperationalError:
            pass  # колонка уже есть

    conn.commit()
    conn.close()


def add_user(user_id: int, username: Optional[str], referrer_id: Optional[int] = None) -> None:
    """Добавляет пользователя, если его ещё нет."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if cursor.fetchone() is None:
        cursor.execute(
            """INSERT INTO users 
               (user_id, username, referrer_id, is_subscribed, tariff, is_blocked) 
               VALUES (?, ?, ?, 0, 'FREE', 0)""",
            (user_id, username, referrer_id)
        )
        conn.commit()
    else:
        # Обновляем username, если изменился
        cursor.execute(
            "UPDATE users SET username = ? WHERE user_id = ?",
            (username, user_id)
        )
        conn.commit()
    conn.close()


def get_user(user_id: int) -> Optional[Tuple[Any, ...]]:
    """
    Возвращает:
    (user_id, username, referrer_id, is_subscribed, tariff, premium_until, is_blocked, created_at)
    """
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        """SELECT user_id, username, referrer_id, is_subscribed, 
                  tariff, premium_until, is_blocked, created_at 
           FROM users WHERE user_id = ?""",
        (user_id,)
    )
    row = cursor.fetchone()
    conn.close()
    return row


def update_subscription(user_id: int, status: int) -> None:
    """Обновляет статус подписки на канал (0/1)."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET is_subscribed = ? WHERE user_id = ?",
        (status, user_id)
    )
    conn.commit()
    conn.close()


def get_referral_count(user_id: int) -> int:
    """Считает приглашённых с is_subscribed = 1."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM users WHERE referrer_id = ? AND is_subscribed = 1",
        (user_id,)
    )
    count = cursor.fetchone()[0]
    conn.close()
    return count


def set_tariff(user_id: int, tariff: str, days: int = 30) -> None:
    """
    Устанавливает тариф (FREE / PRO / ULTRA).
    days — на сколько дней выдать премиум.
    """
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    if tariff.upper() == "FREE":
        cursor.execute(
            "UPDATE users SET tariff = 'FREE', premium_until = NULL WHERE user_id = ?",
            (user_id,)
        )
    else:
        until = (datetime.utcnow() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "UPDATE users SET tariff = ?, premium_until = ? WHERE user_id = ?",
            (tariff.upper(), until, user_id)
        )
    conn.commit()
    conn.close()


def add_premium_days(user_id: int, days: int) -> None:
    """Добавляет дни к текущему премиуму (или создаёт новый)."""
    user = get_user(user_id)
    if not user:
        return

    current_until = user[5]  # premium_until
    now = datetime.utcnow()

    if current_until:
        try:
            until_dt = datetime.strptime(current_until, "%Y-%m-%d %H:%M:%S")
            if until_dt > now:
                new_until = until_dt + timedelta(days=days)
            else:
                new_until = now + timedelta(days=days)
        except ValueError:
            new_until = now + timedelta(days=days)
    else:
        new_until = now + timedelta(days=days)

    tariff = user[4] if user[4] in ("PRO", "ULTRA") else "PRO"

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET tariff = ?, premium_until = ? WHERE user_id = ?",
        (tariff, new_until.strftime("%Y-%m-%d %H:%M:%S"), user_id)
    )
    conn.commit()
    conn.close()


def set_blocked(user_id: int, blocked: bool) -> None:
    """Блокирует / разблокирует пользователя."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET is_blocked = ? WHERE user_id = ?",
        (1 if blocked else 0, user_id)
    )
    conn.commit()
    conn.close()


def get_stats() -> dict:
    """Возвращает статистику для админ-панели."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM users")
    total = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE is_subscribed = 1")
    subscribed = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE tariff = 'PRO'")
    pro = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE tariff = 'ULTRA'")
    ultra = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE is_blocked = 1")
    blocked = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE referrer_id IS NOT NULL")
    with_referrer = cursor.fetchone()[0]

    conn.close()

    return {
        "total": total,
        "subscribed": subscribed,
        "pro": pro,
        "ultra": ultra,
        "blocked": blocked,
        "with_referrer": with_referrer,
    }


def get_all_user_ids() -> List[int]:
    """Возвращает список всех user_id (для рассылки)."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE is_blocked = 0")
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows]


def search_users(query: str, limit: int = 20) -> List[Tuple]:
    """Поиск пользователей по user_id или username."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    if query.isdigit():
        cursor.execute(
            """SELECT user_id, username, tariff, premium_until, is_subscribed, is_blocked 
               FROM users WHERE user_id = ?""",
            (int(query),)
        )
    else:
        cursor.execute(
            """SELECT user_id, username, tariff, premium_until, is_subscribed, is_blocked 
               FROM users WHERE username LIKE ? LIMIT ?""",
            (f"%{query}%", limit)
        )
    rows = cursor.fetchall()
    conn.close()
    return rows
