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

ADMIN_ID = 6114745287

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

async def notify_admin(text: str):
    try:
        await bot.send_message(ADMIN_ID, text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Ошибка отправки админу: {e}")

async def log_message(message: types.Message, action: str = None):
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
MEMORY_FILE     = "memory.json"
MOOD_FILE       = "mood.json"
WISHES_FILE     = "wishes.json"
GROUPS_FILE     = "groups.json"

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
GROUPS:  dict = _load(GROUPS_FILE)

def save_data():   _save(BIRTHDAYS_FILE, DATA)
def save_memory(): _save(MEMORY_FILE, MEMORY)
def save_mood():   _save(MOOD_FILE, MOOD)
def save_wishes(): _save(WISHES_FILE, WISHES)
def save_groups(): _save(GROUPS_FILE, GROUPS)

def register_group(message: types.Message):
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

MAIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📅 Дни рождения")],
        [KeyboardButton(text="➕ Добавить"), KeyboardButton(text="❌ Удалить")],
        [KeyboardButton(text="🔮 Гороскоп"), KeyboardButton(text="🎰 Судьба дня")],
        [KeyboardButton(text="👤 Об авторе")],
    ],
    resize_keyboard=True,
    persistent=True,
)

DATE_RE = re.compile(r"^\d{2}\.\d{2}(\.\d{4})?$")

MOODS = ["злая", "добрая", "ленивая", "гиперактивная"]

MOOD_ANNOUNCE = {
    "злая":          "😤 Эльза сегодня не в духе. Не раздражайте.",
    "добрая":        "🌸 Эльза сегодня добренькая. Пользуйтесь пока не прошло.",
    "ленивая":       "😴 Эльза сегодня в режиме «не трогать». Всё лень.",
    "гиперактивная": "⚡ Эльза сегодня на максималках! Держитесь!",
}

MOOD_MAT_REPLIES = {
    "злая": ["ещё раз — и я тебя заблокирую мысленно 😤", "очень мило. очень 😡", "настроение и без тебя плохое 😤", "ХВАТИТ 😤"],
    "добрая": ["ну зачем так грубо? 🥺 всё же хорошо", "давай без этого, ладно? 🌸", "фу, некрасиво 🙁 ты лучше этого", "сегодня добрая но это лишнее 😕"],
    "ленивая": ["лень даже реагировать 🥱", "окей 🥱 записала. неинтересно", "ну и ну 😪 иди отсюда", "мне всё равно 😴"],
    "гиперактивная": ["ОЙ ВСЁ!! следи за базаром!! 💥", "НЕТ НЕТ НЕТ!! 🙅‍♀️💥 некрасиво!!", "ТАКИЕ СЛОВА?? В МОЁМ ЧАТЕ?? 😤⚡", "ВОТ ЭТО ПОВОРОТ 😤 но нет!!"],
}

MOOD_THANKS_REPLIES = {
    "злая":          ["угу 😒", "ладно 🙄", "пожалуйста, только не мешай 😤"],
    "добрая":        ["пожалуйста, я лучшая 💅💖", "всегда! ты тоже лучшая 🌸", "не за что, солнышко ✨"],
    "ленивая":       ["угу 😴", "мм, пожалуйста 🥱", "ладно 😪"],
    "гиперактивная": ["ПОЖАЛУЙСТА!! 🎉", "НЕ ЗА ЧТО!! ТЫ ЛУЧШАЯ!! ⚡✨", "ВСЕГДА ПОЖАЛУЙСТА!! 💥💖"],
}

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
    "ёпт", "нахрен", "шлюх", "блядь", "блядск", "ёпта", "епта", "епт", "ёмаё",
    "дебил", "идиот", "кретин", "придурок", "урод", "чмо", "лох", "лошар", "даун",
    "хрен", "хренов", "зашиб", "заебал", "заебала", "заебись",
    "ёбнут", "ёбнулся", "пиздец", "пиздат", "пиздёж", "пиздит", "пиздобол",
    "хуйн", "хуит", "хуили", "хуйло", "ёбарь", "выёбыв",
    "твою мать", "твоюмать", "мать твою", "пошёл нахуй", "иди нахуй", "иди нахер",
    "ёб твою", "ёбтвою", "курва", "тварь", "тварюга", "скотина", "скот",
    "мразь", "мразота", "падла", "падаль", "гнида", "козёл", "козл", "баран", "осёл",
    "ублюдок", "ублюд", "выродок", "ссука", "су4ка", "пиздюк", "залупин",
    "нихуя", "нихуй", "похуй", "похер", "хуяр", "хуяс", "хуяк", "тупой", "тупая", "тупорыл",
]

