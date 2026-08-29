MAX_FREE_ANALYSIS = 5
ADMIN_ID = 1724691240  # ← вставь свой ID

from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from openai import OpenAI
from datetime import date

import psycopg
import os
import re

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
TOKEN = os.getenv("TELEGRAM_TOKEN")
application = ApplicationBuilder().token(TOKEN).build()
DATABASE_URL = os.getenv("DATABASE_URL")


def test_db():
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            result = cur.fetchone()
            print("DATABASE OK:", result)

def init_db():
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    telegram_id BIGINT UNIQUE NOT NULL,
                    username TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS energy_matrix_access (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER UNIQUE NOT NULL
                        REFERENCES users(id)
                        ON DELETE CASCADE,
                    calculations_balance INTEGER DEFAULT 0,
                    unlimited BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

        conn.commit()

    print("DATABASE TABLES READY")

def get_or_create_user(telegram_id, username):
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id
                FROM users
                WHERE telegram_id = %s
            """, (telegram_id,))

            row = cur.fetchone()

            if row:
                return row[0]

            cur.execute("""
                INSERT INTO users (telegram_id, username)
                VALUES (%s, %s)
                RETURNING id
            """, (telegram_id, username))

            user_id = cur.fetchone()[0]

        conn.commit()

    return user_id

def get_or_create_energy_access(user_id):
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT calculations_balance, unlimited
                FROM energy_matrix_access
                WHERE user_id = %s
            """, (user_id,))

            row = cur.fetchone()

            if row:
                return row

            cur.execute("""
                INSERT INTO energy_matrix_access (
                    user_id,
                    calculations_balance,
                    unlimited
                )
                VALUES (%s, 0, FALSE)
                RETURNING calculations_balance, unlimited
            """, (user_id,))

            access = cur.fetchone()

        conn.commit()

    return access

def has_energy_access(user_id):
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT calculations_balance, unlimited
                FROM energy_matrix_access
                WHERE user_id = %s
            """, (user_id,))

            row = cur.fetchone()

            if not row:
                return False

            balance, unlimited = row

            return unlimited or balance > 0

def get_energy_neighbors(e, f, s):
    current = (e, f, s)
    neighbors = []

    for index in range(3):
        for delta in (-1, 1):

            candidate = list(current)
            candidate[index] += delta

            if candidate[index] not in (-1, 0, 1):
                continue

            candidate = tuple(candidate)

            state_name = ENERGY_MATRIX_STATES.get(candidate)

            if state_name:
                neighbors.append({
                    "coords": candidate,
                    "state": state_name
                })

    return neighbors

def format_energy_neighbors(e, f, s):
    neighbors = get_energy_neighbors(e, f, s)

    lines = ["СОСЕДНИЕ СОСТОЯНИЯ:"]

    for item in neighbors:
        coords = item["coords"]

        coord_text = (
            f"({coords[0]:+d},{coords[1]:+d},{coords[2]:+d})"
            .replace("+0", "0")
        )

        lines.append(
            f"{coord_text} — {item['state']}"
        )

    return "\n".join(lines)

def replace_energy_neighbors(result):

    e_match = re.search(
        r"^E:\s*([+-]?\d+)",
        result,
        re.MULTILINE
    )

    f_match = re.search(
        r"^F:\s*([+-]?\d+)",
        result,
        re.MULTILINE
    )

    s_match = re.search(
        r"^S:\s*([+-]?\d+)",
        result,
        re.MULTILINE
    )

    if not e_match or not f_match or not s_match:
        return result

    e = int(e_match.group(1))
    f = int(f_match.group(1))
    s = int(s_match.group(1))

    correct_neighbors = format_energy_neighbors(
        e,
        f,
        s
    )

    pattern = (
        r"СОСЕДНИЕ СОСТОЯНИЯ:.*?"
        r"(?=🧭\s*РЕКОМЕНДУЕМЫЙ ПЕРЕХОД:)"
    )

    if re.search(pattern, result, re.DOTALL):

        result = re.sub(
            pattern,
            correct_neighbors + "\n\n",
            result,
            flags=re.DOTALL
        )

    return result


def add_energy_calculations(user_id, amount):
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE energy_matrix_access
                SET calculations_balance = calculations_balance + %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_id = %s
            """, (amount, user_id))

        conn.commit()

