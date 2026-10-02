"""
Футбольный бот «Мяч» — постоянная версия для Amvera
Опросы по субботам, статистика, составы, фактический состав игры,
ввод результата и оплата из личного интерфейса администратора.
"""
import asyncio
import datetime
import hashlib
import json
import math
import os
import random
import re
import shutil
import tempfile
from itertools import combinations
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.client.session.aiohttp import AiohttpSession

BOT_VERSION = "2026-10-04"

# === Настройки из Secrets ===
BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ.get("OWNER_ID", "8378612979"))
CHAT_ID = int(os.environ.get("CHAT_ID", "-5022330317"))

YEKB_TZ = ZoneInfo("Asia/Yekaterinburg")
GAME_TIME = "21.30-23.00"

DATA_DIR = os.environ.get("DATA_DIR", "/data" if os.path.isdir("/data") else "data")
STATS_FILE = os.path.join(DATA_DIR, "stats.json")
OFFSET_FILE = os.path.join(DATA_DIR, "last_offset.txt")
LAST_POLL_FILE = os.path.join(DATA_DIR, "last_poll.txt")
POLL_STATE_FILE = os.path.join(DATA_DIR, "poll_state.json")
GUESTS_FILE = os.path.join(DATA_DIR, "guests.json")
PLAYERS_FILE = os.path.join(DATA_DIR, "players.json")
GAME_FILE = os.path.join(DATA_DIR, "game.json")
GAME_ARCHIVE_FILE = os.path.join(DATA_DIR, "game_archive.json")
META_FILE = os.path.join(DATA_DIR, "ui_meta.json")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
GAMES_FILE = os.path.join(DATA_DIR, "games.json")        # журнал состоявшихся игр (футбольных вечеров) с постоянными номерами
# Состоявшиеся игры №1–4: даты заданы Дмитрием. Составы и голы этих игр в проекте не сохранялись — не придумываются.
HISTORICAL_GAMES = [(1, "2026-09-07"), (2, "2026-09-14"), (3, "2026-09-21"), (4, "2026-09-28")]
BACKUP_KEEP = 40

# Начальный список игроков. Используется ТОЛЬКО для первого создания
# /data/players.json; дальше источник правды — файл, которым управляет
# Дмитрий из личного меню бота (без правки кода и перезапуска).
DEFAULT_PLAYER_USERNAMES = {
    "Ларионов М.": "lmur2000", "Бобров М.": "BobrovMA1996",
    "Серганов А.": "aserganov", "Голыжбин Е.": "Evgen0090",
    "Ковалёв М.": "AkynaMatata555", "Баталов Е.": "BatlDad",
    "Матвеев А.": "matveev_andrei", "Бессмертных С.": "getmorepower",
    "Мирасов Г.": "girfanmir", "Лучинин А.": "LuchininAleksandr",
    "Вахобов Г.": "INVESTORGULOM", "Влад": "VladislavOAR",
    "Антон": "Siimma445", "IIvajan": "IvaJan", "D": "dadadaann",
    "Alexandr": "Footmor", "Сикач И.": "tWoKizaa",
    "Моргун А.": "Cptmorgun", "Калабин Д.": "dv_kalabin",
    "Чичин А.": "temachichin", "Расчётов А.": "go-go131",
}
DEFAULT_DISPLAY_ALIASES = {"михаил": "Волков М.", "вадим": "Большаков В.",
                           "q": "Расчётов А.", "zakhar miakushko": "Мякушко З."}

# Очки
GOAL_POINTS = 2.0
ASSIST_POINTS = 1.0

# Оплата за игру
GAME_TOTAL_RUB = 4500
PAYMENT_PHONE = "+79058056264"
PAYMENT_BANK = "Озон Банк"
BALL_FUND_RUB = 20  # надбавка сверху — копим на новый мяч

TEAM_ICONS = ("⚪", "⚫", "🔴")
TEAM_COLORS = ("белые", "чёрные", "красные")

# Живое оформление: только текст сообщений, в расчётах не участвует.
POLL_PHRASES = [
    "⚽ Пора собирать состав!",
    "⚽ Кто в игре?",
    "⚽ Собираемся на футбол!",
    "⚽ Мяч сам себя не погоняет!",
    "⚽ Пора расчехлять бутсы!",
    "⚽ Начинаем перекличку!",
    "⚽ Пора определяться — играем!",
    "⚽ Футбольный понедельник уже близко!",
    "⚽ Кто на поле, а кто на диване?",
    "⚽ Кто готов забивать, а кто — отдавать?",
    "⚽ Понедельник. Футбол. Кто с нами?",
]
FINAL_PHRASES = [
    "Всем хорошей игры! ⚽",
    "Удачи на поле! ⚽",
    "Красивой игры и без травм! 💪⚽",
    "Всем удачи — увидимся на поле! ⚽",
    "Хорошего футбола! ⚽",
    "Пусть победит сильнейший! ⚽",
    "Главное — без травм. Остальное на поле! ⚽",
    "Команды готовы. Погнали! ⚽",
    "Составы есть — осталось сыграть! ⚽",
    "Всем хорошего футбола и отличного настроения! ⚽",
]

os.makedirs(DATA_DIR, exist_ok=True)

bot = Bot(token=BOT_TOKEN, session=AiohttpSession(timeout=25))
dp = Dispatcher()

POLL_LOCK = asyncio.Lock()
PUBLISH_LOCK = asyncio.Lock()
RECORD_LOCK = asyncio.Lock()
# Ожидание текстового ввода от Дмитрия (в памяти: после перезапуска
# просто начать действие заново).
AWAITING: dict = {}


# ============================================================
# Хранилище: атомарная запись, строгое чтение, резервные копии
# ============================================================

class DataCorrupted(Exception):
    """Файл данных повреждён или имеет неожиданную структуру. Изменяющие
    операции обязаны остановиться, исходный файл не перезаписывается."""

    def __init__(self, path, reason):
        super().__init__(f"{os.path.basename(path)}: {reason}")
        self.path = path
        self.reason = reason


_MISSING = object()


