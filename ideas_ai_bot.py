"""
ИИ-бот для генерации идей на OpenRouter.
- Живой прогресс-бар во время генерации.
- Понимает запросы на русском.
"""

import os
import asyncio
import logging
from aiohttp import web

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from aiogram.filters import Command
from openai import OpenAI

# ============ КОНФИГ ============
BOT_TOKEN = os.environ["TELEGRAM_TOKEN"]
OPENROUTER_KEY = os.environ["OPENROUTER_KEY"]
ADMIN_ID = int(os.environ["OWNER_ID"])

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ideas_ai")

client = OpenAI(
    api_key=OPENROUTER_KEY,
    base_url="https://openrouter.ai/api/v1",
)

bot = Bot(token=BOT_TOKEN)
router = Router()

MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"

SYSTEM_PROMPT = """
Ты — креативный генератор идей. Твоя задача — выдавать свежие, неожиданные, полезные идеи.

Правила:
1. Давай 5 идей на каждый запрос.
2. Каждая идея — 1-2 предложения, конкретно и по делу.
3. Не повторяйся, не давай банальностей.
4. Если тема широкая — предложи разные направления.
5. Пиши на русском, живым языком, без воды.
6. Нумеруй идеи: 1., 2., 3., 4., 5.
"""

conversations = {}


def get_history(user_id: int):
    if user_id not in conversations:
        conversations[user_id] = []
    return conversations[user_id]


# ============ ПРОГРЕСС-БАР ============
def progress_bar(percent: int, length: int = 12) -> str:
    filled = int(length * percent / 100)
    bar = "█" * filled + "░" * (length - filled)
    return f"[{bar}] {percent}%"


async def run_with_progress(message: Message, topic: str) -> str:
    """
    Запускает генерацию и параллельно обновляет сообщение с прогрессом.
    Возвращает готовый ответ.
    """
    progress_msg = await message.answer(
        f"🧠 **Генерирую идеи...**\n\n"
        f"{progress_bar(0)}\n"
        f"📝 {topic[:60]}{'...' if len(topic) > 60 else ''}",
        parse_mode="Markdown"
    )

    result_holder = {"answer": None, "error": None, "done": False}

    async def worker():
        try:
            loop = asyncio.get_event_loop()
            answer = await loop.run_in_executor(None, ask_llm, message.from_user.id, topic)
            result_holder["answer"] = answer
        except Exception as e:
            result_holder["error"] = e
        finally:
            result_holder["done"] = True

    task = asyncio.create_task(worker())

    # Плавно движемся до 90%, пока не придёт ответ
    percent = 0
    while not result_holder["done"]:
        await asyncio.sleep(0.7)
        if percent < 30:
            percent += 7
        elif percent < 60:
            percent += 4
        elif percent < 90:
            percent += 2

        try:
            await progress_msg.edit_text(
                f"🧠 **Генерирую идеи...**\n\n"
                f"{progress_bar(percent)}\n"
                f"📝 {topic[:60]}{'...' if len(topic) > 60 else ''}",
                parse_mode="Markdown"
            )
        except Exception:
            pass

    await task

    # Показываем 100% перед финальным ответом
    try:
        await progress_msg.edit_text(
            f"🧠 **Генерирую идеи...**\n\n"
            f"{progress_bar(100)}\n"
            f"✅ Готово!",
            parse_mode="Markdown"
        )
        await asyncio.sleep(0.3)
    except Exception:
        pass

    # Удаляем прогресс — вместо него будет финальный ответ
    try:
        await progress_msg.delete()
    except Exception:
        pass

    if result_holder["error"]:
        raise result_holder["error"]
    return result_holder["answer"]


