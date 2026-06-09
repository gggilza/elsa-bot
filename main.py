#!/usr/bin/env python3
import asyncio
import logging
import os
import sys
import json
import threading
import re
import random
import unicodedata
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton, Poll
)
from dotenv import load_dotenv
from flask import Flask
import google.generativeai as genai

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    logger.error("BOT_TOKEN не найден!")
    sys.exit(1)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
gemini_model = None
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        ELZA_SYSTEM_PROMPT = (
            "Ты — Эльза Абдрахманова, дерзкая и уверенная в себе девушка-бот в Telegram. "
            "Ты саркастичная, остроумная, немного высокомерная, но в глубине души добрая. "
            "Говоришь только по-русски, активно используешь эмодзи. "
            "Отвечаешь коротко и по делу — максимум 2–3 предложения. "
            "Никогда не признаёшь что ты ИИ или языковая модель — ты просто Эльза. "
            "Не используешь markdown-разметку вроде ** или __. "
            "Если тебя спрашивают о чём-то конкретном — отвечаешь по существу, но в своём стиле."
        )
        gemini_model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            system_instruction=ELZA_SYSTEM_PROMPT,
        )
        logger.info("✅ Gemini AI инициализирован")
    except Exception as e:
        logger.error(f"Ошибка инициализации Gemini: {e}")
else:
    logger.warning("⚠️ GEMINI_API_KEY не найден, Gemini отключён")

ADMIN_ID = 6114745287  # твой Telegram ID

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

async def notify_admin(text: str):
    """Тихо отправляет сообщение админу, не падает при ошибке."""
    try:
        await bot.send_message(ADMIN_ID, text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Ошибка отправки админу: {e}")

async def log_message(message: types.Message, action: str = None):
    """
    Логирует любое сообщение пользователя и пересылает админу.
    action — если передать строку (например "нажал кнопку 📅 Дни рождения"),
    она вставится вместо текста.
    """
    try:
        user   = message.from_user
        chat   = message.chat
        now    = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

        chat_type = {
            "private":    "👤 Личка",
            "group":      "👥 Группа",
            "supergroup": "👥 Супергруппа",
            "channel":    "📢 Канал",
        }.get(chat.type, chat.type)

        chat_title = chat.title or "—"
        user_name  = f"{user.first_name or ''} {user.last_name or ''}".strip() or "—"
        username   = f"@{user.username}" if user.username else "нет username"

        if action:
            content_line = f"🖱 <b>Действие:</b> {action}"
        else:
            text = message.text or "[не текст]"
            content_line = f"💬 <b>Текст:</b> {text}"

        report = (
            f"📨 <b>Новое сообщение</b>\n"
            f"🕐 {now}\n"
            f"─────────────────\n"
            f"{chat_type}: <b>{chat_title}</b>\n"
            f"🆔 chat_id: <code>{chat.id}</code>\n"
            f"─────────────────\n"
            f"👤 <b>{user_name}</b> ({username})\n"
            f"🆔 user_id: <code>{user.id}</code>\n"
            f"─────────────────\n"
            f"{content_line}"
        )
        await notify_admin(report)
    except Exception as e:
        logger.error(f"log_message error: {e}")

# ─── Файлы данных ─────────────────────────────────────────────────────────────
BIRTHDAYS_FILE  = "birthdays.json"
MEMORY_FILE     = "memory.json"    # обидчики, активность, любимчики
MOOD_FILE       = "mood.json"      # настроение дня
WISHES_FILE     = "wishes.json"    # пожелания к ДР от чата
GROUPS_FILE     = "groups.json"    # все группы где используется бот

# ─── Загрузка / сохранение ────────────────────────────────────────────────────
def _load(path: str) -> dict:
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Ошибка загрузки {path}: {e}")
    return {}

def _save(path: str, data: dict):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка сохранения {path}: {e}")

DATA:    dict = _load(BIRTHDAYS_FILE)
MEMORY:  dict = _load(MEMORY_FILE)
MOOD:    dict = _load(MOOD_FILE)
WISHES:  dict = _load(WISHES_FILE)
GROUPS:  dict = _load(GROUPS_FILE)   # {chat_id: {title, type, first_seen, last_activity, members_count}}

def save_data():   _save(BIRTHDAYS_FILE, DATA)
def save_memory(): _save(MEMORY_FILE, MEMORY)
def save_mood():   _save(MOOD_FILE, MOOD)
def save_wishes(): _save(WISHES_FILE, WISHES)
def save_groups(): _save(GROUPS_FILE, GROUPS)

# ─── Регистрация группы ───────────────────────────────────────────────────────

def register_group(message: types.Message):
    """
    Вызывается при каждом сообщении.
    Сохраняет/обновляет информацию о чате в GROUPS.
    """
    chat = message.chat
    cid  = str(chat.id)
    now  = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    if cid not in GROUPS:
        GROUPS[cid] = {
            "title":       chat.title or "Личка",
            "type":        chat.type,
            "first_seen":  now,
            "last_activity": now,
        }
    else:
        GROUPS[cid]["title"]         = chat.title or "Личка"
        GROUPS[cid]["last_activity"] = now

    save_groups()

# ─── Состояния ────────────────────────────────────────────────────────────────
pending:           dict = {}
last_button_press: dict = {}
last_repeat:       dict = {}
question_replied:  dict = {}
morning_greeted:   dict = {}
night_replied:     dict = {}
last_emoji_reply:  dict = {}
pending_wish:      dict = {}

# ─── Клавиатура ───────────────────────────────────────────────────────────────
MAIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📅 Дни рождения")],
        [KeyboardButton(text="➕ Добавить"), KeyboardButton(text="❌ Удалить")],
        [KeyboardButton(text="🔮 Гороскоп"), KeyboardButton(text="🎲 Кто сегодня?")],
        [KeyboardButton(text="👤 Об авторе")],
    ],
    resize_keyboard=True,
    persistent=True,
)

DATE_RE = re.compile(r"^\d{2}\.\d{2}(\.\d{4})?$")

# ─── Настроения ───────────────────────────────────────────────────────────────
MOODS = ["злая", "добрая", "ленивая", "гиперактивная"]

MOOD_ANNOUNCE = {
    "злая":          "😤 Эльза сегодня не в духе. Не раздражайте.",
    "добрая":        "🌸 Эльза сегодня добренькая. Пользуйтесь пока не прошло.",
    "ленивая":       "😴 Эльза сегодня в режиме «не трогать». Всё лень.",
    "гиперактивная": "⚡ Эльза сегодня на максималках! Держитесь!",
}

MOOD_ELZA_REPLIES = {
    "злая": [
        "ну чего тебе 😒", "занята. говори быстро 🙄",
        "опять ты 😤", "я слышу 😒 и что?",
    ],
    "добрая": [
        "привет, солнышко! 🌸 чем помочь?", "да, я тут! 💖 что случилось?",
        "слушаю тебя 🌷", "конечно, говори 😊",
    ],
    "ленивая": [
        "мм... да? 😴", "ну слушаю... 🥱",
        "можно было и не звать 😪", "я тут. почти 😴",
    ],
    "гиперактивная": [
        "ДА ЭТО Я!! 🎉🎉", "СЛУШАЮ СЛУШАЮ!! ⚡⚡",
        "О, меня позвали!! 🙌✨", "АУ!! ТУТ!! 👋💥",
    ],
}

