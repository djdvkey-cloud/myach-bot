"""
Футбольный бот «Мяч» — постоянная версия для Amvera
Опросы по субботам + команды статистики и составов
"""
import asyncio
import datetime
import json
import math
import os
import re
from itertools import combinations
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandObject
from aiogram.client.session.aiohttp import AiohttpSession

# === Настройки из Secrets ===
BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ.get("OWNER_ID", "8378612979"))
CHAT_ID = int(os.environ.get("CHAT_ID", "-1003857417996"))

YEKB_TZ = ZoneInfo("Asia/Yekaterinburg")
GAME_TIME = "21.30-23.00"

DATA_DIR = os.environ.get("DATA_DIR", "/data" if os.path.isdir("/data") else "data")
STATS_FILE = os.path.join(DATA_DIR, "stats.json")
OFFSET_FILE = os.path.join(DATA_DIR, "last_offset.txt")
LAST_POLL_FILE = os.path.join(DATA_DIR, "last_poll.txt")
POLL_STATE_FILE = os.path.join(DATA_DIR, "poll_state.json")
GUESTS_FILE = os.path.join(DATA_DIR, "guests.json")

PLAYER_USERNAMES = {
    "Ларионов М.": "lmur2000", "Бобров М.": "BobrovMA1996",
    "Серганов А.": "aserganov", "Голыжбин Е.": "Evgen0090",
    "Ковалев М.": "AkunaMatata555", "Баталов Е.": "BatlDad",
    "Матвеев А.": "matveev_andrei", "Бессмертных С.": "getmorepower",
    "Мирасов Г.": "girfanmir", "Лучинин А.": "LuchininAleksandr",
    "Вахобов Г.": "INVESTORGULOM", "Влад": "VladislavOAR",
    "Антон": "Simma445", "IIvajan": "IvaJan", "D": "dadadaann",
    "Alexandr": "Footmor", "Сикач И.": "tWoKizaa",
    "Моргун А.": "Cptmorgun", "Калабин Д.": "dv_kalabin",
    "Чичин А.": "temachichin",
}
DISPLAY_ALIASES = {"михаил": "Волков М.", "вадим": "Большаков В.",
                   "q": "Расчётов А.", "zakhar miakushko": "Мякушко З."}

# Очки
GOAL_POINTS = 2.0
ASSIST_POINTS = 1.0

# Оплата за игру
GAME_TOTAL_RUB = 4500
PAYMENT_PHONE = "+79058056264"
PAYMENT_BANK = "Озон Банк"
BALL_FUND_RUB = 20  # надбавка сверху — копим на новый мяч

os.makedirs(DATA_DIR, exist_ok=True)

bot = Bot(token=BOT_TOKEN, session=AiohttpSession(timeout=25))
dp = Dispatcher()


def load_json(path, default=None):
    if default is None:
        default = {}
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_offset() -> int:
    if not os.path.exists(OFFSET_FILE):
        return 0
    try:
        with open(OFFSET_FILE, "r") as f:
            return int(f.read().strip() or 0)
    except Exception:
        return 0


def save_offset(offset: int):
    with open(OFFSET_FILE, "w") as f:
        f.write(str(offset))


def load_stats() -> dict:
    data = load_json(STATS_FILE, {})
    for k, v in list(data.items()):
        if isinstance(v, dict) and "players" not in v:
            data[k] = {"players": v, "last": []}
    return data


def save_stats(stats: dict):
    save_json(STATS_FILE, stats)


def points_of(rec: dict) -> float:
    return rec.get("goals", 0) * GOAL_POINTS + rec.get("assists", 0) * ASSIST_POINTS


def koef_of(rec: dict) -> float:
    games = rec.get("games", 0)
    return points_of(rec) / games if games else 0.0


def fmt_num(v: float) -> str:
    return f"{v:g}"


def calc_payment_per_player(total: int, n: int) -> int:
    """Делит total на n игроков, округляет вверх до ближайших
    10 руб., затем добавляет надбавку на новый мяч."""
    rounded = math.ceil((total / n) / 10) * 10
    return rounded + BALL_FUND_RUB