def atomic_write_text(path: str, text: str):
    """Пишет во временный файл рядом и атомарно подменяет целевой —
    обрыв посреди записи не оставляет наполовину записанный файл."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_json(path, data):
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def _preserve_corrupt(path):
    try:
        stamp = datetime.datetime.now(YEKB_TZ).strftime("%Y%m%d-%H%M%S")
        shutil.copy2(path, f"{path}.corrupt-{stamp}")
    except Exception as e:
        print(f"Не удалось сохранить копию повреждённого файла {path}: {e}")


def load_json(path, default=None):
    """Мягкое чтение для служебных файлов (опрос, игра, меню). При ошибке
    разбора исходный файл сохраняется копией, возвращается default."""
    if default is None:
        default = {}
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Не удалось прочитать {path}: {e}")
        _preserve_corrupt(path)
        return default


def load_json_strict(path):
    """Строгое чтение для данных, потеря которых недопустима. Отсутствие
    файла — _MISSING; нечитаемый файл — DataCorrupted (а не пустой словарь)."""
    if not os.path.exists(path):
        return _MISSING
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        raise DataCorrupted(path, f"{type(e).__name__}: {e}")


def list_backups(prefix: str) -> list:
    if not os.path.isdir(BACKUP_DIR):
        return []
    return sorted(n for n in os.listdir(BACKUP_DIR) if n.startswith(prefix + "-") and n.endswith(".json"))


def backup_file(path: str, prefix: str):
    """Копия действующего (читаемого) файла в /data/backups перед перезаписью.
    Одинаковое содержимое повторно не копируется; хранятся последние BACKUP_KEEP."""
    if not os.path.exists(path):
        return
    try:
        with open(path, "rb") as f:
            raw = f.read()
        json.loads(raw.decode("utf-8"))  # повреждённое не подменяет хорошие копии
        names = list_backups(prefix)
        if names:
            with open(os.path.join(BACKUP_DIR, names[-1]), "rb") as f:
                if f.read() == raw:
                    return
        os.makedirs(BACKUP_DIR, exist_ok=True)
        stamp = datetime.datetime.now(YEKB_TZ).strftime("%Y%m%d-%H%M%S-%f")
        with open(os.path.join(BACKUP_DIR, f"{prefix}-{stamp}.json"), "wb") as f:
            f.write(raw)
        for old in list_backups(prefix)[:-BACKUP_KEEP]:
            try:
                os.unlink(os.path.join(BACKUP_DIR, old))
            except OSError:
                pass
    except Exception as e:
        print(f"Не удалось сделать резервную копию {path}: {e}")


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


# ============================================================
# Статистика
# ============================================================

def _num_ok(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and v >= 0


def normalize_stats(data) -> dict:
    """Приводит старый формат к текущему и проверяет структуру.
    ValueError — данные не похожи на статистику."""
    if not isinstance(data, dict):
        raise ValueError("корневой элемент не является словарём")
    for k, v in list(data.items()):
        if not isinstance(v, dict):
            raise ValueError(f"раздел {k!r} не является словарём")
        if "players" not in v:
            data[k] = v = {"players": v, "last": []}
        players = v["players"]
        if not isinstance(players, dict):
            raise ValueError(f"раздел {k!r}: players не словарь")
        for name, rec in players.items():
            if not isinstance(rec, dict):
                raise ValueError(f"игрок {name!r}: запись не словарь")
            for field in ("games", "goals", "assists"):
                if field in rec and not _num_ok(rec[field]):
                    raise ValueError(f"игрок {name!r}: поле {field} некорректно")
        last = v.get("last", [])
        if not isinstance(last, list) or any(
            not (isinstance(e, (list, tuple)) and len(e) == 3) for e in last
        ):
            raise ValueError(f"раздел {k!r}: last некорректен")
    return data


def load_stats() -> dict:
    """Строго: повреждённая статистика НЕ превращается в пустую."""
    data = load_json_strict(STATS_FILE)
    if data is _MISSING:
        if list_backups("stats"):
            raise DataCorrupted(STATS_FILE, "файл отсутствует, хотя есть резервные копии")
        return {}
    try:
        return normalize_stats(data)
    except ValueError as e:
        raise DataCorrupted(STATS_FILE, str(e))


def save_stats(stats: dict):
    normalize_stats(stats)
    backup_file(STATS_FILE, "stats")
    save_json(STATS_FILE, stats)


def points_of(rec: dict) -> float:
    return rec.get("goals", 0) * GOAL_POINTS + rec.get("assists", 0) * ASSIST_POINTS


def koef_of(rec: dict) -> float:
    games = rec.get("games", 0)
    return points_of(rec) / games if games else 0.0


def fmt_num(v: float) -> str:
    return f"{v:g}"


def norm_key(s: str) -> str:
    """Ключ сравнения имён: без регистра, ё = е, лишние пробелы убраны."""
    return re.sub(r"\s+", " ", s.strip().replace("ё", "е").replace("Ё", "Е")).casefold()


def find_key(name: str, candidates) -> str | None:
    k = norm_key(name)
    for c in candidates:
        if norm_key(c) == k:
            return c
    return None


def resolve_name(raw: str, stats_players=None, registered=()) -> str:
    """Каноническое написание имени: сначала как уже записано в статистике,
    затем как у зарегистрированного игрока, иначе — Обычный Регистр.
    Так один человек не раздваивается из-за регистра, «ё/е» и пробелов."""
    raw = re.sub(r"\s+", " ", raw.strip())
    return find_key(raw, stats_players or {}) or find_key(raw, registered) or raw.title()


def stats_players_of(stats: dict) -> dict:
    return stats.get(str(CHAT_ID), {}).get("players", {})


# ---- журнал игр (games.json): «Игра» = весь футбольный вечер, счёт отдельных матчей не ведётся ----

def _seed_games() -> dict:
    return {"version": 1,
            "games": [{"number": n, "date": d, "teams": None, "source": "historical"} for n, d in HISTORICAL_GAMES],
            "annulled": []}


def validate_games(data):
    if not isinstance(data, dict) or not isinstance(data.get("games"), list):
        raise ValueError("нет списка games")
    seen = set()
    for g in data["games"]:
        if not isinstance(g, dict):
            raise ValueError("игра не словарь")
        n = g.get("number")
        if not isinstance(n, int) or isinstance(n, bool) or n < 1 or n in seen:
            raise ValueError(f"неверный или повторяющийся номер игры {n!r}")
        seen.add(n)
        try:
            datetime.date.fromisoformat(g.get("date", ""))
        except (ValueError, TypeError):
            raise ValueError(f"игра №{n}: неверная дата")
        teams = g.get("teams")
        if teams is not None:
            if not isinstance(teams, list):
                raise ValueError(f"игра №{n}: teams не список")
            for team in teams:
                if not isinstance(team, list):
                    raise ValueError(f"игра №{n}: команда не список")
                for pl in team:
                    if (not isinstance(pl, dict) or not isinstance(pl.get("name"), str)
                            or not _num_ok(pl.get("goals", 0)) or not _num_ok(pl.get("assists", 0))):
                        raise ValueError(f"игра №{n}: некорректная запись игрока")
    if not isinstance(data.get("annulled", []), list):
        raise ValueError("annulled не список")


def load_games() -> dict:
    """Строго, как статистика: повреждённый журнал не считается пустым. Первый запуск — игры №1–4."""
    data = load_json_strict(GAMES_FILE)
    if data is _MISSING:
        if list_backups("games"):
            raise DataCorrupted(GAMES_FILE, "файл отсутствует, хотя есть резервные копии")
        data = _seed_games()
        save_json(GAMES_FILE, data)
        print("Создан /data/games.json: игры №1–4 (только номера и даты)")
        return data
    try:
        validate_games(data)
    except ValueError as e:
        raise DataCorrupted(GAMES_FILE, str(e))
    data.setdefault("annulled", [])
    if data["annulled"]:
        # Совместимость с версией 2026-10-02 (там /отменить убирал игру из журнала): номер возвращается как «отменённые данные».
        for old in data["annulled"]:
            back = {k: v for k, v in old.items() if k != "annulled_at"}
            back["teams_before_undo"], back["teams"], back["status"] = back.get("teams"), None, "reverted"
            if all(g["number"] != back["number"] for g in data["games"]):
                data["games"].append(back)
        data["annulled"] = []
        data["games"].sort(key=lambda g: g["number"])
        try:
            save_games(data)
        except Exception as e:
            print(f"Не удалось сохранить журнал игр после миграции: {e}")
    return data


def save_games(data: dict):
    validate_games(data)
    backup_file(GAMES_FILE, "games")
    save_json(GAMES_FILE, data)


def played_games_count() -> int:
    """Источник числа «сыграно игр: N» — количество состоявшихся пронумерованных игр (не строки статистики)."""
    return len(load_games()["games"])


def next_game_number(data: dict) -> int:
    """Новый номер — всегда больше любого уже выданного (в том числе у игры с отменёнными данными): номера не переиспользуются."""
    used = [g["number"] for g in data["games"]] + [g["number"] for g in data.get("annulled", [])]
    return max(used, default=0) + 1


class NeedGameChoice(Exception):
    """Есть игры с отменёнными данными: при записи нужно явно выбрать — исправление этой игры или новый вечер."""

    def __init__(self, reverted: list):
        super().__init__("нужен выбор: исправление отменённой игры или новый вечер")
        self.reverted = reverted


def game_date_now() -> str:
    """Дата футбольного вечера: запись после полуночи (до 06:00 по Екатеринбургу) относится к прошедшему вечеру."""
    now = datetime.datetime.now(YEKB_TZ)
    if now.hour < 6:
        now = now - datetime.timedelta(days=1)
    return now.date().isoformat()


def fmt_game_date(iso: str) -> str:
    return f"{iso[8:10]}.{iso[5:7]}.{iso[0:4]}"


def game_title(game: dict) -> str:
    return f"Игра №{game['number']} — {fmt_game_date(game['date'])}"


def _read_bytes(path: str):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _restore_bytes(snapshot: dict):
    """Откат файлов к прежнему содержимому (None — файла не было)."""
    for path, raw in snapshot.items():
        try:
            if raw is None:
                if os.path.exists(path):
                    os.unlink(path)
            else:
                with open(path, "wb") as f:
                    f.write(raw)
        except OSError as e:
            print(f"Не удалось откатить {path}: {e}")


def build_game_teams(team_names, written: list, registry: dict) -> list:
    """Составы игры для архива: у каждого игрока — его голы и передачи именно в этой игре и постоянный pid."""
    by = {norm_key(n): (n, gl, a) for n, gl, a in written}
    teams = []
    for team in team_names:
        row = []
        for raw in team:
            name, gl, a = by.get(norm_key(raw), (raw, 0, 0))
            key = find_key(name, registry["players"])
            row.append({"name": name, "pid": registry["players"][key].get("pid") if key else None,
                        "goals": gl, "assists": a})
        teams.append(row)
    return teams


def record_match_full(players: list, teams=None, date: str | None = None, mode=None):
    """Записывает игру (весь вечер): статистику игроков и номер в журнале игр.
    players — [(имя, голы, передачи)]; teams — фактические составы (список списков имён) или None.
    Возвращает (записанные, игра). DataCorrupted — ничего не записано. Если не удалось записать журнал —
    статистика откатывается: либо записано всё, либо ничего.
    mode: None — игр с отменёнными данными нет → новый номер; если они есть, нужно явное решение (NeedGameChoice):
    "new" — новый вечер (следующий номер) или номер N отменённой игры — исправленные данные той же игры (прежние номер и дата),
    сколько бы времени ни прошло."""
    games = load_games()                       # строго и ДО любых изменений
    reverted = [g for g in games["games"] if g.get("status") == "reverted"]
    target = None
    if isinstance(mode, int) and not isinstance(mode, bool):
        target = next((g for g in reverted if g["number"] == mode), None)
        if target is None:
            raise ValueError(f"Игра №{mode} не ждёт исправления.")
    elif mode != "new" and reverted:
        raise NeedGameChoice(reverted)
    stats = load_stats()
    registry = load_players()
    registered = set(registry["players"])
    chat = stats.setdefault(str(CHAT_ID), {"players": {}, "last": []})
    written = []
    for name, goals, assists in players:
        cname = resolve_name(name, chat["players"], registered)
        rec = chat["players"].setdefault(cname, {"games": 0, "goals": 0, "assists": 0})
        rec["games"] = rec.get("games", 0) + 1
        rec["goals"] = rec.get("goals", 0) + goals
        rec["assists"] = rec.get("assists", 0) + assists
        written.append((cname, goals, assists))
    chat["last"] = [list(p) for p in written]
    snapshot = {STATS_FILE: _read_bytes(STATS_FILE)}
    save_stats(stats)
    try:
        split = bool(teams)
        game_teams = build_game_teams(teams if split else [[n for n, _, _ in written]], written, registry)
        day = date or game_date_now()
        game = target
        fields = {"teams": game_teams, "split": split, "source": "result" if split else "text", "recorded_at": now_iso()}
        if game is not None:                    # исправленный ввод той же игры: прежний номер и дата
            for key in ("status", "reverted_at", "teams_before_undo"):
                game.pop(key, None)
            game.update(fields)
            game["reentered_at"] = now_iso()
        else:
            game = {"number": next_game_number(games), "date": day, **fields}
            games["games"].append(game)
        games["last_recorded"] = game["number"]
        save_games(games)
    except BaseException:
        _restore_bytes(snapshot)
        raise
    return written, game


def record_match(players: list, teams=None, date: str | None = None, mode=None) -> list:
    return record_match_full(players, teams, date, mode)[0]


def revert_last_game(games: dict):
    """/отменить: статистика откатывается, а игра ОСТАЁТСЯ в журнале со своим номером и датой (status = reverted, данные убраны,
    прежние — в teams_before_undo). Номер никому больше не выдаётся: исправленные данные можно записать в ту же игру в любое время
    (см. record_match_full, mode). Отменяется игра, записанная последней (last_recorded); №1–4 (historical) не затрагиваются."""
    candidates = {g["number"]: g for g in games["games"] if g.get("source") != "historical" and g.get("status") != "reverted"}
    n = games.get("last_recorded")
    last = candidates.get(n) if n is not None else (candidates[max(candidates)] if candidates else None)
    if last is None:
        return None
    last["teams_before_undo"], last["teams"] = last.get("teams"), None
    last["status"], last["reverted_at"] = "reverted", now_iso()
    games["last_recorded"] = None
    return last


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


# ============================================================
# Алгоритм составов (без изменений)
# ============================================================

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


def koef_for_names(names: list, ratings: dict, stats_players: dict):
    """Действующее правило бота: у игрока со статистикой — его коэффициент;
    у гостя с указанным рейтингом — рейтинг; иначе — средний коэффициент
    известных игроков списка. Возвращает (коэффициенты, без статистики, среднее)."""
    by_key = {norm_key(k): v for k, v in stats_players.items()}
    known = []
    for n in names:
        rec = by_key.get(norm_key(n))
        if rec and rec.get("games"):
            known.append(koef_of(rec))
    fallback = sum(known) / len(known) if known else 1.0
    koefs, unknown = {}, []
    for n in names:
        rec = by_key.get(norm_key(n))
        if rec and rec.get("games"):
            koefs[n] = koef_of(rec)
        elif ratings.get(n):
            koefs[n] = float(ratings[n])
        else:
            koefs[n] = fallback
            unknown.append(n)
    return koefs, unknown, fallback


def format_lineups(teams: list, unknown: list, fallback: float) -> str:
    """Общий формат для /составы, личного черновика и публикации
    в общий чат, чтобы выглядели одинаково."""
    total = sum(len(team) for team in teams)
    lines = [f"⚖️ Составы — {total} игроков, {len(teams)} команды\n"]
    for i, team in enumerate(teams, 1):
        avg = sum(k for _, k in team) / len(team) if team else 0
        lines.append(f"{TEAM_ICONS[i - 1]} Команда {i} ({TEAM_COLORS[i - 1]}) (средний коэф. {avg:.2f}):")
        lines += [f"• {n} — {k:.2f}" for n, k in team]
        lines.append("")
    if unknown:
        lines.append(f"Без статистики (коэф. {fallback:.2f} — среднее по остальным): {', '.join(unknown)}")
    return "\n".join(lines).strip()


# ============================================================
# Игроки и привязки (хранятся в /data/players.json)
# ============================================================

def _seed_players() -> dict:
    players = {}
    for name, username in DEFAULT_PLAYER_USERNAMES.items():
        players[name] = {"usernames": [username], "ids": [], "aliases": []}
    for display, name in DEFAULT_DISPLAY_ALIASES.items():
        rec = players.setdefault(name, {"usernames": [], "ids": [], "aliases": []})
        if display not in rec["aliases"]:
            rec["aliases"].append(display)
    data = {"version": 1, "players": players}
    ensure_pids(data)
    return data


def ensure_pids(data: dict) -> bool:
    """Каждому игроку — постоянный внутренний pid (не меняется при смене имени/username). True — что-то присвоено."""
    players = data["players"]
    used = [rec["pid"] for rec in players.values() if isinstance(rec.get("pid"), int) and not isinstance(rec.get("pid"), bool)]
    nxt = max([data.get("next_pid", 1) - 1] + used) + 1
    changed = False
    for rec in players.values():
        if not isinstance(rec.get("pid"), int) or isinstance(rec.get("pid"), bool):
            rec["pid"] = nxt
            nxt += 1
            changed = True
    if data.get("next_pid") != nxt:
        data["next_pid"] = nxt
        changed = True
    return changed


def player_by_pid(data: dict, pid: int):
    for name, rec in data["players"].items():
        if rec.get("pid") == pid:
            return name, rec
    return None, None


def validate_players(data):
    if not isinstance(data, dict) or not isinstance(data.get("players"), dict):
        raise ValueError("нет словаря players")
    for name, rec in data["players"].items():
        if not isinstance(rec, dict):
            raise ValueError(f"игрок {name!r}: запись не словарь")
        for field in ("usernames", "ids", "aliases"):
            rec.setdefault(field, [])
            if not isinstance(rec[field], list):
                raise ValueError(f"игрок {name!r}: поле {field} не список")
    pids = [rec["pid"] for rec in data["players"].values() if "pid" in rec]
    if any(not isinstance(x, int) or isinstance(x, bool) for x in pids) or len(pids) != len(set(pids)):
        raise ValueError("pid игроков некорректны или повторяются")


def load_players() -> dict:
    data = load_json_strict(PLAYERS_FILE)
    if data is _MISSING:
        data = _seed_players()
        save_json(PLAYERS_FILE, data)
        print("Создан /data/players.json из начального списка")
        return data
    try:
        validate_players(data)
    except ValueError as e:
        raise DataCorrupted(PLAYERS_FILE, str(e))
    if ensure_pids(data):          # совместимое дополнение старого файла: статистика и имена не меняются
        try:
            save_players(data)
            print("players.json: игрокам присвоены постоянные ID")
        except Exception as e:
            print(f"Не удалось сохранить ID игроков: {e}")
    return data


def save_players(data: dict):
    validate_players(data)
    backup_file(PLAYERS_FILE, "players")
    save_json(PLAYERS_FILE, data)


def clean_username(raw: str) -> str:
    return raw.strip().lstrip("@").strip()


def match_player(data: dict, user_id, username: str, display: str) -> str | None:
    uname = (username or "").lower()
    for name, rec in data["players"].items():
        if user_id is not None and user_id in rec.get("ids", []):
            return name
    if uname:
        for name, rec in data["players"].items():
            if uname in (u.lower() for u in rec.get("usernames", [])):
                return name
    disp = (display or "").strip().lower()
    if disp:
        for name, rec in data["players"].items():
            if disp in (a.lower() for a in rec.get("aliases", [])):
                return name
    return None


def player_name_for(user: types.User) -> str | None:
    try:
        data = load_players()
    except DataCorrupted as e:
        print(f"Игроки недоступны: {e}")
        return None
    display = " ".join(x for x in (user.first_name, user.last_name) if x)
    return match_player(data, user.id, user.username or "", display)


def players_add(name: str, username: str | None = None) -> str:
    """Добавляет игрока; возвращает каноническое имя. ValueError — дубль/пусто."""
    name = re.sub(r"\s+", " ", name.strip())
    if not name:
        raise ValueError("Пустое имя.")
    data = load_players()
    if find_key(name, data["players"]):
        raise ValueError(f"Игрок «{find_key(name, data['players'])}» уже есть.")
    data["players"][name] = {"usernames": [clean_username(username)] if username else [], "ids": [], "aliases": [],
                             "pid": data["next_pid"]}
    data["next_pid"] += 1
    save_players(data)
    return name


def players_remove(name: str) -> str:
    data = load_players()
    key = find_key(name, data["players"])
    if not key:
        raise ValueError(f"Игрока «{name}» нет.")
    del data["players"][key]
    save_players(data)
    return key


def players_bind(name: str, username: str | None = None, user_id=None, alias: str | None = None) -> str:
    data = load_players()
    key = find_key(name, data["players"])
    if not key:
        raise ValueError(f"Игрока «{name}» нет.")
    rec = data["players"][key]
    if username:
        u = clean_username(username)
        if u and u.lower() not in (x.lower() for x in rec["usernames"]):
            rec["usernames"].append(u)
    if user_id is not None and user_id not in rec["ids"]:
        rec["ids"].append(user_id)
    if alias:
        a = alias.strip().lower()
        if a and a not in (x.lower() for x in rec["aliases"]):
            rec["aliases"].append(a)
    save_players(data)
    return key


def clean_player_name(raw: str) -> str:
    name = re.sub(r"\s+", " ", raw.strip())
    if not name or "\n" in raw.strip():
        raise ValueError("Имя должно быть в одну строку и не пустым.")
    if len(name) > 60 or name.startswith("/"):
        raise ValueError("Слишком длинное имя (до 60 символов) или оно начинается с «/».")
    return name


def _rename_json(obj, old: str, new: str):
    """Заменяет имя во всех строках и ключах структуры (состав, голы, история замен и т.д.), без учёта регистра и ё/е."""
    key = norm_key(old)
    if isinstance(obj, str):
        return new if norm_key(obj) == key else obj
    if isinstance(obj, list):
        return [_rename_json(x, old, new) for x in obj]
    if isinstance(obj, dict):
        return {(new if isinstance(k, str) and norm_key(k) == key else k): _rename_json(v, old, new) for k, v in obj.items()}
    return obj


def players_rename(pid: int, new_raw: str) -> tuple:
    """Меняет имя игрока, НЕ создавая нового: pid остаётся прежним, статистика, текущий и архивные составы и
    журнал игр переносятся на новое имя. Либо меняется всё, либо ничего (при сбое файлы возвращаются)."""
    new = clean_player_name(new_raw)
    data = load_players()
    old, rec = player_by_pid(data, pid)
    if old is None:
        raise ValueError("Игрок не найден (возможно, уже удалён).")
    if new == old:
        raise ValueError("Имя не изменилось.")
    stats = load_stats()
    games = load_games()
    if norm_key(new) != norm_key(old):                         # смена только регистра/«ё» — тот же человек
        clash = find_key(new, data["players"])
        if clash:
            raise ValueError(f"Игрок «{clash}» уже есть в списке. Чтобы объединить записи, используйте /переименовать.")
        clash = find_key(new, stats_players_of(stats))
        if clash:
            raise ValueError(f"В статистике уже есть «{clash}». Чтобы объединить записи, используйте /переименовать.")
    snapshot = {path: _read_bytes(path) for path in
                (STATS_FILE, GAME_FILE, GAME_ARCHIVE_FILE, POLL_STATE_FILE, GAMES_FILE, PLAYERS_FILE)}
    try:
        chat = stats.get(str(CHAT_ID))
        if chat:
            skey = find_key(old, chat["players"])
            if skey:
                chat["players"] = {(new if k == skey else k): v for k, v in chat["players"].items()}
            for entry in chat.get("last", []):
                if norm_key(entry[0]) == norm_key(old):
                    entry[0] = new
            save_stats(stats)
        changed = False
        for game in games["games"]:
            for team in game.get("teams") or []:
                for pl in team:
                    if pl.get("pid") == pid or (pl.get("pid") is None and norm_key(pl["name"]) == norm_key(old)):
                        pl["name"], pl["pid"] = new, pid
                        changed = True
        if changed:
            save_games(games)
        for path in (GAME_FILE, GAME_ARCHIVE_FILE, POLL_STATE_FILE):
            if os.path.exists(path):
                obj = load_json(path, None)
                if obj is not None:
                    renamed = _rename_json(obj, old, new)
                    if renamed != obj:
                        save_json(path, renamed)
        data["players"] = {(new if k == old else k): v for k, v in data["players"].items()}
        save_players(data)
    except BaseException:
        _restore_bytes(snapshot)
        raise
    return old, new


def players_set_usernames(pid: int, raw: str) -> list:
    """Заменяет username игрока («-» — убрать все). Дубликат у другого игрока не допускается."""
    data = load_players()
    name, rec = player_by_pid(data, pid)
    if name is None:
        raise ValueError("Игрок не найден (возможно, уже удалён).")
    raw = raw.strip()
    if raw in ("-", "—", "нет"):
        usernames = []
    else:
        usernames = [clean_username(x) for x in re.split(r"[\s,;]+", raw) if clean_username(x)]
        if not usernames:
            raise ValueError("Не понял username. Пример: @ivanov")
        for u in usernames:
            if not re.fullmatch(r"[A-Za-z0-9_\-]{3,}", u):
                raise ValueError(f"«{u}» не похож на username (латиница, цифры, _; от 3 символов).")
            for other, orec in data["players"].items():
                if other != name and u.lower() in (x.lower() for x in orec["usernames"]):
                    raise ValueError(f"@{u} уже привязан к игроку «{other}».")
    rec["usernames"] = usernames
    save_players(data)
    return usernames


def players_set_aliases(pid: int, raw: str) -> list:
    """Заменяет отображаемые имена игрока в Telegram (по ним узнаётся проголосовавший без username); «-» — убрать."""
    data = load_players()
    name, rec = player_by_pid(data, pid)
    if name is None:
        raise ValueError("Игрок не найден (возможно, уже удалён).")
    raw = raw.strip()
    clear = raw in ("-", "—", "нет")
    aliases = [] if clear else [a.strip().lower() for a in re.split(r"[,;\n]", raw) if a.strip()]
    if not clear and not aliases:
        raise ValueError("Не понял отображаемое имя. Пример: Миша В.")
    rec["aliases"] = aliases
    save_players(data)
    return aliases


# ============================================================
# Живое оформление (ротация фраз без повторов подряд)
# ============================================================

def pick_phrase(kind: str, phrases: list, rng=random) -> str:
    meta = load_json(META_FILE, {})
    last = meta.get(f"last_{kind}")
    choices = [i for i in range(len(phrases)) if i != last] or list(range(len(phrases)))
    idx = rng.choice(choices)
    meta[f"last_{kind}"] = idx
    try:
        save_json(META_FILE, meta)
    except Exception as e:
        print(f"Не удалось сохранить ротацию фраз: {e}")
    return phrases[idx]


# ============================================================
# Опрос
# ============================================================

def poll_done_today(day: datetime.date) -> bool:
    """Сегодняшний опрос уже создан — автоматикой или вручную."""
    if os.path.exists(LAST_POLL_FILE):
        try:
            with open(LAST_POLL_FILE) as f:
                if datetime.date.fromisoformat(f.read().strip()) == day:
                    return True
        except Exception:
            pass
    return load_json(POLL_STATE_FILE, {}).get("date") == day.isoformat()


async def create_game_poll(chat_id: int, *, manual: bool = False) -> str:
    """Публикует опрос. В субботу и ручной, и автоматический опрос считаются
    «опросом этой субботы» (общий маркер), поэтому автоматика не пришлёт
    второй. Ручной опрос в другой день маркер субботы не ставит."""
    now = datetime.datetime.now(YEKB_TZ)
    today = now.date()
    days_until_monday = (7 - today.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7
    monday = today + datetime.timedelta(days=days_until_monday)
    phrase = pick_phrase("poll", POLL_PHRASES)
    question = f"{phrase} Футбол Лестех понедельник {monday.strftime('%d.%m.%Y')} {GAME_TIME}"
    poll_message = await bot.send_poll(
        chat_id=chat_id,
        question=question,
        options=["+", "-"],
        is_anonymous=False,
    )
    save_json(POLL_STATE_FILE, {
        "poll_id": poll_message.poll.id,
        "message_id": poll_message.message_id,
        "date": today.isoformat(),
        "voters": {},
        "manual": manual,
    })
    if today.weekday() == 5:
        with open(LAST_POLL_FILE, "w") as f:
            f.write(today.isoformat())
    print(f"Опрос отправлен ({'вручную' if manual else 'авто'}): {question}")
    return question


async def maybe_send_poll(now: datetime.datetime | None = None) -> bool:
    """Автоопрос: суббота, начиная с 12:00 (по Екатеринбургу). Если бот был
    выключен и вернулся позже в ту же субботу — опрос всё равно создаётся,
    но не больше одного за день."""
    now = now or datetime.datetime.now(YEKB_TZ)
    if now.weekday() != 5 or now.hour < 12:
        return False
    if poll_done_today(now.date()):
        return False
    async with POLL_LOCK:
        if poll_done_today(now.date()):
            return False
        try:
            await create_game_poll(CHAT_ID)
            return True
        except Exception as e:
            print(f"Ошибка отправки опроса: {e}")
            return False


async def poll_scheduler():
    """Раз в минуту проверяет, не пора ли отправить опрос.

    17.09.2026: процесс формально не падал (событий рестарта не было),
    но кнопки меню не отвечали почти сутки — а в логах за это время не
    было ни строчки. Теперь раз в ~30 минут пишем простое "сердцебиение"
    — не чинит зависание само по себе, но хотя бы видно в getRunLogs,
    что цикл ещё тикает."""
    tick = 0
    print(f"[DEBUG] планировщик опроса запущен {datetime.datetime.now(YEKB_TZ):%d.%m.%Y %H:%M}")
    while True:
        await maybe_send_poll()
        tick += 1
        if tick % 30 == 0:
            print(f"[DEBUG] планировщик жив, тик {tick}, {datetime.datetime.now(YEKB_TZ):%d.%m.%Y %H:%M}")
        await asyncio.sleep(60)


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


# ============================================================
# Фактический состав игры (game.json)
# ============================================================

def now_iso() -> str:
    return datetime.datetime.now(YEKB_TZ).isoformat(timespec="seconds")


def load_game() -> dict | None:
    g = load_json(GAME_FILE, None)
    return g if isinstance(g, dict) and isinstance(g.get("teams"), list) else None


def save_game(g: dict):
    save_json(GAME_FILE, g)


def archive_game(g: dict):
    archive = load_json(GAME_ARCHIVE_FILE, [])
    if not isinstance(archive, list):
        archive = []
    archive.append(g)
    save_json(GAME_ARCHIVE_FILE, archive[-20:])


def new_game(teams: list, ratings: dict | None = None, guests=()) -> dict:
    names_teams = [[n for n in team] for team in teams]
    return {
        "id": datetime.datetime.now(YEKB_TZ).strftime("%Y%m%d%H%M%S"),
        "created": now_iso(),
        "rev": 1,
        "initial": [list(t) for t in names_teams],
        "teams": names_teams,
        "ratings": dict(ratings or {}),
        "guests": list(guests),
        "history": [],
        "published_rev": None,
        "payment_rev": None,
        "result": None,
        "result_recorded": False,
        "pending": None,
    }


def game_players(g: dict) -> list:
    return [n for team in g["teams"] for n in team]


def squad_flat(g: dict) -> list:
    return [(ti, n) for ti, team in enumerate(g["teams"]) for n in team]


def _log(g: dict, **kw):
    g["history"].append({"t": now_iso(), **kw})
    g["rev"] += 1


def _find_in_squad(g: dict, name: str):
    for ti, team in enumerate(g["teams"]):
        for pos, n in enumerate(team):
            if norm_key(n) == norm_key(name):
                return ti, pos
    return None


def squad_remove(g: dict, name: str):
    loc = _find_in_squad(g, name)
    if not loc:
        raise ValueError(f"«{name}» нет в составе.")
    ti, pos = loc
    removed = g["teams"][ti].pop(pos)
    _log(g, type="remove", name=removed, team=ti + 1)


def _register_guest(g: dict, name: str, rating, guest: bool):
    if guest and name not in g["guests"]:
        g["guests"].append(name)
    if rating:
        g["ratings"][name] = float(rating)


def squad_add(g: dict, name: str, team_idx: int, rating=None, guest=False):
    if _find_in_squad(g, name):
        raise ValueError(f"«{name}» уже в составе.")
    if not 0 <= team_idx < len(g["teams"]):
        raise ValueError("Нет такой команды.")
    g["teams"][team_idx].append(name)
    _register_guest(g, name, rating, guest)
    _log(g, type="add", name=name, team=team_idx + 1, guest=guest, mode="place")


def squad_replace_in_place(g: dict, out_name: str, new_name: str, rating=None, guest=False):
    """Новый игрок занимает место выбывшего в той же команде."""
    if _find_in_squad(g, new_name):
        raise ValueError(f"«{new_name}» уже в составе.")
    loc = _find_in_squad(g, out_name)
    if not loc:
        raise ValueError(f"«{out_name}» нет в составе.")
    ti, pos = loc
    outgoing = g["teams"][ti][pos]
    g["teams"][ti][pos] = new_name
    _register_guest(g, new_name, rating, guest)
    _log(g, type="replace", out=outgoing, **{"in": new_name}, team=ti + 1, guest=guest, mode="place")


def squad_rebuild(g: dict, stats_players: dict, reason: str = "rebuild"):
    """Новый фактический список целиком проходит через алгоритм деления."""
    names = game_players(g)
    if len(names) < 2:
        raise ValueError("Для деления нужно минимум два игрока.")
    koefs, _, _ = koef_for_names(names, g["ratings"], stats_players)
    teams, _ = split_teams([(n, koefs[n]) for n in names], team_count_for(len(names)))
    g["teams"] = [[n for n, _ in team] for team in teams]
    _log(g, type="rebuild", reason=reason)


def squad_replace_rebuild(g: dict, out_name: str, new_name: str, stats_players: dict, rating=None, guest=False):
    if _find_in_squad(g, new_name):
        raise ValueError(f"«{new_name}» уже в составе.")
    loc = _find_in_squad(g, out_name)
    if not loc:
        raise ValueError(f"«{out_name}» нет в составе.")
    ti, pos = loc
    outgoing = g["teams"][ti].pop(pos)
    smallest = min(range(len(g["teams"])), key=lambda i: len(g["teams"][i]))
    g["teams"][smallest].append(new_name)
    _register_guest(g, new_name, rating, guest)
    squad_rebuild(g, stats_players, reason="replace")
    g["history"][-1].update({"type": "replace", "out": outgoing, "in": new_name, "mode": "rebuild", "guest": guest})


def teams_with_koef(g: dict, stats_players: dict):
    names = game_players(g)
    koefs, unknown, fallback = koef_for_names(names, g["ratings"], stats_players)
    return [[(n, koefs[n]) for n in team] for team in g["teams"]], unknown, fallback


def lineup_text(g: dict, stats_players: dict) -> str:
    teams, unknown, fallback = teams_with_koef(g, stats_players)
    return format_lineups(teams, unknown, fallback)


def publish_text(g: dict, stats_players: dict) -> str:
    header = "🔄 Обновлённые составы (замены в составе)\n\n" if g.get("published_rev") is not None else ""
    return f"{header}{lineup_text(g, stats_players)}\n\n{pick_phrase('final', FINAL_PHRASES)}"


def squad_text(g: dict, stats_players: dict, header: str = "") -> str:
    teams, unknown, fallback = teams_with_koef(g, stats_players)
    n = sum(len(t) for t in teams)
    lines = []
    if header:
        lines += [header, ""]
    lines.append(f"⚙️ Фактический состав — {n} игроков (изменений: {len(g['history'])})")
    avgs = []
    for i, team in enumerate(teams):
        avg = sum(k for _, k in team) / len(team) if team else 0
        avgs.append(avg)
        lines.append(f"\n{TEAM_ICONS[i]} Команда {i + 1} ({TEAM_COLORS[i]}) — {len(team)} чел., ср. {avg:.2f}")
        lines += [f"• {name} — {k:.2f}" for name, k in team]
    if avgs:
        lines.append(f"\nБаланс: разброс средних {max(avgs) - min(avgs):.2f}")
    if unknown:
        lines.append(f"Без статистики (коэф. {fallback:.2f} — среднее по остальным): {', '.join(unknown)}")
    if g.get("published_rev") == g["rev"]:
        lines.append("📢 Этот состав опубликован в общем чате.")
    else:
        lines.append("📢 Актуальный состав ещё не опубликован в общем чате.")
    return "\n".join(lines)


def history_text(g: dict) -> str:
    if not g["history"]:
        return "Изменений состава пока не было."
    lines = ["📜 История изменений (первоначальный состав сохранён)"]
    for h in g["history"]:
        when = f"{h['t'][8:10]}.{h['t'][5:7]} {h['t'][11:16]}"
        if h["type"] == "remove":
            lines.append(f"{when} — ➖ убран {h['name']}")
        elif h["type"] == "add":
            lines.append(f"{when} — ➕ добавлен {h['name']}")
        elif h["type"] == "replace":
            how = "на его место" if h.get("mode") == "place" else "с пересборкой команд"
            lines.append(f"{when} — 🔄 {h['out']} → {h['in']} ({how})")
        else:
            lines.append(f"{when} — 🔁 команды пересобраны")
    return "\n".join(lines)


# ---- результат матча -------------------------------------------------------

def result_init(g: dict):
    """Черновик ввода результата по фактическому составу; значения игроков,
    оставшихся в составе, сохраняются."""
    roster = game_players(g)
    old = g.get("result") or {}
    g["result"] = {
        "roster": roster,
        "g": {n: v for n, v in old.get("g", {}).items() if n in roster},
        "a": {n: v for n, v in old.get("a", {}).items() if n in roster},
    }


def result_adjust(g: dict, idx: int, field: str, delta: int):
    res = g["result"]
    name = res["roster"][idx]
    res[field][name] = max(0, min(99, res[field].get(name, 0) + delta))


def result_entries(g: dict) -> list:
    res = g["result"]
    return [(n, res["g"].get(n, 0), res["a"].get(n, 0)) for n in res["roster"]]


# ============================================================
# Тексты: Help
# ============================================================

HELP_SECTIONS = {
    "poll": (
        "🗳 ОПРОС\n\n"
        "Что делает: раз в неделю публикует в общем чате опрос «+ / −» на ближайший понедельник. "
        "Текст начинается с короткой фразы, затем «Футбол Лестех понедельник <дата> 21.30-23.00».\n\n"
        "Автоматически: суббота, 12:00 (Екатеринбург). Если бот был выключен и вернулся позже "
        "в ту же субботу — создаст опрос после запуска. Одна суббота — один опрос.\n\n"
        "Вручную: кнопка «🗳 Опрос» в меню или /опрос (/poll). Ручной опрос в субботу считается "
        "субботним — автоматика второй не пришлёт. Если опрос за сегодня уже есть, "
        "повторный создаётся только командой /опрос да.\n\n"
        "Ограничения: опрос всегда на ближайший понедельник; автокатч-ап работает только в субботу."
    ),
    "players": (
        "👥 ИГРОКИ\n\n"
        "Что делает: список игроков и привязка telegram-аккаунтов к именам. Хранится в /data/players.json, "
        "править код и перезапускать бота не нужно.\n\n"
        "В меню: «👥 Игроки» — список, добавить, привязать username, удалить. "
        "Если при /разделить есть проголосовавший без привязки — под сообщением появятся кнопки привязки.\n\n"
        "Команды:\n"
        "/игроки — показать список\n"
        "/игрок добавить Фамилия И. @username — новый игрок (username можно не указывать)\n"
        "/игрок удалить Фамилия И.\n"
        "/игрок username Фамилия И. @новый — добавить ещё один username\n"
        "/привязать @username = Фамилия И. — привязать аккаунт\n\n"
        "Редактирование: «👥 Игроки» → «✏️ Редактировать игрока» → выбрать игрока → «✏️ Имя», «🔗 Username» "
        "(заменить; «-» убрать) или «🏷 Отображаемое имя». У игрока есть постоянный внутренний номер: смена имени "
        "НЕ создаёт нового игрока — статистика, игры, голы, передачи и составы прошлых игр остаются у него. Если новое имя "
        "уже занято, бот откажет (объединить — /переименовать).\n\n"
        "Пример: /игрок добавить Иванов П. @ivanov\n\n"
        "Ограничения: удаление игрока не трогает его статистику. Имена сравниваются без учёта "
        "регистра и «ё/е»."
    ),
    "split": (
        "⚖️ СОСТАВЫ\n\n"
        "/разделить (/split) — только в личном чате. Берёт проголосовавших «+», добавляет гостей "
        "(/добавить Алексей, Максим 2.3 — рейтинг по желанию) и делит на 2 команды (до 14 человек) "
        "или 3 (от 15). Игрок без статистики получает средний коэффициент остальных; у гостя с "
        "рейтингом используется рейтинг.\n\n"
        "Черновик приходит вам в личку. «📢 Опубликовать в общий чат» срабатывает ОДИН раз: после отправки "
        "кнопка меняется на «✅ Опубликовано». Если отправка не удалась — кнопка остаётся.\n\n"
        "⚙️ Изменить состав (/состав) — работает с фактическим составом, в том числе после публикации: "
        "➕ добавить (игрока или гостя), ➖ убрать, 🔄 заменить. Замена: «📌 Поставить на его место» "
        "(та же команда, остальные не меняются) или «🔄 Пересобрать команды» (заново по коэффициентам). "
        "Первоначальный состав сохраняется, история изменений доступна. После правок появится кнопка "
        "«📢 Опубликовать обновлённый состав».\n\n"
        "Ручной вариант для всех: /составы Иванов, Петров, ... — разбить список (в фактический состав "
        "не записывается).\n\n"
        "Ограничения: /разделить и /добавить работают только в личке; новый /разделить начинает новую игру "
        "(прежняя уходит в архив)."
    ),
    "match": (
        "⚽ МАТЧ\n\n"
        "Кнопка «⚽ Внести результат матча» (или /результат). Игроки берутся из ФАКТИЧЕСКОГО состава. "
        "Нажмите на игрока и выставите ⚽ голы и 🎯 передачи кнопками ➖/➕ (по умолчанию 0/0 — "
        "меняйте только тех, кто забил или отдал). Затем «➡️ Далее» — предварительный просмотр. "
        "Статистика меняется только после «✅ Записать матч». «✏️ Изменить» возвращает к вводу, "
        "«❌ Отмена» ничего не записывает.\n\n"
        "Резервный текстовый способ: /матч Иванов 2+1, Петров 0+3, Соломин — «голы+передачи», игрок без "
        "цифр записывается как 0+0. Эта команда доступна и участникам общего чата.\n\n"
        "Каждая записанная игра (весь футбольный вечер) получает постоянный номер: «Игра №5 — 12.10.2026». Номер "
        "дают только фактически записанному вечеру; отменённый вечер не нумеруется. История игр — «📚 История игр» (/история).\n"
        "Отмена: /отменить убирает последнюю записанную игру (одну): статистика откатывается, но НОМЕР игры остаётся за этим вечером и никогда не переиспользуется. "
        "Исправленные данные этой игры можно записать в ту же Игра №N в любое время, без ограничения по срокам: когда есть отменённая игра, "
        "при записи бот спрашивает — «✏️ Исправление» (та же Игра №N, прежние номер и дата) или «🆕 Новый вечер» (следующий номер); "
        "для /матч — слова «исправление N» или «новый» (/матч исправление 5 Иванов 2+1; /матч новый Иванов 2+1). "
        "Новый вечер получает следующий номер, номер отменённой игры никому не достаётся.\n\n"
        "Ограничения: результат одного состава записывается один раз — повтор блокируется; если состав "
        "изменился, ввод идёт уже по новому. Гость после записи попадает в статистику под своим именем; "
        "регистр и «ё/е» не создают дублей."
    ),
    "stats": (
        "📊 СТАТИСТИКА\n\n"
        "/статистика (/stats) — таблица: И — игры, Г — голы, П — передачи, О — очки (гол = 2, "
        "передача = 1), К — коэффициент = очки / игры. В заголовке — «сыграно игр: N»: сколько состоявшихся "
        "футбольных вечеров (число из журнала игр, не из строк таблицы).\n"
        "В личке под таблицей кнопка «📢 Опубликовать в общий чат»: бот публикует ту же актуальную статистику от своего "
        "имени. Одна версия статистики публикуется один раз (затем кнопка «✅ Опубликовано»); изменилась статистика — "
        "откройте её заново.\n\n"
        "/переименовать Старое = Новое — объединяет записи игрока (например, после смены написания).\n"
        "/обнулить да — стирает всю статистику (без слова «да» не сработает).\n\n"
        "Защита: запись атомарная; перед изменением делается копия в /data/backups (последние 40). "
        "Если stats.json повреждён или непонятен, бот НЕ считает его пустым: изменяющие операции "
        "останавливаются, файл сохраняется, вам приходит сообщение об ошибке.\n\n"
        "Ограничения: статистика общая на один чат; /обнулить стирает статистику, но не журнал игр."
    ),
    "history": (
        "📚 ИСТОРИЯ ИГР\n\n"
        "Кнопка «📚 История игр» в панели или /история (только у вас в личке). Список состоявшихся игр, новые сверху: "
        "«Игра №4 — 28.09.2026». При выборе игры — составы всех команд именно той игры и возле каждого игрока ⚽ его голы "
        "и 🎯 передачи в этой игре. Общей статистики и счёта отдельных матчей там нет.\n\n"
        "Игры №1–4 (07.09, 14.09, 21.09, 28.09.2026) заведены по датам; их составы и голы в проекте не сохранялись и не "
        "выдумываются. Начиная с №5 архив пополняется при записи результата («⚽ Внести результат матча» или /матч). "
        "Журнал хранится в /data/games.json.\n\n"
        "Ограничения: игра, записанная через /матч, показывается списком игроков без деления на команды."
    ),
    "pay": (
        "💰 ОПЛАТА\n\n"
        "Кнопка «💰 Рассчитать оплату». Число игроков берётся из ФАКТИЧЕСКОГО состава (с учётом замен). "
        "Формула прежняя: 4500 ₽ делится на число игроков, округляется вверх до 10 ₽, "
        "плюс 20 ₽ на мяч. Бот показывает предварительный просмотр сообщения; в общий чат оно уходит только "
        "после «✅ Отправить в общий чат» и один раз для этого состава.\n\n"
        "Резервный ручной вариант: /оплата N (/pay N), например /оплата 14. Без числа бот покажет "
        "кнопки 12–18.\n\n"
        "Ограничения: больше 30 игроков бот не принимает; если состав изменился, расчёт нужно открыть заново."
    ),
    "misc": (
        "🔧 СЛУЖЕБНЫЕ\n\n"
        "/start — приветствие и панель администратора\n"
        "/menu — панель администратора (кнопки всех функций)\n"
        "/help — эта инструкция\n"
        "/id — ID чата и ваш ID\n"
        "/отмена — сбросить ожидание ввода текста\n\n"
        "Меню команд (кнопка «Функции») показывается только в вашем личном чате с ботом; в общем чате и "
        "у остальных его нет. Сами команды работают по-прежнему.\n\n"
        "При каждом запуске бот присылает вам сообщение «🟢 Мяч запущен» с состоянием статистики, "
        "игроков и меню.\n\n"
        "Данные в /data: stats.json (статистика), players.json (игроки), poll_state.json (опрос и голоса), "
        "game.json / game_archive.json (фактический состав), games.json (журнал игр с номерами), guests.json, backups/.\n\n"
        "Правило: любая новая admin-функция добавляется в этот Help в том же изменении."
    ),
}
HELP_TITLES = {
    "poll": "🗳 Опрос", "players": "👥 Игроки", "split": "⚖️ Составы", "match": "⚽ Матч",
    "stats": "📊 Статистика", "history": "📚 История игр", "pay": "💰 Оплата", "misc": "🔧 Служебные",
}
HELP_ALL_TEXT = "\n".join(HELP_SECTIONS.values())

PLAYER_HELP = (
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


# ============================================================
# Права и общие хелперы интерфейса
# ============================================================

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


def is_owner_private(message) -> bool:
    return bool(message.from_user and message.from_user.id == OWNER_ID and message.chat.type == "private")


def owner_cb_ok(query) -> bool:
    return bool(
        query.from_user and query.from_user.id == OWNER_ID
        and query.message is not None and query.message.chat.type == "private"
    )


def kb(rows: list) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def btn(text: str, data: str) -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(text=text, callback_data=data)


def chunk_rows(buttons: list, per_row: int = 2) -> list:
    return [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]


async def ack(query, text: str | None = None, alert: bool = False):
    """Ответ на callback, безопасный при повторном вызове."""
    try:
        if text is None:
            await query.answer()
        else:
            await query.answer(text, show_alert=alert)
    except Exception:
        pass


async def edit_or_send(query, text: str, markup=None):
    try:
        await query.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as e:
        if "not modified" not in str(e).lower():
            await query.message.answer(text, reply_markup=markup)
    except Exception:
        await query.message.answer(text, reply_markup=markup)


async def report_data_error(e: DataCorrupted, message=None, query=None):
    """Понятная ошибка Дмитрию; при вызове из общего чата — короткий ответ там."""
    text = (
        "⚠️ Данные повреждены или не читаются — операция остановлена, файл НЕ изменён.\n"
        f"Файл: {os.path.basename(e.path)}\nПричина: {e.reason}\n\n"
        "Исходный файл сохранён как есть, автоматические копии — в /data/backups. "
        "Пока проблема не устранена, изменяющие операции со статистикой недоступны."
    )
    print(f"[DATA ERROR] {e}")
    in_owner_private = (
        (message is not None and is_owner_private(message)) or (query is not None and owner_cb_ok(query))
    )
    try:
        if in_owner_private:
            target = message if message is not None else query.message
            await target.answer(text)
        else:
            await bot.send_message(OWNER_ID, text)
            if message is not None:
                await message.answer("⚠️ Статистика временно недоступна, администратор уведомлён.")
    except Exception as err:
        print(f"Не удалось сообщить об ошибке данных: {err}")
    if query is not None:
        await ack(query, "Ошибка данных", True)


def main_panel() -> types.InlineKeyboardMarkup:
    return kb([
        [btn("🗳 Опрос", "m:poll"), btn("👥 Игроки", "m:players")],
        [btn("⚖️ Составы (разделить)", "m:split"), btn("⚙️ Изменить состав", "m:squad")],
        [btn("⚽ Внести результат матча", "m:result"), btn("💰 Рассчитать оплату", "m:pay")],
        [btn("📊 Статистика", "m:stats"), btn("📚 История игр", "m:history")],
        [btn("❓ Help", "m:help")],
    ])


PANEL_TEXT = "⚽ Мяч — панель администратора\nВыберите действие."


# ============================================================
# Базовые команды
# ============================================================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if not is_allowed(message, "start"):
        return
    if is_owner_private(message):
        AWAITING.pop(OWNER_ID, None)
        await message.answer(
            f"Привет, {message.from_user.first_name}!\nЯ бот «Мяч». Ниже — панель администратора, "
            "подробная инструкция — в «❓ Help».",
            reply_markup=main_panel(),
        )
        return
    await message.answer(
        f"Привет, {message.from_user.first_name}!\n"
        "Я бот «Мяч».\n"
        "Команда /help — список команд."
    )


@dp.message(Command("menu", "меню"))
async def cmd_menu(message: types.Message):
    if not is_owner_private(message):
        return
    AWAITING.pop(OWNER_ID, None)
    await message.answer(PANEL_TEXT, reply_markup=main_panel())


@dp.message(Command("отмена", "cancel"))
async def cmd_cancel(message: types.Message):
    if not is_owner_private(message):
        return
    AWAITING.pop(OWNER_ID, None)
    await message.answer("Ожидание ввода сброшено.", reply_markup=main_panel())


def help_home_markup() -> types.InlineKeyboardMarkup:
    keys = list(HELP_SECTIONS)
    return kb(chunk_rows([btn(HELP_TITLES[k], f"hp:{k}") for k in keys], 2) + [[btn("⬅️ Меню", "m:home")]])


HELP_HOME_TEXT = (
    "❓ Help — инструкция администратора\n\n"
    "Выберите раздел: для каждой функции — что делает, как пользоваться, текстовая команда, "
    "пример и ограничения."
)


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    if not is_allowed(message, "help"):
        return
    if is_owner_private(message):
        await message.answer(HELP_HOME_TEXT, reply_markup=help_home_markup())
        return
    await message.answer(PLAYER_HELP)


@dp.message(Command("id"))
async def cmd_id(message: types.Message):
    if not is_allowed(message, "id"):
        return
    await message.answer(
        f"ID чата: `{message.chat.id}`\n"
        f"Ваш ID: `{message.from_user.id}`",
        parse_mode="Markdown"
    )


@dp.callback_query(F.data.startswith("hp:"))
async def cb_help(query: types.CallbackQuery):
    if not owner_cb_ok(query):
        return await query.answer("Недоступно", show_alert=True)
    key = query.data.split(":", 1)[1]
    markup = kb([[btn("⬅️ К разделам", "m:help")]])
    await edit_or_send(query, HELP_SECTIONS.get(key, HELP_HOME_TEXT), markup)
    await ack(query)


# ============================================================
# Панель администратора
# ============================================================

@dp.callback_query(F.data.startswith("m:"))
async def cb_panel(query: types.CallbackQuery):
    if not owner_cb_ok(query):
        return await query.answer("Недоступно", show_alert=True)
    action = query.data.split(":", 1)[1]
    AWAITING.pop(OWNER_ID, None)
    if action == "home":
        await edit_or_send(query, PANEL_TEXT, main_panel())
    elif action == "help":
        await edit_or_send(query, HELP_HOME_TEXT, help_home_markup())
    elif action == "players":
        await show_players(query)
    elif action == "split":
        await do_split(query.message.answer)
    elif action == "squad":
        await show_squad(query)
    elif action == "result":
        await show_result_menu(query)
    elif action == "pay":
        await show_payment(query)
    elif action == "stats":
        await send_stats(query.message.answer, owner=True)
    elif action == "history":
        await show_history_list(query, 0)
    elif action == "poll":
        if poll_done_today(datetime.datetime.now(YEKB_TZ).date()):
            note = "\n\n⚠️ Опрос на сегодня уже создан. Создать ещё один?"
        else:
            note = ""
        await edit_or_send(
            query,
            "🗳 Создать опрос в общем чате на ближайший понедельник?" + note,
            kb([[btn("✅ Создать опрос", "m:pollgo"), btn("❌ Отмена", "m:home")]]),
        )
    elif action == "pollgo":
        try:
            async with POLL_LOCK:
                question = await create_game_poll(CHAT_ID, manual=True)
            await edit_or_send(query, f"✅ Опрос отправлен в общий чат:\n{question}", kb([[btn("⬅️ Меню", "m:home")]]))
        except Exception as e:
            await edit_or_send(query, f"❌ Не удалось отправить опрос: {e}", kb([[btn("⬅️ Меню", "m:home")]]))
    await ack(query)


@dp.callback_query(F.data == "noop")
async def cb_noop(query: types.CallbackQuery):
    await query.answer("Это действие уже выполнено")


# ============================================================
# Игроки
# ============================================================

def players_text(data: dict) -> str:
    lines = [f"👥 Игроки ({len(data['players'])})"]
    for i, (name, rec) in enumerate(sorted(data["players"].items(), key=lambda kv: norm_key(kv[0])), 1):
        tags = [f"@{u}" for u in rec["usernames"]] + [f"«{a}»" for a in rec["aliases"]]
        if not tags:
            tags = ["без привязки"]
        lines.append(f"{i}. {name} — {', '.join(tags)}")
    return "\n".join(lines)


def sorted_player_names(data: dict) -> list:
    return sorted(data["players"], key=norm_key)


def players_markup() -> types.InlineKeyboardMarkup:
    return kb([
        [btn("➕ Добавить игрока", "pl:add"), btn("🔗 Привязать username", "pl:bind")],
        [btn("✏️ Редактировать игрока", "pl:ed"), btn("🗑 Удалить игрока", "pl:del")],
        [btn("⬅️ Меню", "m:home")],
    ])


async def show_players(query):
    try:
        data = load_players()
    except DataCorrupted as e:
        return await report_data_error(e, query=query)
    await edit_or_send(query, players_text(data), players_markup())


def picker_markup(names: list, prefix: str, back: str) -> types.InlineKeyboardMarkup:
    buttons = [btn(n, f"{prefix}:{i}") for i, n in enumerate(names)]
    return kb(chunk_rows(buttons, 2) + [[btn("⬅️ Назад", back)]])


@dp.callback_query(F.data.startswith("pl:"))
async def cb_players(query: types.CallbackQuery):
    if not owner_cb_ok(query):
        return await query.answer("Недоступно", show_alert=True)
    parts = query.data.split(":")
    action = parts[1]
    try:
        data = load_players()
        names = sorted_player_names(data)
        if action == "add":
            AWAITING[OWNER_ID] = {"kind": "add_player"}
            await edit_or_send(query, "Отправьте одним сообщением: Фамилия И. @username\n"
                                      "(username можно не указывать). Отмена — /отмена.",
                               kb([[btn("⬅️ К игрокам", "m:players")]]))
        elif action == "bind":
            await edit_or_send(query, "Кому добавить username?", picker_markup(names, "pl:bp", "m:players"))
        elif action == "bp":
            name = names[int(parts[2])]
            AWAITING[OWNER_ID] = {"kind": "bind_username", "name": name}
            await edit_or_send(query, f"Отправьте @username для «{name}». Отмена — /отмена.",
                               kb([[btn("⬅️ К игрокам", "m:players")]]))
        elif action == "ed":
            buttons = [btn(n, f"pe:c:{data['players'][n]['pid']}") for n in names]
            await edit_or_send(query, "Кого редактировать?", kb(chunk_rows(buttons, 2) + [[btn("⬅️ Назад", "m:players")]]))
        elif action == "del":
            await edit_or_send(query, "Кого удалить из списка игроков? (статистика останется)",
                               picker_markup(names, "pl:dp", "m:players"))
        elif action == "dp":
            name = names[int(parts[2])]
            await edit_or_send(query, f"Удалить «{name}» из списка игроков? Статистика не изменится.",
                               kb([[btn("✅ Удалить", f"pl:dy:{parts[2]}"), btn("❌ Нет", "m:players")]]))
        elif action == "dy":
            name = players_remove(names[int(parts[2])])
            await edit_or_send(query, f"🗑 «{name}» удалён.\n\n" + players_text(load_players()), players_markup())
    except DataCorrupted as e:
        return await report_data_error(e, query=query)
    except (ValueError, IndexError) as e:
        await query.message.answer(f"⚠️ {e}")
    await ack(query)


@dp.message(Command("игроки", "players"))
async def cmd_players(message: types.Message):
    if not is_owner_private(message):
        return
    try:
        await message.answer(players_text(load_players()), reply_markup=players_markup())
    except DataCorrupted as e:
        await report_data_error(e, message=message)


def player_card_text(data: dict, stats: dict, pid: int):
    name, rec = player_by_pid(data, pid)
    if name is None:
        return None
    st = stats_players_of(stats)
    skey = find_key(name, st)
    lines = [f"✏️ {name}", "",
             "Username: " + (", ".join("@" + u for u in rec["usernames"]) or "не указан"),
             "Отображаемые имена: " + (", ".join(f"«{a}»" for a in rec["aliases"]) or "нет")]
    if skey:
        r_ = st[skey]
        lines.append(f"Статистика: игр {r_.get('games', 0)}, голов {r_.get('goals', 0)}, передач {r_.get('assists', 0)}")
    lines += ["", "Статистика, игры и история сохраняются при любых изменениях — игрок остаётся тем же."]
    return "\n".join(lines)


def player_card_markup(pid: int) -> types.InlineKeyboardMarkup:
    return kb([
        [btn("✏️ Имя", f"pe:n:{pid}"), btn("🔗 Username", f"pe:u:{pid}")],
        [btn("🏷 Отображаемое имя", f"pe:a:{pid}")],
        [btn("⬅️ К игрокам", "m:players")],
    ])


@dp.callback_query(F.data.startswith("pe:"))
async def cb_player_edit(query: types.CallbackQuery):
    if not owner_cb_ok(query):
        return await query.answer("Недоступно", show_alert=True)
    parts = query.data.split(":")
    action, pid = parts[1], int(parts[2])
    try:
        data = load_players()
        name, _ = player_by_pid(data, pid)
        if name is None:
            await query.answer("Игрок не найден", show_alert=True)
            return
        cancel = kb([[btn("⬅️ К карточке", f"pe:c:{pid}")]])
        if action == "c":
            AWAITING.pop(OWNER_ID, None)
            await edit_or_send(query, player_card_text(data, load_stats(), pid), player_card_markup(pid))
        elif action == "n":
            AWAITING[OWNER_ID] = {"kind": "edit_name", "pid": pid}
            await edit_or_send(query, f"Введите новое имя для «{name}».\nНапример: Иванов П.\n\n"
                                      "Статистика и история игр сохранятся. Отмена — /отмена.", cancel)
        elif action == "u":
            AWAITING[OWNER_ID] = {"kind": "edit_username", "pid": pid}
            await edit_or_send(query, f"Введите username для «{name}».\nНапример: @ivanov\n"
                                      "Несколько — через пробел; «-» — убрать все. Отмена — /отмена.", cancel)
        elif action == "a":
            AWAITING[OWNER_ID] = {"kind": "edit_alias", "pid": pid}
            await edit_or_send(query, f"Введите отображаемое имя «{name}» в Telegram (так он подписан в чате).\n"
                                      "Например: Миша В.\nНесколько — через запятую; «-» — убрать все. Отмена — /отмена.", cancel)
    except DataCorrupted as e:
        return await report_data_error(e, query=query)
    await ack(query)


# ---- история игр -----------------------------------------------------------

HISTORY_PAGE = 10


def game_view_text(game: dict, registry: dict) -> str:
    """Архив конкретной игры: составы и голы/передачи каждого именно в этой игре. Без счёта матчей и общей статистики."""
    lines = [f"⚽ {game_title(game)}", ""]
    teams = game.get("teams")
    if game.get("status") == "reverted":
        lines.append(f"⚠️ Данные этой игры отменены (/отменить). Номер и дата сохранены за этим вечером; "
                     f"исправленные данные можно записать как Игра №{game['number']} (выбор при записи результата).")
        return "\n".join(lines)
    if not teams:
        lines.append("Составы и индивидуальная статистика этой игры не сохранялись (игра состоялась до ведения истории).")
        return "\n".join(lines)
    by_pid = {rec.get("pid"): name for name, rec in registry["players"].items()}
    split = game.get("split", True)
    if not split:
        lines.append("Игроки (деление на команды при записи не указывалось):")
    for i, team in enumerate(teams):
        if split:
            if i:
                lines.append("")
            lines.append(f"{TEAM_ICONS[i % len(TEAM_ICONS)]} Команда {i + 1} ({TEAM_COLORS[i % len(TEAM_COLORS)]})")
        for pl in team:
            name = by_pid.get(pl.get("pid")) or pl["name"]
            lines.append(f"{name} — ⚽ {pl.get('goals', 0)} · 🎯 {pl.get('assists', 0)}")
    return "\n".join(lines)


def history_list_view(games: dict, page: int):
    items = sorted(games["games"], key=lambda g: g["number"], reverse=True)
    pages = max(1, (len(items) + HISTORY_PAGE - 1) // HISTORY_PAGE)
    page = max(0, min(page, pages - 1))
    chunk = items[page * HISTORY_PAGE:(page + 1) * HISTORY_PAGE]
    mark = lambda g: game_title(g) + (" ⚠️" if g.get("status") == "reverted" else "")
    lines = [f"📚 История игр — сыграно: {len(items)}", "", *[mark(g) for g in chunk]]
    rows = [[btn(mark(g), f"gh:g:{g['number']}")] for g in chunk]
    nav = []
    if page > 0:
        nav.append(btn("◀️", f"gh:l:{page - 1}"))
    if page < pages - 1:
        nav.append(btn("▶️", f"gh:l:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([btn("⬅️ Меню", "m:home")])
    return "\n".join(lines), kb(rows)


async def show_history_list(query, page: int):
    try:
        text, markup = history_list_view(load_games(), page)
    except DataCorrupted as e:
        return await report_data_error(e, query=query)
    await edit_or_send(query, text, markup)


@dp.callback_query(F.data.startswith("gh:"))
async def cb_history(query: types.CallbackQuery):
    if not owner_cb_ok(query):
        return await query.answer("Недоступно", show_alert=True)
    parts = query.data.split(":")
    try:
        if parts[1] == "l":
            await show_history_list(query, int(parts[2]))
        else:
            games = load_games()
            game = next((g for g in games["games"] if g["number"] == int(parts[2])), None)
            if game is None:
                return await query.answer("Игра не найдена", show_alert=True)
            await edit_or_send(query, game_view_text(game, load_players()), kb([[btn("⬅️ К истории", "gh:l:0"), btn("⬅️ Меню", "m:home")]]))
    except DataCorrupted as e:
        return await report_data_error(e, query=query)
    await ack(query)


@dp.message(Command("история", "history"))
async def cmd_history(message: types.Message):
    if not is_owner_private(message):
        return
    try:
        text, markup = history_list_view(load_games(), 0)
    except DataCorrupted as e:
        return await report_data_error(e, message=message)
    await message.answer(text, reply_markup=markup)


PLAYER_USAGE = (
    "Формат:\n"
    "/игрок добавить Фамилия И. @username\n"
    "/игрок удалить Фамилия И.\n"
    "/игрок username Фамилия И. @новый"
)


def split_name_and_username(text: str):
    """«Иванов П. @ivanov» → («Иванов П.», «ivanov»); username необязателен."""
    text = text.strip()
    m = re.match(r"^(.*?)\s*@([A-Za-z0-9_\-]{3,})$", text)
    if m and m.group(1).strip():
        return m.group(1).strip(), m.group(2)
    return text, None


@dp.message(Command("игрок", "player"))
async def cmd_player(message: types.Message, command: CommandObject):
    if not is_owner_private(message):
        return
    args = (command.args or "").strip()
    sub, _, rest = args.partition(" ")
    try:
        if sub in ("добавить", "add") and rest.strip():
            name, username = split_name_and_username(rest)
            name = players_add(name, username)
            await message.answer(f"✅ Игрок «{name}» добавлен" + (f" (@{username})" if username else "") + ".")
        elif sub in ("удалить", "remove") and rest.strip():
            await message.answer(f"🗑 «{players_remove(rest.strip())}» удалён (статистика не тронута).")
        elif sub in ("username", "юзернейм") and rest.strip():
            name, username = split_name_and_username(rest)
            if not username:
                return await message.answer(PLAYER_USAGE)
            await message.answer(f"✅ Для «{players_bind(name, username=username)}» добавлен @{username}.")
        else:
            await message.answer(PLAYER_USAGE)
    except DataCorrupted as e:
        await report_data_error(e, message=message)
    except ValueError as e:
        await message.answer(f"⚠️ {e}")


@dp.message(Command("привязать", "bind"))
async def cmd_bind(message: types.Message, command: CommandObject):
    if not is_owner_private(message):
        return
    args = command.args or ""
    if "=" not in args:
        return await message.answer("Формат: /привязать @username = Фамилия И.")
    left, right = [x.strip() for x in args.split("=", 1)]
    username = clean_username(left)
    if not username or not right:
        return await message.answer("Формат: /привязать @username = Фамилия И.")
    try:
        name = players_bind(right, username=username)
        state = load_json(POLL_STATE_FILE, {})
        changed = False
        for v in state.get("voters", {}).values():
            if v.get("username", "").lower() == username.lower() and not v.get("player"):
                v["player"] = name
                changed = True
        if changed:
            save_json(POLL_STATE_FILE, state)
        await message.answer(f"✅ @{username} привязан к «{name}».")
    except DataCorrupted as e:
        await report_data_error(e, message=message)
    except ValueError as e:
        await message.answer(f"⚠️ {e}")


@dp.message(F.text, ~F.text.startswith("/"), F.chat.type == "private")
async def owner_text_input(message: types.Message):
    """Ответ Дмитрия на вопрос бота (добавить игрока, username, гость, имя)."""
    if message.from_user.id != OWNER_ID or (message.text or "").startswith("/"):
        return
    st = AWAITING.pop(OWNER_ID, None)
    if not st:
        return
    text = message.text.strip()
    kind = st["kind"]
    try:
        if kind == "add_player":
            name, username = split_name_and_username(text)
            name = players_add(name, username)
            await message.answer(f"✅ Игрок «{name}» добавлен.\n\n" + players_text(load_players()), reply_markup=players_markup())
        elif kind == "bind_username":
            username = clean_username(text)
            if not username:
                raise ValueError("Пустой username.")
            name = players_bind(st["name"], username=username)
            await message.answer(f"✅ Для «{name}» добавлен @{username}.", reply_markup=players_markup())
        elif kind == "new_player_for_voter":
            voter = load_json(POLL_STATE_FILE, {}).get("voters", {}).get(st["voter"])
            if not voter:
                raise ValueError("Этот голос уже не найден в текущем опросе.")
            name = players_add(text)
            await bind_voter(st["voter"], name)
            await message.answer(f"✅ Игрок «{name}» создан и привязан.")
            await do_split(message.answer)
        elif kind in ("edit_name", "edit_username", "edit_alias"):
            pid = st["pid"]
            if kind == "edit_name":
                old, new = players_rename(pid, text)
                note = f"✅ «{old}» → «{new}». Статистика и история игр сохранены, дубля нет."
            elif kind == "edit_username":
                names = players_set_usernames(pid, text)
                note = "✅ Username: " + (", ".join("@" + u for u in names) or "убран")
            else:
                names = players_set_aliases(pid, text)
                note = "✅ Отображаемые имена: " + (", ".join(f"«{a}»" for a in names) or "убраны")
            await message.answer(f"{note}\n\n" + player_card_text(load_players(), load_stats(), pid),
                                 reply_markup=player_card_markup(pid))
        elif kind == "squad_guest":
            await handle_squad_guest_text(message, st, text)
    except DataCorrupted as e:
        await report_data_error(e, message=message)
    except ValueError as e:
        AWAITING[OWNER_ID] = st
        await message.answer(f"⚠️ {e}\nПопробуйте ещё раз или /отмена.")


# ============================================================
# Составы: /разделить, привязка проголосовавших, публикация
# ============================================================

async def bind_voter(voter_key: str, player_name: str):
    """Привязывает проголосовавшего к игроку: по ID и username, а если username нет —
    по отображаемому имени."""
    state = load_json(POLL_STATE_FILE, {})
    voter = state.get("voters", {}).get(voter_key)
    if not voter:
        raise ValueError("Этот голос уже не найден в текущем опросе.")
    username = voter.get("username") or None
    alias = None if username else (voter.get("display") or None)
    name = players_bind(player_name, username=username, user_id=int(voter_key), alias=alias)
    voter["player"] = name
    save_json(POLL_STATE_FILE, state)


async def do_split(reply):
    """Делит проголосовавших «+» и гостей на команды, создаёт новую игру
    с фактическим составом и присылает черновик с кнопкой публикации."""
    state = load_json(POLL_STATE_FILE, {})
    voters = state.get("voters", {})
    try:
        pdata = load_players()
        stats = load_stats()
    except DataCorrupted as e:
        return await reply(f"⚠️ Данные недоступны: {e.reason} ({os.path.basename(e.path)}). "
                           "Составы не сформированы, файл не изменён.")
    unresolved = []
    for key, v in voters.items():
        if not v.get("player"):
            found = match_player(pdata, int(key), v.get("username", ""), v.get("display", ""))
            if found:
                v["player"] = found
            else:
                unresolved.append((key, v))
    if unresolved:
        save_json(POLL_STATE_FILE, state)
        rows = [[btn(f"🔗 {v.get('display') or '@' + v.get('username', '?')}", f"vb:{key}")] for key, v in unresolved[:10]]
        rows.append([btn("⬅️ Меню", "m:home")])
        names = ", ".join(v.get("display") or ("@" + v.get("username", "")) for _, v in unresolved)
        return await reply(
            f"Сначала нужно привязать: {names}\nНажмите на имя — выберите игрока или создайте нового.",
            reply_markup=kb(rows),
        )
    guests = load_json(GUESTS_FILE, [])
    ratings = {g["name"]: g.get("rating") for g in guests}
    names, seen = [], set()
    for n in [v["player"] for v in voters.values()] + [g["name"] for g in guests]:
        if norm_key(n) not in seen:
            seen.add(norm_key(n))
            names.append(n)
    if len(names) < 2:
        return await reply("Для деления нужно минимум два игрока.")
    sp = stats_players_of(stats)
    koefs, _, _ = koef_for_names(names, ratings, sp)
    teams, _ = split_teams([(n, koefs[n]) for n in names], team_count_for(len(names)))
    registered = set(pdata["players"])
    g = new_game(
        [[n for n, _ in team] for team in teams],
        ratings={n: r for n, r in ratings.items() if r},
        guests=[n for n in names if n not in registered],
    )
    previous = load_game()
    if previous:
        archive_game(previous)
    save_game(g)
    await reply(
        lineup_text(g, sp),
        reply_markup=draft_markup(g),
    )


def draft_markup(g: dict, published: bool | None = None) -> types.InlineKeyboardMarkup:
    if published is None:
        published = g.get("published_rev") == g["rev"]
    first = btn("✅ Опубликовано", "noop") if published else btn(
        "📢 Опубликовать в общий чат" if g.get("published_rev") is None else "📢 Опубликовать обновлённый состав",
        f"pub:{g['id']}:{g['rev']}")
    return kb([[first], [btn("⚙️ Изменить состав", "sq:menu"), btn("💰 Оплата", "pm:menu")]])


@dp.message(Command("разделить", "split"))
async def cmd_split_poll(message: types.Message):
    if not is_owner_private(message):
        return
    await do_split(message.answer)


@dp.callback_query(F.data.startswith("vb:"))
async def cb_voter_bind(query: types.CallbackQuery):
    if not owner_cb_ok(query):
        return await query.answer("Недоступно", show_alert=True)
    parts = query.data.split(":")
    try:
        data = load_players()
        names = sorted_player_names(data)
        voter = load_json(POLL_STATE_FILE, {}).get("voters", {}).get(parts[1])
        if not voter:
            await query.answer("Голос не найден — повторите /разделить", show_alert=True)
            return
        label = voter.get("display") or "@" + voter.get("username", "")
        if len(parts) == 2:
            buttons = [btn(n, f"vb:{parts[1]}:{i}") for i, n in enumerate(names)]
            rows = chunk_rows(buttons, 2) + [[btn("➕ Новый игрок", f"vb:{parts[1]}:new")], [btn("⬅️ Назад", "m:split")]]
            await edit_or_send(query, f"Кто это: {label}?", kb(rows))
        elif parts[2] == "new":
            AWAITING[OWNER_ID] = {"kind": "new_player_for_voter", "voter": parts[1]}
            await edit_or_send(query, f"Как записать игрока «{label}»? Отправьте: Фамилия И. Отмена — /отмена.")
        else:
            name = names[int(parts[2])]
            await bind_voter(parts[1], name)
            await edit_or_send(query, f"✅ {label} → «{name}»")
            await do_split(query.message.answer)
    except DataCorrupted as e:
        return await report_data_error(e, query=query)
    except (ValueError, IndexError) as e:
        await query.message.answer(f"⚠️ {e}")
    await ack(query)


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


@dp.callback_query(F.data == "publish_lineups")
async def publish_lineups_legacy(query: types.CallbackQuery):
    """Кнопка из старых черновиков (до обновления): безопасно отказываем."""
    await query.answer("Эта кнопка устарела. Запустите /split заново.", show_alert=True)


@dp.callback_query(F.data.startswith("pub:"))
async def cb_publish(query: types.CallbackQuery):
    if not owner_cb_ok(query):
        return await query.answer("Недоступно", show_alert=True)
    _, gid, rev_raw = query.data.split(":")
    rev = int(rev_raw)
    async with PUBLISH_LOCK:
        g = load_game()
        if not g or g["id"] != gid:
            return await query.answer("Состав устарел. Запустите /split заново.", show_alert=True)
        if g.get("published_rev") == rev:
            try:
                await query.message.edit_reply_markup(reply_markup=draft_markup(g, published=True))
            except Exception:
                pass
            return await query.answer("Уже опубликовано")
        if rev != g["rev"]:
            return await query.answer("Состав изменился. Откройте «⚙️ Изменить состав».", show_alert=True)
        try:
            stats = load_stats()
        except DataCorrupted as e:
            return await report_data_error(e, query=query)
        text = publish_text(g, stats_players_of(stats))
        try:
            await bot.send_message(CHAT_ID, text)
        except Exception as e:
            print(f"Не удалось опубликовать составы: {e}")
            return await query.answer("❌ Не удалось отправить в общий чат. Можно повторить.", show_alert=True)
        g["published_rev"] = rev
        g["published_at"] = now_iso()
        save_game(g)
        save_json(GUESTS_FILE, [])
    try:
        await query.message.edit_reply_markup(reply_markup=draft_markup(g, published=True))
    except Exception as e:
        print(f"Не удалось обновить кнопку публикации: {e}")
    await query.answer("Опубликовано")


# ============================================================
# Изменить фактический состав
# ============================================================

def squad_markup(g: dict) -> types.InlineKeyboardMarkup:
    rows = [
        [btn("➕ Добавить", "sq:adm"), btn("➖ Убрать", "sq:rmm")],
        [btn("🔄 Заменить", "sq:rpm"), btn("🔁 Пересобрать команды", "sq:rbc")],
    ]
    published = g.get("published_rev") == g["rev"]
    rows.append([btn("✅ Опубликовано", "noop") if published else btn(
        "📢 Опубликовать обновлённый состав" if g.get("published_rev") is not None else "📢 Опубликовать в общий чат",
        f"pub:{g['id']}:{g['rev']}")])
    rows.append([btn("📜 История", "sq:hist"), btn("💰 Оплата", "pm:menu")])
    rows.append([btn("⚽ Результат", "m:result"), btn("⬅️ Меню", "m:home")])
    return kb(rows)


async def show_squad(query, header: str = ""):
    g = load_game()
    if not g:
        return await edit_or_send(query, "Состава пока нет. Сначала сформируйте его: ⚖️ /разделить.",
                                  kb([[btn("⚖️ Составы (разделить)", "m:split"), btn("⬅️ Меню", "m:home")]]))
    try:
        sp = stats_players_of(load_stats())
    except DataCorrupted as e:
        return await report_data_error(e, query=query)
    g["pending"] = None
    save_game(g)
    await edit_or_send(query, squad_text(g, sp, header), squad_markup(g))


@dp.message(Command("состав", "squad"))
async def cmd_squad(message: types.Message):
    if not is_owner_private(message):
        return
    g = load_game()
    if not g:
        return await message.answer("Состава пока нет. Сначала сформируйте его: /разделить.")
    try:
        sp = stats_players_of(load_stats())
    except DataCorrupted as e:
        return await report_data_error(e, message=message)
    await message.answer(squad_text(g, sp), reply_markup=squad_markup(g))


def candidates_for(g: dict, pdata: dict) -> list:
    return [n for n in sorted_player_names(pdata) if not _find_in_squad(g, n)]


def team_buttons(g: dict, sp: dict, prefix: str) -> list:
    teams, _, _ = teams_with_koef(g, sp)
    rows = []
    for i, team in enumerate(teams):
        avg = sum(k for _, k in team) / len(team) if team else 0
        rows.append([btn(f"{TEAM_ICONS[i]} В команду {i + 1} ({len(team)} чел., ср. {avg:.2f})", f"{prefix}:{g['rev']}:{i}")])
    return rows


async def after_choose_incoming(target_answer_or_edit, g: dict, sp: dict):
    """Следующий шаг после выбора нового игрока: куда поставить / каким способом."""
    pending = g["pending"]
    who = pending["in"] + (" (гость)" if pending.get("guest") else "")
    if pending["op"] == "add":
        rows = team_buttons(g, sp, "sq:at") + [[btn("🔄 Пересобрать команды", f"sq:ar:{g['rev']}")],
                                               [btn("⬅️ Отмена", "sq:menu")]]
        text = f"➕ Добавить {who}. В какую команду?"
    else:
        rows = [[btn("📌 Поставить на его место", f"sq:mp:{g['rev']}")],
                [btn("🔄 Пересобрать команды", f"sq:mr:{g['rev']}")],
                [btn("⬅️ Отмена", "sq:menu")]]
        text = (f"🔄 Заменить {pending['out']} → {who}.\n\n"
                "📌 На его место — новичок встаёт в ту же команду, остальные составы не меняются.\n"
                "🔄 Пересобрать — все команды формируются заново по коэффициентам.")
    await target_answer_or_edit(text, kb(rows))


def parse_guest_text(text: str):
    m = re.match(r"^(.+?)(?:\s+(\d+(?:[.,]\d+)?))?$", text.strip())
    if not m or not m.group(1).strip():
        raise ValueError("Не понял имя. Формат: Имя или Имя 2.3 (рейтинг необязателен).")
    rating = float(m.group(2).replace(",", ".")) if m.group(2) else None
    return re.sub(r"\s+", " ", m.group(1).strip()), rating


async def handle_squad_guest_text(message, st, text):
    g = load_game()
    if not g or g["id"] != st["gid"] or g["rev"] != st["rev"] or not g.get("pending"):
        raise ValueError("Состав изменился — начните действие заново (⚙️ /состав).")
    name, rating = parse_guest_text(text)
    pdata = load_players()
    sp = stats_players_of(load_stats())
    canon = resolve_name(name, sp, pdata["players"])
    if _find_in_squad(g, canon):
        raise ValueError(f"«{canon}» уже в составе.")
    guest = not find_key(canon, pdata["players"])
    g["pending"].update({"in": canon, "rating": rating, "guest": guest})
    save_game(g)
    await after_choose_incoming(lambda t, m: message.answer(t, reply_markup=m), g, sp)


@dp.callback_query(F.data.startswith("sq:"))
async def cb_squad(query: types.CallbackQuery):
    if not owner_cb_ok(query):
        return await query.answer("Недоступно", show_alert=True)
    parts = query.data.split(":")
    action = parts[1]
    g = load_game()
    if not g:
        return await query.answer("Состава нет. Запустите /split.", show_alert=True)
    if action == "menu":
        await show_squad(query)
        return await ack(query)
    try:
        pdata = load_players()
        sp = stats_players_of(load_stats())
    except DataCorrupted as e:
        return await report_data_error(e, query=query)
    # все шаги с выбором по индексу несут ревизию: после изменения состава старые кнопки не действуют
    indexed = {"rm", "ro", "ai", "at", "ar", "mp", "mr", "ag", "rb"}
    if action in indexed and int(parts[2]) != g["rev"]:
        await query.answer("Состав уже изменился — откройте заново.", show_alert=True)
        return await show_squad(query)

    async def edit(text, markup):
        await edit_or_send(query, text, markup)

    try:
        if action == "hist":
            await edit(history_text(g), kb([[btn("⬅️ К составу", "sq:menu")]]))
        elif action == "rmm":
            buttons = [btn(f"{TEAM_ICONS[ti]} {n}", f"sq:rm:{g['rev']}:{i}") for i, (ti, n) in enumerate(squad_flat(g))]
            await edit("Кого убрать из состава (не сможет прийти)?", kb(chunk_rows(buttons, 2) + [[btn("⬅️ Назад", "sq:menu")]]))
        elif action == "rm":
            _, name = squad_flat(g)[int(parts[3])]
            squad_remove(g, name)
            save_game(g)
            await show_squad(query, f"➖ {name} убран из состава.")
        elif action == "adm":
            g["pending"] = {"op": "add"}
            save_game(g)
            names = candidates_for(g, pdata)
            buttons = [btn(n, f"sq:ai:{g['rev']}:{i}") for i, n in enumerate(names)]
            rows = chunk_rows(buttons, 2) + [[btn("🧑‍🤝‍🧑 Гость (ввести имя)", f"sq:ag:{g['rev']}")], [btn("⬅️ Назад", "sq:menu")]]
            await edit("Кого добавить? Выберите игрока или добавьте гостя.", kb(rows))
        elif action == "rpm":
            buttons = [btn(f"{TEAM_ICONS[ti]} {n}", f"sq:ro:{g['rev']}:{i}") for i, (ti, n) in enumerate(squad_flat(g))]
            await edit("Кого заменяем (выбывает)?", kb(chunk_rows(buttons, 2) + [[btn("⬅️ Назад", "sq:menu")]]))
        elif action == "ro":
            _, out_name = squad_flat(g)[int(parts[3])]
            g["pending"] = {"op": "replace", "out": out_name}
            save_game(g)
            names = candidates_for(g, pdata)
            buttons = [btn(n, f"sq:ai:{g['rev']}:{i}") for i, n in enumerate(names)]
            rows = chunk_rows(buttons, 2) + [[btn("🧑‍🤝‍🧑 Гость (ввести имя)", f"sq:ag:{g['rev']}")], [btn("⬅️ Назад", "sq:menu")]]
            await edit(f"Вместо {out_name} — кто?", kb(rows))
        elif action == "ai":
            if not g.get("pending"):
                raise ValueError("Начните действие заново.")
            name = candidates_for(g, pdata)[int(parts[3])]
            g["pending"].update({"in": name, "rating": None, "guest": False})
            save_game(g)
            await after_choose_incoming(edit, g, sp)
        elif action == "ag":
            if not g.get("pending"):
                raise ValueError("Начните действие заново.")
            AWAITING[OWNER_ID] = {"kind": "squad_guest", "gid": g["id"], "rev": g["rev"]}
            await edit("Отправьте имя гостя, при желании с рейтингом: Алексей или Алексей 2.3\n"
                       "Без рейтинга и статистики будет использован средний коэффициент игроков. Отмена — /отмена.",
                       kb([[btn("⬅️ Назад", "sq:menu")]]))
        elif action == "at":
            p = g.get("pending") or {}
            squad_add(g, p["in"], int(parts[3]), p.get("rating"), p.get("guest", False))
            g["pending"] = None
            save_game(g)
            await show_squad(query, f"➕ {p['in']} добавлен.")
        elif action == "ar":
            p = g.get("pending") or {}
            t = int(min(range(len(g["teams"])), key=lambda i: len(g["teams"][i])))
            squad_add(g, p["in"], t, p.get("rating"), p.get("guest", False))
            squad_rebuild(g, sp, reason="add")
            g["pending"] = None
            save_game(g)
            await show_squad(query, f"➕ {p['in']} добавлен, команды пересобраны.")
        elif action == "mp":
            p = g.get("pending") or {}
            squad_replace_in_place(g, p["out"], p["in"], p.get("rating"), p.get("guest", False))
            g["pending"] = None
            save_game(g)
            await show_squad(query, f"🔄 {p['out']} → {p['in']} (на его место, остальные команды не менялись).")
        elif action == "mr":
            p = g.get("pending") or {}
            squad_replace_rebuild(g, p["out"], p["in"], sp, p.get("rating"), p.get("guest", False))
            g["pending"] = None
            save_game(g)
            await show_squad(query, f"🔄 {p['out']} → {p['in']}, команды пересобраны.")
        elif action == "rbc":
            await edit("Пересобрать команды заново по коэффициентам? Текущее деление заменится "
                       "(первоначальный состав и история сохранятся).",
                       kb([[btn("✅ Да, пересобрать", f"sq:rb:{g['rev']}"), btn("❌ Отмена", "sq:menu")]]))
        elif action == "rb":
            squad_rebuild(g, sp, reason="manual")
            save_game(g)
            await show_squad(query, "🔁 Команды пересобраны.")
    except (ValueError, KeyError, IndexError) as e:
        await query.message.answer(f"⚠️ {e}")
    await ack(query)


# ============================================================
# Ввод результата матча кнопками
# ============================================================

def result_list_markup(g: dict) -> types.InlineKeyboardMarkup:
    res = g["result"]
    buttons = []
    for i, n in enumerate(res["roster"]):
        gl, a = res["g"].get(n, 0), res["a"].get(n, 0)
        mark = f" {gl}+{a}" if gl or a else ""
        buttons.append(btn(f"{n}{mark}", f"rs:p:{i}"))
    rows = chunk_rows(buttons, 2)
    rows.append([btn("➡️ Далее (проверить)", "rs:prev")])
    rows.append([btn("❌ Отмена", "rs:cancel")])
    return kb(rows)


def result_list_text(g: dict) -> str:
    return ("⚽ Внести результат матча\n"
            f"Фактический состав: {len(g['result']['roster'])} игроков.\n"
            "Нажмите на игрока, чтобы указать ⚽ голы и 🎯 передачи. У остальных будет 0+0.")


def result_preview_text(g: dict) -> str:
    entries = result_entries(g)
    scorers = [(n, gl, a) for n, gl, a in entries if gl or a]
    others = [n for n, gl, a in entries if not gl and not a]
    lines = ["🔎 Проверьте результат перед записью", ""]
    lines += [f"• {n} — ⚽ {gl} 🎯 {a}" for n, gl, a in scorers] or ["Голов и передач нет ни у кого."]
    if others:
        lines += ["", "Остальные 0+0: " + ", ".join(others)]
    lines += ["", f"Всего игроков: {len(entries)}. Статистика изменится только после «Записать матч»."]
    return "\n".join(lines)


async def show_result_menu(query):
    g = load_game()
    if not g:
        return await edit_or_send(query, "Состава пока нет. Сначала сформируйте его: /разделить.",
                                  kb([[btn("⚖️ Составы (разделить)", "m:split"), btn("⬅️ Меню", "m:home")]]))
    if g.get("result_recorded"):
        return await edit_or_send(
            query, "Результат для этого состава уже записан. Исправить можно командой /отменить "
                   "(убирает последнюю игру) и новым вводом.", kb([[btn("⬅️ Меню", "m:home")]]))
    if not g.get("result") or set(g["result"]["roster"]) != set(game_players(g)):
        result_init(g)
        save_game(g)
    await edit_or_send(query, result_list_text(g), result_list_markup(g))


@dp.message(Command("результат", "result"))
async def cmd_result(message: types.Message):
    if not is_owner_private(message):
        return
    g = load_game()
    if not g:
        return await message.answer("Состава пока нет. Сначала сформируйте его: /разделить.")
    if g.get("result_recorded"):
        return await message.answer("Результат для этого состава уже записан. Исправить: /отменить и ввод заново.")
    if not g.get("result") or set(g["result"]["roster"]) != set(game_players(g)):
        result_init(g)
        save_game(g)
    await message.answer(result_list_text(g), reply_markup=result_list_markup(g))


def player_screen(g: dict, idx: int):
    res = g["result"]
    n = res["roster"][idx]
    gl, a = res["g"].get(n, 0), res["a"].get(n, 0)
    text = f"{n}\n\n⚽ Голы: {gl}\n🎯 Передачи: {a}"
    markup = kb([
        [btn("➖ гол", f"rs:g-:{idx}"), btn(f"⚽ {gl}", "noop"), btn("➕ гол", f"rs:g+:{idx}")],
        [btn("➖ пас", f"rs:a-:{idx}"), btn(f"🎯 {a}", "noop"), btn("➕ пас", f"rs:a+:{idx}")],
        [btn("⬅️ К списку", "rs:menu")],
    ])
    return text, markup


@dp.callback_query(F.data.startswith("rs:"))
async def cb_result(query: types.CallbackQuery):
    if not owner_cb_ok(query):
        return await query.answer("Недоступно", show_alert=True)
    parts = query.data.split(":")
    action = parts[1]
    g = load_game()
    if not g or not g.get("result"):
        await query.answer("Ввод результата устарел. Откройте заново.", show_alert=True)
        return
    if g.get("result_recorded") and action not in ("ok", "okn", "okf"):
        await query.answer("Результат уже записан.", show_alert=True)
        return
    try:
        if action == "menu":
            await edit_or_send(query, result_list_text(g), result_list_markup(g))
        elif action == "p":
            text, markup = player_screen(g, int(parts[2]))
            await edit_or_send(query, text, markup)
        elif action in ("g+", "g-", "a+", "a-"):
            result_adjust(g, int(parts[2]), "g" if action[0] == "g" else "a", 1 if action[1] == "+" else -1)
            save_game(g)
            text, markup = player_screen(g, int(parts[2]))
            await edit_or_send(query, text, markup)
        elif action == "prev":
            await edit_or_send(query, result_preview_text(g), kb([
                [btn("✅ Записать матч", f"rs:ok:{g['id']}")],
                [btn("✏️ Изменить", "rs:menu"), btn("❌ Отмена", "rs:cancel")],
            ]))
        elif action == "cancel":
            g["result"] = None
            save_game(g)
            await edit_or_send(query, "Ввод результата отменён, статистика не менялась.", kb([[btn("⬅️ Меню", "m:home")]]))
        elif action in ("ok", "okn", "okf"):
            async with RECORD_LOCK:
                g = load_game()
                if not g or g["id"] != parts[2]:
                    return await query.answer("Состав устарел. Откройте заново.", show_alert=True)
                if g.get("result_recorded"):
                    return await query.answer("Результат уже записан.", show_alert=True)
                if not g.get("result") or set(g["result"]["roster"]) != set(game_players(g)):
                    return await query.answer("Состав изменился — откройте ввод заново.", show_alert=True)
                entries = result_entries(g)
                mode = "new" if action == "okn" else (int(parts[3]) if action == "okf" else None)
                try:
                    written, game = record_match_full(entries, teams=g["teams"], mode=mode)
                except NeedGameChoice as need:          # есть отменённая игра: выбор делает Дмитрий, бот не угадывает
                    rows = [[btn(f"✏️ Исправление: {game_title(x)}", f"rs:okf:{g['id']}:{x['number']}")] for x in need.reverted]
                    rows.append([btn(f"🆕 Новый вечер (Игра №{next_game_number(load_games())})", f"rs:okn:{g['id']}")])
                    rows.append([btn("✏️ Изменить", "rs:menu"), btn("❌ Отмена", "rs:cancel")])
                    await ack(query)
                    return await edit_or_send(
                        query, "Есть игра, данные которой были отменены: " + ", ".join(game_title(x) for x in need.reverted)
                        + ".\n\nКуда записать этот результат?\n• «Исправление» — те же номер и дата, статистика добавится один раз.\n"
                        "• «Новый вечер» — следующий номер; отменённая игра сохранит свой номер.", kb(rows))
                g["result_recorded"] = True
                g["game_number"] = game["number"]
                g["recorded_at"] = now_iso()
                g["recorded"] = [list(w) for w in written]
                save_game(g)
            scorers = [f"{n} {gl}+{a}" for n, gl, a in written if gl or a]
            await edit_or_send(
                query,
                f"✅ Матч записан ({len(written)} игроков) — {game_title(game)}.\n" + ("Результативные: " + ", ".join(scorers) if scorers else "Голов и передач нет."),
                kb([[btn("💰 Рассчитать оплату", "pm:menu")], [btn("⬅️ Меню", "m:home")]]),
            )
    except DataCorrupted as e:
        return await report_data_error(e, query=query)
    except (ValueError, IndexError, KeyError) as e:
        await query.message.answer(f"⚠️ {e}")
    await ack(query)


# ============================================================
# Оплата
# ============================================================

def format_payment(n: int) -> str:
    per_player = calc_payment_per_player(GAME_TOTAL_RUB, n)
    return (
        f"Переводим по {per_player} рублей по номеру телефона "
        f"{PAYMENT_PHONE}. Только {PAYMENT_BANK}."
    )


async def show_payment(query):
    g = load_game()
    if not g:
        return await edit_or_send(query, "Фактического состава нет, число игроков неизвестно. "
                                         "Сформируйте состав (/разделить) или используйте /оплата N.",
                                  kb([[btn("⬅️ Меню", "m:home")]]))
    n = len(game_players(g))
    if n <= 0 or n > 30:
        return await edit_or_send(query, f"В составе {n} игроков — проверьте состав или используйте /оплата N.",
                                  kb([[btn("⚙️ Изменить состав", "sq:menu"), btn("⬅️ Меню", "m:home")]]))
    sent = g.get("payment_rev") == g["rev"]
    text = (f"💰 Оплата по фактическому составу\n\nИгроков: {n}\n"
            f"С человека: {calc_payment_per_player(GAME_TOTAL_RUB, n)} ₽\n\n"
            f"Сообщение для общего чата:\n{format_payment(n)}")
    first = btn("✅ Отправлено", "noop") if sent else btn("✅ Отправить в общий чат", f"pm:send:{g['id']}:{g['rev']}")
    await edit_or_send(query, text, kb([[first], [btn("⚙️ Изменить состав", "sq:menu"), btn("⬅️ Меню", "m:home")]]))


@dp.callback_query(F.data.startswith("pm:"))
async def cb_pay_auto(query: types.CallbackQuery):
    if not owner_cb_ok(query):
        return await query.answer("Недоступно", show_alert=True)
    parts = query.data.split(":")
    if parts[1] == "menu":
        await show_payment(query)
        return await ack(query)
    if parts[1] == "send":
        async with PUBLISH_LOCK:
            g = load_game()
            if not g or g["id"] != parts[2]:
                return await query.answer("Состав устарел. Откройте заново.", show_alert=True)
            if g.get("payment_rev") == g["rev"]:
                return await query.answer("Уже отправлено")
            if int(parts[3]) != g["rev"]:
                await query.answer("Состав изменился — расчёт обновлён.", show_alert=True)
                return await show_payment(query)
            n = len(game_players(g))
            if n <= 0 or n > 30:
                return await query.answer("Проверьте число игроков.", show_alert=True)
            try:
                await bot.send_message(CHAT_ID, format_payment(n))
            except Exception as e:
                print(f"Не удалось отправить оплату: {e}")
                return await query.answer("❌ Не удалось отправить в общий чат. Можно повторить.", show_alert=True)
            g["payment_rev"] = g["rev"]
            g["payment_n"] = n
            save_game(g)
        await show_payment(query)
        await query.answer("Отправлено")


# Число игроков нажатием кнопки — команда из меню Telegram (/pay) всегда
# улетает в чат сразу, БЕЗ аргумента: дописать к ней число с кнопки
# невозможно, это ограничение самого Telegram, не бага бота. Поэтому
# показываем готовые варианты.
PAYMENT_QUICK_COUNTS = [12, 13, 14, 15, 16, 17, 18]


@dp.message(Command("оплата", "pay"))
async def cmd_payment(message: types.Message, command: CommandObject):
    if not is_allowed(message, "оплата"):
        return
    args = (command.args or "").strip()
    if not args.isdigit() or int(args) <= 0:
        rows = [[
            types.InlineKeyboardButton(text=str(n), callback_data=f"pay:{n}")
            for n in PAYMENT_QUICK_COUNTS
        ]]
        if is_owner_private(message):
            rows.insert(0, [btn("💰 По фактическому составу", "pm:menu")])
        await message.answer(
            "Сколько сегодня играло? Нажмите число ниже "
            "или напишите вручную: /оплата N",
            reply_markup=kb(rows),
        )
        return
    n = int(args)
    if n > 30:
        await message.answer(f"Многовато — {n} человек? Проверьте число.")
        return
    await message.answer(format_payment(n))


@dp.callback_query(lambda q: q.data and q.data.startswith("pay:"))
async def cmd_payment_callback(query: types.CallbackQuery):
    allowed = query.from_user.id == OWNER_ID or (
        query.message and query.message.chat.id == CHAT_ID
    )
    if not allowed:
        return await query.answer("Недоступно", show_alert=True)
    n = int(query.data.split(":", 1)[1])
    await query.message.answer(format_payment(n))
    await ack(query)


# ============================================================
# Статистика и прочие текстовые команды
# ============================================================

def stats_text(chat_stats: dict, games_played: int) -> str:
    rows = [(koef_of(r), points_of(r), name, r) for name, r in chat_stats.items()]
    rows.sort(reverse=True)
    lines = [
        f"📊 Статистика (сыграно игр: {games_played})",
        "Гол = 2 очка, пас = 1 очко\n"
        "И — игры, Г — голы, П — пасы, О — очки, К — коэффициент\n"
    ]
    for i, (koef, pts, name, r) in enumerate(rows, 1):
        lines.append(
            f"{i}. {name} — И:{r.get('games', 0)} Г:{r.get('goals', 0)} П:{r.get('assists', 0)} "
            f"О:{fmt_num(pts)} К:{koef:.2f}"
        )
    return "\n".join(lines)


def stats_snapshot():
    """Актуальный текст статистики и его отпечаток (для кнопки публикации). None — статистики пока нет."""
    chat_stats = stats_players_of(load_stats())
    if not chat_stats:
        return None, ""
    text = stats_text(chat_stats, played_games_count())
    return text, hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]


def stats_publish_markup(token: str, published: bool) -> types.InlineKeyboardMarkup:
    first = btn("✅ Опубликовано в общем чате", "noop") if published else btn("📢 Опубликовать в общий чат", f"sp:pub:{token}")
    return kb([[first], [btn("⬅️ Меню", "m:home")]])


async def send_stats(answer, owner: bool = False):
    """owner=True — личный чат Дмитрия: под таблицей кнопка «Опубликовать в общий чат»."""
    try:
        text, token = stats_snapshot()
    except DataCorrupted as e:
        print(f"[DATA ERROR] {e}")
        return await answer("⚠️ Статистика сейчас недоступна (файл не читается). Администратор уведомлён.")
    if text is None:
        await answer("Статистики пока нет.\nДобавьте игру: /матч Иванов 2+1")
        return
    if owner:
        published = load_json(META_FILE, {}).get("stats_published_hash") == token
        await answer(text, reply_markup=stats_publish_markup(token, published))
    else:
        await answer(text)


@dp.callback_query(F.data.startswith("sp:"))
async def cb_stats_publish(query: types.CallbackQuery):
    """Публикация актуальной статистики в общий чат от имени бота. Только владелец в личке. Одна версия статистики
    публикуется один раз (как составы): повторное нажатие не дублирует сообщение."""
    if not owner_cb_ok(query):
        return await query.answer("Недоступно", show_alert=True)
    token = query.data.split(":")[2]
    async with PUBLISH_LOCK:
        try:
            text, current = stats_snapshot()
        except DataCorrupted as e:
            return await report_data_error(e, query=query)
        if text is None or current != token:
            return await query.answer("Статистика изменилась. Откройте «📊 Статистика» заново.", show_alert=True)
        if load_json(META_FILE, {}).get("stats_published_hash") == current:
            try:
                await query.message.edit_reply_markup(reply_markup=stats_publish_markup(current, True))
            except Exception:
                pass
            return await query.answer("Уже опубликовано")
        try:
            await bot.send_message(CHAT_ID, text)
        except Exception as e:
            print(f"Не удалось опубликовать статистику: {e}")
            return await query.answer("❌ Не удалось отправить в общий чат. Можно повторить.", show_alert=True)
        meta = load_json(META_FILE, {})
        meta["stats_published_hash"] = current
        meta["stats_published_at"] = now_iso()
        save_json(META_FILE, meta)
    try:
        await query.message.edit_reply_markup(reply_markup=stats_publish_markup(current, True))
    except Exception as e:
        print(f"Не удалось обновить кнопку публикации статистики: {e}")
    await query.answer("Опубликовано")


@dp.message(Command("статистика", "stats"))
async def cmd_stats(message: types.Message):
    if not is_allowed(message, "статистика"):
        return
    try:
        load_stats()
    except DataCorrupted as e:
        await report_data_error(e, message=message)
        return
    await send_stats(message.answer, owner=is_owner_private(message))


@dp.message(Command("матч"))
async def cmd_match(message: types.Message, command: CommandObject):
    if not is_allowed(message, "матч"):
        return
    usage = (
        "Формат:\n"
        "/матч Иванов 2+1, Петров 0+3, Соломин\n\n"
        "Если есть игра с отменёнными данными, добавьте слово: «исправление N» (та же Игра №N) или «новый» (новый вечер).\n\n"
        "Гол = 2 очка, пас = 1 очко."
    )
    if not command.args:
        await message.answer(usage)
        return
    args, mode = command.args.strip(), None
    m = re.match(r"^(?:новый|новая|new)\b\s*", args, re.IGNORECASE)
    if m:
        args, mode = args[m.end():], "new"
    else:
        m = re.match(r"^(?:исправление|исправить|fix)\s+(\d+)\s*", args, re.IGNORECASE)
        if m:
            args, mode = args[m.end():], int(m.group(1))
    players, errors = parse_match_line(args)
    if not players:
        await message.answer(f"Не понял игроков.\n{usage}")
        return
    try:
        written, game = record_match_full(players, mode=mode)
    except NeedGameChoice as need:
        lines = ["Есть игра, данные которой были отменены. Укажите, куда записать результат:"]
        for x in need.reverted:
            lines.append(f"• исправление {game_title(x)}: /матч исправление {x['number']} Иванов 2+1, Петров 0+3")
        lines.append(f"• новый вечер (Игра №{next_game_number(load_games())}): /матч новый Иванов 2+1, Петров 0+3")
        await message.answer("\n".join(lines))
        return
    except ValueError as e:
        await message.answer(f"⚠️ {e}")
        return
    except DataCorrupted as e:
        await report_data_error(e, message=message)
        return
    lines = [f"Записал игру ({len(written)} чел.):"]
    for name, g, a in written:
        lines.append(f"• {name}: {g}+{a}")
    lines.append(f"\n{game_title(game)}")
    if errors:
        lines.append(f"\nНе разобрал: {', '.join(errors)}")
    await message.answer("\n".join(lines))


@dp.message(Command("отменить"))
async def cmd_undo(message: types.Message):
    if not is_allowed(message, "отменить"):
        return
    reverted = None
    try:
        stats = load_stats()
        games = load_games()
        chat = stats.setdefault(str(target_chat(message)), {"players": {}, "last": []})
        last = chat.get("last") or []
        if not last:
            await message.answer("Нечего отменять.")
            return
        for name, goals, assists in last:
            key = find_key(name, chat["players"])
            rec = chat["players"].get(key) if key else None
            if not rec:
                continue
            rec["games"] = max(0, rec.get("games", 0) - 1)
            rec["goals"] = max(0, rec.get("goals", 0) - goals)
            rec["assists"] = max(0, rec.get("assists", 0) - assists)
            if rec["games"] == 0 and rec["goals"] == 0 and rec["assists"] == 0:
                chat["players"].pop(key, None)
        chat["last"] = []
        snapshot = {STATS_FILE: _read_bytes(STATS_FILE)}
        save_stats(stats)
        try:
            reverted = revert_last_game(games)
            if reverted:
                save_games(games)
        except BaseException:
            _restore_bytes(snapshot)
            raise
    except DataCorrupted as e:
        await report_data_error(e, message=message)
        return
    tail = ""
    if reverted:
        tail = (f"\nИгра №{reverted['number']} ({fmt_game_date(reverted['date'])}) остаётся за этим вечером: её номер не освобождается "
                f"и не достанется другому вечеру. Следующий результат бот запишет по вашему выбору: исправление Игры №{reverted['number']} "
                f"(в любое время) или новый вечер.")
        g = load_game()                           # результат из кнопок можно ввести заново для той же игры
        if g and g.get("game_number") == reverted["number"] and g.get("result_recorded"):
            g["result_recorded"] = False
            save_game(g)
    await message.answer(f"Последняя игра отменена: {', '.join(p[0] for p in last)}{tail}")


@dp.message(Command("переименовать"))
async def cmd_rename(message: types.Message, command: CommandObject):
    if not is_allowed(message, "переименовать"):
        return
    if not command.args or "=" not in command.args:
        await message.answer("Формат: /переименовать Старое = Новое")
        return
    old_raw, new_raw = [x.strip() for x in command.args.split("=", 1)]
    try:
        stats = load_stats()
        chat = stats.setdefault(str(target_chat(message)), {"players": {}, "last": []})
        old = find_key(old_raw, chat["players"])
        if old is None:
            await message.answer(f"Игрока «{old_raw.title()}» нет.")
            return
        registered = set(load_players()["players"])
        new = resolve_name(new_raw, chat["players"], registered)
        rec = chat["players"].pop(old)
        target = chat["players"].setdefault(new, {"games": 0, "goals": 0, "assists": 0})
        for k in ("games", "goals", "assists"):
            target[k] = target.get(k, 0) + rec.get(k, 0)
        for entry in chat.get("last", []):
            if entry[0] == old:
                entry[0] = new
        save_stats(stats)
    except DataCorrupted as e:
        await report_data_error(e, message=message)
        return
    await message.answer(f"«{old}» → «{new}»")


@dp.message(Command("обнулить"))
async def cmd_reset(message: types.Message, command: CommandObject):
    if not is_allowed(message, "обнулить"):
        return
    if (command.args or "").strip().lower() != "да":
        await message.answer("Чтобы стереть статистику, напишите:\n/обнулить да")
        return
    try:
        stats = load_stats()
        stats.pop(str(target_chat(message)), None)
        save_stats(stats)
    except DataCorrupted as e:
        await report_data_error(e, message=message)
        return
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
    try:
        chat_players = stats_players_of(load_stats())
    except DataCorrupted as e:
        await report_data_error(e, message=message)
        return
    koefs, unknown, fallback = koef_for_names(names, {}, chat_players)
    players = [(n, koefs[n]) for n in names]
    team_count = team_count_for(len(players))
    teams, spread = split_teams(players, team_count)
    await message.answer(format_lineups(teams, unknown, fallback))


@dp.message(Command("опрос", "poll"))
async def cmd_poll(message: types.Message, command: CommandObject):
    if not message.from_user or message.from_user.id != OWNER_ID:
        return
    today = datetime.datetime.now(YEKB_TZ).date()
    if poll_done_today(today) and (command.args or "").strip().lower() != "да":
        await message.answer("Опрос на сегодня уже создан. Чтобы создать ещё один, напишите: /опрос да")
        return
    try:
        async with POLL_LOCK:
            question = await create_game_poll(CHAT_ID, manual=True)
        if message.chat.id != CHAT_ID:
            await message.answer(f"Опрос отправлен в общий чат: {question}")
    except Exception as e:
        await message.answer(f"Не удалось отправить опрос: {e}")


# ============================================================
# Меню команд, запуск
# ============================================================

OWNER_MENU_COMMANDS = [
    ("menu", "Панель администратора"),
    ("poll", "Создать опрос в общем чате"),
    ("split", "Разделить выбравших + на составы"),
    ("squad", "Изменить фактический состав"),
    ("result", "Внести результат матча"),
    ("pay", "Рассчитать оплату"),
    ("stats", "Показать статистику"),
    ("history", "История игр"),
    ("players", "Игроки и привязки"),
    ("help", "Help — инструкция администратора"),
]


async def setup_menu() -> dict:
    """Меню команд («Функции») — только в личном чате владельца. Сначала
    очищаются default/группы/личные/админские/чат-scope (и языковые
    варианты), иначе Telegram показывал бы старое меню через fallback."""
    report = {"before_button": "?", "after_button": "?", "counts": {}, "errors": []}
    try:
        report["before_button"] = (await bot.get_chat_menu_button()).type
    except Exception as e:
        report["errors"].append(f"get_chat_menu_button: {e}")
    scopes = {
        "default": types.BotCommandScopeDefault(),
        "groups": types.BotCommandScopeAllGroupChats(),
        "private": types.BotCommandScopeAllPrivateChats(),
        "admins": types.BotCommandScopeAllChatAdministrators(),
        "chat": types.BotCommandScopeChat(chat_id=CHAT_ID),
    }
    for label, scope in scopes.items():
        for lang in (None, "ru", "en"):
            try:
                await bot.delete_my_commands(scope=scope, language_code=lang)
            except Exception as e:
                report["errors"].append(f"delete {label}/{lang}: {e}")
    owner_scope = types.BotCommandScopeChat(chat_id=OWNER_ID)
    try:
        await bot.set_my_commands(
            [types.BotCommand(command=c, description=d) for c, d in OWNER_MENU_COMMANDS],
            scope=owner_scope,
        )
    except Exception as e:
        report["errors"].append(f"set owner: {e}")
    try:
        await bot.set_chat_menu_button(menu_button=types.MenuButtonDefault())
        await bot.set_chat_menu_button(chat_id=OWNER_ID, menu_button=types.MenuButtonCommands())
        report["after_button"] = (await bot.get_chat_menu_button()).type
    except Exception as e:
        report["errors"].append(f"menu button: {e}")
    checks = dict(scopes)
    checks["owner"] = owner_scope
    for label, scope in checks.items():
        try:
            report["counts"][label] = len(await bot.get_my_commands(scope=scope))
        except Exception as e:
            report["errors"].append(f"get {label}: {e}")
    print(f"[MENU] {report}")
    return report


def storage_report() -> list:
    lines = []
    try:
        stats = load_stats()
        backup_file(STATS_FILE, "stats")
        lines.append(f"📊 Статистика: OK, игроков {len(stats_players_of(stats))}")
    except DataCorrupted as e:
        lines.append(f"⚠️ Статистика НЕ читается ({e.reason}) — изменения статистики остановлены, файл не тронут")
    try:
        data = load_players()
        lines.append(f"👥 Игроки: OK, {len(data['players'])}")
    except DataCorrupted as e:
        lines.append(f"⚠️ Игроки НЕ читаются ({e.reason})")
    try:
        games = load_games()
        last = max(games["games"], key=lambda g: g["number"]) if games["games"] else None
        lines.append(f"🎮 Игры: сыграно {len(games['games'])}" + (f", последняя {game_title(last)}" if last else ""))
    except DataCorrupted as e:
        lines.append(f"⚠️ Журнал игр НЕ читается ({e.reason})")
    return lines


async def notify_started(menu: dict):
    counts = menu.get("counts", {})
    hidden = all(counts.get(k, 0) == 0 for k in ("default", "groups", "private", "admins", "chat"))
    lines = [
        f"🟢 Мяч запущен · версия {BOT_VERSION}",
        *storage_report(),
        f"📋 Меню: у вас {counts.get('owner', '?')} команд, в общем чате и у остальных "
        + ("скрыто ✅" if hidden else f"НЕ скрыто ⚠️ {counts}"),
        f"🔘 Кнопка меню до настройки: {menu.get('before_button')}, после: {menu.get('after_button')}",
    ]
    if menu.get("errors"):
        lines.append("Замечания: " + "; ".join(menu["errors"][:3]))
    lines.append("\nПанель: /menu · Инструкция: /help")
    try:
        await bot.send_message(OWNER_ID, "\n".join(lines))
    except Exception as e:
        print(f"Не удалось отправить стартовое сообщение: {e}")


async def main():
    print(f"Постоянный запуск бота {datetime.datetime.now(YEKB_TZ)} версия {BOT_VERSION}")
    menu = await setup_menu()
    await notify_started(menu)
    scheduler = asyncio.create_task(poll_scheduler())
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