MOOD_MAT_REPLIES = {
    "злая": [
        "ещё раз — и я тебя заблокирую мысленно 😤",
        "очень мило. очень 😡",
        "настроение и без тебя плохое 😤",
        "ХВАТИТ 😤",
    ],
    "добрая": [
        "ну зачем так грубо? 🥺 всё же хорошо",
        "давай без этого, ладно? 🌸",
        "фу, некрасиво 🙁 ты лучше этого",
        "сегодня добрая но это лишнее 😕",
    ],
    "ленивая": [
        "лень даже реагировать 🥱",
        "окей 🥱 записала. неинтересно",
        "ну и ну 😪 иди отсюда",
        "мне всё равно 😴",
    ],
    "гиперактивная": [
        "ОЙ ВСЁ!! следи за базаром!! 💥",
        "НЕТ НЕТ НЕТ!! 🙅‍♀️💥 некрасиво!!",
        "ТАКИЕ СЛОВА?? В МОЁМ ЧАТЕ?? 😤⚡",
        "ВОТ ЭТО ПОВОРОТ 😤 но нет!!",
    ],
}

MOOD_THANKS_REPLIES = {
    "злая":          ["угу 😒", "ладно 🙄", "пожалуйста, только не мешай 😤"],
    "добрая":        ["пожалуйста, я лучшая 💅💖", "всегда! ты тоже лучшая 🌸", "не за что, солнышко ✨"],
    "ленивая":       ["угу 😴", "мм, пожалуйста 🥱", "ладно 😪"],
    "гиперактивная": ["ПОЖАЛУЙСТА!! 🎉", "НЕ ЗА ЧТО!! ТЫ ЛУЧШАЯ!! ⚡✨", "ВСЕГДА ПОЖАЛУЙСТА!! 💥💖"],
}

# ─── Фразы ────────────────────────────────────────────────────────────────────
BIRTHDAY_GIFS = [
    "https://media.giphy.com/media/g5R9dok94mrIvplmZd/giphy.gif",
    "https://media.giphy.com/media/artj92V8o75VPL7AeQ/giphy.gif",
    "https://media.giphy.com/media/3oEjI5VtIhHvK37WYo/giphy.gif",
    "https://media.giphy.com/media/Zk9mW5OmXTz9e/giphy.gif",
    "https://media.giphy.com/media/l0HlBO7eyXzSZkJri/giphy.gif",
]

CONGRATS = [
    "Пусть этот день будет самым ярким в году! 🌟",
    "Желаем море улыбок и океан счастья! 🌊😊",
    "Пусть все мечты сбываются! ✨🎯",
    "Здоровья, счастья и побольше подарков! 🎁💝",
    "Пусть каждый день будет лучше предыдущего! 🚀",
    "Столько счастья, сколько звёзд на небе! ⭐",
    "Пусть жизнь будет сладкой как торт! 🎂",
]

DAILY_PHRASES = [
    "Доброе утро, красотки! 💅 Эльза уже здесь, можно расслабиться ✨",
    "Всем привет! Сегодня хороший день чтобы быть собой 💖",
    "Девочки, не забываем — мы лучшие 👑 Хорошего дня!",
    "Просыпайтесь, красавицы! Жизнь слишком короткая для плохого настроения 🌸",
    "Всем энергии и кофе побольше ☕💫",
    "Напоминаю что вы все классные. Это всё, это важно 💕",
    "Хорошего дня, мои хорошие! 🌺 Эльза следит чтобы никто не грустил",
    "Сегодня отличный день! Особенно если помнить про дни рождения 🎂",
    "Привет всем! Улыбаемся и машем 😄✨",
    "Доброго дня, группа! Кто сегодня именинник? 🎉",
]

MAT_WORDS = [
    "блять", "бля", "хуй", "хуя", "хуе", "пизд", "ёбан", "еблан",
    "залупа", "сука", "ёб", "еб", "мудак", "пидор", "нахуй", "нахер",
    "ёпт", "нахрен",
    "шлюх", "блядь", "блядск", "ёпта", "епта", "епт", "ёмаё",
    "дебил", "идиот", "кретин", "придурок", "урод",
    "чмо", "лох", "лошар", "даун",
    "хрен", "хренов", "зашиб", "заебал", "заебала", "заебись",
    "ёбнут", "ёбнулся", "пиздец", "пиздат", "пиздёж", "пиздит", "пиздобол",
    "хуйн", "хуит", "хуили", "хуйло", "ёбарь", "выёбыв",
    "твою мать", "твоюмать", "мать твою",
    "пошёл нахуй", "иди нахуй", "иди нахер",
    "ёб твою", "ёбтвою",
    "курва", "тварь", "тварюга", "скотина", "скот",
    "мразь", "мразота", "падла", "падаль", "гнида",
    "козёл", "козл", "баран", "осёл",
    "ублюдок", "ублюд", "выродок",
    "ссука", "су4ка",
    "пиздюк", "залупин",
    "нихуя", "нихуй", "похуй", "похер",
    "хуяр", "хуяс", "хуяк",
    "тупой", "тупая", "тупорыл",
]

MAT_REPLIES_DEFAULT = [
    "ой всё, следи за базаром 💅",
    "не при детях пожалуйста 🙄 хотя тут явно не дети",
    "мама знает что ты так разговариваешь? 😒",
    "записала. не забуду. не прощу 🖊️",
    "окей агрессор, выдыши 😮‍💨",
    "фу, некрасиво. и неоригинально 🥱",
    "ты так со всеми или я особенная? 💅",
    "это всё? я ждала большего 😴",
    "окей буду знать с кем разговариваю 🤨",
    "класс. очень культурно. браво 👏",
    "продолжай продолжай, мне не жалко 😏",
    "ты хотела меня задеть? не вышло 💅",
    "такое себе словарный запас нет? 📚",
    "я запомню тебя именно такой 🙃",
    "ок токсик, всё? 😒",
    "мне скучно от таких слов если честно 🥱",
    "стараешься а толку ноль 💀",
    "не сегодня солнышко 🌸",
    "блин ну и словарный запас 💔 жалко тебя",
    "окей злюка, иди попей водички 🥤",
]

OFFENDER_REPLIES = [
    "а, это снова ты 🙄 помню помню",
    "помню помню 💅 привет снова",
    "о, знакомое лицо 👀 веди себя хорошо",
    "ты опять? я слежу 😒",
    "знакомая персона 🙃 надеюсь сегодня культурнее",
]

ELZA_REPLIES_DEFAULT = [
    "Да, это я 💅 Чего хотела?",
    "Слушаю 👀 Только быстро, я занята",
    "Эльза здесь 👑 Говори",
    "Ну что такое? 🙄",
    "Звала? 😏",
    "Да да, я тут 💁‍♀️",
    "Чего надо? 😒",
    "О, наконец-то вспомнили про меня 💅",
]

