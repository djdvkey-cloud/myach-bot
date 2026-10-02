"""Автотесты «Мяча». Telegram подменён заглушками, данные — во временной папке;
реальные /data и Telegram не затрагиваются.

Запуск: python -m unittest discover -s tests -v
"""
import asyncio
import datetime as real_dt
import json
import math
import os
import random
import sys
import tempfile
import types as pytypes
import unittest
from unittest import mock

os.environ.setdefault("BOT_TOKEN", "123456789:" + "A" * 35)
os.environ.setdefault("OWNER_ID", "1000")
os.environ.setdefault("CHAT_ID", "-2000")
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="myach-import-")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot as B  # noqa: E402
from aiogram.filters import Command, CommandObject  # noqa: E402

OWNER, CHAT = B.OWNER_ID, B.CHAT_ID


# ---------------- заглушки Telegram ----------------

class FakeBot:
    def __init__(self):
        self.sent = []          # (chat_id, text)
        self.polls = []
        self.fail_send = False
        self.calls = []
        self.commands = {}      # scope-ключ -> список команд
        self.menu_button = "default"

    async def send_message(self, chat_id, text, **kw):
        if self.fail_send:
            raise RuntimeError("network down")
        self.sent.append((chat_id, text))

    async def send_poll(self, chat_id, question, options, is_anonymous=True, **kw):
        self.polls.append((chat_id, question, options))
        return pytypes.SimpleNamespace(poll=pytypes.SimpleNamespace(id=f"poll{len(self.polls)}"),
                                       message_id=len(self.polls))

    @staticmethod
    def _key(scope):
        return type(scope).__name__ + (str(getattr(scope, "chat_id", "")) if scope else "")

    async def delete_my_commands(self, scope=None, language_code=None, **kw):
        self.calls.append(("delete", self._key(scope), language_code))
        if language_code is None:
            self.commands.pop(self._key(scope), None)

    async def set_my_commands(self, commands, scope=None, **kw):
        self.calls.append(("set", self._key(scope)))
        self.commands[self._key(scope)] = list(commands)

    async def get_my_commands(self, scope=None, **kw):
        return self.commands.get(self._key(scope), [])

    async def get_chat_menu_button(self, chat_id=None, **kw):
        return pytypes.SimpleNamespace(type=self.menu_button)

    async def set_chat_menu_button(self, chat_id=None, menu_button=None, **kw):
        self.calls.append(("menu_button", chat_id, type(menu_button).__name__))
        if chat_id is None:
            self.menu_button = "default"


class FakeMsg:
    def __init__(self, chat_id, chat_type="private", user_id=None, text="", first_name="Тест"):
        self.chat = pytypes.SimpleNamespace(id=chat_id, type=chat_type)
        self.from_user = pytypes.SimpleNamespace(id=user_id if user_id is not None else chat_id,
                                                 first_name=first_name, last_name=None, username=None)
        self.text = text
        self.answers = []       # (text, markup)
        self.edits = []
        self.markup_edits = []
        self.message_id = 1

    async def answer(self, text, reply_markup=None, **kw):
        self.answers.append((text, reply_markup))
        return FakeMsg(self.chat.id, self.chat.type)

    async def edit_text(self, text, reply_markup=None, **kw):
        self.edits.append((text, reply_markup))

    async def edit_reply_markup(self, reply_markup=None, **kw):
        self.markup_edits.append(reply_markup)


class FakeQuery:
    def __init__(self, data, user_id=None, chat_type="private", chat_id=None):
        uid = OWNER if user_id is None else user_id
        self.data = data
        self.from_user = pytypes.SimpleNamespace(id=uid, username=None, first_name="Тест", last_name=None)
        self.message = FakeMsg(uid if chat_id is None else chat_id, chat_type, uid)
        self.answered = []

    async def answer(self, text=None, show_alert=False, **kw):
        self.answered.append((text, show_alert))


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def owner_msg(text="", chat_type="private"):
    return FakeMsg(OWNER, chat_type, OWNER, text)


def cmd(name, args=None):
    return CommandObject(command=name, args=args)


def all_button_data(markup):
    if markup is None:
        return []
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def all_button_text(markup):
    return [b.text for row in markup.inline_keyboard for b in row] if markup else []


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="myach-test-")
        self.patches = []
        paths = {
            "DATA_DIR": self.tmp, "STATS_FILE": "stats.json", "LAST_POLL_FILE": "last_poll.txt",
            "POLL_STATE_FILE": "poll_state.json", "GUESTS_FILE": "guests.json",
            "PLAYERS_FILE": "players.json", "GAME_FILE": "game.json",
            "GAME_ARCHIVE_FILE": "game_archive.json", "GAMES_FILE": "games.json", "META_FILE": "ui_meta.json",
            "BACKUP_DIR": "backups",
        }
        for name, val in paths.items():
            p = mock.patch.object(B, name, val if name == "DATA_DIR" else os.path.join(self.tmp, val))
            p.start()
            self.patches.append(p)
        self.fake = FakeBot()
        p = mock.patch.object(B, "bot", self.fake)
        p.start()
        self.patches.append(p)
        B.AWAITING.clear()
        B._POLL_SENT_DAY = None

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def write_stats(self, players, last=None):
        B.save_json(B.STATS_FILE, {str(CHAT): {"players": players, "last": last or []}})

    def read_stats(self):
        with open(B.STATS_FILE, encoding="utf-8") as f:
            return json.load(f)

    def set_now(self, dt):
        """Фиксирует «сейчас» (по Екатеринбургу) для бота."""
        fixed = dt.replace(tzinfo=B.YEKB_TZ)

        class FakeDT(real_dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)

        fake_module = pytypes.SimpleNamespace(datetime=FakeDT, date=real_dt.date,
                                              timedelta=real_dt.timedelta, timezone=real_dt.timezone)
        p = mock.patch.object(B, "datetime", fake_module)
        p.start()
        self.patches.append(p)
        return fixed


# ---------------- статистика и защита ----------------