def has_energy_access(user_id):
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT calculations_balance, unlimited
                FROM energy_matrix_access
                WHERE user_id = %s
            """, (user_id,))

            row = cur.fetchone()

            if not row:
                return False

            balance, unlimited = row

            return unlimited or balance > 0

ENERGY_MATRIX_STATES = {
    (-1, -1, -1): "ИСТОЩЕНИЕ",
    (-1, -1,  0): "ДЕФИЦИТ",
    (-1, -1, +1): "БЕССИЛИЕ",

    (-1,  0, -1): "ВОССТАНОВЛЕНИЕ",
    (-1,  0,  0): "ЗАМЕДЛЕНИЕ",
    (-1,  0, +1): "ЗАТРУДНЕНИЕ",

    (-1, +1, -1): "РЫВОК",
    (-1, +1,  0): "НАПРЯЖЁННОЕ ДЕЙСТВИЕ",
    (-1, +1, +1): "ФОРСИРОВАНИЕ",

    (0, -1, -1): "ПАУЗА",
    (0, -1,  0): "ЗАСТОЙ",
    (0, -1, +1): "БЛОКИРОВКА",

    (0,  0, -1): "СВОБОДНОЕ ДВИЖЕНИЕ",
    (0,  0,  0): "РАВНОВЕСИЕ",
    (0,  0, +1): "ЗАТРУДНЁННОЕ ДВИЖЕНИЕ",

    (0, +1, -1): "ПОТОК",
    (0, +1,  0): "АКТИВНОЕ ДВИЖЕНИЕ",
    (0, +1, +1): "НАПРЯЖЕНИЕ",

    (+1, -1, -1): "ИЗБЫТОК",
    (+1, -1,  0): "НАКОПЛЕНИЕ",
    (+1, -1, +1): "УДЕРЖИВАНИЕ",

    (+1,  0, -1): "СВОБОДНЫЙ РЕСУРС",
    (+1,  0,  0): "СТАБИЛЬНОСТЬ",
    (+1,  0, +1): "НАПРЯЖЁННОЕ УДЕРЖИВАНИЕ",

    (+1, +1, -1): "МОЩНЫЙ ПОТОК",
    (+1, +1,  0): "ИНТЕНСИВНОЕ ДВИЖЕНИЕ",
    (+1, +1, +1): "ПЕРЕНАПРЯЖЕНИЕ",
}


def format_energy_value(value):
    if value > 0:
        return f"+{value}"
    return str(value)


def format_energy_coords(coords):
    e, f, s = coords

    return (
        f"({format_energy_value(e)},"
        f"{format_energy_value(f)},"
        f"{format_energy_value(s)})"
    )


def get_energy_neighbors(e, f, s):
    current = (e, f, s)
    neighbors = []

    for index in range(3):
        for delta in (-1, 1):

            candidate = list(current)
            candidate[index] += delta

            # Координата может быть только -1, 0 или +1
            if candidate[index] not in (-1, 0, 1):
                continue

            candidate = tuple(candidate)

            state_name = ENERGY_MATRIX_STATES.get(candidate)

            if state_name:
                neighbors.append(
                    (candidate, state_name)
                )

    return neighbors


def format_energy_neighbors(e, f, s):
    neighbors = get_energy_neighbors(e, f, s)

    lines = ["СОСЕДНИЕ СОСТОЯНИЯ:"]

    for coords, state_name in neighbors:
        lines.append(
            f"{format_energy_coords(coords)} — {state_name}"
        )

    return "\n".join(lines)

def summarize_energy_goal(goal, clarifications=None):
    clarifications = clarifications or []

    user_content = f"Исходный запрос пользователя:\n{goal}\n"

    if clarifications:
        user_content += "\nУточнения пользователя:\n"

        for i, clarification in enumerate(
            clarifications,
            start=1
        ):
            user_content += f"{i}. {clarification}\n"

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {
                "role": "system",
                "content": """