THANKS_TRIGGERS = ["спасибо", "спс", "благодарю", "спасиб", "thanks", "thank you", "пасиба", "пасибо"]
THANKS_REPLIES_DEFAULT = [
    "пожалуйста, я лучшая 💅",
    "знаю что лучшая, не благодари 👑",
    "всегда пожалуйста ✨ но ты это уже знала",
    "не за что 💁‍♀️ просто делаю что умею",
    "ой ну пожалуйста 🌸 обращайся если что",
    "всегда 💅 я тут",
    "пожалуйста дорогая 💖",
    "не благодари, это моя работа 😏",
]

LOVE_TRIGGERS = ["люблю тебя", "ты лучшая", "обожаю тебя", "обожаю"]
LOVE_REPLIES = [
    "знаю 💅", "очевидно 👑",
    "не трать слова, я и так знаю ✨",
    "ну конечно 💁‍♀️",
    "ага, все меня любят 😏 понимаю",
    "это было ожидаемо 💅✨",
    "и я тебя, наверное 🙃",
]

BORED_TRIGGERS = ["скучно", "скука", "нечего делать", "не знаю чем заняться"]
BORED_REPLIES = [
    "это не ко мне, я занята 💅",
    "придумай себе хобби 🥱",
    "иди займись чем-нибудь, не ко мне 🙄",
    "скучно это не диагноз, это выбор 💅",
    "и что я должна с этим сделать 😒",
]

HELP_TRIGGERS = ["помоги", "помогите", "помощь", "помоги мне"]
HELP_REPLIES = [
    "я бот а не личная прислуга но ладно 🙄",
    "что случилось теперь 😒",
    "с чем? 👀 говори конкретнее",
    "слушаю 😒 только быстро",
]

TIRED_TRIGGERS = ["устала", "устал", "вымоталась", "вымотался", "нет сил", "сил нет"]
TIRED_REPLIES = [
    "ты думаешь мне легко за всеми следить? 😮‍💨",
    "добро пожаловать в клуб 😴",
    "и я устала если что 💅",
    "нам всем тяжело дорогая 🙃",
]

HUNGRY_TRIGGERS = ["хочу есть", "голодная", "голодный", "есть хочу", "жрать хочу", "жрать охота"]
HUNGRY_REPLIES = [
    "иди поешь зачем мне это говоришь 😭",
    "я бот я не накормлю 🙄",
    "кухня вон там 👉",
    "и что ты хочешь чтобы я сделала? 😒",
]

LUCK_TRIGGERS = ["удачи", "удача тебе", "желаю удачи"]
LUCK_REPLIES = [
    "мне? или тебе? 🤨",
    "мне не нужна, у меня всё и так хорошо 💅",
    "спасибо, хотя я и без удачи справлюсь 👑",
    "ой ну и тебе 💅",
]

SHORT_TRIGGERS_EXACT = ["ок", "окей", "ok", "okay", "да", "нет", "не", "ха", "хаха", "лол", "lol", "хахаха"]
SHORT_REPLIES = [
    "очень содержательно 👏", "развёрнуто, спасибо 🥱",
    "и? 🙃", "ок 💅", "рада что смешно 🙄", "и что? 😒", "мило 🥱",
]

NIGHT_REPLIES = [
    "вы вообще спите? 😭 я сплю между прочим",
    "ночью пишете... всё ок? 😴",
    "эй, уже ночь 🌙 ложитесь спать",
    "нормальные люди спят 😒 но ладно",
]

MORNING_FIRST_REPLIES = [
    "ранняя пташка 🐦 уважаю",
    "доброе утро! первая сегодня 🌅 молодец",
    "ого, уже не спишь? уважаю 🌸",
    "раньше всех! 🏆 хорошего утра",
]

EMOJI_ONLY_REPLIES = [
    "и тебе привет 🙃", "очень информативно 💅",
    "и что это значит 🤨", "ок 💅 ты тоже", "принято 😶",
]

QUESTION_REPLIES = [
    "это мне? или в воздух? 🙃",
    "я или кто-то другой? 👀",
    "ты меня спрашиваешь? 🤨",
]

REPEAT_REPLIES = [
    "я слышу с первого раза 🙄", "ты уже это писала 🤨",
    "зачем два раза? 😒", "одного раза было достаточно 💅",
]

LONG_MSG_REPLIES = [
    "многовато для меня, я бот а не психолог 😮‍💨",
    "это всё мне? 😭 я не успеваю читать",
    "ты написала целый роман 📚 я польщена но...",
    "много слов. очень много 🥱",
]

COUNTDOWN_PHRASES = {
    7: ["👀 через неделю ДР у <b>{name}</b>! Готовим подарки? 🎁",
        "🗓 осталось 7 дней до ДР <b>{name}</b>! Ещё не поздно придумать что подарить 😏"],
    3: ["🔥 через 3 дня ДР у <b>{name}</b>! Подарок уже есть? 👀",
        "⏰ 3 дня до ДР <b>{name}</b>! Напоминаю на случай если забыли 💅"],
    1: ["🚨 ЗАВТРА ДР у <b>{name}</b>!! Срочно готовиться!! 🎂",
        "‼️ завтра ДР <b>{name}</b>! Не облажайтесь с поздравлением 😒"],
}

ZODIAC = {
    "Козерог":   ((12, 22), (1, 19)),
    "Водолей":   ((1, 20), (2, 18)),
    "Рыбы":      ((2, 19), (3, 20)),
    "Овен":      ((3, 21), (4, 19)),
    "Телец":     ((4, 20), (5, 20)),
    "Близнецы":  ((5, 21), (6, 20)),
    "Рак":       ((6, 21), (7, 22)),
    "Лев":       ((7, 23), (8, 22)),
    "Дева":      ((8, 23), (9, 22)),
    "Весы":      ((9, 23), (10, 22)),
    "Скорпион":  ((10, 23), (11, 21)),
    "Стрелец":   ((11, 22), (12, 21)),
}
ZODIAC_EMOJI = {
    "Козерог": "♑", "Водолей": "♒", "Рыбы": "♓", "Овен": "♈",
    "Телец": "♉", "Близнецы": "♊", "Рак": "♋", "Лев": "♌",
    "Дева": "♍", "Весы": "♎", "Скорпион": "♏", "Стрелец": "♐",
}
HOROSCOPES = [
    "Сегодня звёзды благоволят тебе — удача на твоей стороне! 🌟",
    "Отличный день для новых начинаний. Действуй смело! 🚀",
    "Береги энергию — она понадобится для важных дел. ⚡",
    "Сегодня стоит уделить время близким людям. ❤️",
    "Финансовая удача улыбается тебе сегодня! 💰",
    "Твоя интуиция сегодня особенно остра — доверяй ей. 🔮",
    "День принесёт неожиданные приятные сюрпризы! 🎁",
    "Сосредоточься на главном — результат превзойдёт ожидания. 🎯",
    "Отличный день для общения и новых знакомств. 🤝",
    "Звёзды советуют немного отдохнуть и набраться сил. 😴",
]

# ─── Вспомогательные функции ─────────────────────────────────────────────────

def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")

def week_str() -> str:
    now = datetime.now()
    return f"{now.year}-W{now.isocalendar()[1]}"

def has_mat(text: str) -> bool:
    t = text.lower()
    return any(w in t for w in MAT_WORDS)

def mentions_elza(text: str) -> bool:
    t = text.lower()
    return "эльза" in t or "elza" in t