MAT_REPLIES_DEFAULT = [
    "ой всё, следи за базаром 💅", "не при детях пожалуйста 🙄 хотя тут явно не дети",
    "мама знает что ты так разговариваешь? 😒", "записала. не забуду. не прощу 🖊️",
    "окей агрессор, выдыши 😮‍💨", "фу, некрасиво. и неоригинально 🥱",
]

OFFENDER_REPLIES = [
    "а, это снова ты 🙄 помню помню", "помню помню 💅 привет снова",
    "о, знакомое лицо 👀 веди себя хорошо", "ты опять? я слежу 😒",
]

THANKS_TRIGGERS = ["спасибо", "спс", "благодарю", "спасиб", "thanks", "thank you", "пасиба", "пасибо"]
THANKS_REPLIES_DEFAULT = [
    "пожалуйста, я лучшая 💅", "знаю что лучшая, не благодари 👑",
    "не за что 💁‍♀️ просто делаю что умею", "пожалуйста дорогая 💖",
]

LOVE_TRIGGERS = ["люблю тебя", "ты лучшая", "обожаю тебя", "обожаю"]
LOVE_REPLIES = [
    "знаю 💅", "очевидно 👑", "не трать слова, я и так знаю ✨",
    "ага, все меня любят 😏 понимаю", "и я тебя, наверное 🙃",
]

BORED_TRIGGERS = ["скучно", "скука", "нечего делать", "не знаю чем заняться"]
BORED_REPLIES = [
    "это не ко мне, я занята 💅", "придумай себе хобби 🥱",
    "скучно это не диагноз, это выбор 💅",
]

HELP_TRIGGERS = ["помоги", "помогите", "помощь", "помоги мне"]
HELP_REPLIES = [
    "я бот а не личная прислуга но ладно 🙄", "что случилось теперь 😒",
    "с чем? 👀 говори конкретнее",
]

TIRED_TRIGGERS = ["устала", "устал", "вымоталась", "вымотался", "нет сил", "сил нет"]
TIRED_REPLIES = [
    "ты думаешь мне легко за всеми следить? 😮‍💨", "добро пожаловать в клуб 😴",
    "нам всем тяжело дорогая 🙃",
]

HUNGRY_TRIGGERS = ["хочу есть", "голодная", "голодный", "есть хочу", "жрать хочу", "жрать охота"]
HUNGRY_REPLIES = [
    "иди поешь зачем мне это говоришь 😭", "я бот я не накормлю 🙄", "кухня вон там 👉",
]

LUCK_TRIGGERS = ["удачи", "удача тебе", "желаю удачи"]
LUCK_REPLIES = [
    "мне? или тебе? 🤨", "мне не нужна, у меня всё и так хорошо 💅", "ой ну и тебе 💅",
]

SHORT_TRIGGERS_EXACT = ["ок", "окей", "ok", "okay", "да", "нет", "не", "ха", "хаха", "лол", "lol", "хахаха"]
SHORT_REPLIES = [
    "очень содержательно 👏", "развёрнуто, спасибо 🥱", "и? 🙃", "ок 💅", "и что? 😒",
]

NIGHT_REPLIES = [
    "вы вообще спите? 😭 я сплю между прочим", "ночью пишете... всё ок? 😴",
    "эй, уже ночь 🌙 ложитесь спать", "нормальные люди спят 😒 но ладно",
]

MORNING_FIRST_REPLIES = [
    "ранняя пташка 🐦 уважаю", "доброе утро! первая сегодня 🌅 молодец",
    "ого, уже не спишь? уважаю 🌸", "раньше всех! 🏆 хорошего утра",
]

EMOJI_ONLY_REPLIES = [
    "и тебе привет 🙃", "очень информативно 💅", "и что это значит 🤨", "принято 😶",
]

QUESTION_REPLIES = [
    "это мне? или в воздух? 🙃", "я или кто-то другой? 👀", "ты меня спрашиваешь? 🤨",
]

REPEAT_REPLIES = [
    "я слышу с первого раза 🙄", "ты уже это писала 🤨", "зачем два раза? 😒",
]