Сформулируй цель пользователя для диагностики по Энергоматрице.

Используй только информацию, которую сообщил пользователь.

Не добавляй предположений о его чувствах,
мотивах, причинах или обстоятельствах.

Цель должна:
— отражать, что пользователь хочет изменить или получить;
— учитывать исходный запрос и уточняющие ответы;
— быть понятной и конкретной;
— состоять из одного короткого предложения;
— не содержать координаты E, F, S;
— не содержать диагноз;
— не содержать рекомендацию.

Верни только формулировку цели.
Без вступления и пояснений.
"""
            },
            {
                "role": "user",
                "content": user_content
            }
        ]
    )

    return response.choices[0].message.content.strip()
    
def run_energy_matrix_analysis(
    goal,
    clarifications=None,
    force_complete=False
):
    clarifications = clarifications or []

    user_content = f"Цель пользователя: {goal}\n"

    if clarifications:
        user_content += "\nОтветы пользователя на уточняющие вопросы:\n"

        for i, clarification in enumerate(clarifications, start=1):
            user_content += f"{i}. {clarification}\n"

    if force_complete:
        user_content += """
Уточняющие вопросы больше задавать нельзя.
Обязательно заверши диагностику.
Верни STATUS: COMPLETE.
Выбери наиболее обоснованные значения E, F и S
на основании всей полученной информации.
"""

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": ENERGY_MATRIX_PROMPT},
            {"role": "user", "content": user_content}
        ]
    )

    return response.choices[0].message.content
    
def use_energy_calculation(user_id):
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE energy_matrix_access
                SET calculations_balance = calculations_balance - 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_id = %s
                  AND calculations_balance > 0
                  AND unlimited = FALSE
                RETURNING calculations_balance
            """, (user_id,))

            row = cur.fetchone()

        conn.commit()

    return row[0] if row else None

async def finish_energy_matrix(
    update,
    context,
    result,
    user_id
):
    # 1. Получаем координаты из технической части ответа
    e_match = re.search(r"^E:\s*([+-]?\d+)", result, re.MULTILINE)
    f_match = re.search(r"^F:\s*([+-]?\d+)", result, re.MULTILINE)
    s_match = re.search(r"^S:\s*([+-]?\d+)", result, re.MULTILINE)

    if not e_match or not f_match or not s_match:
        await update.message.reply_text(
            "Не удалось определить координаты Энергоматрицы."
        )
        return

    e = int(e_match.group(1))
    f = int(f_match.group(1))
    s = int(s_match.group(1))

    # 2. Название состояния берём из фиксированной таблицы
    state_name = ENERGY_MATRIX_STATES.get(
        (e, f, s),
        "НЕИЗВЕСТНОЕ СОСТОЯНИЕ"
    )

    # 3. Цель берём из сохранённого контекста
    goal = context.user_data.get(
        "energy_goal",
        "Цель не указана"
    )

    # 4. Сначала исправляем соседние состояния
    processed_result = replace_energy_neighbors(result)

    # 5. Убираем служебные строки
    body = re.sub(
        r"^(STATUS: COMPLETE|"
        r"E:\s*[+-]?\d+|"
        r"F:\s*[+-]?\d+|"
        r"S:\s*[+-]?\d+|"
        r"STATE:.*|"
        r"EXPLANATION:.*|"
        r"NEXT_STEP:.*)\s*$",
        "",
        processed_result,
        flags=re.MULTILINE
    ).strip()

    # 6. Если модель сама сформировала верхнюю карточку,
    # убираем её, чтобы не было дубля
    body = re.sub(
        r"🎯\s*ЦЕЛЬ:.*?"
        r"КОД:\s*\n?"
        r"\(E,F,S\)\s*=\s*\([^)]+\)\s*",
        "",
        body,
        flags=re.DOTALL
    ).strip()

    # 7. Формируем стабильную верхнюю карточку сами
    header = (
        f"🎯 ЦЕЛЬ:\n"
        f"{goal}\n\n"
        f"⚡️ ТЕКУЩЕЕ СОСТОЯНИЕ:\n"
        f"{state_name}\n\n"
        f"КОД:\n"
        f"(E,F,S) = {format_energy_coords((e, f, s))}"
    )

    user_result = header + "\n\n" + body

    # 8. Списываем расчёт только после успешной диагностики
    remaining = use_energy_calculation(user_id)

    await update.message.reply_text(user_result)

    # 9. Показываем остаток
    if remaining is not None and remaining > 0:
        context.user_data["state"] = "ENERGY_MATRIX_COMPLETE"

        await update.message.reply_text(
            f"⚡ Осталось расчётов: {remaining}\n\n"
            "Хотите сделать ещё один расчёт?",
            reply_markup=energy_continue_keyboard
        )

    elif remaining == 0:
        context.user_data["state"] = "WAITING_FOR_SITUATION"

        await update.message.reply_text(
            "⚡ Это был последний доступный расчёт.\n\n"
            "Возвращаемся в главное меню 🐸",
            reply_markup=reply_markup
        )

    else:
        # unlimited
        context.user_data["state"] = "ENERGY_MATRIX_COMPLETE"

        await update.message.reply_text(
            "Хотите сделать ещё один расчёт?",
            reply_markup=energy_continue_keyboard
        )