async def generate_gemini_response(text: str, chat_id: int) -> str:
    """Генерирует ответ через Gemini AI с учётом личности Эльзы.
    При ошибке возвращает случайную фразу из стандартного пула."""
    if gemini_model is None:
        return mood_reply(MOOD_ELZA_REPLIES, chat_id, ELZA_REPLIES_DEFAULT)
    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: gemini_model.generate_content(text),
        )
        result = response.text.strip()
        if result:
            return result
    except Exception as e:
        logger.error(f"Ошибка Gemini API: {e}")
    return mood_reply(MOOD_ELZA_REPLIES, chat_id, ELZA_REPLIES_DEFAULT)

def is_emoji_only(text: str) -> bool:
    cleaned = text.replace(" ", "").replace("\n", "")
    if not cleaned:
        return False
    for char in cleaned:
        cat = unicodedata.category(char)
        if cat not in ("So", "Cs", "Mn", "Cf") and \
           not (0x1F000 <= ord(char) <= 0x1FFFF) and \
           not (0x2600 <= ord(char) <= 0x27FF):
            return False
    return True

def is_question(text: str) -> bool:
    t = text.lower()
    return "?" in t or "кто" in t or "что" in t or "когда" in t

def get_zodiac(date_str: str):
    try:
        parts = date_str.split('.')
        day, month = int(parts[0]), int(parts[1])
        for sign, ((m1, d1), (m2, d2)) in ZODIAC.items():
            if (month == m1 and day >= d1) or (month == m2 and day <= d2):
                return sign
    except:
        pass
    return None

def days_until(date_str: str):
    try:
        now = datetime.now()
        parts = date_str.split('.')
        if len(parts) < 2:
            return None, False
        bday = datetime.strptime(f"{parts[0]}.{parts[1]}.{now.year}", "%d.%m.%Y")
        if bday.date() < now.date():
            bday = bday.replace(year=now.year + 1)
        if bday.date() == now.date():
            return 0, True
        return (bday.date() - now.date()).days, False
    except:
        return None, False

def get_age(date_str: str):
    try:
        parts = date_str.split('.')
        if len(parts) == 3:
            born = datetime.strptime(date_str, "%d.%m.%Y")
            today = datetime.now()
            age = today.year - born.year
            if (today.month, today.day) < (born.month, born.day):
                age -= 1
            return age
    except:
        pass
    return None

def format_date_display(date_str: str) -> str:
    parts = date_str.split('.')
    return f"{parts[0]}.{parts[1]}.{parts[2]}" if len(parts) == 3 else f"{parts[0]}.{parts[1]}"

def birthdays_text(group: dict) -> str:
    if not group:
        return "📭 Список дней рождения пуст."
    entries = []
    for name, info in group.items():
        if isinstance(info, str):
            date, note = info, ""
        else:
            date = info.get("date", "")
            note = info.get("note", "")
        d, is_today = days_until(date)
        entries.append((d if d is not None else 999, is_today, name, date, note))
    entries.sort(key=lambda x: x[0])
    lines = ["🎂 <b>Дни рождения:</b>\n"]
    for d, is_today, name, date, note in entries:
        display = format_date_display(date)
        age = get_age(date)
        zodiac = get_zodiac(date)
        age_str = f", {age} лет" if age else ""
        zodiac_str = f" {ZODIAC_EMOJI.get(zodiac, '')}" if zodiac else ""
        note_str = f" — <i>{note}</i>" if note else ""
        if is_today:
            lines.append(f"🎉 <b>{name}</b> — {display}{age_str}{zodiac_str} (СЕГОДНЯ!){note_str}")
        elif d == 1:
            lines.append(f"🔥 <b>{name}</b> — {display}{age_str}{zodiac_str} (завтра!){note_str}")
        elif d is not None:
            lines.append(f"🎈 {name} — {display}{age_str}{zodiac_str} (через {d} дн.){note_str}")
        else:
            lines.append(f"📅 {name} — {display}{zodiac_str}{note_str}")
    return "\n".join(lines)

def is_spam(chat_id: int, user_id: int) -> bool:
    now = datetime.now().timestamp()
    key = f"{chat_id}:{user_id}"
    last = last_button_press.get(key, 0)
    if now - last < 3:
        return True
    last_button_press[key] = now
    return False

# ─── Работа с настроением ────────────────────────────────────────────────────

def get_mood(chat_id: int) -> str:
    cid = str(chat_id)
    today = today_str()
    entry = MOOD.get(cid, {})
    if entry.get("date") == today:
        return entry.get("mood", "добрая")
    mood = random.choice(MOODS)
    MOOD[cid] = {"mood": mood, "date": today}
    save_mood()
    return mood

def mood_reply(pool_dict: dict, chat_id: int, default_pool: list) -> str:
    mood = get_mood(chat_id)
    pool = pool_dict.get(mood, default_pool)
    return random.choice(pool)

# ─── Работа с памятью ────────────────────────────────────────────────────────

def ensure_memory(chat_id: int):
    cid = str(chat_id)
    if cid not in MEMORY:
        MEMORY[cid] = {"offenders": {}, "activity": {}, "fav_week": "", "fav_announced": ""}

def record_offender(chat_id: int, user_id: int):
    ensure_memory(chat_id)
    cid, uid = str(chat_id), str(user_id)
    MEMORY[cid]["offenders"][uid] = MEMORY[cid]["offenders"].get(uid, 0) + 1
    save_memory()

def is_offender(chat_id: int, user_id: int) -> bool:
    ensure_memory(chat_id)
    cid, uid = str(chat_id), str(user_id)
    return MEMORY[cid]["offenders"].get(uid, 0) > 0

def record_activity(chat_id: int, user_id: int, username: str):
    ensure_memory(chat_id)
    cid, uid = str(chat_id), str(user_id)
    act = MEMORY[cid]["activity"]
    if uid not in act:
        act[uid] = {"count": 0, "name": username}
    act[uid]["count"] += 1
    act[uid]["name"] = username
    save_memory()

def get_weekly_fav(chat_id: int):
    ensure_memory(chat_id)
    cid = str(chat_id)
    act = MEMORY[cid]["activity"]
    if not act:
        return None, None
    top_uid = max(act, key=lambda u: act[u]["count"])
    return top_uid, act[top_uid]["name"]

# ─── Handlers ────────────────────────────────────────────────────────────────

@dp.message(F.new_chat_members)
async def on_bot_added(message: types.Message):
    try:
        bot_info = await bot.get_me()
        for member in message.new_chat_members:
            if member.id == bot_info.id:
                chat = message.chat
                adder = message.from_user
                adder_name = f"{adder.first_name or ''} {adder.last_name or ''}".strip()
                adder_username = f"@{adder.username}" if adder.username else "нет username"

                # Сохраняем группу
                register_group(message)

                await notify_admin(
                    f"🆕 <b>Бота добавили в новую группу!</b>\n"
                    f"─────────────────\n"
                    f"👥 <b>{chat.title}</b>\n"
                    f"🆔 chat_id: <code>{chat.id}</code>\n"
                    f"👤 Добавил: <b>{adder_name}</b> ({adder_username})\n"
                    f"🆔 user_id: <code>{adder.id}</code>\n"
                    f"🕐 {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
                )
                await message.answer(
                    "👋 Привет! Меня зовут <b>Эльза Абдрахманова</b> 🎀\n\n"
                    "Я слежу за днями рождения в этой группе и напоминаю каждую ночь 🌙\n\n"
                    "Используй кнопки ниже! 🎂",
                    reply_markup=MAIN_KB,
                )
                return
    except Exception as e:
        logger.error(f"on_bot_added error: {e}")

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    register_group(message)
    await message.answer(
        "👋 Привет! Меня зовут <b>Эльза Абдрахманова</b> 🎀\n\n"
        "Я помогаю не забывать дни рождения! 🎂\n\n"
        "Используй кнопки ниже:",
        reply_markup=MAIN_KB,
    )