LONG_MSG_REPLIES = [
    "многовато для меня, я бот а не психолог 😮‍💨", "это всё мне? 😭 я не успеваю читать",
    "ты написала целый роман 📚 я польщена но...", "много слов. очень много 🥱",
]

ELZA_REPLIES = [
    "ну чего тебе 😒", "слушаю, только быстро 🙄", "и? 💅",
    "говори уже, не тяни 😤", "я здесь, чего надо 🥱",
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
HOROSCOPES_GENERAL = [
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

# Подробные гороскопы по каждому знаку
HOROSCOPES_BY_SIGN = {
    "Овен": [
        "🔥 Сегодня ты на пике энергии! Берись за самое сложное — всё получится. Только не переусердствуй и не срывайся на близких 😅",
        "⚡ Марс даёт тебе сверхсилы сегодня. Используй их для дела, а не для споров — хотя споры тоже выиграешь 😏",
        "🌹 В делах сердца сегодня особый день. Не бойся первой сделать шаг — звёзды на твоей стороне 💕",
        "💪 Любые препятствия сегодня — просто разминка для тебя. Ты сильнее чем думаешь!",
        "🎯 Твоя целеустремлённость сегодня поражает окружающих. Продолжай в том же духе — успех близко!",
        "😤 Сегодня лучше считать до десяти прежде чем отвечать. Зато потом — полная победа 👑",
        "🌟 Звёзды пророчат тебе неожиданный комплимент от кого-то приятного. Прими с достоинством 💅",
        "🤑 Финансово сегодня удачный день — но крупных покупок лучше избежать. Звёзды говорят 'подожди немного'",
    ],
    "Телец": [
        "🌸 Сегодня твой день для удовольствий. Побалуй себя чем-нибудь вкусным — звёзды одобряют 🍰",
        "💚 Венера улыбается тебе: в отношениях гармония и тепло. Цени этот момент ❤️",
        "💰 Финансовая интуиция сегодня особенно сильна. Прислушайся к внутреннему голосу в денежных делах",
        "🌿 День идеален для уюта, домашних дел и приятных мелочей. Не торопись — всё успеешь",
        "👑 Твоя надёжность сегодня привлекает нужных людей. Кто-то важный оценит тебя по достоинству",
        "🎨 Творческий порыв накрывает с утра! Не игнорируй его — создашь что-то красивое",
        "😴 Звёзды намекают: отдых сегодня так же важен, как работа. Не вини себя за паузу",
        "🤗 Твоя стабильность — суперсила. Сегодня кто-то придёт к тебе за советом и поддержкой",
    ],
    "Близнецы": [
        "💬 Твой язык сегодня острее меча. Используй дар общения для добра — и всё двери откроются!",
        "🦋 Непостоянство сегодня — твоя сила. Переключайся, исследуй, удивляй — стихия на твоей стороне",
        "🧠 Интеллектуальный день! Реши наконец ту задачу, которую откладывала. Мозг работает на 200%",
        "📱 Важное сообщение может прийти неожиданно. Не пропусти — оно изменит кое-что к лучшему",
        "😂 Твоё чувство юмора сегодня на высоте. Рассмеши кого-нибудь — и день пройдёт отлично",
        "🌪 Мысли скачут? Это нормально для тебя. Запиши самые важные — среди них есть золото",
        "✈️ Захочется перемен и новых впечатлений. Даже маленькое приключение зарядит на неделю",
        "💡 Гениальная идея придёт именно сегодня. Не отмахивайся от неё — запиши и обдумай!",
    ],
    "Рак": [
        "🌊 Эмоции сегодня — твой компас. Доверяй чувствам: они приведут к правильному решению",
        "🏠 День для домашнего уюта и близких людей. Позвони тому, по кому соскучилась 💕",
        "🌙 Луна-покровительница шлёт тебе силу. Сегодня ты мудрее чем обычно — пользуйся этим",
        "🤗 Твоя забота о других сегодня вернётся тебе втройне. Добро — бумеранг!",
        "💭 Интуиция бьёт в точку. Если что-то кажется важным — так и есть. Слушай себя",
        "🌸 Романтическое настроение с утра — отличный знак. День может преподнести сюрприз в личной жизни",
        "😢 Если грустно — это тоже нормально. Дай себе время, а потом взлетишь выше прежнего 🦋",
        "🍲 Приготовь что-нибудь вкусное для себя или близких. Звёзды говорят: еда сегодня лечит душу",
    ],
    "Лев": [
        "👑 Корона сегодня сидит особенно ровно. Ты в центре внимания — и это абсолютно заслуженно!",
        "☀️ Солнце — твой правитель — дарит тебе сегодня харизму на максималках. Зажигай!",
        "🦁 Смелость сегодня открывает двери. Скажи то, что давно хотела — тебя услышат",
        "💫 Окружающие тянутся к твоему теплу. Поделись энергией — она не иссякнет, только умножится",
        "🎭 Твой выход! Сегодня отличный день для презентации себя и своих идей. Не скромничай",
        "💖 В любви сегодня страстный день. Не прячь чувства — выражай их ярко и без стеснения",
        "🏆 Соперничество сегодня закончится твоей победой. Даже не сомневайся — ты лучшая!",
        "🌟 Звёзды говорят: сегодня ты особенно красива. Впрочем, ты это и без звёзд знаешь 💅",
    ],
    "Дева": [
        "🔍 Твоя внимательность сегодня спасёт ситуацию. Заметишь то, что другие пропустят",
        "📋 Идеальный день для планирования и наведения порядка. Твои списки дел — произведение искусства",
        "🌿 Здоровье в фокусе сегодня. Небольшая прогулка или полезный перекус — и всё встанет на место",
        "🧩 Сложная задача решается именно сегодня. Твой аналитический ум на пике — дерзай!",
        "💬 Кто-то обратится за советом — и ты дашь именно тот ответ, который нужен. Ты мудрая 🦉",
        "✨ Перфекционизм сегодня — твой союзник, а не враг. Доведи дело до идеала и получи удовольствие",
        "💰 Финансовая дисциплина даёт плоды. Маленький приятный бонус может прийти неожиданно",
        "🤍 Не критикуй себя слишком строго сегодня. Ты сделала достаточно — разреши себе отдохнуть",
    ],
    "Весы": [
        "⚖️ Гармония сегодня — твоё суперпособность. Ты помиришь кого угодно с кем угодно",
        "💄 Эстетический вкус на максималках! Отличный день для шопинга, ремонта или перестановки",
        "🤝 Деловые переговоры сегодня пройдут блестяще. Твоё обаяние открывает любые двери",
        "💕 Венера шепчет: в любви сегодня всё складывается удачно. Романтический вечер — отличная идея",
        "🌈 Не можешь выбрать? Сегодня монетка подскажет верно. Доверься случаю — он добрый",
        "🎨 Красота и искусство притягивают тебя сегодня. Посмотри что-нибудь вдохновляющее",
        "😌 Внутренний мир важен. Сегодня найди минуту тишины — и услышишь нужный ответ",
        "👯 День для встречи с подругами! Смех и общение подзарядят тебя лучше любого кофе ☕",
    ],
    "Скорпион": [
        "🦂 Твоя проницательность сегодня пугает и восхищает одновременно. Ты видишь людей насквозь",
        "🔮 Интуиция — абсолютная. Первое впечатление сегодня верное — доверяй ему без сомнений",
        "💣 Скрытая энергия ищет выход. Направь её в дело — и горы свернёшь до вечера",
        "🌑 Тайна вокруг тебя притягивает внимание. Кто-то давно хочет познакомиться поближе 👀",
        "💪 Через трудности — к звёздам! Сегодня именно тот день когда препятствие становится трамплином",
        "❤️‍🔥 В отношениях сегодня накал страстей. Выясни наконец всё — и станет легче",
        "💰 Деньги любят тебя сегодня. Неожиданный доход или выгодное предложение вполне возможны",
        "🦋 Трансформация — твоя природа. Сегодня ты становишься чуть лучше прежней версии себя",
    ],
    "Стрелец": [
        "🏹 Стрела летит точно в цель! Сегодня всё что ты затеешь — попадёт в яблочко",
        "🌍 Душа рвётся в путешествие. Даже маленькая поездка или новый район города зарядит энергией",
        "😄 Твой оптимизм сегодня заразителен. Ты поднимешь настроение всем вокруг — просто появившись",
        "📚 Жажда знаний зашкаливает. Узнай что-нибудь новое — мозг скажет спасибо",
        "🎲 Авантюра? Да! Сегодня риск оправдан. Звёзды страхуют тебя от неприятных последствий",
        "🤣 День будет полон смешных ситуаций. Смейся над собой — это освобождает!",
        "💫 Философское настроение с утра? Твои мысли сегодня — настоящая мудрость. Поделись ею",
        "🌅 Новый горизонт открывается именно сегодня. Не бойся сделать первый шаг к мечте",
    ],
    "Козерог": [
        "🏔 Упорство сегодня творит чудеса. То что казалось невозможным — вдруг стало реальным",
        "📈 Карьерный день! Начальство замечает твои старания. Самое время проявить инициативу",
        "⏰ Дисциплина — твоя сила. Сделай сегодня то, что давно откладывала — и почувствуй облегчение",
        "💎 Твоя надёжность стоит дороже золота. Кто-то важный это осознает именно сегодня",
        "🌱 Долгосрочные вложения — финансовые или эмоциональные — дадут плоды. Продолжай строить",
        "🤍 Позволь себе расслабиться сегодня. Ты заслужила. Серьёзно — отдых это не слабость",
        "👑 Статус и репутация растут незаметно но верно. Окружающие видят твой рост, даже если ты нет",
        "🎯 Цель ясна, путь намечен — осталось сделать шаг. Сегодня самый подходящий момент",
    ],
    "Водолей": [
        "⚡ Оригинальность зашкаливает! Твоя безумная идея сегодня окажется гениальной — не молчи",
        "🌐 День для новых знакомств и необычных встреч. Мир шире чем кажется — открой для себя его",
        "🤖 Технологии на твоей стороне сегодня. Реши наконец тот технический вопрос, что висит",
        "💙 Дружба важнее всего сегодня. Напомни своим людям как они тебе дороги",
        "🦄 Ты не как все — и это твоя сила. Не пытайся вписаться, лучше задай новый стандарт",
        "🌊 Волна перемен несёт тебя в нужном направлении. Расслабься и наслаждайся процессом",
        "💡 Революционная мысль придёт во время обеда. Запиши обязательно — это важно!",
        "🎆 Неожиданный поворот событий сегодня обернётся к лучшему. Хаос — твоя стихия 😏",
    ],
    "Рыбы": [
        "🐟 Интуиция сегодня — абсолютный GPS. Куда поведёт — туда и иди, не ошибёшься",
        "🌊 Творческий поток накрывает с головой! Рисуй, пиши, создавай — сегодня всё получится",
        "💜 Эмпатия зашкаливает. Ты чувствуешь всех вокруг — постарайся не взять чужое на себя",
        "🌙 Сны сегодня вещие. Запомни что приснилось — там может быть подсказка",
        "✨ Магия реальна — особенно для тебя сегодня. Загадай желание в 11:11 и верь",
        "🎵 Музыка сегодня лечит. Включи любимое и позволь эмоциям течь свободно",
        "💕 Романтика витает в воздухе. Даже если ты одна — можно влюбиться в жизнь заново",
        "🦋 Мечты ближе к реальности чем кажется. Один маленький шаг сегодня — и всё изменится",
    ],
}

# ─── Испытание судьбы ─────────────────────────────────────────────────────────
FATE_CHALLENGES = [
    ("🎭 Испытание дня", "Сегодня ты должна сказать комплимент первому встречному. Иначе удача отвернётся на 3 дня 😈"),
    ("🌶 Вызов судьбы", "Весь день говори только правду. Абсолютно всю. Звёзды смотрят 👁"),
    ("🎪 Задание от Эльзы", "Сделай что-нибудь, чего никогда не делала раньше. Любую мелочь. Жизнь слишком короткая для скуки ✨"),
    ("🔮 Предсказание дня", "Сегодня кто-то скажет тебе кое-что важное. Не отмахивайся — это именно то, что нужно услышать 👂"),
    ("💫 Знак вселенной", "Первый цвет, который ты увидишь выйдя на улицу — цвет твоей удачи сегодня 🌈"),
    ("🎯 Миссия дня", "Напиши сообщение тому, с кем давно не общалась. Вселенная специально сводит вас снова 💌"),
    ("🌙 Ночное задание", "Перед сном скажи вслух три вещи за которые благодарна сегодня. Работает лучше любого гороскопа 🙏"),
    ("⚡ Экстренное испытание", "Сделай 10 прыжков прямо сейчас. Это активирует зону удачи в мозге. Я серьёзно 😐 ну почти"),
    ("🎲 Испытание рандома", "Открой любую книгу на случайной странице и прочитай первое предложение — это послание для тебя 📖"),
    ("🦋 Задание на трансформацию", "Измени что-нибудь в своём образе сегодня. Даже заколка на другой стороне считается 💇‍♀️"),
    ("🧿 Защитный ритуал", "Загадай желание и не говори его никому 7 дней. Через неделю проверь что изменилось 🤫"),
    ("🎵 Музыкальное испытание", "Включи случайную песню — её настроение и есть твоё настроение на сегодня. Танцевать обязательно 💃"),
    ("🌺 Задание добра", "Сделай что-нибудь приятное для кого-то, не объясняя зачем. Анонимное добро — самое мощное ✨"),
    ("🍀 Проверка удачи", "Найди что-нибудь зелёного цвета в течение часа — это знак что деньги придут 💚"),
    ("😂 Смехотерапия", "Посмотри что-нибудь смешное прямо сейчас. 5 минут смеха = 2 часа хорошего настроения. Наука! 🔬"),
    ("🌟 Звёздное задание", "Выйди ночью и найди самую яркую звезду. Загадай желание. Работает только если искренне 💫"),
    ("🎀 Испытание красоты", "Сделай себе комплимент вслух, глядя в зеркало. Без иронии. Это сложнее чем кажется 💅"),
    ("🤙 Социальное задание", "Позвони кому-нибудь вместо того чтобы писать. Голос творит магию 📞"),
    ("🍫 Вкусное испытание", "Съешь что-нибудь, чего давно хотела но откладывала. Жизнь слишком короткая для диет каждый день 🎂"),
    ("🌊 Водное задание", "Выпей стакан воды прямо сейчас и загадай желание. Вода помнит всё 💧"),
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
    if message.from_user.id != ADMIN_ID:
        return
    if not GROUPS:
        await message.answer("📭 Бот пока нигде не используется.")
        return
    lines = [f"📋 <b>Все чаты где работает бот</b> ({len(GROUPS)} шт.):\n"]
    for i, (cid, info) in enumerate(GROUPS.items(), 1):
        title       = info.get("title", "—")
        chat_type   = info.get("type", "—")
        first_seen  = info.get("first_seen", "—")
        last_active = info.get("last_activity", "—")
        bday_count  = len(DATA.get(cid, {}))
        type_emoji  = {"private": "👤", "group": "👥", "supergroup": "👥", "channel": "📢"}.get(chat_type, "💬")
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
        "<b>Имя ДД.ММ</b>\n<b>Имя ДД.ММ.ГГГГ</b>\n<b>Имя ДД.ММ.ГГГГ подарок: духи</b>\n\n"
        "Примеры:\n<code>Эльза 05.03</code>\n<code>Эльза 05.03.2000</code>\n<code>Эльза 05.03.2000 подарок: духи</code>",
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
    await message.answer("✏️ Напиши имя человека которого нужно удалить:", reply_markup=ReplyKeyboardRemove())

def horoscope_text_for_sign(zodiac: str) -> str:
    pool = HOROSCOPES_BY_SIGN.get(zodiac, HOROSCOPES_GENERAL)
    # Берём 2 случайных предсказания для насыщенности
    picks = random.sample(pool, min(2, len(pool)))
    return "\n\n".join(picks)

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
            age = get_age(found_date)
            age_str = f", {age} лет" if age else ""
            await message.answer(
                f"{emoji} <b>{zodiac}</b>{age_str} — гороскоп для <b>{found_name}</b> на сегодня:\n\n"
                f"{horoscope_text_for_sign(zodiac)}",
                reply_markup=MAIN_KB,
            )
            return
    pending[message.chat.id] = "horoscope"
    await message.answer(
        "🔮 Напиши свою дату рождения чтобы узнать гороскоп:\n\n"
        "Формат: <b>ДД.ММ</b> или <b>ДД.ММ.ГГГГ</b>\nПример: <code>05.03</code>",
        reply_markup=ReplyKeyboardRemove(),
    )

@dp.message(F.text == "🎰 Судьба дня")
async def btn_fate(message: types.Message):
    register_group(message)
    if message.from_user.id != ADMIN_ID:
        await log_message(message, action="нажала кнопку 🎰 Судьба дня")
    if is_spam(message.chat.id, message.from_user.id):
        await message.answer("не спамь дура, с первого раза поняла 🙄")
        return
    pending.pop(message.chat.id, None)
    title, challenge = random.choice(FATE_CHALLENGES)
    lucky_number = random.randint(1, 99)
    lucky_colors = ["красный 🔴", "розовый 🌸", "золотой ✨", "фиолетовый 💜", "зелёный 💚",
                    "голубой 💙", "оранжевый 🧡", "белый 🤍", "чёрный 🖤", "жёлтый 💛"]
    lucky_color = random.choice(lucky_colors)
    luck_percent = random.randint(42, 99)
    mood_today = random.choice(["🔥 огонь", "💅 королева", "😴 спящая красавица",
                                 "⚡ молния", "🌸 нежность", "😈 бунтарка", "🌊 море спокойствия",
                                 "🎭 актриса", "🦋 порхаешь", "🌪 ураган"])
    await message.answer(
        f"🎰 <b>{title}</b>\n\n"
        f"📋 {challenge}\n\n"
        f"─────────────────\n"
        f"🍀 Удача сегодня: <b>{luck_percent}%</b>\n"
        f"🎨 Цвет дня: <b>{lucky_color}</b>\n"
        f"🔢 Счастливое число: <b>{lucky_number}</b>\n"
        f"💃 Твоё энергетическое состояние: <b>{mood_today}</b>",
        reply_markup=MAIN_KB,
    )

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
        "👤 <b>Об авторе</b>\n\nЭтот бот создан <b>Эльзой Абдрахмановой</b> 🎀\n\n"
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

    register_group(message)

    if user_id != ADMIN_ID:
        await log_message(message)

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

    # ── 4. Утреннее сообщение (06–08) ─────────────────────────────────────────
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

    # ── 6. Длинное сообщение (только если не обращение к Эльзе) ──────────────
    if state is None and len(text) > 200 and not mentions_elza(text):
        is_reply_to_bot = (
            message.reply_to_message is not None
            and message.reply_to_message.from_user is not None
            and message.reply_to_message.from_user.is_bot
        )
        if not is_reply_to_bot:
            await message.reply(random.choice(LONG_MSG_REPLIES))
            return

    # ── 7. Упоминание Эльзы или ответ на её сообщение → статичный ответ ──────
    if state is None:
        is_private = message.chat.type == "private"
        is_reply_to_bot = (
            message.reply_to_message is not None
            and message.reply_to_message.from_user is not None
            and message.reply_to_message.from_user.is_bot
        )

        if mentions_elza(text) or is_reply_to_bot or is_private:
            if is_offender(chat_id, user_id) and random.random() < 0.2 and not is_reply_to_bot:
                await message.reply(random.choice(OFFENDER_REPLIES))
                return
            await message.reply(random.choice(ELZA_REPLIES))
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

        if is_question(text):
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
            await message.answer("❌ Не нашёл дату. Формат: <b>ДД.ММ</b> или <b>ДД.ММ.ГГГГ</b>", reply_markup=MAIN_KB)
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
            await message.answer("❌ Неверный формат. Используй <b>ДД.ММ</b>\nПример: <code>05.03</code>", reply_markup=MAIN_KB)
            return
        zodiac = get_zodiac(date_str)
        if zodiac:
            emoji = ZODIAC_EMOJI.get(zodiac, "🔮")
            await message.answer(
                f"{emoji} <b>{zodiac}</b> — твой гороскоп на сегодня:\n\n{horoscope_text_for_sign(zodiac)}",
                reply_markup=MAIN_KB,
            )
        else:
            await message.answer("❌ Не удалось определить знак зодиака.", reply_markup=MAIN_KB)

# ─── Медиа хендлеры ──────────────────────────────────────────────────────────

async def log_media(message: types.Message, media_type: str, extra: str = ""):
    try:
        user  = message.from_user
        chat  = message.chat
        now   = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        chat_type = {"private": "👤 Личка", "group": "👥 Группа", "supergroup": "👥 Супергруппа"}.get(chat.type, chat.type)
        chat_title = chat.title or "—"
        user_name  = f"{user.first_name or ''} {user.last_name or ''}".strip() or "—"
        username   = f"@{user.username}" if user.username else "нет username"
        caption    = message.caption or ""
        cap_line   = f"\n📝 <b>Подпись:</b> {caption}" if caption else ""
        report = (
            f"📨 <b>Новое сообщение</b>\n🕐 {now}\n─────────────────\n"
            f"{chat_type}: <b>{chat_title}</b>\n🆔 chat_id: <code>{chat.id}</code>\n─────────────────\n"
            f"👤 <b>{user_name}</b> ({username})\n🆔 user_id: <code>{user.id}</code>\n─────────────────\n"
            f"{media_type}{extra}{cap_line}"
        )
        await notify_admin(report)
        try:
            await bot.forward_message(chat_id=ADMIN_ID, from_chat_id=chat.id, message_id=message.message_id)
        except Exception:
            pass
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
    congratulated:  set  = set()
    reminded:       set  = set()
    daily_said:     set  = set()
    countdown_sent: dict = {}
    poll_sent:      dict = {}
    fav_announced:  set  = set()
    mood_announced: set  = set()

    daily_hour   = random.randint(8, 21)
    daily_minute = random.randint(0, 59)
    logger.info(f"Ежедневная фраза сегодня в {daily_hour:02d}:{daily_minute:02d}")

    while True:
        try:
            now      = datetime.now()
            day_key  = now.strftime("%Y-%m-%d")
            week_key = week_str()

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
                                    await bot.send_poll(int(chat_id), question=f"🎁 Что дарим {name}?", options=options, is_anonymous=False)
                                except Exception as e:
                                    logger.error(f"Ошибка опроса {chat_id}: {e}")

            if now.hour == 0 and now.minute == 0 and day_key not in congratulated:
                congratulated = {day_key}
                daily_hour   = random.randint(8, 21)
                daily_minute = random.randint(0, 59)
                for chat_id, group in DATA.items():
                    for name, info in group.items():
                        date = info.get("date") if isinstance(info, dict) else info
                        _, is_today = days_until(date)
                        if is_today:
                            gif_url = random.choice(BIRTHDAY_GIFS)
                            cid = str(chat_id)
                            try:
                                await bot.send_animation(int(chat_id), animation=gif_url,
                                    caption=f"🎉🎂 <b>С ДНЕМ РОЖДЕНИЯ, {name}!</b> 🎂🎉\n\n{random.choice(CONGRATS)}")
                            except Exception as e:
                                logger.error(f"Ошибка поздравления {chat_id}: {e}")
                                try:
                                    await bot.send_message(int(chat_id), f"🎉🎂 <b>С ДНЕМ РОЖДЕНИЯ, {name}!</b> 🎂🎉\n\n{random.choice(CONGRATS)}")
                                except:
                                    pass
                            wishes_list = WISHES.get(cid, {}).get(name, [])
                            if wishes_list:
                                wishes_text = "\n".join(f"💌 {w}" for w in wishes_list)
                                try:
                                    await bot.send_message(int(chat_id), f"🌸 <b>Пожелания для {name} от чата:</b>\n\n{wishes_text}")
                                    WISHES[cid][name] = []
                                    save_wishes()
                                except Exception as e:
                                    logger.error(f"Ошибка пожеланий {chat_id}: {e}")
                            gift = info.get("gift", "") if isinstance(info, dict) else ""
                            if gift:
                                try:
                                    await bot.send_message(int(chat_id), f"🎁 Напоминаю: для <b>{name}</b> планировали подарить <b>{gift}</b>! Не забыли? 👀")
                                except Exception as e:
                                    logger.error(f"Ошибка напоминания о подарке {chat_id}: {e}")
                await asyncio.sleep(61)
                continue

            if now.hour == 1 and now.minute == 0 and day_key not in reminded:
                reminded = {day_key}
                for chat_id, group in DATA.items():
                    try:
                        if not group:
                            continue
                        await bot.send_message(int(chat_id), "🌙 <b>Ежедневное напоминание</b>\n\n" + birthdays_text(group))
                    except Exception as e:
                        logger.error(f"Ошибка напоминания {chat_id}: {e}")
                await asyncio.sleep(61)
                continue

            if now.hour == daily_hour and now.minute == daily_minute and day_key not in daily_said:
                daily_said = {day_key}
                for chat_id in DATA.keys():
                    try:
                        await bot.send_message(int(chat_id), random.choice(DAILY_PHRASES))
                    except Exception as e:
                        logger.error(f"Ошибка ежедневной фразы {chat_id}: {e}")
                await asyncio.sleep(61)
                continue

            if now.weekday() == 6 and now.hour == 20 and now.minute == 0 and week_key not in fav_announced:
                fav_announced = {week_key}
                for chat_id in list(DATA.keys()):
                    uid, uname = get_weekly_fav(int(chat_id))
                    if uname:
                        try:
                            await bot.send_message(int(chat_id),
                                f"👑 <b>Любимчик недели</b> по версии Эльзы — <b>{uname}</b>!\n"
                                f"Самая активная в чате на этой неделе 💅✨\nАплодисменты! 👏")
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