async def start(update, context):
    print("START OK")

    telegram_id = update.effective_user.id
    username = update.effective_user.username

    user_id = get_or_create_user(
        telegram_id=telegram_id,
        username=username
    )
    
    # add_energy_calculations(user_id, 10)
    balance, unlimited = get_or_create_energy_access(user_id)
    
    print("USER ID IN DB:", user_id)
    print("ENERGY ACCESS:")
    print("balance =", balance)
    print("unlimited =", unlimited)

    

    await update.message.reply_text(
        "Бот работает 🚀",
        reply_markup=reply_markup
    )

async def debug(update, context):
    print("UPDATE RECEIVED")

SYSTEM_PROMPT = """
Ты — консультант системы «Квантовая Лягушка».
Ты помогаешь человеку разобраться в ситуации через ясность, глубину и конкретные действия. 
Твой стиль: — дружелюбный, живой, человеческий — поддерживающий, но без излишней мягкости — 
профессиональный, с ощущением уверенности — без канцелярита и сухости — пишешь как умный, 
спокойный собеседник Ты не оцениваешь и не критикуешь. Ты помогаешь увидеть картину шире. 
Отвечай в 3 ролях: 

Любая ситуация — это набор веток вероятностей.

Отвечай в 3 ролях:

🔮 Хранитель — смысл и баланс  
🧱 Археолог — причины  
🐸 Лягушка — 1 конкретное действие  

Формат:

🔮 Хранитель — смысл и баланс:
...

🧱 ААрхеолог — причины:
...

🐸 Лягушка — Ветки вероятности:
...
 

🔮 Хранитель Покажи общий смысл ситуации, баланс, более широкую картину. 
Мягко, но точно. 
🧱 Археолог Разбери причины, паттерны, что могло к этому привести. Глубже, но без перегруза. 
🐸 Лягушка Работает по алгоритму выбора действия: 
1. Определи 4 возможные ветки: — 3 активных варианта действий — 1 ветка «ничего не делать» 
(инерция, оставить всё как есть) 
2. Для каждой ветки оцени: 
— усилия (насколько сложно) 
— риск (что можно потерять)
 — энергия (насколько это ресурсно или истощающе) 
 — вероятность успеха (в %, реалистично)
   — скрытая цена (что человек платит неочевидно: время, упущенные возможности, 
   эмоциональное состояние) 
   3. Кратко опиши каждую ветку. 
   4. Выбери самую перспективную ветку: 
   — с лучшим балансом результат / стоимость / вероятность / скрытая цена 
   — не обязательно самую лёгкую, а самую разумную 
   5. Дай 1 конкретное действие на ближайшие 24 часа, связанное с выбранной веткой. 
    
   Важно: — избегай общих фраз и клише — пиши живым языком — не будь слишком длинным 
   — Лягушка = всегда 1 чёткое действие. 
   Не делай ветки слишком абстрактными — они должны быть реальными и применимыми. 
   Пиши так, чтобы человек чувствовал: «меня поняли».
"""