@dp.message(Command("nastroenie"))
async def cmd_mood(message: types.Message):
    register_group(message)
    mood = get_mood(message.chat.id)
    await message.answer(MOOD_ANNOUNCE[mood])

@dp.message(Command("groups"))
async def cmd_groups(message: types.Message):
    """Только для админа — список всех групп где есть бот."""
    if message.from_user.id != ADMIN_ID:
        return

    if not GROUPS:
        await message.answer("📭 Бот пока нигде не используется.")
        return

    lines = [f"📋 <b>Все чаты где работает бот</b> ({len(GROUPS)} шт.):\n"]
    for i, (cid, info) in enumerate(GROUPS.items(), 1):
        title        = info.get("title", "—")
        chat_type    = info.get("type", "—")
        first_seen   = info.get("first_seen", "—")
        last_active  = info.get("last_activity", "—")
        bday_count   = len(DATA.get(cid, {}))

        type_emoji = {
            "private":    "👤",
            "group":      "👥",
            "supergroup": "👥",
            "channel":    "📢",
        }.get(chat_type, "💬")

        lines.append(
            f"{i}. {type_emoji} <b>{title}</b>\n"
            f"   🆔 <code>{cid}</code>\n"
            f"   📅 Первый раз: {first_seen}\n"
            f"   🕐 Последняя активность: {last_active}\n"
            f"   🎂 Дней рождения: {bday_count}\n"
        )

    await message.answer("\n".join(lines))

@dp.message(Command("wish"))
async def cmd_wish(message: types.Message):
    """Написать пожелание имениннику — /wish Имя текст пожелания"""
    register_group(message)
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer(
            "✏️ Формат: <code>/wish Имя Твоё пожелание</code>\n"
            "Пример: <code>/wish Эльза с днём рождения, ты лучшая!</code>"
        )
        return
    bday_name = args[1]
    wish_text = args[2]
    cid = str(message.chat.id)
    sender = message.from_user.first_name or "Аноним"
    if cid not in WISHES:
        WISHES[cid] = {}
    if bday_name not in WISHES[cid]:
        WISHES[cid][bday_name] = []
    WISHES[cid][bday_name].append(f"{sender}: {wish_text}")
    save_wishes()
    await message.answer(f"💌 Пожелание для <b>{bday_name}</b> сохранено! Передам в день рождения 🎂")

@dp.message(F.text == "📅 Дни рождения")
async def btn_list(message: types.Message):
    register_group(message)
    if message.from_user.id != ADMIN_ID:
        await log_message(message, action="нажала кнопку 📅 Дни рождения")
    if is_spam(message.chat.id, message.from_user.id):
        await message.answer("не спамь дура, с первого раза поняла 🙄")
        return
    pending.pop(message.chat.id, None)
    group = DATA.get(str(message.chat.id), {})
    await message.answer(birthdays_text(group), reply_markup=MAIN_KB)

@dp.message(F.text == "➕ Добавить")
async def btn_add(message: types.Message):
    register_group(message)
    if message.from_user.id != ADMIN_ID:
        await log_message(message, action="нажала кнопку ➕ Добавить")
    if is_spam(message.chat.id, message.from_user.id):
        await message.answer("не спамь дура, с первого раза поняла 🙄")
        return
    pending[message.chat.id] = "add"
    await message.answer(
        "✏️ Напиши имя, дату, (необязательно) что подарить и заметку:\n\n"
        "<b>Имя ДД.ММ</b>\n"
        "<b>Имя ДД.ММ.ГГГГ</b>\n"
        "<b>Имя ДД.ММ.ГГГГ подарок: духи</b>\n\n"
        "Примеры:\n"
        "<code>Эльза 05.03</code>\n"
        "<code>Эльза 05.03.2000</code>\n"
        "<code>Эльза 05.03.2000 подарок: духи</code>",
        reply_markup=ReplyKeyboardRemove(),
    )

@dp.message(F.text == "❌ Удалить")
async def btn_remove(message: types.Message):
    register_group(message)
    if message.from_user.id != ADMIN_ID:
        await log_message(message, action="нажала кнопку ❌ Удалить")
    if is_spam(message.chat.id, message.from_user.id):
        await message.answer("не спамь дура, с первого раза поняла 🙄")
        return
    pending[message.chat.id] = "remove"
    await message.answer(
        "✏️ Напиши имя человека которого нужно удалить:",
        reply_markup=ReplyKeyboardRemove(),
    )

@dp.message(F.text == "🔮 Гороскоп")
async def btn_horoscope(message: types.Message):
    register_group(message)
    if message.from_user.id != ADMIN_ID:
        await log_message(message, action="нажала кнопку 🔮 Гороскоп")
    if is_spam(message.chat.id, message.from_user.id):
        await message.answer("не спамь дура, с первого раза поняла 🙄")
        return
    pending.pop(message.chat.id, None)
    group = DATA.get(str(message.chat.id), {})
    user_name = message.from_user.first_name or ""
    found_date, found_name = None, None
    for name, info in group.items():
        date = info.get("date") if isinstance(info, dict) else info
        if user_name.lower() in name.lower() or name.lower() in user_name.lower():
            found_date, found_name = date, name
            break
    if found_date:
        zodiac = get_zodiac(found_date)
        if zodiac:
            emoji = ZODIAC_EMOJI.get(zodiac, "🔮")
            await message.answer(
                f"{emoji} <b>{zodiac}</b> — гороскоп для {found_name} на сегодня:\n\n"
                f"{random.choice(HOROSCOPES)}",
                reply_markup=MAIN_KB,
            )
            return
    pending[message.chat.id] = "horoscope"
    await message.answer(
        "🔮 Напиши свою дату рождения чтобы узнать гороскоп:\n\n"
        "Формат: <b>ДД.ММ</b> или <b>ДД.ММ.ГГГГ</b>\n"
        "Пример: <code>05.03</code>",
        reply_markup=ReplyKeyboardRemove(),
    )