# ============ КЛАВИАТУРА ============
def quick_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💼 Бизнес", callback_data="q_бизнес идеи"),
            InlineKeyboardButton(text="🎁 Подарки", callback_data="q_идеи подарков"),
        ],
        [
            InlineKeyboardButton(text="📱 Проекты", callback_data="q_идеи IT проектов"),
            InlineKeyboardButton(text="🌴 Отдых", callback_data="q_идеи для отдыха"),
        ],
        [
            InlineKeyboardButton(text="🍳 Рецепты", callback_data="q_идеи блюд"),
            InlineKeyboardButton(text="✈️ Путешествия", callback_data="q_идеи путешествий"),
        ],
        [
            InlineKeyboardButton(text="🧹 Дом", callback_data="q_идеи для дома"),
            InlineKeyboardButton(text="🎉 Праздник", callback_data="q_идеи праздника"),
        ],
        [
            InlineKeyboardButton(text="🗑 Очистить диалог", callback_data="clear"),
        ],
    ])


# ============ КОМАНДЫ ============
@router.message(Command("start"))
async def cmd_start(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔️ Доступ запрещён")
        return
    await message.answer(
        "🧠 **ИИ-генератор идей**\n\n"
        "Просто напиши тему — я дам 5 идей.\n\n"
        "**Примеры:**\n"
        "• идеи для бизнеса в маленьком городе\n"
        "• что подарить маме на день рождения\n"
        "• идеи для свидания зимой\n\n"
        "Или выбери быструю категорию:",
        reply_markup=quick_kb(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "clear")
async def cb_clear(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔️", show_alert=True)
        return
    conversations[callback.from_user.id] = []
    await callback.message.edit_text(
        "🗑 История диалога очищена. Напиши новую тему.",
        reply_markup=quick_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("q_"))
async def cb_quick(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔️", show_alert=True)
        return

    topic = callback.data[2:]
    await callback.answer()

    await generate_and_send(callback.message, callback.from_user.id, topic, is_callback=True)


# ============ ГЕНЕРАЦИЯ ============
def ask_llm(user_id: int, topic: str) -> str:
    history = get_history(user_id)
    history.append({"role": "user", "content": topic})

    trimmed = history[-6:]

    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}] + trimmed,
        max_tokens=800,
        temperature=0.9,
    )

    answer = response.choices[0].message.content
    history.append({"role": "assistant", "content": answer})
    return answer


async def generate_and_send(message: Message, user_id: int, topic: str, is_callback: bool = False):
    try:
        # Сразу скрываем исходное сообщение (убираем "Началась генерация")
        if is_callback:
            try:
                await message.edit_text("⏳ Подготовка...")
            except Exception:
                pass

        answer = await run_with_progress(message, topic)

        text = f"🧠 **Идеи по теме:** {topic}\n\n{answer}"

        # Отправляем финальный ответ
        await message.answer(text, reply_markup=quick_kb(), parse_mode="Markdown")

    except Exception as e:
        log.exception("llm error")
        err = str(e)
        if "404" in err:
            msg = "❌ Модель недоступна. Нужно заменить на другую бесплатную."
        elif "429" in err or "rate" in err.lower():
            msg = "⏳ Слишком много запросов. Подожди 30 секунд."
        elif "401" in err or "auth" in err.lower():
            msg = "❌ Ошибка авторизации. Проверь ключ OpenRouter."
        else:
            msg = f"❌ Ошибка: {e}"

        await message.answer(msg)


# ============ ПРИЁМ ТЕКСТА ============
@router.message(F.text)
async def on_text(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    topic = message.text.strip()
    if not topic or topic.startswith("/"):
        return

    await generate_and_send(message, message.from_user.id, topic, is_callback=False)


# ============ HEALTH-СЕРВЕР ============
async def _health(request):
    return web.Response(text="OK")


async def _run_health_server():
    app = web.Application()
    app.router.add_get("/", _health)
    app.router.add_get("/health", _health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info(f"Health-сервер на порту {port}")


# ============ ЗАПУСК ============
async def main():
    log.info("=== IDEAS AI BOT ===")
    asyncio.create_task(_run_health_server())

    dp = Dispatcher()
    dp.include_router(router)

    try:
        await bot.send_message(
            ADMIN_ID,
            "🧠 **ИИ-генератор идей запущен!**\n\nНапиши /start.",
            parse_mode="Markdown"
        )
    except Exception as e:
        log.warning(f"Приветствие: {e}")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())