def parse_match_line(text: str):
    players, errors = [], []
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = re.match(r"^(.+?)\s+(\d+)\s*\+\s*(\d+)$", chunk)
        if m:
            players.append((m.group(1).strip().title(), int(m.group(2)), int(m.group(3))))
        elif not any(c.isdigit() for c in chunk):
            players.append((chunk.title(), 0, 0))
        else:
            errors.append(chunk)
    return players, errors


EXACT_LIMIT = 15  # до 15 игроков — точный перебор, дальше — эвристика
                  # (перебор от 16 игроков уже занимает больше 5 секунд)

def team_count_for(n: int) -> int:
    """2 команды, если играющих меньше 15, иначе 3."""
    return 3 if n >= 15 else 2


def team_sizes(n: int, team_count: int) -> list:
    """Размеры команд максимально равные — отличаются не больше чем на 1."""
    base = n // team_count
    extra = n % team_count
    return [base + 1] * extra + [base] * (team_count - extra)


def exact_split(players: list, team_count: int):
    """Точный перебор всех вариантов — минимизирует разброс СРЕДНЕГО
    коэффициента между командами (не суммы — размеры команд могут
    отличаться, и по сумме команда с меньшим числом игроков нечестно
    выигрывает)."""
    sizes = team_sizes(len(players), team_count)
    indices = list(range(len(players)))
    best_spread, best_groups = None, None

    def helper(remaining, sizes_left, groups):
        nonlocal best_spread, best_groups
        if not sizes_left:
            avgs = [sum(players[i][1] for i in g) / len(g) for g in groups]
            spread = max(avgs) - min(avgs)
            if best_spread is None or spread < best_spread:
                best_spread, best_groups = spread, [list(g) for g in groups]
            return
        size = sizes_left[0]
        for combo in combinations(remaining, size):
            rest = [i for i in remaining if i not in combo]
            helper(rest, sizes_left[1:], groups + [combo])

    helper(indices, sizes, [])
    return [[players[i] for i in g] for g in best_groups], best_spread


def heuristic_split(players: list, team_count: int, iterations: int = 3000):
    """Для больших групп (перебор нереален): раскладка змейкой с учётом
    целевых размеров команд, затем локальные обмены игроками между
    самой сильной и самой слабой командой, пока это уменьшает разброс
    средних. Быстро (доли секунды) даже на 30+ игроках."""
    sorted_p = sorted(range(len(players)), key=lambda i: -players[i][1])
    sizes = team_sizes(len(players), team_count)
    teams = [[] for _ in range(team_count)]
    ti, forward = 0, True
    for idx in sorted_p:
        while len(teams[ti]) >= sizes[ti]:
            ti = (ti + 1) % team_count
        teams[ti].append(idx)
        if forward:
            if ti == team_count - 1:
                forward = False
            else:
                ti += 1
        else:
            if ti == 0:
                forward = True
            else:
                ti -= 1
        ti = ti % team_count

    def team_avg(t):
        return sum(players[i][1] for i in t) / len(t)

    improved, it = True, 0
    while improved and it < iterations:
        improved = False
        it += 1
        avgs = [team_avg(t) for t in teams]
        hi, lo = avgs.index(max(avgs)), avgs.index(min(avgs))
        if hi == lo:
            break
        best_gain, best_swap = 0, None
        for a in teams[hi]:
            for b in teams[lo]:
                new_avgs = avgs[:]
                new_avgs[hi] = (sum(players[i][1] for i in teams[hi]) - players[a][1] + players[b][1]) / len(teams[hi])
                new_avgs[lo] = (sum(players[i][1] for i in teams[lo]) - players[b][1] + players[a][1]) / len(teams[lo])
                old_spread = max(avgs) - min(avgs)
                new_spread = max(new_avgs) - min(new_avgs)
                if old_spread - new_spread > best_gain:
                    best_gain, best_swap = old_spread - new_spread, (a, b)
        if best_swap:
            a, b = best_swap
            teams[hi].remove(a); teams[hi].append(b)
            teams[lo].remove(b); teams[lo].append(a)
            improved = True

    final_avgs = [team_avg(t) for t in teams]
    return [[players[i] for i in t] for t in teams], max(final_avgs) - min(final_avgs)


