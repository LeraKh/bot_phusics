import asyncio
import os
import sqlite3
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "8822167411:AAEkwDSwZQK7hDAQEyOlAGjDqV05dGZVUYg")
ADMIN_ID = int(os.getenv("ADMIN_ID", 6457087349))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
DB_FILE = "leads.db"


# структура SQLite
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS leads (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            target_exam TEXT,
            score INTEGER DEFAULT 0,
            segment TEXT,
            phone TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    conn.close()


# Сохранение и обновление профиля лида с результатами квиза
def save_user_lead(user_id, username, full_name, target_exam, score, segment, phone=None):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO leads (user_id, username, full_name, target_exam, score, segment, phone)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            target_exam=excluded.target_exam,
            score=excluded.score,
            segment=excluded.segment,
            phone=COALESCE(excluded.phone, leads.phone)
        """,
        (user_id, username, full_name, target_exam, score, segment, phone),
    )
    conn.commit()
    conn.close()


# Дозапись контактного телефона
def update_phone(user_id, phone):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("UPDATE leads SET phone = ? WHERE user_id = ?", (phone, user_id))
    conn.commit()
    conn.close()


# Агрегация аналитики
def get_analytics():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM leads")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM leads WHERE phone IS NOT NULL AND phone != ''")
    leads_count = cur.fetchone()[0]
    cur.execute("SELECT segment, COUNT(*) FROM leads GROUP BY segment")
    segments = cur.fetchall()
    conn.close()
    return total, leads_count, segments


# FSM
class QuizStates(StatesGroup):
    choosing_exam = State()
    q1 = State()
    q2 = State()
    q3 = State()
    waiting_for_lead = State()


# Вопросы
QUESTIONS = [
    {
        "text": (
            "📌 **Вопрос 1 (Механика):**\n"
            "Камень бросили вертикально вверх. Чему равно его ускорение в наивысшей точке траектории?\n"
            "(сопротивлением воздуха пренебречь)"
        ),
        "options": [
            ("0 м/с²", 0),
            ("9.8 м/с² (направлено вниз)", 1),
            ("Зависит от массы камня", 0),
        ],
        "explanation": "💡 *В наивысшей точке скорость равна 0, но сила тяжести никуда не исчезает! По 2 закону Ньютона ускорение равно g ≈ 9.8 м/с² вниз.*",
    },
    {
        "text": (
            "📌 **Вопрос 2 (Термодинамика):**\n"
            "Идеальный газ изотермически расширяется. Как при этом меняется его внутренняя энергия?"
        ),
        "options": [
            ("Увеличивается", 0),
            ("Не изменяется", 1),
            ("Уменьшается", 0),
        ],
        "explanation": "💡 *Внутренняя энергия идеального газа зависит ТОЛЬКО от температуры (U = 3/2 νRT). Если процесс изотермический (T = const), то ΔU = 0.*",
    },
    {
        "text": (
            "📌 **Вопрос 3 (Электродинамика):**\n"
            "Медный проводник сложили вдвое. Как изменилось его электрическое сопротивление?"
        ),
        "options": [
            ("Уменьшилось в 2 раза", 0),
            ("Уменьшилось в 4 раза", 1),
            ("Не изменилось", 0),
        ],
        "explanation": "💡 *Ловушка ЕГЭ! При складывании вдвое длина L уменьшается в 2 раза, а площадь сечения S увеличивается в 2 раза. По формуле R = ρL/S сопротивление падает в 4 раза.*",
    },
]

# для шеринга контакта
contact_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="📱 Оставить контакт в 1 клик", request_contact=True)]],
    resize_keyboard=True,
    one_time_keyboard=True,
)


# вход: приветствие и выбор цели обучения
@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎯 Готовлюсь к ЕГЭ (10-11 класс)", callback_data="exam_ege")],
            [InlineKeyboardButton(text="⚡ Готовлюсь к ОГЭ (8-9 класс)", callback_data="exam_oge")],
        ]
    )
    await message.answer(
        f"Здравствуйте, {message.from_user.first_name}! 👋\n\n"
        "Это экспресс-диагностика от онлайн-школы физики.\n"
        "Около **68% учеников теряют баллы на экзамене не из-за формул, а на неочевидных ловушках** составителей.\n\n"
        "Пройдите короткий тест из 3 вопросов (займет 2 минуты), чтобы узнать свои шансы на 80+ баллов.",
        reply_markup=kb,
    )
    await state.set_state(QuizStates.choosing_exam)


# Фиксация экзамена и переход к первому вопросу
@dp.callback_query(QuizStates.choosing_exam, F.data.startswith("exam_"))
async def process_exam_choice(callback: types.CallbackQuery, state: FSMContext):
    exam = "ЕГЭ" if "ege" in callback.data else "ОГЭ"
    await state.update_data(exam=exam, score=0)

    q = QUESTIONS[0]
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=opt[0], callback_data=f"ans_0_{opt[1]}")] for opt in q["options"]]
    )
    await callback.message.edit_text(
        f"Выбран экзамен: **{exam}**\n\n{q['text']}",
        reply_markup=kb,
        parse_mode="Markdown",
    )
    await state.set_state(QuizStates.q1)


# Универсальный маршрутизатор шагов квиза и подсчета баллов
async def handle_question(callback: types.CallbackQuery, state: FSMContext, current_q_idx: int, next_state: State):
    _, q_idx, points = callback.data.split("_")
    data = await state.get_data()
    new_score = data.get("score", 0) + int(points)
    await state.update_data(score=new_score)

    explanation = QUESTIONS[int(q_idx)]["explanation"]
    verdict = "✅ **Верно!**" if int(points) == 1 else "❌ **Ошибка!**"
    await callback.message.answer(f"{verdict}\n{explanation}", parse_mode="Markdown")

    if current_q_idx < len(QUESTIONS) - 1:
        next_q = QUESTIONS[current_q_idx + 1]
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=opt[0], callback_data=f"ans_{current_q_idx+1}_{opt[1]}")]
                for opt in next_q["options"]
            ]
        )
        await callback.message.answer(next_q["text"], reply_markup=kb, parse_mode="Markdown")
        await state.set_state(next_state)
    else:
        await finish_quiz(callback.message, state, new_score)


@dp.callback_query(QuizStates.q1, F.data.startswith("ans_0_"))
async def ans_q1(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.delete_reply_markup()
    await handle_question(callback, state, 0, QuizStates.q2)


@dp.callback_query(QuizStates.q2, F.data.startswith("ans_1_"))
async def ans_q2(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.delete_reply_markup()
    await handle_question(callback, state, 1, QuizStates.q3)


@dp.callback_query(QuizStates.q3, F.data.startswith("ans_2_"))
async def ans_q3(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.delete_reply_markup()
    _, _, points = callback.data.split("_")
    data = await state.get_data()
    total_score = data.get("score", 0) + int(points)
    await state.update_data(score=total_score)

    explanation = QUESTIONS[2]["explanation"]
    verdict = "✅ **Верно!**" if int(points) == 1 else "❌ **Ошибка!**"
    await callback.message.answer(f"{verdict}\n{explanation}", parse_mode="Markdown")
    await finish_quiz(callback.message, state, total_score)


# Разбор лида по баллам и выдача результата
async def finish_quiz(message: types.Message, state: FSMContext, score: int):
    data = await state.get_data()
    exam = data.get("exam", "ЕГЭ")

    if score == 3:
        segment = "Сильный (80+)"
        diagnostics = (
            "🔥 **Отличная база (3 из 3)!**\n"
            "Вы хорошо чувствуете физическую суть явлений. Чтобы гарантировать 85–90+ баллов на экзамене, "
            "сфокусируйтесь на оформлении задач 2-й части."
        )
    elif score == 2:
        segment = "Средний (60-75)"
        diagnostics = (
            "⚠️ **Неплохо, но есть опасные пробелы (2 из 3).**\n"
            "Теорию помните, но на качественных задачах с подвохом легко потерять 15-20 первичных баллов. "
            "Нужно структурировать темы термодинамики и механики."
        )
    else:
        segment = "Зона риска (<60)"
        diagnostics = (
            "🚨 **Тревожный сигнал (1 или 0 из 3).**\n"
            "Формулы без глубокого понимания законов приводят к потере баллов даже в первой части. "
            "Самостоятельно закрыть такие пробелы крайне тяжело."
        )

    save_user_lead(
        user_id=message.chat.id,
        username=message.chat.username or "",
        full_name=message.chat.full_name or "",
        target_exam=exam,
        score=score,
        segment=segment,
    )

    offer_text = (
        f"📊 **Результаты диагностики ({exam}):**\n\n"
        f"{diagnostics}\n\n"
        "🎁 **Спецпредложение для вас:**\n"
        "Мы дарим доступ к бесплатному **индивидуальному разбору ваших ошибок с экспертом ОГЭ/ЕГЭ** (30 минут онлайн) + "
        "чек-лист «Топ-20 ловушек составителей экзамена».\n\n"
        "Отправьте контакт кнопкой ниже или напишите номер телефона в чат:"
    )

    await message.answer(offer_text, reply_markup=contact_kb, parse_mode="Markdown")
    await state.set_state(QuizStates.waiting_for_lead)


# Обработка полученного контакта (кнопка Telegram или ручной ввод текста)
@dp.message(QuizStates.waiting_for_lead, F.contact)
async def process_contact_button(message: types.Message, state: FSMContext):
    phone = message.contact.phone_number
    await save_and_notify_lead(message, state, phone)


@dp.message(QuizStates.waiting_for_lead, F.text)
async def process_contact_text(message: types.Message, state: FSMContext):
    phone = message.text.strip()
    if len(phone) < 7:
        await message.answer("Пожалуйста, введите корректный номер телефона или нажмите кнопку:")
        return
    await save_and_notify_lead(message, state, phone)


# Сохранение конверсии и моментальный пуш администратору
async def save_and_notify_lead(message: types.Message, state: FSMContext, phone: str):
    update_phone(message.from_user.id, phone)
    data = await state.get_data()
    exam = data.get("exam", "Не указан")
    score = data.get("score", 0)

    await state.clear()
    await message.answer(
        "🎉 **Заявка принята!**\n\n"
        "Наш методист свяжется с вами в течение рабочего дня, согласует удобное время разбора и пришлет чек-лист.\n"
        "До встречи на занятии!",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown",
    )

    admin_alert = (
        "🔥 **НОВЫЙ ЛИД ИЗ БОТА!**\n\n"
        f"👤 **Имя:** {message.from_user.full_name} (@{message.from_user.username})\n"
        f"📱 **Телефон:** `{phone}`\n"
        f"🎯 **Цель:** {exam}\n"
        f"📊 **Результат теста:** {score}/3\n"
        f"🆔 **ID:** `{message.from_user.id}`"
    )
    try:
        await bot.send_message(ADMIN_ID, admin_alert, parse_mode="Markdown")
    except Exception as e:
        print(f"Не удалось отправить уведомление админу: {e}")


# Аналитическая панель администратора
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("Доступ ограничен.")
        return

    total, leads_count, segments = get_analytics()
    conv = (leads_count / total * 100) if total > 0 else 0
    seg_stat = "\n".join([f"  • {seg if seg else 'Не завершили'}: {cnt}" for seg, cnt in segments])

    report = (
        "📈 **Аналитика воронки школы:**\n\n"
        f"👥 Всего начали тест: **{total}**\n"
        f"🎯 Оставили заявку (лиды): **{leads_count}**\n"
        f"📊 Конверсия в лид: **{conv:.1f}%**\n\n"
        f"**Сегменты аудитории:**\n{seg_stat}\n\n"
        "Команды:\n"
        "`/broadcast ТЕКСТ` — рассылка по всем пользователям базы."
    )
    await message.answer(report, parse_mode="Markdown")


# Модуль рассылки по базе лидов
@dp.message(Command("broadcast"))
async def broadcast_msg(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    text = message.text.replace("/broadcast", "").strip()
    if not text:
        await message.answer("Использование: `/broadcast Текст сообщения`")
        return

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM leads")
    users = cur.fetchall()
    conn.close()

    sent = 0
    for (uid,) in users:
        try:
            await bot.send_message(uid, text, parse_mode="Markdown")
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass

    await message.answer(f"Рассылка завершена. Доставлено: {sent}/{len(users)}")


# HTTP-сервер
async def ping_server():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Bot is running!"))
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()


async def main():
    init_db()
    await ping_server()
    print("🚀 Бот онлайн-школы физики успешно запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())