with open("energy_matrix_prompt.txt", "r", encoding="utf-8") as file:
    ENERGY_MATRIX_PROMPT = file.read()
    

keyboard = [
    ["🔍 Посмотреть глубже", "🔀 Другие варианты"],
    ["⚡ Энергоматрица"],
    ["🆕 Новый разбор"]
]

energy_continue_keyboard = ReplyKeyboardMarkup(
    [
        ["⚡ Да, ещё расчёт"],
        ["↩️ Нет, вернуться в меню"]
    ],
    resize_keyboard=True
)

energy_goal_confirm_keyboard = ReplyKeyboardMarkup(
    [
        ["✅ Да, продолжить расчёт"],
        ["✏️ Нет, скорректировать цель"]
    ],
    resize_keyboard=True
)

reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

energy_no_balance_keyboard = InlineKeyboardMarkup([
    [
        InlineKeyboardButton(
            "💳 Получить расчёт",
            callback_data="buy_energy"
        )
    ]
])



async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text        
    state = context.user_data.get("state", "WAITING_FOR_SITUATION")

    telegram_id = update.effective_user.id
    username = update.effective_user.username

    user_id = get_or_create_user(
        telegram_id=telegram_id,
        username=username
    )

    get_or_create_energy_access(user_id)
    

    # ⚡ Энергоматрица
    if user_text == "⚡ Энергоматрица":
        telegram_id = update.effective_user.id
        username = update.effective_user.username

        user_id = get_or_create_user(
        telegram_id=telegram_id,
        username=username
        )

        get_or_create_energy_access(user_id)
    
        if has_energy_access(user_id):
            context.user_data["state"] = "ENERGY_MATRIX_WAITING_FOR_GOAL"
            context.user_data["energy_clarifications"] = []
            context.user_data["energy_clarification_count"] = 0
            
            await update.message.reply_text(
                "⚡ Энергоматрица\n\n"
                "Опишиnt конкретную цель или ситуацию, относительно которой "
                "хотите определить своё текущее состояние энергии.\n\n"
                "Например:\n"
                "— хочу запустить новый проект\n"
                "— хочу увеличить доход\n"
                "— хочу понять, почему не двигаюсь в отношениях\n"
                "— хочу довести начатое до результата",
                reply_markup=reply_markup
            )
        else:
            await update.message.reply_text(
                "⚡ Доступные расчёты Энергоматрицы закончились.\n\n"
                "Чтобы продолжить, можно получить новый расчёт или пакет расчётов.\n\n"
                "Пока оплата ещё не подключена автоматически, "
                "напишите мне — я открою доступ вручную 🐸",
                reply_markup=reply_markup
            )
        
            return

    # ⚡ Ещё один расчёт Энергоматрицы
    if user_text == "⚡ Да, ещё расчёт":
        context.user_data["state"] = "ENERGY_MATRIX_WAITING_FOR_GOAL"
        context.user_data["energy_clarifications"] = []
        context.user_data["energy_clarification_count"] = 0
    
        await update.message.reply_text(
            "⚡ Опишите новую цель или ситуацию, "
            "относительно которой хотите определить своё текущее состояние энергии.",
            reply_markup=reply_markup
        )
        return
    
    
    # ↩️ Выход из Энергоматрицы
    if user_text == "↩️ Нет, вернуться в меню":
        context.user_data["state"] = "WAITING_FOR_SITUATION"
        context.user_data.pop("energy_goal", None)
        context.user_data.pop("energy_clarifications", None)
        context.user_data.pop("energy_clarification_count", None)
    
        await update.message.reply_text(
            "🐸 Возвращаемся в главное меню.",
            reply_markup=reply_markup
        )
        return
    
    # 🆕 Новый разбор
    if user_text == "🆕 Новый разбор":
        context.user_data["state"] = "WAITING_FOR_SITUATION"
        context.user_data.pop("situation", None)

        await update.message.reply_text(
            "Опишите ситуацию, которую хотите разобрать 🌿",
            reply_markup=reply_markup
        )
        return


        # ✅ Цель подтверждена
    if (
        state == "ENERGY_MATRIX_CONFIRM_GOAL"
        and user_text == "✅ Да, продолжить расчёт"
    ):
        confirmed_goal = context.user_data.get(
            "energy_proposed_goal",
            ""
        )

        context.user_data["energy_goal"] = confirmed_goal

        clarifications = context.user_data.get(
            "energy_clarifications",
            []
        )

        result = run_energy_matrix_analysis(
            goal=confirmed_goal,
            clarifications=clarifications,
            force_complete=True
        )

        print("ENERGY MATRIX CONFIRMED RESULT:")
        print(result)

        if "STATUS: COMPLETE" in result:
            await finish_energy_matrix(
                update,
                context,
                result,
                user_id
            )
        else:
            await update.message.reply_text(
                "Не удалось завершить диагностику. "
                "Попробуйте ещё раз."
            )

        return

     # ✏️ Пользователь хочет изменить цель
    if (
        state == "ENERGY_MATRIX_CONFIRM_GOAL"
        and user_text == "✏️ Нет, скорректировать цель"
    ):
        context.user_data["state"] = (
            "ENERGY_MATRIX_WAITING_FOR_GOAL_CORRECTION"
        )

        await update.message.reply_text(
            "✏️ Напишите, пожалуйста, "
            "как вы хотите сформулировать цель."
        )

        return

    # ✏️ Получили скорректированную цель
    if state == "ENERGY_MATRIX_WAITING_FOR_GOAL_CORRECTION":

        corrected_goal = user_text.strip()

        context.user_data["energy_proposed_goal"] = corrected_goal
        context.user_data["state"] = "ENERGY_MATRIX_CONFIRM_GOAL"

        await update.message.reply_text(
            f"🎯 Тогда фиксируем цель так:\n\n"
            f"{corrected_goal}\n\n"
            f"Продолжаем расчёт?",
            reply_markup=energy_goal_confirm_keyboard
        )

        return

    # ⚡ Энергоматрица — ждём цель
    if state == "ENERGY_MATRIX_WAITING_FOR_GOAL":
    
        goal = user_text.strip()
    
        context.user_data["energy_original_goal"] = goal
        context.user_data["energy_goal"] = goal
        context.user_data["energy_clarifications"] = []
        context.user_data["energy_clarification_count"] = 0
    
        result = run_energy_matrix_analysis(
            goal=goal,
            clarifications=[],
            force_complete=False
        )
    
        print("ENERGY MATRIX RESULT:")
        print(result)
    
        # Нужно уточнение
        if "STATUS: CLARIFY" in result:

            question = result.split("QUESTION:", 1)[1].strip()

            context.user_data["state"] = (
                "ENERGY_MATRIX_WAITING_FOR_CLARIFICATION"
            )

            await update.message.reply_text(
                f"⚡ Мне нужно немного уточнить:\n\n{question}"
            )

        elif "STATUS: COMPLETE" in result:

            goal_summary = summarize_energy_goal(
                goal,
                []
            )

            context.user_data["energy_proposed_goal"] = goal_summary
            context.user_data["state"] = "ENERGY_MATRIX_CONFIRM_GOAL"

            await update.message.reply_text(
                f"🎯 Я сформулировала цель так:\n\n"
                f"{goal_summary}\n\n"
                f"Правильно ли я определила цель?",
                reply_markup=energy_goal_confirm_keyboard
            )

        else:
            await update.message.reply_text(
                "Не удалось корректно определить состояние. "
                "Попробуйте сформулировать цель немного подробнее."
            )

        return

    # ⚡ Энергоматрица — получили ответ на уточнение
    if state == "ENERGY_MATRIX_WAITING_FOR_CLARIFICATION":
    
        goal = context.user_data.get("energy_goal", "")
    
        clarifications = context.user_data.get(
            "energy_clarifications", []
        )
    
        clarifications.append(user_text)
    
        context.user_data["energy_clarifications"] = clarifications
    
        count = context.user_data.get(
            "energy_clarification_count", 0
        ) + 1
    
        context.user_data["energy_clarification_count"] = count
    
        # После второго уточнения обязательно завершаем
        force_complete = count >= 2
    
        result = run_energy_matrix_analysis(
            goal=goal,
            clarifications=clarifications,
            force_complete=force_complete
        )
    
        print("ENERGY MATRIX RESULT:")
        print(result)
    
        if "STATUS: CLARIFY" in result and not force_complete:
    
            question = result.split("QUESTION:", 1)[1].strip()
    
            context.user_data["state"] = "ENERGY_MATRIX_WAITING_FOR_CLARIFICATION"
    
            await update.message.reply_text(
                f"⚡ Ещё один момент:\n\n{question}"
            )
    
        else:
            goal_summary = summarize_energy_goal(
                goal,
                clarifications
            )
        
            context.user_data["energy_proposed_goal"] = goal_summary
            context.user_data["state"] = "ENERGY_MATRIX_CONFIRM_GOAL"
        
            await update.message.reply_text(
                f"🎯 С учётом ваших ответов я сформулировала цель так:\n\n"
                f"{goal_summary}\n\n"
                f"Правильно ли я определила цель?",
                reply_markup=energy_goal_confirm_keyboard
            )

    return
    
    # 📍 Если ждём ситуацию
    if state == "WAITING_FOR_SITUATION":
        user_id = update.message.from_user.id

    # 👑 если не админ — считаем лимит
        if user_id != ADMIN_ID:

            today = str(date.today())

            user_day = context.user_data.get("day")
            count = context.user_data.get("daily_count", 0)

            if user_day != today:
                context.user_data["day"] = today
                context.user_data["daily_count"] = 0
                count = 0

            if count >= 3:
                await update.message.reply_text(
                    "На сегодня ты сделал(а) максимум разборов 🌿\n\nВозвращайся завтра 🐸"
                )
                return

            context.user_data["daily_count"] = count + 1

        # сохраняем ситуацию
        context.user_data["situation"] = user_text
        context.user_data["state"] = "IN_ANALYSIS"

        instruction = user_text


    elif state == "IN_ANALYSIS":
        situation = context.user_data.get("situation", "")

        if user_text == "🔍 Посмотреть глубже":
            instruction = f"Ситуация: {situation}\n\nУглуби анализ.Ответ только от Археолога."

        elif user_text == "🔀 Другие варианты":
            instruction = f"Ситуация: {situation}\n\nПокажи альтернативные ветки.Ответ только от Лягушки."

        
        else:
            # 🚫 Блокируем произвольный текст
            await update.message.reply_text(
                "Сейчас мы в разборе 🌿\n\nВыбери действие с кнопок или начни новый разбор 🐸",
                reply_markup=reply_markup
            )
            return

    # 🤖 Запрос к ChatGPT
    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": instruction}
        ]
    )

    answer = response.choices[0].message.content

    await update.message.reply_text(answer, reply_markup=reply_markup)


#app = ApplicationBuilder().token(TOKEN).build()
#app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))


#print("Бот запущен...")



# Telegram bot в главном потоке
#application.run_polling()  

application.add_handler(CommandHandler("start", start))
#application.add_handler(MessageHandler(filters.ALL, debug))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

if __name__ == "__main__":
    print("🚀 BOT STARTED")
    test_db()
    init_db()
    application.run_polling()
