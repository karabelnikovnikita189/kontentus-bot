import asyncio
import sqlite3
import google.generativeai as genai
from aiogram import Bot, Dispatcher, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters import CommandStart
import os
from dotenv import load_dotenv
import threading
from http.server import SimpleHTTPRequestHandler, HTTPServer

# === 1. Настройки ===
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
CHANNEL_ID = "@kontentus_chanel"  # <-- замените на username вашего канала, например "@kontentus"

# === 2. Бот и Gemini ===
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
genai.configure(api_key=GEMINI_API_KEY)
MODEL_NAME = "models/gemini-2.0-flash"

# === 3. База данных ===
conn = sqlite3.connect("users.db")
cur = conn.cursor()
cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    free_generations INTEGER DEFAULT 5,
    referrer_id INTEGER DEFAULT NULL,
    invited_count INTEGER DEFAULT 0,
    joined_bonus INTEGER DEFAULT 0
)
""")
conn.commit()

# === 4. Клавиатуры ===
def main_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="📝 Генерация текста", callback_data="gen_text")
    kb.button(text="💡 Идея для поста", callback_data="idea_post")
    kb.button(text="👥 Пригласить друга", callback_data="invite_friend")
    kb.adjust(1)
    return kb.as_markup()

def back_to_menu_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="🏠 Главное меню", callback_data="menu")
    return kb.as_markup()

def join_channel_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="📢 Вступить в сообщество", url=f"https://t.me/{CHANNEL_ID.lstrip('@')}")
    kb.button(text="✅ Проверить подписку", callback_data="joined_channel")
    kb.adjust(1)
    return kb.as_markup()

# === 5. Работа с БД ===
def get_user(user_id):
    cur.execute("SELECT free_generations FROM users WHERE user_id = ?", (user_id,))
    result = cur.fetchone()
    if result:
        return result[0]
    else:
        cur.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()
        return 5

def decrease_generation(user_id):
    cur.execute("UPDATE users SET free_generations = free_generations - 1 WHERE user_id = ?", (user_id,))
    conn.commit()

# === 6. /start и рефералка + приветствие ===
@dp.message(CommandStart())
async def start(message: Message):
    user_id = message.from_user.id
    args = message.text.split()
    referrer_id = None

    if len(args) > 1:
        try:
            referrer_id = int(args[1])
        except:
            referrer_id = None

    # проверим, есть ли пользователь в таблице
    cur.execute("SELECT referrer_id FROM users WHERE user_id = ?", (user_id,))
    existing = cur.fetchone()

    if existing is None:
        # новый пользователь — создаём запись с referrer (если есть)
        cur.execute("INSERT INTO users (user_id, free_generations, referrer_id) VALUES (?, ?, ?)",
                    (user_id, 5, referrer_id))
        conn.commit()

        # если есть валидный реферер (и это не сам пользователь) — начисляем ему +1 и увеличиваем invited_count
        if referrer_id and referrer_id != user_id:
            cur.execute("SELECT user_id FROM users WHERE user_id = ?", (referrer_id,))
            if cur.fetchone():  # реферер существует
                cur.execute("""
                    UPDATE users
                    SET free_generations = free_generations + 2,
                        invited_count = invited_count + 1
                    WHERE user_id = ?
                """, (referrer_id,))
                conn.commit()
                try:
                    await bot.send_message(
                        referrer_id,
                        "🎉 По твоей ссылке зарегистрировался новый пользователь! Ты получил +2 бесплатных генераций 💫"
                    )
                except:
                    pass

        # отправляем приветственное сообщение с просьбой вступить в сообщество
        await message.answer(
            "👋 Привет! Добро пожаловать в <b>Контентус</b> — твой ИИ-помощник по контенту.\n\n"
            "📢 Подпишись на наше сообщество, чтобы получать новости и бонусы.\n\n"
            "🎁 За подписку — <b>+3 бесплатные генерации</b> (нажми «✅ Проверить подписку» после подписки).",
            reply_markup=join_channel_keyboard()
        )
        return

    # если пользователь уже есть — показываем меню с текущим количеством
    left = get_user(user_id)
    invite_link = f"https://t.me/{(await bot.me()).username}?start={user_id}"
    await message.answer(
        f"👋 С возвращением, <b>{message.from_user.first_name}</b>!\n\n"
        f"✨ У тебя осталось <b>{left}</b> бесплатных генераций.\n\n"
        f"💌 Поделись своей ссылкой и получи +2 генерации за каждого друга: {invite_link}",
        reply_markup=main_keyboard()
    )

# === 7. Проверка подписки (бонус один раз) ===
@dp.callback_query(F.data == "joined_channel")
async def joined_channel(callback: CallbackQuery):
    user_id = callback.from_user.id

    # сначала проверим, есть ли запись в БД (на всякий)
    cur.execute("SELECT joined_bonus FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    if row is None:
        # нет пользователя — создаём и говорим выполнить /start
        cur.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()
        await callback.message.answer("⚠️ Пожалуйста, заново нажми /start и попробуй ещё раз.")
        return

    already = row[0]

    # если пользователь уже получал бонус — не даём повторно
    if already == 1:
        await callback.message.answer("✅ Ты уже получал бонус за подписку.", reply_markup=main_keyboard())
        return

    # пытаемся проверить статус через get_chat_member
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        if member.status in ("member", "administrator", "creator"):
            # начисляем +3 и ставим флаг
            cur.execute("UPDATE users SET free_generations = free_generations + 3, joined_bonus = 1 WHERE user_id = ?", (user_id,))
            conn.commit()
            await callback.message.answer("🎉 Спасибо! Подписка подтверждена — тебе начислено +3 бесплатных генерации.", reply_markup=main_keyboard())
        else:
            await callback.message.answer("❌ Вы ещё не подписаны на канал. Подпишитесь и нажмите «✅ Проверить подписку» снова.", reply_markup=join_channel_keyboard())
    except Exception as e:
        # если get_chat_member даёт ошибку (например, приватный канал или бот не админ) — выводим понятное сообщение
        await callback.message.answer(f"⚠️ Не удалось проверить подписку: {e}\n\nЕсли канал приватный, убедитесь, что бот в нём является администратором.", reply_markup=join_channel_keyboard())

# === 8. "Пригласить друга" — показывает ссылку и кнопку шаринга ===
@dp.callback_query(F.data == "invite_friend")
async def invite_friend(callback: CallbackQuery):
    user_id = callback.from_user.id
    invite_link = f"https://t.me/{(await bot.me()).username}?start={user_id}"
    # показываем ссылку и кнопку для быстрого шаринга (switch_inline_query недоступен для внешних ссылок, используем url share)
    kb = InlineKeyboardBuilder()
    share_text = f"🔥 Присоединяйся к боту Контентус!\nОн помогает придумывать идеи и тексты для постов 💡\n➡️ Моя ссылка-приглашение:{invite_link}"
    kb.button(text="📤 Поделиться ссылкой", url=f"https://t.me/share/url?url={invite_link}&text={share_text}")
    kb.button(text="🏠 Главное меню", callback_data="menu")
    await callback.message.answer(f"💌 Поделись этой ссылкой и получай бонусы:\n\n{invite_link}", reply_markup=kb.as_markup())

# === 9. Генерация текста (диалог) ===
# при нажатии переводим пользователя в режим ввода запроса — регистрируем обработчик кратковременно
@dp.callback_query(F.data == "gen_text")
async def gen_text_start(callback: CallbackQuery):
    await callback.message.answer("✍️ Введите запрос для генерации текста:", reply_markup=back_to_menu_keyboard())
    # регистрируем временный хэндлер на получение следующего текстового сообщения
    dp.message.register(gen_text_handle, F.text)

async def gen_text_handle(message: Message):
    user_id = message.from_user.id
    left = get_user(user_id)
    if left <= 0:
        await message.answer("🚫 У тебя закончились бесплатные генерации.", reply_markup=back_to_menu_keyboard())
        return

    prompt = message.text.strip()
    await message.answer("⏳ Генерирую...")

    try:
        model = genai.GenerativeModel(MODEL_NAME)
        response = model.generate_content(prompt).text
    except Exception as e:
        await message.answer(f"⚠️ Ошибка при генерации: {e}")
        return

    decrease_generation(user_id)
    left -= 1
    await message.answer(f"{response}\n\nОсталось бесплатных генераций: {left}", reply_markup=back_to_menu_keyboard())

# === 10. Идея для поста (диалог) ===
@dp.callback_query(F.data == "idea_post")
async def idea_post_start(callback: CallbackQuery):
    await callback.message.answer("💡 Введите тему для поста — я создам короткий пост на эту тему:", reply_markup=back_to_menu_keyboard())
    dp.message.register(idea_post_handle, F.text)

async def idea_post_handle(message: Message):
    user_id = message.from_user.id
    left = get_user(user_id)
    if left <= 0:
        await message.answer("🚫 У тебя закончились бесплатные генерации.", reply_markup=back_to_menu_keyboard())
        return

    topic = message.text.strip()
    prompt = f"Создай короткий и цепляющий пост для соцсетей на тему: {topic}. Не используй хэштеги, добавь 1-2 эмодзи."

    await message.answer("⏳ Генерирую идею для поста...")

    try:
        model = genai.GenerativeModel(MODEL_NAME)
        response = model.generate_content(prompt).text
    except Exception as e:
        await message.answer(f"⚠️ Ошибка при генерации: {e}")
        return

    decrease_generation(user_id)
    left -= 1
    await message.answer(f"💡 Вот короткий пост:\n\n{response}\n\nОсталось бесплатных генераций: {left}", reply_markup=back_to_menu_keyboard())

# === 11. Главное меню ===
@dp.callback_query(F.data == "menu")
async def back_to_menu(callback: CallbackQuery):
    user_id = callback.from_user.id
    left = get_user(user_id)
    if left > 0:
        text = f"🏠 <b>Главное меню</b>\n\n✨ У тебя осталось: <b>{left}</b> бесплатных генераций 💫"
    else:
        text = "🏠 <b>Главное меню</b>\n\n❌ Бесплатных генераций нет. Пригласи друга или подпишись на сообщество 🎁"
    await callback.message.answer(text, reply_markup=main_keyboard())

# === 12. Обработка произвольных сообщений (если юзер просто пишет текст вне диалога) ===
@dp.message()
async def handle_message(message: Message):
    # поведение: если пользователь просто пишет текст (не через кнопки/режимы), обрабатываем как генерацию текста
    user_id = message.from_user.id
    left = get_user(user_id)
    if left <= 0:
        await message.answer("🚫 У тебя закончились бесплатные генерации.", reply_markup=back_to_menu_keyboard())
        return

    await message.answer("⏳ Генерирую...")

    try:
        model = genai.GenerativeModel(MODEL_NAME)
        response = model.generate_content(message.text).text
    except Exception as e:
        await message.answer(f"⚠️ Ошибка при генерации: {e}")
        return

    decrease_generation(user_id)
    left -= 1
    await message.answer(f"{response}\n\nОсталось бесплатных генераций: {left}", reply_markup=back_to_menu_keyboard())

# === 13. Запуск ===
async def main():
    print("🤖 Бот запущен!")
    await dp.start_polling(bot)

def run_web_server():
    try:
        server = HTTPServer(('0.0.0.0', 8080), SimpleHTTPRequestHandler)
        print("✅ Render: фиктивный HTTP сервер запущен на порту 8080")
        server.serve_forever()
    except Exception as e:
        print("⚠️ Ошибка при запуске фиктивного веб-сервера:", e)

# Запускаем в отдельном потоке
threading.Thread(target=run_web_server, daemon=True).start()

if __name__ == "__main__":
    asyncio.run(main())