def split_teams(players: list, team_count: int):
    """players — список (имя, коэффициент). Выбирает точный или
    приближённый метод в зависимости от числа игроков."""
    if len(players) <= EXACT_LIMIT:
        return exact_split(players, team_count)
    return heuristic_split(players, team_count)


def player_name_for(user: types.User) -> str | None:
    username = (user.username or "").lower()
    for name, saved_username in PLAYER_USERNAMES.items():
        if username and username == saved_username.lower():
            return name
    display = " ".join(x for x in (user.first_name, user.last_name) if x).strip().lower()
    return DISPLAY_ALIASES.get(display)


@dp.poll_answer()
async def poll_answer(answer: types.PollAnswer):
    state = load_json(POLL_STATE_FILE, {})
    if answer.poll_id != state.get("poll_id"):
        return
    voters = state.setdefault("voters", {})
    key = str(answer.user.id)
    if 0 in answer.option_ids:
        voters[key] = {
            "username": answer.user.username or "",
            "display": " ".join(x for x in (answer.user.first_name, answer.user.last_name) if x),
            "player": player_name_for(answer.user),
        }
    else:
        voters.pop(key, None)
    save_json(POLL_STATE_FILE, state)


@dp.message(Command("добавить"))
async def cmd_add_guest(message: types.Message, command: CommandObject):
    if message.from_user.id != OWNER_ID or message.chat.type != "private":
        return
    if not command.args:
        return await message.answer("Формат: /добавить Алексей, Максим 2.3")
    guests = load_json(GUESTS_FILE, [])
    for part in command.args.split(","):
        part = part.strip()
        m = re.match(r"^(.+?)(?:\s+(\d+(?:[.,]\d+)?))?$", part)
        if m:
            guests.append({"name": m.group(1).strip(), "rating": float(m.group(2).replace(",", ".")) if m.group(2) else None})
    save_json(GUESTS_FILE, guests)
    await message.answer("Добавлены: " + ", ".join(g["name"] for g in guests))


@dp.message(Command("разделить", "split"))
async def cmd_split_poll(message: types.Message):
    if message.from_user.id != OWNER_ID or message.chat.type != "private":
        return
    state = load_json(POLL_STATE_FILE, {})
    voters = list(state.get("voters", {}).values())
    unresolved = [v["display"] or ("@" + v["username"]) for v in voters if not v.get("player")]
    if unresolved:
        return await message.answer("Сначала нужно привязать: " + ", ".join(unresolved))
    names = [v["player"] for v in voters] + [g["name"] for g in load_json(GUESTS_FILE, [])]
    if len(names) < 2:
        return await message.answer("Для деления нужно минимум два игрока.")
    stats = load_stats().get(str(CHAT_ID), {}).get("players", {})
    known = [koef_of(stats[n]) for n in names if n in stats and stats[n].get("games")]
    fallback = sum(known) / len(known) if known else 1.0
    guest_ratings = {g["name"]: g.get("rating") for g in load_json(GUESTS_FILE, [])}
    players = [(n, koef_of(stats[n]) if n in stats and stats[n].get("games") else (guest_ratings.get(n) or fallback)) for n in names]
    teams, _ = split_teams(players, team_count_for(len(players)))
    lines = ["⚖️ Предварительные составы"]
    for i, team in enumerate(teams, 1):
        lines.append(f"\n{('⚪', '⚫', '🔴')[i - 1]} Команда {i} ({('белые', 'чёрные', 'красные')[i - 1]}):")
        lines.extend(f"• {name}" for name, _ in team)
    text = "\n".join(lines)
    state["draft"] = text
    save_json(POLL_STATE_FILE, state)
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="Опубликовать в общий чат", callback_data="publish_lineups")]])
    await message.answer(text, reply_markup=keyboard)


