MAX_FREE_ANALYSIS = 5
ADMIN_ID = 1724691240  # ← вставь свой ID

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from openai import OpenAI
from datetime import date
from flask import Flask

import os

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
TOKEN = os.getenv("TELEGRAM_TOKEN")
application = ApplicationBuilder().token(TOKEN).build()


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

keyboard = [
    ["🔍 Посмотреть глубже", "🔀 Другие варианты"],
    ["🆕 Новый разбор"]
]

reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text    

    state = context.user_data.get("state", "WAITING_FOR_SITUATION")
        

    # 🆕 Новый разбор
    if user_text == "🆕 Новый разбор":
        context.user_data["state"] = "WAITING_FOR_SITUATION"
        context.user_data.pop("situation", None)

        await update.message.reply_text(
            "Опиши ситуацию, которую хочешь разобрать 🌿",
            reply_markup=reply_markup
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


app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

print("Бот запущен...")


if __name__ == "__main__":
    print("BOT STARTED")

    from flask import Flask
    import os
    from threading import Thread

    app = Flask(__name__)

    @app.route("/")
    def home():
        return "Bot is running"

    # запускаем Telegram bot в фоне
    Thread(target=lambda: application.run_polling()).start()

    # открываем порт для Render
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)