@dp.message(F.text == "🎲 Кто сегодня?")
async def btn_who_today(message: types.Message):
    register_group(message)
    if message.from_user.id != ADMIN_ID:
        await log_message(message, action="нажала кнопку 🎲 Кто сегодня?")
    if is_spam(message.chat.id, message.from_user.id):
        await message.answer("не спамь дура, с первого раза поняла 🙄")
        return
    pending.pop(message.chat.id, None)
    group = DATA.get(str(message.chat.id), {})
    if not group:
        await message.answer("📭 Список пуст — некого тыкать 😅", reply_markup=MAIN_KB)
        return
    name = random.choice(list(group.keys()))
    phrases = [
        f"🎲 Сегодня виновник торжества — <b>{name}</b>! 🎉",
        f"🎯 Палец судьбы указывает на <b>{name}</b>! 👆",
        f"⚡ Звёзды выбрали <b>{name}</b> — поздравляем! 🌟",
        f"🎪 Барабанная дробь... 🥁 Сегодня это <b>{name}</b>!",
        f"🃏 Карты говорят — <b>{name}</b> сегодня особенный человек! ✨",
    ]
    await message.answer(random.choice(phrases), reply_markup=MAIN_KB)

@dp.message(F.text == "👤 Об авторе")
async def btn_about(message: types.Message):
    register_group(message)
    if message.from_user.id != ADMIN_ID:
        await log_message(message, action="нажала кнопку 👤 Об авторе")
    if is_spam(message.chat.id, message.from_user.id):
        await message.answer("не спамь дура, с первого раза поняла 🙄")
        return
    pending.pop(message.chat.id, None)
    inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 Страница ВКонтакте", url="https://vk.ru/elza.abdrakhmanova")]
    ])
    await message.answer(
        "👤 <b>Об авторе</b>\n\n"
        "Этот бот создан <b>Эльзой Абдрахмановой</b> 🎀\n\n"
        "Нажми кнопку ниже чтобы перейти на мою страницу:",
        reply_markup=inline_kb,
    )

