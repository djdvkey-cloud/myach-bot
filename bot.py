"""
Футбольный бот «Мяч» — версия для GitHub Actions
Опросы по субботам + команды статистики и составов
"""

import asyncio
import datetime
import json
import os
import re
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

DATA_DIR = "data"
STATS_FILE = os.path.join(DATA_DIR, "stats.json")
OFFSET_FILE = os.path.join(DATA_DIR, "last_offset.txt")
LAST_POLL_FILE = os.path.join(DATA_DIR, "last_poll.txt")

# Очки
GOAL_POINTS = 2.0
ASSIST_POINTS = 1.0

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


def snake_teams(players: list, team_count: int):
    teams = [[] for _ in range(team_count)]
    i, forward = 0, True
    for p in players:
        teams[i].append(p)
        if forward:
            if i == team_count - 1:
                forward = False
            else:
                i += 1
        else:
            if i == 0:
                forward = True
            else:
                i -= 1
    return teams


def teams_and_bench_size(n: int):
    if n == 10:
        return 2, 0
    if n == 15:
        return 3, 0
    if 10 < n < 15:
        return 2, n - 10
    if n > 15:
        return 3, n - 15
    return 2, 0


def is_allowed(message: types.Message) -> bool:
    if message.from_user and message.from_user.id == OWNER_ID:
        return True
    return message.chat.id == CHAT_ID


def target_chat(message: types.Message) -> int:
    return CHAT_ID


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if not is_allowed(message):
        return
    await message.answer(
        f"Привет, {message.from_user.first_name}!\n"
        "Я бот «Мяч» (версия для GitHub Actions).\n"
        "Команда /help — список команд."
    )


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    if not is_allowed(message):
        return
    await message.answer(
        "Команды:\n"
        "/матч Иванов 2+1, Петров 0+3, Соломин — записать игру\n"
        "/статистика — таблица\n"
        "/составы Иванов, Петров, ... — разбить на команды\n"
        "/отменить — убрать последнюю игру\n"
        "/переименовать Старое = Новое\n"
        "/обнулить да — стереть статистику\n"
        "/id — узнать ID\n\n"
        "Опрос публикуется автоматически каждую субботу в 12:00."
    )


@dp.message(Command("id"))
async def cmd_id(message: types.Message):
    if not is_allowed(message):
        return
    await message.answer(
        f"ID чата: `{message.chat.id}`\n"
        f"Ваш ID: `{message.from_user.id}`",
        parse_mode="Markdown"
    )


@dp.message(Command("матч"))
async def cmd_match(message: types.Message, command: CommandObject):
    if not is_allowed(message):
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


@dp.message(Command("статистика"))
async def cmd_stats(message: types.Message):
    if not is_allowed(message):
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
    if not is_allowed(message):
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
    if not is_allowed(message):
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
    if not is_allowed(message):
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
    if not is_allowed(message):
        return
    usage = (
        "Формат:\n"
        "/составы Иванов, Петров, Сидоров, ...\n\n"
        "10 человек → 2 команды, 15 → 3 команды.\n"
        "Команды собираются змейкой по коэффициенту."
    )
    if not command.args:
        await message.answer(usage)
        return

    names = [x.strip().title() for x in command.args.split(",") if x.strip()]
    if not names:
        await message.answer(usage)
        return

    chat_players = load_stats().get(str(target_chat(message)), {}).get("players", {})
    players = []
    unknown = []
    for name in names:
        rec = chat_players.get(name)
        koef = koef_of(rec) if rec and rec.get("games") else 0.0
        if not rec or not rec.get("games"):
            unknown.append(name)
        players.append((name, koef))
    players.sort(key=lambda x: -x[1])

    team_count, bench_size = teams_and_bench_size(len(players))
    bench = players[-bench_size:] if bench_size else []
    playing = players[:-bench_size] if bench_size else players
    teams = snake_teams(playing, team_count)

    lines = [f"⚖️ Составы — {len(players)} игроков, {team_count} команд\n"]
    for i, team in enumerate(teams, 1):
        avg = sum(k for _, k in team) / len(team) if team else 0
        lines.append(f"Команда {i} (ср. коэф. {avg:.2f}):")
        lines += [f"• {n} — {k:.2f}" for n, k in team]
        lines.append("")
    if bench:
        lines.append("Запасные:")
        lines += [f"• {n} — {k:.2f}" for n, k in bench]
    if unknown:
        lines.append(f"\nБез статистики (коэф. 0): {', '.join(unknown)}")
    await message.answer("\n".join(lines).strip())


async def maybe_send_poll():
    now = datetime.datetime.now(YEKB_TZ)
    if now.weekday() != 5:  # суббота
        return
    if not (11 <= now.hour <= 13):
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

    monday = today + datetime.timedelta(days=2)
    question = f"Футбол Лестех понедельник {monday.strftime('%d.%m.%Y')} {GAME_TIME}"
    try:
        await bot.send_poll(
            chat_id=CHAT_ID,
            question=question,
            options=["+", "-"],
            is_anonymous=False,
        )
        with open(LAST_POLL_FILE, "w") as f:
            f.write(today.isoformat())
        print(f"Опрос отправлен: {question}")
    except Exception as e:
        print(f"Ошибка отправки опроса: {e}")


async def main():
    print(f"Запуск бота {datetime.datetime.now(YEKB_TZ)}")

    try:
        await maybe_send_poll()

        offset = load_offset()
        try:
            updates = await bot.get_updates(offset=offset, timeout=20, limit=50)
        except Exception as e:
            print(f"Ошибка getUpdates: {e}")
            return

        if not updates:
            print("Новых сообщений нет")
            return

        max_id = offset
        for update in updates:
            max_id = max(max_id, update.update_id + 1)
            try:
                await dp.feed_update(bot, update)
            except Exception as e:
                print(f"Ошибка обработки update {update.update_id}: {e}")

        save_offset(max_id)
        print(f"Обработано обновлений: {len(updates)}, новый offset: {max_id}")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