@dp.callback_query(lambda q: q.data == "publish_lineups")
async def publish_lineups(query: types.CallbackQuery):
    if query.from_user.id != OWNER_ID:
        return await query.answer("Недоступно", show_alert=True)
    state = load_json(POLL_STATE_FILE, {})
    if state.get("draft"):
        await bot.send_message(CHAT_ID, state["draft"])
        save_json(GUESTS_FILE, [])
    await query.answer("Опубликовано")


# Команды, доступные любому участнику чата — то, чем пользуются каждую игру.
# «Переименовать» и «обнулить» — только владельцу, это не игровые действия.
OPEN_COMMANDS = {"матч", "статистика", "составы", "отменить", "оплата", "start", "help", "id"}


def is_allowed(message: types.Message, command: str = "") -> bool:
    if message.from_user and message.from_user.id == OWNER_ID:
        return True
    if command in OPEN_COMMANDS:
        return message.chat.id == CHAT_ID
    return False


def target_chat(message: types.Message) -> int:
    return CHAT_ID


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if not is_allowed(message, "start"):
        return
    await message.answer(
        f"Привет, {message.from_user.first_name}!\n"
        "Я бот «Мяч».\n"
        "Команда /help — список команд."
    )


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    if not is_allowed(message, "help"):
        return
    await message.answer(
        "Команды:\n"
        "/матч Иванов 2+1, Петров 0+3, Соломин — записать игру\n"
        "/статистика — таблица\n"
        "/составы Иванов, Петров, ... — разбить на команды\n"
        "/опрос — сразу опубликовать опрос на понедельник (на случай сбоя автоматики)\n"
        "/разделить — разбить проголосовавших «+» на команды\n"
        "/отменить — убрать последнюю игру\n"
        "/переименовать Старое = Новое\n"
        "/обнулить — стереть статистику\n"
        "/id — узнать ID\n\n"
        "Опрос публикуется автоматически каждую субботу в 12:00."
    )


@dp.message(Command("id"))
async def cmd_id(message: types.Message):
    if not is_allowed(message, "id"):
        return
    await message.answer(
        f"ID чата: `{message.chat.id}`\n"
        f"Ваш ID: `{message.from_user.id}`",
        parse_mode="Markdown"
    )


@dp.message(Command("матч"))
async def cmd_match(message: types.Message, command: CommandObject):
    if not is_allowed(message, "матч"):
        return
    usage = (
        "Формат:\n"
        "/матч Иванов 2+1, Петров 0+3, Соломин\n\n"
        "Гол = 2 очка, пас = 1 очко."
    )
    if not command.args:
        await message.answer(usage)
        return
    players, errors = parse_match_line(command.args)
    if not players:
        await message.answer(f"Не понял игроков.\n{usage}")
        return
    stats = load_stats()
    chat = stats.setdefault(str(target_chat(message)), {"players": {}, "last": []})
    for name, goals, assists in players:
        rec = chat["players"].setdefault(name, {"games": 0, "goals": 0, "assists": 0})
        rec["games"] += 1
        rec["goals"] += goals
        rec["assists"] += assists
    chat["last"] = [list(p) for p in players]
    save_stats(stats)
    lines = [f"Записал игру ({len(players)} чел.):"]
    for name, g, a in players:
        lines.append(f"• {name}: {g}+{a}")
    if errors:
        lines.append(f"\nНе разобрал: {', '.join(errors)}")
    await message.answer("\n".join(lines))


@dp.message(Command("оплата"))
async def cmd_payment(message: types.Message, command: CommandObject):
    if not is_allowed(message, "оплата"):
        return
    args = (command.args or "").strip()
    if not args.isdigit() or int(args) <= 0:
        await message.answer("Формат: /оплата N — где N число игравших сегодня, например /оплата 14")
        return
    n = int(args)
    if n > 30:
        await message.answer(f"Многовато — {n} человек? Проверьте число.")
        return
    per_player = calc_payment_per_player(GAME_TOTAL_RUB, n)
    await message.answer(
        f"Переводим по {per_player} рублей по номеру телефона "
        f"{PAYMENT_PHONE}. Только {PAYMENT_BANK}."
    )