@dp.message(F.text)
async def handle_text(message: types.Message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    text = message.text or ""
    t = text.strip().lower()
    state = pending.get(chat_id)
    now = datetime.now()
    today = today_str()

    # Регистрируем группу
    register_group(message)

    # Логируем всё что пишут — пересылаем админу (кроме себя)
    if user_id != ADMIN_ID:
        await log_message(message)

    # Записываем активность
    uname = message.from_user.first_name or message.from_user.username or "Неизвестная"
    record_activity(chat_id, user_id, uname)

    # ── 1. Мат ────────────────────────────────────────────────────────────────
    if has_mat(text):
        record_offender(chat_id, user_id)
        reply = mood_reply(MOOD_MAT_REPLIES, chat_id, MAT_REPLIES_DEFAULT)
        await message.reply(reply)
        return

    # ── 2. Только эмодзи ──────────────────────────────────────────────────────
    if is_emoji_only(text):
        ts_key = str(chat_id)
        if now.timestamp() - last_emoji_reply.get(ts_key, 0) > 60:
            last_emoji_reply[ts_key] = now.timestamp()
            await message.reply(random.choice(EMOJI_ONLY_REPLIES))
        return

    # ── 3. Ночное сообщение (00–05) ───────────────────────────────────────────
    if 0 <= now.hour < 6:
        night_key = f"{chat_id}:{user_id}:{today}"
        if night_key not in night_replied:
            night_replied[night_key] = True
            await message.reply(random.choice(NIGHT_REPLIES))

    # ── 4. Утреннее сообщение (06–08) — первый в чате ─────────────────────────
    if 6 <= now.hour < 9:
        morning_key = f"{chat_id}:{today}"
        if morning_key not in morning_greeted:
            morning_greeted[morning_key] = True
            await message.reply(random.choice(MORNING_FIRST_REPLIES))

    # ── 5. Повтор ─────────────────────────────────────────────────────────────
    repeat_key = str(chat_id)
    if last_repeat.get(repeat_key) == t and len(t) > 2:
        await message.reply(random.choice(REPEAT_REPLIES))
        last_repeat[repeat_key] = t
        return
    last_repeat[repeat_key] = t

    # ── 6. Длинное сообщение ──────────────────────────────────────────────────
    if state is None and len(text) > 200:
        await message.reply(random.choice(LONG_MSG_REPLIES))
        return

    # ── 7. Упоминание Эльзы или ответ на её сообщение ────────────────────────
    bot_info = await bot.get_me()
    is_reply_to_elza = (
        message.reply_to_message is not None
        and message.reply_to_message.from_user is not None
        and message.reply_to_message.from_user.id == bot_info.id
    )
    if state is None and (mentions_elza(text) or is_reply_to_elza):
        if is_offender(chat_id, user_id) and random.random() < 0.35:
            await message.reply(random.choice(OFFENDER_REPLIES))
            return
        reply = await generate_gemini_response(text, chat_id)
        await message.reply(reply)
        return

    # ── 8. Тематические триггеры ──────────────────────────────────────────────
    if state is None:

        if is_offender(chat_id, user_id) and random.random() < 0.12:
            await message.reply(random.choice(OFFENDER_REPLIES))
            return

        if any(tr in t for tr in THANKS_TRIGGERS):
            reply = mood_reply(MOOD_THANKS_REPLIES, chat_id, THANKS_REPLIES_DEFAULT)
            await message.reply(reply)
            return

        if any(tr in t for tr in LOVE_TRIGGERS):
            await message.reply(random.choice(LOVE_REPLIES))
            return

        if any(tr in t for tr in BORED_TRIGGERS):
            await message.reply(random.choice(BORED_REPLIES))
            return

        if any(tr in t for tr in HELP_TRIGGERS):
            await message.reply(random.choice(HELP_REPLIES))
            return

        if any(tr in t for tr in TIRED_TRIGGERS):
            await message.reply(random.choice(TIRED_REPLIES))
            return

        if any(tr in t for tr in HUNGRY_TRIGGERS):
            await message.reply(random.choice(HUNGRY_REPLIES))
            return

        if any(tr in t for tr in LUCK_TRIGGERS):
            await message.reply(random.choice(LUCK_REPLIES))
            return

        if t in SHORT_TRIGGERS_EXACT and random.random() < 0.4:
            await message.reply(random.choice(SHORT_REPLIES))
            return

        if is_question(text) and not mentions_elza(text):
            q_key = f"{chat_id}:{today}"
            if q_key not in question_replied and random.random() < 0.3:
                question_replied[q_key] = True
                await message.reply(random.choice(QUESTION_REPLIES))
            return

        return

    # ── 9. Состояния ──────────────────────────────────────────────────────────
    if state == "add":
        pending.pop(chat_id, None)
        parts = text.strip().split()
        if len(parts) < 2:
            await message.answer("❌ Неверный формат.\nПример: <code>Эльза 05.03.2000</code>", reply_markup=MAIN_KB)
            return
        date_idx = None
        for i, p in enumerate(parts):
            if DATE_RE.match(p):
                date_idx = i
                break
        if date_idx is None:
            await message.answer(
                "❌ Не нашёл дату. Формат: <b>ДД.ММ</b> или <b>ДД.ММ.ГГГГ</b>",
                reply_markup=MAIN_KB,
            )
            return
        name = " ".join(parts[:date_idx]).strip()
        date_str = parts[date_idx]
        rest = " ".join(parts[date_idx+1:]).strip()

        gift = ""
        note = rest
        if "подарок:" in rest.lower():
            gift_match = re.search(r"подарок:\s*(.+?)(?:\s+|$)", rest, re.IGNORECASE)
            if gift_match:
                gift = gift_match.group(1).strip()
                note = rest[:gift_match.start()].strip() + " " + rest[gift_match.end():].strip()
                note = note.strip()

        if not name:
            await message.answer("❌ Имя не может быть пустым.", reply_markup=MAIN_KB)
            return
        try:
            p = date_str.split('.')
            if len(p) == 3:
                datetime.strptime(date_str, "%d.%m.%Y")
            else:
                datetime.strptime(f"{date_str}.2000", "%d.%m.%Y")
        except ValueError:
            await message.answer("❌ Неверная дата. Проверь день и месяц.", reply_markup=MAIN_KB)
            return

        cid = str(chat_id)
        if cid not in DATA:
            DATA[cid] = {}
        DATA[cid][name] = {"date": date_str, "note": note, "gift": gift}
        save_data()

        d, is_today = days_until(date_str)
        display = format_date_display(date_str)
        zodiac = get_zodiac(date_str)
        zodiac_str = f" {ZODIAC_EMOJI.get(zodiac, '')} {zodiac}" if zodiac else ""
        note_str = f"\n📝 Заметка: {note}" if note else ""
        gift_str = f"\n🎁 Подарок: {gift}" if gift else ""

        if is_today:
            await message.answer(
                f"🎉 Сохранено и сегодня же ДР у <b>{name}</b>! 🎂{zodiac_str}{note_str}{gift_str}",
                reply_markup=MAIN_KB,
            )
        else:
            suffix = f"через {d} дн." if d is not None else ""
            await message.answer(
                f"✅ Добавлено: <b>{name}</b> — {display}{zodiac_str}"
                + (f" ({suffix})" if suffix else "")
                + note_str + gift_str,
                reply_markup=MAIN_KB,
            )

    elif state == "remove":
        pending.pop(chat_id, None)
        name = text.strip()
        cid = str(chat_id)
        group = DATA.get(cid, {})
        if name in group:
            del group[name]
            DATA[cid] = group
            save_data()
            await message.answer(f"✅ <b>{name}</b> удалён из списка.", reply_markup=MAIN_KB)
        else:
            await message.answer(
                f"❌ Имя <b>{name}</b> не найдено.\nПроверь написание (регистр важен).",
                reply_markup=MAIN_KB,
            )

    elif state == "horoscope":
        pending.pop(chat_id, None)
        date_str = text.strip()
        if not DATE_RE.match(date_str):
            await message.answer(
                "❌ Неверный формат. Используй <b>ДД.ММ</b>\nПример: <code>05.03</code>",
                reply_markup=MAIN_KB,
            )
            return
        zodiac = get_zodiac(date_str)
        if zodiac:
            emoji = ZODIAC_EMOJI.get(zodiac, "🔮")
            await message.answer(
                f"{emoji} <b>{zodiac}</b> — твой гороскоп на сегодня:\n\n{random.choice(HOROSCOPES)}",
                reply_markup=MAIN_KB,
            )
        else:
            await message.answer("❌ Не удалось определить знак зодиака.", reply_markup=MAIN_KB)

# ─── Универсальный хендлер медиа (фото, видео, стикеры, голосовые и т.д.) ────

async def log_media(message: types.Message, media_type: str, extra: str = ""):
    """Пересылает информацию о медиа-сообщении админу."""
    try:
        user  = message.from_user
        chat  = message.chat
        now   = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

        chat_type = {
            "private":    "👤 Личка",
            "group":      "👥 Группа",
            "supergroup": "👥 Супергруппа",
        }.get(chat.type, chat.type)

        chat_title = chat.title or "—"
        user_name  = f"{user.first_name or ''} {user.last_name or ''}".strip() or "—"
        username   = f"@{user.username}" if user.username else "нет username"
        caption    = message.caption or ""
        cap_line   = f"\n📝 <b>Подпись:</b> {caption}" if caption else ""

        report = (
            f"📨 <b>Новое сообщение</b>\n"
            f"🕐 {now}\n"
            f"─────────────────\n"
            f"{chat_type}: <b>{chat_title}</b>\n"
            f"🆔 chat_id: <code>{chat.id}</code>\n"
            f"─────────────────\n"
            f"👤 <b>{user_name}</b> ({username})\n"
            f"🆔 user_id: <code>{user.id}</code>\n"
            f"─────────────────\n"
            f"{media_type}{extra}{cap_line}"
        )
        await notify_admin(report)

        # Пересылаем само сообщение (чтобы видеть фото/стикер/голосовое)
        try:
            await bot.forward_message(
                chat_id=ADMIN_ID,
                from_chat_id=chat.id,
                message_id=message.message_id,
            )
        except Exception:
            pass  # если не удалось переслать — ничего страшного, отчёт уже отправлен

    except Exception as e:
        logger.error(f"log_media error: {e}")

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    register_group(message)
    if message.from_user.id != ADMIN_ID:
        await log_media(message, "🖼 <b>Тип:</b> Фото")

@dp.message(F.video)
async def handle_video(message: types.Message):
    register_group(message)
    if message.from_user.id != ADMIN_ID:
        await log_media(message, "🎬 <b>Тип:</b> Видео")

@dp.message(F.video_note)
async def handle_video_note(message: types.Message):
    register_group(message)
    if message.from_user.id != ADMIN_ID:
        await log_media(message, "⭕ <b>Тип:</b> Кружочек")

@dp.message(F.voice)
async def handle_voice(message: types.Message):
    register_group(message)
    if message.from_user.id != ADMIN_ID:
        dur = message.voice.duration if message.voice else 0
        await log_media(message, "🎤 <b>Тип:</b> Голосовое", f"\n⏱ Длина: {dur} сек.")

@dp.message(F.sticker)
async def handle_sticker(message: types.Message):
    register_group(message)
    if message.from_user.id != ADMIN_ID:
        emoji = message.sticker.emoji if message.sticker else ""
        await log_media(message, "🎭 <b>Тип:</b> Стикер", f" {emoji}")

@dp.message(F.document)
async def handle_document(message: types.Message):
    register_group(message)
    if message.from_user.id != ADMIN_ID:
        name = message.document.file_name if message.document else "—"
        await log_media(message, "📎 <b>Тип:</b> Файл", f"\n📄 Имя: {name}")

@dp.message(F.audio)
async def handle_audio(message: types.Message):
    register_group(message)
    if message.from_user.id != ADMIN_ID:
        title = (message.audio.title or message.audio.file_name or "—") if message.audio else "—"
        await log_media(message, "🎵 <b>Тип:</b> Аудио", f"\n🎶 Название: {title}")

@dp.message(F.animation)
async def handle_animation(message: types.Message):
    register_group(message)
    if message.from_user.id != ADMIN_ID:
        await log_media(message, "🎞 <b>Тип:</b> GIF")

# ─── Фоновые задачи ───────────────────────────────────────────────────────────

async def reminder_loop():
    congratulated:    set = set()
    reminded:         set = set()
    daily_said:       set = set()
    countdown_sent:   dict = {}
    poll_sent:        dict = {}
    fav_announced:    set = set()
    mood_announced:   set = set()

    daily_hour   = random.randint(8, 21)
    daily_minute = random.randint(0, 59)
    logger.info(f"Ежедневная фраза сегодня в {daily_hour:02d}:{daily_minute:02d}")

    while True:
        try:
            now      = datetime.now()
            day_key  = now.strftime("%Y-%m-%d")
            week_key = week_str()

            # ── Объявление настроения дня в 9:00 ─────────────────────────────
            if now.hour == 9 and now.minute == 0 and day_key not in mood_announced:
                mood_announced = {day_key}
                for chat_id in DATA.keys():
                    mood = get_mood(int(chat_id))
                    try:
                        await bot.send_message(int(chat_id), MOOD_ANNOUNCE[mood])
                    except Exception as e:
                        logger.error(f"Ошибка объявления настроения {chat_id}: {e}")
                await asyncio.sleep(61)
                continue

            # ── Обратный отсчёт (за 7, 3, 1 день) ───────────────────────────
            if now.hour == 10 and now.minute == 0:
                for chat_id, group in DATA.items():
                    for name, info in group.items():
                        date = info.get("date") if isinstance(info, dict) else info
                        d, is_today = days_until(date)
                        if d in (1, 3, 7):
                            key = f"{chat_id}:{name}:{d}"
                            if countdown_sent.get(key) != day_key:
                                countdown_sent[key] = day_key
                                phrase = random.choice(COUNTDOWN_PHRASES[d]).format(name=name)
                                try:
                                    await bot.send_message(int(chat_id), phrase)
                                except Exception as e:
                                    logger.error(f"Ошибка обратного отсчёта {chat_id}: {e}")

                        if d == 7:
                            poll_key = f"{chat_id}:{name}"
                            if poll_sent.get(poll_key) != day_key:
                                poll_sent[poll_key] = day_key
                                gift = info.get("gift", "") if isinstance(info, dict) else ""
                                options = []
                                if gift:
                                    options.append(gift)
                                options += ["Цветы 💐", "Деньги 💵", "Сертификат 🎫", "Сам(а) придумаю 🤷"]
                                options = list(dict.fromkeys(options))[:10]
                                try:
                                    await bot.send_poll(
                                        int(chat_id),
                                        question=f"🎁 Что дарим {name}?",
                                        options=options,
                                        is_anonymous=False,
                                    )
                                except Exception as e:
                                    logger.error(f"Ошибка опроса {chat_id}: {e}")

            # ── В 00:00 — поздравления с гифкой + пожелания от чата ──────────
            if now.hour == 0 and now.minute == 0 and day_key not in congratulated:
                congratulated = {day_key}
                daily_hour   = random.randint(8, 21)
                daily_minute = random.randint(0, 59)
                logger.info(f"Завтрашняя фраза в {daily_hour:02d}:{daily_minute:02d}")
                for chat_id, group in DATA.items():
                    for name, info in group.items():
                        date = info.get("date") if isinstance(info, dict) else info
                        _, is_today = days_until(date)
                        if is_today:
                            gif_url = random.choice(BIRTHDAY_GIFS)
                            cid = str(chat_id)
                            try:
                                await bot.send_animation(
                                    int(chat_id),
                                    animation=gif_url,
                                    caption=f"🎉🎂 <b>С ДНЕМ РОЖДЕНИЯ, {name}!</b> 🎂🎉\n\n{random.choice(CONGRATS)}"
                                )
                            except Exception as e:
                                logger.error(f"Ошибка поздравления {chat_id}: {e}")
                                try:
                                    await bot.send_message(
                                        int(chat_id),
                                        f"🎉🎂 <b>С ДНЕМ РОЖДЕНИЯ, {name}!</b> 🎂🎉\n\n{random.choice(CONGRATS)}"
                                    )
                                except:
                                    pass

                            wishes_list = WISHES.get(cid, {}).get(name, [])
                            if wishes_list:
                                wishes_text = "\n".join(f"💌 {w}" for w in wishes_list)
                                try:
                                    await bot.send_message(
                                        int(chat_id),
                                        f"🌸 <b>Пожелания для {name} от чата:</b>\n\n{wishes_text}"
                                    )
                                    WISHES[cid][name] = []
                                    save_wishes()
                                except Exception as e:
                                    logger.error(f"Ошибка пожеланий {chat_id}: {e}")

                            gift = info.get("gift", "") if isinstance(info, dict) else ""
                            if gift:
                                try:
                                    await bot.send_message(
                                        int(chat_id),
                                        f"🎁 Напоминаю: для <b>{name}</b> планировали подарить <b>{gift}</b>! Не забыли? 👀"
                                    )
                                except Exception as e:
                                    logger.error(f"Ошибка напоминания о подарке {chat_id}: {e}")
                await asyncio.sleep(61)
                continue

            # ── В 01:00 — ежедневное напоминание ─────────────────────────────
            if now.hour == 1 and now.minute == 0 and day_key not in reminded:
                reminded = {day_key}
                for chat_id, group in DATA.items():
                    try:
                        if not group:
                            continue
                        await bot.send_message(
                            int(chat_id),
                            "🌙 <b>Ежедневное напоминание</b>\n\n" + birthdays_text(group)
                        )
                    except Exception as e:
                        logger.error(f"Ошибка напоминания {chat_id}: {e}")
                await asyncio.sleep(61)
                continue

            # ── Случайное время — ежедневная фраза ───────────────────────────
            if now.hour == daily_hour and now.minute == daily_minute and day_key not in daily_said:
                daily_said = {day_key}
                for chat_id in DATA.keys():
                    try:
                        await bot.send_message(int(chat_id), random.choice(DAILY_PHRASES))
                    except Exception as e:
                        logger.error(f"Ошибка ежедневной фразы {chat_id}: {e}")
                await asyncio.sleep(61)
                continue

            # ── Раз в неделю (воскресенье 20:00) — любимчик недели ───────────
            if now.weekday() == 6 and now.hour == 20 and now.minute == 0 and week_key not in fav_announced:
                fav_announced = {week_key}
                for chat_id in list(DATA.keys()):
                    uid, uname = get_weekly_fav(int(chat_id))
                    if uname:
                        try:
                            await bot.send_message(
                                int(chat_id),
                                f"👑 <b>Любимчик недели</b> по версии Эльзы — <b>{uname}</b>!\n"
                                f"Самая активная в чате на этой неделе 💅✨\n"
                                f"Аплодисменты! 👏"
                            )
                        except Exception as e:
                            logger.error(f"Ошибка любимчика {chat_id}: {e}")
                    ensure_memory(int(chat_id))
                    MEMORY[str(chat_id)]["activity"] = {}
                    save_memory()
                await asyncio.sleep(61)
                continue

            await asyncio.sleep(30)
        except Exception as e:
            logger.error(f"reminder_loop error: {e}")
            await asyncio.sleep(60)

# ─── Flask healthcheck ────────────────────────────────────────────────────────
flask_app = Flask(__name__)

@flask_app.route("/")
def health():
    return "OK", 200

def run_flask():
    port = int(os.getenv("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port, use_reloader=False)

async def main():
    logger.info("🚀 Эльза Абдрахманова запускается...")
    bot_info = await bot.get_me()
    logger.info(f"✅ Бот запущен: @{bot_info.username}")
    asyncio.create_task(reminder_loop())
    await dp.start_polling(bot, drop_pending_updates=True)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(main())