class StatsSafety(Base):
    def test_roundtrip_and_backup(self):
        self.write_stats({"Иванов": {"games": 1, "goals": 2, "assists": 0}})
        stats = B.load_stats()
        stats[str(CHAT)]["players"]["Иванов"]["games"] = 2
        B.save_stats(stats)
        self.assertEqual(B.load_stats()[str(CHAT)]["players"]["Иванов"]["games"], 2)
        backups = B.list_backups("stats")
        self.assertEqual(len(backups), 1)  # копия состояния ДО изменения
        with open(os.path.join(B.BACKUP_DIR, backups[0]), encoding="utf-8") as f:
            self.assertEqual(json.load(f)[str(CHAT)]["players"]["Иванов"]["games"], 1)

    def test_legacy_format_still_loads(self):
        B.save_json(B.STATS_FILE, {str(CHAT): {"Иванов": {"games": 3, "goals": 1, "assists": 1}}})
        self.assertEqual(B.load_stats()[str(CHAT)]["players"]["Иванов"]["games"], 3)

    def test_atomic_write_failure_keeps_original(self):
        self.write_stats({"Иванов": {"games": 1, "goals": 0, "assists": 0}})
        before = read_bytes(B.STATS_FILE)
        with mock.patch("os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                B.save_json(B.STATS_FILE, {"x": 1})
        self.assertEqual(read_bytes(B.STATS_FILE), before)
        self.assertEqual([n for n in os.listdir(self.tmp) if n.endswith(".tmp")], [])

    async def _assert_corrupted_blocks(self, raw: bytes):
        with open(B.STATS_FILE, "wb") as f:
            f.write(raw)
        with self.assertRaises(B.DataCorrupted):
            B.load_stats()
        m = owner_msg()
        await B.cmd_match(m, cmd("матч", "Иванов 1+0"))
        await B.cmd_undo(m)
        await B.cmd_rename(m, cmd("переименовать", "А = Б"))
        await B.cmd_reset(m, cmd("обнулить", "да"))
        with self.assertRaises(B.DataCorrupted):
            B.record_match([("Иванов", 1, 0)])
        self.assertEqual(read_bytes(B.STATS_FILE), raw)   # исходник не тронут
        self.assertTrue(all("повреждены" in t for t, _ in m.answers), m.answers)
        self.assertEqual(len(m.answers), 4)

    async def test_corrupted_json_blocks_changes(self):
        await self._assert_corrupted_blocks('{"-2000": {"players": {"Ив'.encode("utf-8"))

    async def test_empty_file_blocks_changes(self):
        await self._assert_corrupted_blocks(b"")

    async def test_wrong_structure_blocks_changes(self):
        await self._assert_corrupted_blocks(json.dumps([1, 2, 3]).encode())

    async def test_bad_counter_blocks_changes(self):
        bad = {str(CHAT): {"players": {"Иванов": {"games": "много", "goals": 0, "assists": 0}}, "last": []}}
        await self._assert_corrupted_blocks(json.dumps(bad).encode())

    def test_missing_stats_with_backups_is_not_empty(self):
        self.write_stats({"Иванов": {"games": 1, "goals": 0, "assists": 0}})
        B.save_stats(B.load_stats())
        B.save_stats({str(CHAT): {"players": {}, "last": []}})
        os.unlink(B.STATS_FILE)
        with self.assertRaises(B.DataCorrupted):
            B.load_stats()

    def test_missing_stats_without_backups_is_first_run(self):
        self.assertEqual(B.load_stats(), {})

    async def test_group_user_error_goes_to_owner_dm(self):
        with open(B.STATS_FILE, "w") as f:
            f.write("{oops")
        m = FakeMsg(CHAT, "group", 555)
        await B.cmd_stats(m)
        self.assertEqual(self.fake.sent[0][0], OWNER)
        self.assertIn("повреждены", self.fake.sent[0][1])
        self.assertIn("недоступна", m.answers[0][0])

    async def test_existing_data_not_lost_on_normal_ops(self):
        self.write_stats({"Иванов": {"games": 5, "goals": 4, "assists": 3},
                          "Петров": {"games": 2, "goals": 0, "assists": 1}}, last=[["Иванов", 1, 0]])
        await B.cmd_match(owner_msg(), cmd("матч", "Иванов 2+1, Сидоров"))
        p = self.read_stats()[str(CHAT)]["players"]
        self.assertEqual(p["Иванов"], {"games": 6, "goals": 6, "assists": 4})
        self.assertEqual(p["Петров"], {"games": 2, "goals": 0, "assists": 1})
        self.assertEqual(p["Сидоров"], {"games": 1, "goals": 0, "assists": 0})
        await B.cmd_undo(owner_msg())
        p = self.read_stats()[str(CHAT)]["players"]
        self.assertEqual(p["Иванов"], {"games": 5, "goals": 4, "assists": 3})
        self.assertNotIn("Сидоров", p)


class NamesAndPlayers(Base):
    def test_seed_contains_all_existing_players(self):
        data = B.load_players()
        self.assertEqual(set(B.DEFAULT_PLAYER_USERNAMES) | set(B.DEFAULT_DISPLAY_ALIASES.values()),
                         set(data["players"]))
        self.assertEqual(data["players"]["Ларионов М."]["usernames"], ["lmur2000"])
        self.assertIn("михаил", data["players"]["Волков М."]["aliases"])
        self.assertTrue(os.path.exists(B.PLAYERS_FILE))

    def test_players_file_is_source_after_seed(self):
        B.load_players()
        B.players_add("Новичок Н.", "newbie")
        self.assertIn("Новичок Н.", B.load_players()["players"])   # не пересоздаётся из кода
        user = pytypes.SimpleNamespace(id=7, username="NEWBIE", first_name="X", last_name=None)
        self.assertEqual(B.player_name_for(user), "Новичок Н.")

    def test_bind_by_id_and_alias(self):
        B.load_players()
        B.players_bind("Бобров М.", user_id=42)
        user = pytypes.SimpleNamespace(id=42, username="other_handle", first_name="Q", last_name=None)
        self.assertEqual(B.player_name_for(user), "Бобров М.")

    def test_corrupted_players_not_overwritten(self):
        with open(B.PLAYERS_FILE, "w") as f:
            f.write("{broken")
        with self.assertRaises(B.DataCorrupted):
            B.players_add("Иванов")
        self.assertEqual(read_text(B.PLAYERS_FILE), "{broken")

    def test_no_duplicate_player_by_spelling(self):
        B.load_players()
        with self.assertRaises(ValueError):
            B.players_add("ковалев м.")      # уже есть «Ковалёв М.»

    def test_resolve_name_avoids_duplicates(self):
        stats = {"Ковалев М.": {}, "Петров": {}}
        self.assertEqual(B.resolve_name("ковалёв м.", stats, ["Ковалёв М."]), "Ковалев М.")
        self.assertEqual(B.resolve_name("iivajan", {}, ["IIvajan"]), "IIvajan")
        self.assertEqual(B.resolve_name("новый игрок", {}, []), "Новый Игрок")

    async def test_match_uses_existing_spelling_no_duplicate(self):
        self.write_stats({"Ковалев М.": {"games": 3, "goals": 1, "assists": 0}})
        await B.cmd_match(owner_msg(), cmd("матч", "Ковалёв М. 1+0"))
        players = self.read_stats()[str(CHAT)]["players"]
        self.assertEqual(list(players), ["Ковалев М."])
        self.assertEqual(players["Ковалев М."]["games"], 4)

    async def test_players_text_commands(self):
        m = owner_msg()
        await B.cmd_player(m, cmd("игрок", "добавить Иванов П. @ivanov"))
        self.assertIn("добавлен", m.answers[-1][0])
        await B.cmd_player(m, cmd("игрок", "username Иванов П. @ivanov2"))
        await B.cmd_bind(m, cmd("привязать", "@third = Иванов П."))
        rec = B.load_players()["players"]["Иванов П."]
        self.assertEqual(rec["usernames"], ["ivanov", "ivanov2", "third"])
        await B.cmd_player(m, cmd("игрок", "удалить Иванов П."))
        self.assertNotIn("Иванов П.", B.load_players()["players"])

    async def test_players_ui_add_via_text_input(self):
        q = FakeQuery("pl:add")
        await B.cb_players(q)
        self.assertEqual(B.AWAITING[OWNER]["kind"], "add_player")
        m = owner_msg("Сидоров К. @sidorov")
        await B.owner_text_input(m)
        self.assertIn("Сидоров К.", B.load_players()["players"])

    async def test_owner_ui_not_available_to_others(self):
        q = FakeQuery("pl:add", user_id=555)
        await B.cb_players(q)
        self.assertNotIn(OWNER, B.AWAITING)
        self.assertTrue(q.answered[0][1])
        m = FakeMsg(555, "private", 555)
        await B.cmd_players(m)
        await B.cmd_menu(m)
        self.assertEqual(m.answers, [])
        # и из общего чата владелец не получает панель
        g = FakeMsg(CHAT, "group", OWNER)
        await B.cmd_menu(g)
        self.assertEqual(g.answers, [])


# ---------------- оплата ----------------

class Payment(Base):
    def test_formula_unchanged(self):
        for n in range(1, 31):
            expected = math.ceil((4500 / n) / 10) * 10 + 20
            self.assertEqual(B.calc_payment_per_player(4500, n), expected)
        self.assertEqual([B.calc_payment_per_player(4500, n) for n in (12, 14, 15, 18)], [400, 350, 320, 270])
        self.assertEqual(B.format_payment(14),
                         "Переводим по 350 рублей по номеру телефона +79058056264. Только Озон Банк.")

    async def test_manual_pay_command_still_works(self):
        m = FakeMsg(CHAT, "group", 555)
        await B.cmd_payment(m, cmd("оплата", "14"))
        self.assertEqual(m.answers[0][0], B.format_payment(14))
        await B.cmd_payment(m, cmd("оплата", "31"))
        self.assertIn("Многовато", m.answers[1][0])
        await B.cmd_payment(m, cmd("оплата", None))
        self.assertEqual(all_button_data(m.answers[2][1]), [f"pay:{n}" for n in B.PAYMENT_QUICK_COUNTS])

    async def test_auto_payment_uses_actual_squad(self):
        g = B.new_game([["A", "B", "C", "D", "E", "F", "G"], ["H", "I", "J", "K", "L", "M", "N"]])
        B.save_game(g)
        q = FakeQuery("pm:menu")
        await B.cb_pay_auto(q)
        text, markup = q.message.edits[-1]
        self.assertIn("Игроков: 14", text)
        self.assertIn(str(B.calc_payment_per_player(4500, 14)), text)
        # выбыл один без замены → актуальное N = 13
        g = B.load_game()
        B.squad_remove(g, "A")
        B.save_game(g)
        q = FakeQuery("pm:menu")
        await B.cb_pay_auto(q)
        self.assertIn("Игроков: 13", q.message.edits[-1][0])
        data = [d for d in all_button_data(q.message.edits[-1][1]) if d.startswith("pm:send")][0]
        self.assertEqual(self.fake.sent, [])            # до подтверждения ничего не ушло
        await B.cb_pay_auto(FakeQuery(data))
        self.assertEqual(self.fake.sent, [(CHAT, B.format_payment(13))])
        await B.cb_pay_auto(FakeQuery(data))            # повторное нажатие
        self.assertEqual(len(self.fake.sent), 1)

    async def test_payment_replacement_keeps_count(self):
        g = B.new_game([["A", "B", "C"], ["D", "E", "F"]])
        B.squad_replace_in_place(g, "A", "Гость", guest=True)
        B.save_game(g)
        q = FakeQuery("pm:menu")
        await B.cb_pay_auto(q)
        self.assertIn("Игроков: 6", q.message.edits[-1][0])


# ---------------- опрос ----------------

class PollTests(Base):
    async def test_question_has_phrase_and_fixed_info(self):
        self.set_now(real_dt.datetime(2026, 10, 3, 12, 0))
        q = await B.create_game_poll(CHAT)
        self.assertTrue(any(q.startswith(p) for p in B.POLL_PHRASES))
        self.assertIn("Футбол Лестех понедельник 05.10.2026 21.30-23.00", q)
        self.assertEqual(self.fake.polls[0][2], ["+", "-"])
        self.assertLessEqual(len(q), 300)

    async def test_saturday_12_creates_once(self):
        self.set_now(real_dt.datetime(2026, 10, 3, 12, 0))
        self.assertTrue(await B.maybe_send_poll())
        self.assertFalse(await B.maybe_send_poll())
        self.assertEqual(len(self.fake.polls), 1)

    async def test_before_12_and_other_days_do_nothing(self):
        self.set_now(real_dt.datetime(2026, 10, 3, 11, 59))
        self.assertFalse(await B.maybe_send_poll())
        self.set_now(real_dt.datetime(2026, 10, 2, 12, 30))   # пятница
        self.assertFalse(await B.maybe_send_poll())
        self.set_now(real_dt.datetime(2026, 10, 4, 12, 30))   # воскресенье
        self.assertFalse(await B.maybe_send_poll())
        self.assertEqual(self.fake.polls, [])

    async def test_catch_up_after_downtime_then_no_duplicate_after_restarts(self):
        self.set_now(real_dt.datetime(2026, 10, 3, 12, 40))   # бот вернулся в 12:40
        self.assertTrue(await B.maybe_send_poll())
        for minute in (41, 50):                                # несколько «перезапусков»
            self.set_now(real_dt.datetime(2026, 10, 3, 13, minute % 60))
            self.assertFalse(await B.maybe_send_poll())
        self.set_now(real_dt.datetime(2026, 10, 3, 23, 59))
        self.assertFalse(await B.maybe_send_poll())
        self.assertEqual(len(self.fake.polls), 1)
        self.set_now(real_dt.datetime(2026, 10, 10, 12, 0))   # следующая суббота — снова один
        self.assertTrue(await B.maybe_send_poll())
        self.assertEqual(len(self.fake.polls), 2)

    async def test_marker_survives_restart_via_poll_state_only(self):
        self.set_now(real_dt.datetime(2026, 10, 3, 12, 5))
        await B.maybe_send_poll()
        os.unlink(B.LAST_POLL_FILE)        # потерян основной маркер — остаётся poll_state.json
        self.assertFalse(await B.maybe_send_poll())

    async def test_manual_poll_on_saturday_blocks_auto(self):
        self.set_now(real_dt.datetime(2026, 10, 3, 9, 0))
        m = owner_msg()
        await B.cmd_poll(m, cmd("опрос", None))
        self.assertEqual(len(self.fake.polls), 1)
        self.set_now(real_dt.datetime(2026, 10, 3, 12, 1))
        self.assertFalse(await B.maybe_send_poll())
        self.assertEqual(len(self.fake.polls), 1)

    async def test_manual_poll_on_other_day_does_not_block_saturday(self):
        self.set_now(real_dt.datetime(2026, 10, 2, 15, 0))     # пятница
        await B.cmd_poll(owner_msg(), cmd("опрос", None))
        self.assertEqual(len(self.fake.polls), 1)
        self.set_now(real_dt.datetime(2026, 10, 3, 12, 0))
        self.assertTrue(await B.maybe_send_poll())
        self.assertEqual(len(self.fake.polls), 2)

    async def test_second_manual_poll_needs_confirmation(self):
        self.set_now(real_dt.datetime(2026, 10, 3, 9, 0))
        m = owner_msg()
        await B.cmd_poll(m, cmd("опрос", None))
        await B.cmd_poll(m, cmd("опрос", None))
        self.assertEqual(len(self.fake.polls), 1)
        self.assertIn("уже создан", m.answers[-1][0])
        await B.cmd_poll(m, cmd("опрос", "да"))
        self.assertEqual(len(self.fake.polls), 2)

    async def test_poll_answers_still_recorded(self):
        self.set_now(real_dt.datetime(2026, 10, 3, 12, 0))
        await B.create_game_poll(CHAT)
        user = pytypes.SimpleNamespace(id=9, username="lmur2000", first_name="М", last_name=None)
        await B.poll_answer(pytypes.SimpleNamespace(poll_id="poll1", option_ids=[0], user=user))
        voters = B.load_json(B.POLL_STATE_FILE)["voters"]
        self.assertEqual(voters["9"]["player"], "Ларионов М.")
        await B.poll_answer(pytypes.SimpleNamespace(poll_id="poll1", option_ids=[1], user=user))
        self.assertEqual(B.load_json(B.POLL_STATE_FILE)["voters"], {})

    async def test_failed_send_does_not_mark_poll(self):
        self.set_now(real_dt.datetime(2026, 10, 3, 12, 0))
        with mock.patch.object(self.fake, "send_poll", side_effect=RuntimeError("x")):
            self.assertFalse(await B.maybe_send_poll())
        self.assertTrue(await B.maybe_send_poll())      # повторная попытка в следующую минуту


class Phrases(Base):
    def test_no_consecutive_repeat_and_full_lists(self):
        self.assertEqual(len(B.POLL_PHRASES), 11)
        self.assertEqual(len(B.FINAL_PHRASES), 10)
        rng = random.Random(1)
        for kind, lst in (("poll", B.POLL_PHRASES), ("final", B.FINAL_PHRASES)):
            prev, seen = None, set()
            for _ in range(300):
                ph = B.pick_phrase(kind, lst, rng)
                self.assertNotEqual(ph, prev)
                prev = ph
                seen.add(ph)
            self.assertEqual(seen, set(lst))

    def test_rotation_survives_restart(self):
        # последняя фраза хранится в файле, а не в памяти процесса
        first = B.pick_phrase("poll", B.POLL_PHRASES, random.Random(5))
        self.assertEqual(B.load_json(B.META_FILE)["last_poll"], B.POLL_PHRASES.index(first))
        first_choice = mock.Mock()
        first_choice.choice = lambda choices: choices[0]
        second = B.pick_phrase("poll", B.POLL_PHRASES, first_choice)
        self.assertNotEqual(first, second)


# ---------------- составы, фактический состав ----------------

def setup_stats(base, names_koef):
    """names_koef: {имя: (игры, голы, передачи)}"""
    base.write_stats({n: {"games": g, "goals": goals, "assists": a} for n, (g, goals, a) in names_koef.items()})


class Squad(Base):
    def stats14(self):
        data = {}
        for i in range(14):
            data[f"P{i}"] = (10, i % 5, i % 3)
        setup_stats(self, data)
        return [f"P{i}" for i in range(14)]

    def test_guest_without_stats_gets_average(self):
        koefs, unknown, fallback = B.koef_for_names(
            ["A", "B", "Гость"], {}, {"A": {"games": 2, "goals": 2, "assists": 0}, "B": {"games": 1, "goals": 0, "assists": 1}})
        self.assertAlmostEqual(fallback, (2.0 + 1.0) / 2)
        self.assertEqual(koefs["Гость"], fallback)
        self.assertEqual(unknown, ["Гость"])
        # гость с рейтингом — рейтинг; игрок со статистикой — свой коэффициент
        koefs2, unknown2, _ = B.koef_for_names(["A", "Гость"], {"Гость": 2.3}, {"A": {"games": 2, "goals": 2, "assists": 0}})
        self.assertEqual(koefs2["Гость"], 2.3)
        self.assertEqual(koefs2["A"], 2.0)
        self.assertEqual(unknown2, [])

    def make_game(self):
        names = self.stats14()
        sp = B.stats_players_of(B.load_stats())
        koefs, _, _ = B.koef_for_names(names, {}, sp)
        teams, _ = B.split_teams([(n, koefs[n]) for n in names], 2)
        g = B.new_game([[n for n, _ in t] for t in teams])
        return g, sp

    def test_replace_in_place_keeps_team_and_others(self):
        g, sp = self.make_game()
        before = [list(t) for t in g["teams"]]
        out = g["teams"][1][3]
        B.squad_replace_in_place(g, out, "Гость Новый", guest=True)
        self.assertEqual(g["teams"][0], before[0])
        self.assertEqual(g["teams"][1][3], "Гость Новый")
        self.assertEqual([n for i, n in enumerate(g["teams"][1]) if i != 3], [n for i, n in enumerate(before[1]) if i != 3])
        self.assertEqual(g["initial"], before)                       # первоначальный состав цел
        self.assertEqual(g["history"][-1]["out"], out)
        self.assertEqual(g["history"][-1]["in"], "Гость Новый")
        self.assertEqual(g["history"][-1]["mode"], "place")
        self.assertIn("Гость Новый", g["guests"])
        self.assertEqual(len(B.game_players(g)), 14)

    def test_replace_rebuild_reruns_algorithm(self):
        g, sp = self.make_game()
        out = g["teams"][0][0]
        B.squad_replace_rebuild(g, out, "Гость Новый", sp, guest=True)
        names = B.game_players(g)
        self.assertEqual(len(names), 14)
        self.assertIn("Гость Новый", names)
        self.assertNotIn(out, names)
        # результат равен работе алгоритма на новом списке
        koefs, _, _ = B.koef_for_names(names, g["ratings"], sp)
        _, spread = B.split_teams([(n, koefs[n]) for n in names], B.team_count_for(len(names)))
        teams_k, _, _ = B.teams_with_koef(g, sp)
        avgs = [sum(k for _, k in t) / len(t) for t in teams_k]
        self.assertAlmostEqual(max(avgs) - min(avgs), spread)
        self.assertEqual(g["history"][-1]["mode"], "rebuild")
        self.assertEqual(g["history"][-1]["type"], "replace")

    def test_remove_without_replacement(self):
        g, sp = self.make_game()
        victim = g["teams"][1][0]
        rev = g["rev"]
        B.squad_remove(g, victim)
        self.assertEqual(len(B.game_players(g)), 13)
        self.assertNotIn(victim, B.game_players(g))
        self.assertEqual(g["rev"], rev + 1)
        self.assertEqual(g["history"][-1]["type"], "remove")
        self.assertEqual(len(g["initial"][0]) + len(g["initial"][1]), 14)
        with self.assertRaises(ValueError):
            B.squad_remove(g, victim)

    def test_add_and_duplicates_rejected(self):
        g, sp = self.make_game()
        B.squad_add(g, "Новичок", 0, rating=1.5, guest=True)
        self.assertEqual(g["ratings"]["Новичок"], 1.5)
        with self.assertRaises(ValueError):
            B.squad_add(g, "новичок", 1)
        with self.assertRaises(ValueError):
            B.squad_replace_in_place(g, "P0", "Новичок")

    def test_guest_koef_used_for_balance_display(self):
        g, sp = self.make_game()
        out = g["teams"][0][0]
        B.squad_replace_in_place(g, out, "Гость", guest=True)
        koefs, unknown, fallback = B.koef_for_names(B.game_players(g), g["ratings"], sp)
        self.assertIn("Гость", unknown)
        self.assertAlmostEqual(koefs["Гость"], fallback)
        self.assertIn("Баланс", B.squad_text(g, sp))

    async def test_squad_ui_replace_both_modes(self):
        g, sp = self.make_game()
        B.save_game(g)
        out = g["teams"][0][2]
        rev = g["rev"]
        idx = [i for i, (_, n) in enumerate(B.squad_flat(g)) if n == out][0]
        await B.cb_squad(FakeQuery(f"sq:ro:{rev}:{idx}"))
        self.assertEqual(B.load_game()["pending"]["out"], out)
        q = FakeQuery(f"sq:ag:{rev}")
        await B.cb_squad(q)
        self.assertEqual(B.AWAITING[OWNER]["kind"], "squad_guest")
        m = owner_msg("Алексей 2.5")
        await B.owner_text_input(m)
        text, markup = m.answers[-1]
        datas = all_button_data(markup)
        self.assertIn(f"sq:mp:{rev}", datas)
        self.assertIn(f"sq:mr:{rev}", datas)
        await B.cb_squad(FakeQuery(f"sq:mp:{rev}"))
        g2 = B.load_game()
        self.assertEqual(g2["teams"][0][2], "Алексей")
        self.assertEqual(g2["ratings"]["Алексей"], 2.5)
        self.assertEqual(g2["rev"], rev + 1)
        # старая кнопка после изменения не действует
        q = FakeQuery(f"sq:mr:{rev}")
        await B.cb_squad(q)
        self.assertEqual(B.load_game()["rev"], rev + 1)
        self.assertTrue(q.answered[0][1])

    async def test_guest_typed_as_registered_player_is_not_duplicated(self):
        g, sp = self.make_game()
        B.save_game(g)
        B.load_players()
        rev = g["rev"]
        out = g["teams"][0][0]
        idx = [i for i, (_, n) in enumerate(B.squad_flat(g)) if n == out][0]
        await B.cb_squad(FakeQuery(f"sq:ro:{rev}:{idx}"))
        await B.cb_squad(FakeQuery(f"sq:ag:{rev}"))
        await B.owner_text_input(owner_msg("ковалев м."))      # зарегистрирован как «Ковалёв М.»
        self.assertEqual(B.load_game()["pending"]["in"], "Ковалёв М.")
        self.assertFalse(B.load_game()["pending"]["guest"])


class SplitFlow(Base):
    async def vote(self, uid, username, display, player=None):
        state = B.load_json(B.POLL_STATE_FILE, {"poll_id": "p", "voters": {}})
        state.setdefault("voters", {})[str(uid)] = {"username": username, "display": display, "player": player}
        B.save_json(B.POLL_STATE_FILE, state)

    async def test_unresolved_voter_bound_from_ui_then_split(self):
        names = list(B.DEFAULT_PLAYER_USERNAMES)[:13]
        for i, n in enumerate(names):
            await self.vote(100 + i, B.DEFAULT_PLAYER_USERNAMES[n], n, n)
        await self.vote(999, "newguy", "Новый Парень")
        m = owner_msg()
        await B.cmd_split_poll(m)
        text, markup = m.answers[-1]
        self.assertIn("Сначала нужно привязать", text)
        self.assertEqual(all_button_data(markup)[0], "vb:999")
        self.assertIsNone(B.load_game())
        # выбрать игрока в списке
        q = FakeQuery("vb:999")
        await B.cb_voter_bind(q)
        plist = sorted(B.load_players()["players"], key=B.norm_key)
        target = "Сикач И."
        datas = all_button_data(q.message.edits[-1][1])
        pick = f"vb:999:{plist.index(target)}"
        self.assertIn(pick, datas)
        # «Влад» уже проголосовал? нет — среди первых 13 его может не быть
        q2 = FakeQuery(pick)
        await B.cb_voter_bind(q2)
        self.assertEqual(B.load_players()["players"][target]["usernames"][-1], "newguy")
        self.assertEqual(B.load_game() is not None, True)
        self.assertEqual(len(B.game_players(B.load_game())), 14)
        # следующий голос этого человека распознаётся без кода
        user = pytypes.SimpleNamespace(id=999, username="newguy", first_name="X", last_name=None)
        self.assertEqual(B.player_name_for(user), target)

    async def test_split_creates_game_and_publish_once(self):
        names = list(B.DEFAULT_PLAYER_USERNAMES)[:14]
        for i, n in enumerate(names):
            await self.vote(100 + i, B.DEFAULT_PLAYER_USERNAMES[n], n, n)
        B.save_json(B.GUESTS_FILE, [])
        await B.cmd_split_poll(owner_msg())
        g = B.load_game()
        self.assertEqual(len(B.game_players(g)), 14)
        self.assertEqual(g["initial"], g["teams"])
        self.assertIsNone(g["published_rev"])
        pub = f"pub:{g['id']}:{g['rev']}"
        q = FakeQuery(pub)
        await B.cb_publish(q)
        self.assertEqual(len(self.fake.sent), 1)
        self.assertEqual(self.fake.sent[0][0], CHAT)
        sent = self.fake.sent[0][1]
        self.assertTrue(sent.startswith("⚖️ Составы — 14 игроков, 2 команды"))
        self.assertTrue(any(sent.endswith(p) for p in B.FINAL_PHRASES))
        self.assertEqual(all_button_text(q.message.markup_edits[-1])[0], "✅ Опубликовано")
        self.assertEqual(all_button_data(q.message.markup_edits[-1])[0], "noop")
        # повторное нажатие той же кнопки
        q2 = FakeQuery(pub)
        await B.cb_publish(q2)
        await B.cb_publish(FakeQuery(pub))
        self.assertEqual(len(self.fake.sent), 1)
        self.assertEqual(q2.answered[0][0], "Уже опубликовано")

    async def test_failed_publish_keeps_button_and_allows_retry(self):
        names = list(B.DEFAULT_PLAYER_USERNAMES)[:14]
        for i, n in enumerate(names):
            await self.vote(100 + i, B.DEFAULT_PLAYER_USERNAMES[n], n, n)
        await B.cmd_split_poll(owner_msg())
        g = B.load_game()
        pub = f"pub:{g['id']}:{g['rev']}"
        self.fake.fail_send = True
        q = FakeQuery(pub)
        await B.cb_publish(q)
        self.assertEqual(self.fake.sent, [])
        self.assertIsNone(B.load_game()["published_rev"])
        self.assertEqual(q.message.markup_edits, [])        # кнопка не заблокирована
        self.assertTrue(q.answered[0][1])
        self.fake.fail_send = False
        await B.cb_publish(FakeQuery(pub))
        self.assertEqual(len(self.fake.sent), 1)
        self.assertEqual(B.load_game()["published_rev"], g["rev"])

    async def test_concurrent_double_tap_sends_once(self):
        names = list(B.DEFAULT_PLAYER_USERNAMES)[:14]
        for i, n in enumerate(names):
            await self.vote(100 + i, B.DEFAULT_PLAYER_USERNAMES[n], n, n)
        await B.cmd_split_poll(owner_msg())
        g = B.load_game()
        pub = f"pub:{g['id']}:{g['rev']}"
        await asyncio.gather(B.cb_publish(FakeQuery(pub)), B.cb_publish(FakeQuery(pub)), B.cb_publish(FakeQuery(pub)))
        self.assertEqual(len(self.fake.sent), 1)

    async def test_republish_after_substitution_is_new_draft(self):
        names = list(B.DEFAULT_PLAYER_USERNAMES)[:14]
        for i, n in enumerate(names):
            await self.vote(100 + i, B.DEFAULT_PLAYER_USERNAMES[n], n, n)
        await B.cmd_split_poll(owner_msg())
        g = B.load_game()
        await B.cb_publish(FakeQuery(f"pub:{g['id']}:{g['rev']}"))
        g = B.load_game()
        B.squad_replace_in_place(g, g["teams"][0][0], "Гость", guest=True)
        B.save_game(g)
        old = FakeQuery(f"pub:{g['id']}:{g['rev'] - 1}")
        await B.cb_publish(old)                              # старая кнопка не публикует второй раз
        self.assertEqual(len(self.fake.sent), 1)
        await B.cb_publish(FakeQuery(f"pub:{g['id']}:{g['rev']}"))
        self.assertEqual(len(self.fake.sent), 2)
        self.assertIn("Обновлённые составы", self.fake.sent[1][1])
        self.assertIn("Гость", self.fake.sent[1][1])

    async def test_legacy_publish_button_refuses(self):
        q = FakeQuery("publish_lineups")
        await B.publish_lineups_legacy(q)
        self.assertEqual(self.fake.sent, [])
        self.assertTrue(q.answered[0][1])

    async def test_new_split_archives_previous_game(self):
        for i, n in enumerate(list(B.DEFAULT_PLAYER_USERNAMES)[:6]):
            await self.vote(100 + i, B.DEFAULT_PLAYER_USERNAMES[n], n, n)
        await B.cmd_split_poll(owner_msg())
        first = B.load_game()
        await B.cmd_split_poll(owner_msg())
        archive = B.load_json(B.GAME_ARCHIVE_FILE, [])
        self.assertEqual(len(archive), 1)
        self.assertEqual(archive[0]["id"], first["id"])       # прежняя игра не потеряна


class ResultFlow(Base):
    def prep(self, published=False):
        setup_stats(self, {"A": (4, 4, 2), "B": (4, 1, 1), "C": (2, 0, 0), "D": (3, 1, 0)})
        g = B.new_game([["A", "B"], ["C", "Гость"]], guests=["Гость"])
        B.save_game(g)
        return g

    async def test_buttons_preview_confirm_records(self):
        g = self.prep()
        q = FakeQuery("m:result")
        await B.cb_panel(q)
        self.assertIn("Фактический состав: 4", q.message.edits[-1][0])
        await B.cb_result(FakeQuery("rs:g+:0"))
        await B.cb_result(FakeQuery("rs:g+:0"))
        await B.cb_result(FakeQuery("rs:a+:0"))
        await B.cb_result(FakeQuery("rs:a+:3"))              # Гость — 1 передача
        await B.cb_result(FakeQuery("rs:g-:1"))              # ниже нуля не уходит
        self.assertEqual(B.load_game()["result"]["g"].get("B", 0), 0)
        q = FakeQuery("rs:prev")
        await B.cb_result(q)
        text, markup = q.message.edits[-1]
        self.assertIn("A — ⚽ 2 🎯 1", text)
        self.assertIn("Гость — ⚽ 0 🎯 1", text)
        self.assertIn("Остальные 0+0: B, C", text)
        texts = all_button_text(markup)
        self.assertEqual([t for t in texts if "Записать" in t or "Изменить" in t or "Отмена" in t],
                         ["✅ Записать матч", "✏️ Изменить", "❌ Отмена"])
        # до подтверждения статистика не менялась
        self.assertEqual(self.read_stats()[str(CHAT)]["players"]["A"]["games"], 4)
        ok = [d for d in all_button_data(markup) if d.startswith("rs:ok")][0]
        q = FakeQuery(ok)
        await B.cb_result(q)
        players = self.read_stats()[str(CHAT)]["players"]
        self.assertEqual(players["A"], {"games": 5, "goals": 6, "assists": 3})
        self.assertEqual(players["B"], {"games": 5, "goals": 1, "assists": 1})
        self.assertEqual(players["Гость"], {"games": 1, "goals": 0, "assists": 1})
        self.assertEqual(self.read_stats()[str(CHAT)]["last"][0], ["A", 2, 1])
        # повторное нажатие «Записать» ничего не удваивает
        q2 = FakeQuery(ok)
        await B.cb_result(q2)
        self.assertEqual(self.read_stats()[str(CHAT)]["players"]["A"]["games"], 5)
        self.assertTrue(B.load_game()["result_recorded"])
        self.assertTrue(q2.answered[0][1])

    async def test_cancel_writes_nothing(self):
        self.prep()
        await B.cb_panel(FakeQuery("m:result"))
        await B.cb_result(FakeQuery("rs:g+:0"))
        await B.cb_result(FakeQuery("rs:cancel"))
        self.assertEqual(self.read_stats()[str(CHAT)]["players"]["A"]["games"], 4)
        self.assertIsNone(B.load_game()["result"])

    async def test_changed_squad_blocks_old_confirm_and_uses_new_roster(self):
        g = self.prep()
        await B.cb_panel(FakeQuery("m:result"))
        await B.cb_result(FakeQuery("rs:g+:0"))
        g = B.load_game()
        q = FakeQuery("rs:prev")
        await B.cb_result(q)
        ok = [d for d in all_button_data(q.message.edits[-1][1]) if d.startswith("rs:ok")][0]
        g = B.load_game()
        B.squad_remove(g, "C")
        B.save_game(g)
        await B.cb_result(FakeQuery(ok))
        self.assertEqual(self.read_stats()[str(CHAT)]["players"]["A"]["games"], 4)
        await B.cb_panel(FakeQuery("m:result"))
        self.assertEqual(B.load_game()["result"]["roster"], ["A", "B", "Гость"])
        self.assertEqual(B.load_game()["result"]["g"].get("A"), 1)     # значения оставшихся сохранены

    async def test_corrupted_stats_stops_recording(self):
        self.prep()
        await B.cb_panel(FakeQuery("m:result"))
        q = FakeQuery("rs:prev")
        await B.cb_result(q)
        ok = [d for d in all_button_data(q.message.edits[-1][1]) if d.startswith("rs:ok")][0]
        with open(B.STATS_FILE, "w") as f:
            f.write("{broken")
        q = FakeQuery(ok)
        await B.cb_result(q)
        self.assertEqual(read_text(B.STATS_FILE), "{broken")
        self.assertFalse(B.load_game()["result_recorded"])
        self.assertIn("повреждены", q.message.answers[-1][0])

    async def test_text_match_command_remains(self):
        self.prep()
        m = owner_msg()
        await B.cmd_match(m, cmd("матч", "A 1+1, Гость"))
        self.assertIn("Записал игру (2 чел.)", m.answers[-1][0])


# ---------------- меню, help, права ----------------

class MenuAndHelp(Base):
    async def test_menu_only_for_owner_with_cleanup(self):
        self.fake.commands = {
            "BotCommandScopeDefault": ["old"], "BotCommandScopeAllGroupChats": ["old"],
            "BotCommandScopeAllPrivateChats": ["old"], f"BotCommandScopeChat{CHAT}": ["old"],
        }
        self.fake.menu_button = "web_app"
        report = await B.setup_menu()
        self.assertEqual(report["before_button"], "web_app")
        self.assertEqual(report["counts"]["default"], 0)
        self.assertEqual(report["counts"]["groups"], 0)
        self.assertEqual(report["counts"]["private"], 0)
        self.assertEqual(report["counts"]["chat"], 0)
        self.assertEqual(report["counts"]["owner"], len(B.OWNER_MENU_COMMANDS))
        sets = [c for c in self.fake.calls if c[0] == "set"]
        self.assertEqual(sets, [("set", f"BotCommandScopeChat{OWNER}")])
        deletes = [c[1] for c in self.fake.calls if c[0] == "delete"]
        for needed in ("BotCommandScopeDefault", "BotCommandScopeAllGroupChats", "BotCommandScopeAllPrivateChats"):
            self.assertIn(needed, deletes)
        # удаление идёт ДО установки
        first_set = next(i for i, c in enumerate(self.fake.calls) if c[0] == "set")
        self.assertTrue(all(c[0] != "delete" for c in self.fake.calls[first_set:]))
        self.assertEqual(report["errors"], [])

    async def test_startup_report_sent_only_to_owner(self):
        report = await B.setup_menu()
        await B.notify_started(report)
        self.assertEqual([c for c, _ in self.fake.sent], [OWNER])
        self.assertIn("скрыто ✅", self.fake.sent[0][1])

    def test_help_documents_every_registered_command(self):
        commands = set()
        for handler in B.dp.message.handlers:
            for flt in handler.filters:
                if isinstance(flt.callback, Command):
                    commands.add(tuple(str(c) for c in flt.callback.commands))
        self.assertGreater(len(commands), 15)
        for aliases in commands:
            self.assertTrue(any(f"/{a}" in B.HELP_ALL_TEXT for a in aliases),
                            f"Команда {aliases} не описана в Help — обновите Help вместе с функцией")
        for section in ("Опрос", "Игроки", "Составы", "Матч", "Статистика", "Оплата", "Служебные"):
            self.assertTrue(any(section.lower() in t.lower() for t in B.HELP_TITLES.values()), section)
        for text in B.HELP_SECTIONS.values():
            self.assertLess(len(text), 3800)

    async def test_help_owner_private_vs_group(self):
        m = owner_msg()
        await B.cmd_help(m)
        self.assertEqual(m.answers[-1][0], B.HELP_HOME_TEXT)
        self.assertEqual(len(all_button_data(m.answers[-1][1])), len(B.HELP_SECTIONS) + 1)
        q = FakeQuery("hp:match")
        await B.cb_help(q)
        self.assertIn("Внести результат матча", q.message.edits[-1][0])
        g = FakeMsg(CHAT, "group", 555)
        await B.cmd_help(g)
        self.assertEqual(g.answers[-1][0], B.PLAYER_HELP)      # игрокам — прежний короткий help
        other = FakeMsg(555, "private", 555)
        await B.cmd_help(other)
        self.assertEqual(other.answers, [])

    async def test_rights_unchanged_for_players(self):
        self.write_stats({"Иванов": {"games": 1, "goals": 1, "assists": 0}})
        g = FakeMsg(CHAT, "group", 555)
        await B.cmd_stats(g)
        self.assertIn("Статистика", g.answers[-1][0])
        await B.cmd_reset(g, cmd("обнулить", "да"))            # не владелец
        await B.cmd_rename(g, cmd("переименовать", "Иванов = Петров"))
        self.assertIn("Иванов", self.read_stats()[str(CHAT)]["players"])
        dm = FakeMsg(555, "private", 555)
        await B.cmd_stats(dm)                                   # в личке не-владельцу — тишина
        self.assertEqual(dm.answers, [])

    async def test_owner_panel_buttons_cover_functions(self):
        m = owner_msg()
        await B.cmd_menu(m)
        datas = all_button_data(m.answers[-1][1])
        for needed in ("m:poll", "m:players", "m:split", "m:squad", "m:result", "m:pay", "m:stats", "m:help"):
            self.assertIn(needed, datas)


class Restart(Base):
    def test_repeated_restart_keeps_data(self):
        self.write_stats({"Иванов": {"games": 2, "goals": 1, "assists": 1}})
        B.load_players()
        B.players_add("Новичок Н.", "nov")
        snapshot = (read_bytes(B.STATS_FILE), read_bytes(B.PLAYERS_FILE))
        for _ in range(3):      # «перезапуск»: стартовые проверки
            lines = B.storage_report()
            self.assertTrue(lines[0].startswith("📊 Статистика: OK"))
            self.assertTrue(lines[1].startswith("👥 Игроки: OK"))
        self.assertEqual(snapshot, (read_bytes(B.STATS_FILE), read_bytes(B.PLAYERS_FILE)))
        self.assertEqual(len(B.list_backups("stats")), 1)       # одинаковые копии не плодятся

    def test_startup_report_flags_corruption_without_touching_file(self):
        with open(B.STATS_FILE, "w") as f:
            f.write("{bad")
        lines = B.storage_report()
        self.assertIn("НЕ читается", lines[0])
        self.assertEqual(read_text(B.STATS_FILE), "{bad")


if __name__ == "__main__":
    unittest.main()