@dp.message(Command("статистика"))
async def cmd_stats(message: types.Message):
    if not is_allowed(message, "статистика"):
        return
    chat_stats = load_stats().get(str(target_chat(message)), {}).get("players", {})
    if not chat_stats:
        await message.answer("Статистики пока нет.\nДобавьте игру: /матч Иванов 2+1")
        return
    rows = [(koef_of(r), points_of(r), name, r) for name, r in chat_stats.items()]
    rows.sort(reverse=True)
    lines = [
        "📊 Статистика",
        "Гол = 2 очка, пас = 1 очко\n"
        "И — игры, Г — голы, П — пасы, О — очки, К — коэффициент\n"
    ]
    for i, (koef, pts, name, r) in enumerate(rows, 1):
        lines.append(
            f"{i}. {name} — И:{r['games']} Г:{r['goals']} П:{r['assists']} "
            f"О:{fmt_num(pts)} К:{koef:.2f}"
        )
    await message.answer("\n".join(lines))


@dp.message(Command("отменить"))
async def cmd_undo(message: types.Message):
    if not is_allowed(message, "отменить"):
        return
    stats = load_stats()
    chat = stats.setdefault(str(target_chat(message)), {"players": {}, "last": []})
    last = chat.get("last") or []
    if not last:
        await message.answer("Нечего отменять.")
        return
    for name, goals, assists in last:
        rec = chat["players"].get(name)
        if not rec:
            continue
        rec["games"] = max(0, rec["games"] - 1)
        rec["goals"] = max(0, rec["goals"] - goals)
        rec["assists"] = max(0, rec["assists"] - assists)
        if rec["games"] == 0 and rec["goals"] == 0 and rec["assists"] == 0:
            chat["players"].pop(name, None)
    chat["last"] = []
    save_stats(stats)
    await message.answer(f"Последняя игра отменена: {', '.join(p[0] for p in last)}")


@dp.message(Command("переименовать"))
async def cmd_rename(message: types.Message, command: CommandObject):
    if not is_allowed(message, "переименовать"):
        return
    if not command.args or "=" not in command.args:
        await message.answer("Формат: /переименовать Старое = Новое")
        return
    old, new = [x.strip().title() for x in command.args.split("=", 1)]
    stats = load_stats()
    chat = stats.setdefault(str(target_chat(message)), {"players": {}, "last": []})
    rec = chat["players"].pop(old, None)
    if rec is None:
        await message.answer(f"Игрока «{old}» нет.")
        return
    target = chat["players"].setdefault(new, {"games": 0, "goals": 0, "assists": 0})
    for k in ("games", "goals", "assists"):
        target[k] += rec[k]
    for entry in chat.get("last", []):
        if entry[0] == old:
            entry[0] = new
    save_stats(stats)
    await message.answer(f"«{old}» → «{new}»")


@dp.message(Command("обнулить"))
async def cmd_reset(message: types.Message, command: CommandObject):
    if not is_allowed(message, "обнулить"):
        return
    if (command.args or "").strip().lower() != "да":
        await message.answer("Чтобы стереть статистику, напишите:\n/обнулить да")
        return
    stats = load_stats()
    stats.pop(str(target_chat(message)), None)
    save_stats(stats)
    await message.answer("Статистика очищена.")


@dp.message(Command("составы"))
async def cmd_lineups(message: types.Message, command: CommandObject):
    if not is_allowed(message, "составы"):
        return
    usage = (
        "Формат:\n"
        "/составы Иванов, Петров, Сидоров, ...\n\n"
        "Меньше 15 человек → 2 команды, 15 и больше → 3 команды.\n"
        "Все введённые играют, запасных нет — размеры команд могут "
        "отличаться на 1 человека. Составы подбираются точным перебором "
        "(до 15 игроков) или эвристикой (больше), чтобы средний "
        "коэффициент команд был как можно ближе."
    )
    if not command.args:
        await message.answer(usage)
        return
    names = [x.strip().title() for x in command.args.split(",") if x.strip()]
    if not names:
        await message.answer(usage)
        return
    chat_players = load_stats().get(str(target_chat(message)), {}).get("players", {})
    known_koefs = [koef_of(chat_players[n]) for n in names if n in chat_players and chat_players[n].get("games")]
    fallback = sum(known_koefs) / len(known_koefs) if known_koefs else 1.0
    players = []
    unknown = []
    for name in names:
        rec = chat_players.get(name)
        if rec and rec.get("games"):
            koef = koef_of(rec)
        else:
            koef = fallback
            unknown.append(name)
        players.append((name, koef))
    team_count = team_count_for(len(players))
    teams, spread = split_teams(players, team_count)
    lines = [f"⚖️ Составы — {len(players)} игроков, {team_count} команды\n"]
    for i, team in enumerate(teams, 1):
        avg = sum(k for _, k in team) / len(team) if team else 0
        lines.append(f"{('⚪', '⚫', '🔴')[i - 1]} Команда {i} ({('белые', 'чёрные', 'красные')[i - 1]}) (средний коэф. {avg:.2f}):")
        lines += [f"• {n} — {k:.2f}" for n, k in team]
        lines.append("")
    if unknown:
        lines.append(f"Без статистики (коэф. {fallback:.2f} — среднее по остальным): {', '.join(unknown)}")
    await message.answer("\n".join(lines).strip())


async def create_game_poll(chat_id: int):
    now = datetime.datetime.now(YEKB_TZ)
    today = now.date()
    days_until_monday = (7 - today.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7
    monday = today + datetime.timedelta(days=days_until_monday)
    question = f"Футбол Лестех понедельник {monday.strftime('%d.%m.%Y')} {GAME_TIME}"
    poll_message = await bot.send_poll(
        chat_id=chat_id,
        question=question,
        options=["+", "-"],
        is_anonymous=False,
    )
    with open(LAST_POLL_FILE, "w") as f:
        f.write(today.isoformat())
    save_json(POLL_STATE_FILE, {
        "poll_id": poll_message.poll.id,
        "message_id": poll_message.message_id,
        "date": today.isoformat(),
        "voters": {},
    })
    print(f"Опрос отправлен: {question}")
    return question


@dp.message(Command("опрос", "poll"))
async def cmd_poll(message: types.Message):
    if not message.from_user or message.from_user.id != OWNER_ID:
        return
    try:
        question = await create_game_poll(CHAT_ID)
        if message.chat.id != CHAT_ID:
            await message.answer(f"Опрос отправлен в общий чат: {question}")
    except Exception as e:
        await message.answer(f"Не удалось отправить опрос: {e}")


async def maybe_send_poll():
    now = datetime.datetime.now(YEKB_TZ)
    if now.weekday() != 5:  # суббота
        return
    # При перезапуске в течение часа всё равно отправим опрос, но не раньше 12:00.
    if now.hour != 12:
        return
    today = now.date()
    last = None
    if os.path.exists(LAST_POLL_FILE):
        try:
            with open(LAST_POLL_FILE) as f:
                last = datetime.date.fromisoformat(f.read().strip())
        except Exception:
            pass
    if last == today:
        return
    try:
        await create_game_poll(CHAT_ID)
    except Exception as e:
        print(f"Ошибка отправки опроса: {e}")


async def poll_scheduler():
    while True:
        await maybe_send_poll()
        await asyncio.sleep(60)


async def main():
    print(f"Постоянный запуск бота {datetime.datetime.now(YEKB_TZ)}")
    await bot.set_my_commands([
        types.BotCommand(command="poll", description="Создать опрос в общем чате"),
        types.BotCommand(command="split", description="Разделить выбравших + на составы"),
        types.BotCommand(command="stats", description="Показать статистику"),
        types.BotCommand(command="pay", description="Рассчитать оплату"),
        types.BotCommand(command="help", description="Список команд"),
    ])
    scheduler = asyncio.create_task(poll_scheduler())
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
