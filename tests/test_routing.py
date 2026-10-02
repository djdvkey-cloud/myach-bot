"""Интеграционные тесты маршрутизации: настоящие Dispatcher, фильтры и модели
aiogram; подменён только сетевой слой (запросы к Telegram записываются)."""
import datetime as dt
import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("BOT_TOKEN", "123456789:" + "A" * 35)
os.environ.setdefault("OWNER_ID", "1000")
os.environ.setdefault("CHAT_ID", "-2000")
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="myach-import-")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot as B  # noqa: E402
from aiogram import Bot  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.methods import TelegramMethod  # noqa: E402
from aiogram.types import (CallbackQuery, Chat, Message, MessageEntity, MenuButtonDefault,  # noqa: E402
                           Poll, PollAnswer, PollOption, Update, User)

OWNER, CHAT = B.OWNER_ID, B.CHAT_ID


class RecSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.counter = 100

    async def close(self):
        pass

    async def stream_content(self, *a, **kw):
        if False:
            yield b""

    async def make_request(self, bot, method: TelegramMethod, timeout=None):
        self.calls.append(method)
        name = type(method).__name__
        self.counter += 1
        chat_id = getattr(method, "chat_id", OWNER)
        now = dt.datetime.now(dt.timezone.utc)
        if name in ("SendMessage", "EditMessageText"):
            return Message(message_id=self.counter, date=now, chat=Chat(id=chat_id, type="private"),
                           text=getattr(method, "text", ""))
        if name == "SendPoll":
            return Message(message_id=self.counter, date=now, chat=Chat(id=chat_id, type="supergroup"),
                           poll=Poll(id=f"p{self.counter}", question=method.question,
                                     options=[PollOption(text="+", voter_count=0), PollOption(text="-", voter_count=0)],
                                     total_voter_count=0, is_closed=False, is_anonymous=False, type="regular",
                                     allows_multiple_answers=False))
        if name == "GetChatMenuButton":
            return MenuButtonDefault()
        if name == "GetMyCommands":
            return []
        return True

    def names(self):
        return [type(c).__name__ for c in self.calls]


def user(uid, name="Тест", username=None):
    return User(id=uid, is_bot=False, first_name=name, username=username)


def text_update(uid, chat_id, chat_type, text, n=1):
    entities = [MessageEntity(type="bot_command", offset=0, length=len(text.split()[0]))] if text.startswith("/") else None
    msg = Message(message_id=n, date=dt.datetime.now(dt.timezone.utc), chat=Chat(id=chat_id, type=chat_type),
                  from_user=user(uid), text=text, entities=entities)
    return Update(update_id=n, message=msg)


def cb_update(uid, data, chat_type="private", chat_id=None, n=1):
    msg = Message(message_id=50, date=dt.datetime.now(dt.timezone.utc),
                  chat=Chat(id=chat_id if chat_id is not None else uid, type=chat_type), text="x",
                  from_user=User(id=1, is_bot=True, first_name="bot"))
    return Update(update_id=n, callback_query=CallbackQuery(id="cb1", from_user=user(uid), chat_instance="ci",
                                                            data=data, message=msg))


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="myach-route-")
        self.patches = []
        paths = {"STATS_FILE": "stats.json", "LAST_POLL_FILE": "last_poll.txt", "POLL_STATE_FILE": "poll_state.json",
                 "GUESTS_FILE": "guests.json", "PLAYERS_FILE": "players.json", "GAME_FILE": "game.json",
                 "GAME_ARCHIVE_FILE": "game_archive.json", "GAMES_FILE": "games.json", "META_FILE": "ui_meta.json", "BACKUP_DIR": "backups"}
        for name, val in paths.items():
            p = mock.patch.object(B, name, os.path.join(self.tmp, val))
            p.start()
            self.patches.append(p)
        self.session = RecSession()
        self.bot = Bot(token=os.environ["BOT_TOKEN"], session=self.session)
        p = mock.patch.object(B, "bot", self.bot)
        p.start()
        self.patches.append(p)
        B.AWAITING.clear()
        B._POLL_SENT_DAY = None

    def tearDown(self):
        for p in self.patches:
            p.stop()

    async def feed(self, update):
        self.session.calls.clear()
        return await B.dp.feed_update(self.bot, update)

    def sent(self, name="SendMessage"):
        return [c for c in self.session.calls if type(c).__name__ == name]

    async def test_owner_private_menu_and_help(self):
        await self.feed(text_update(OWNER, OWNER, "private", "/menu"))
        sent = self.sent()
        self.assertEqual(len(sent), 1)
        self.assertIn("панель администратора", sent[0].text)
        buttons = [b.callback_data for row in sent[0].reply_markup.inline_keyboard for b in row]
        self.assertIn("m:result", buttons)
        await self.feed(text_update(OWNER, OWNER, "private", "/help"))
        self.assertIn("Help", self.sent()[0].text)
        await self.feed(cb_update(OWNER, "hp:pay"))
        edits = self.sent("EditMessageText")
        self.assertEqual(len(edits), 1)
        self.assertIn("ОПЛАТА", edits[0].text)

    async def test_russian_and_english_commands_route(self):
        for text in ("/игроки", "/players", "/меню", "/состав", "/squad", "/результат", "/result"):
            await self.feed(text_update(OWNER, OWNER, "private", text))
            self.assertTrue(self.session.calls, text)

    async def test_group_and_strangers(self):
        B.save_json(B.STATS_FILE, {str(CHAT): {"players": {"Иванов": {"games": 1, "goals": 1, "assists": 0}}, "last": []}})
        await self.feed(text_update(555, CHAT, "group", "/статистика"))
        self.assertIn("Статистика", self.sent()[0].text)
        await self.feed(text_update(555, CHAT, "group", "/menu"))         # панели в группе нет
        self.assertEqual(self.session.calls, [])
        await self.feed(text_update(OWNER, CHAT, "group", "/menu"))        # и у владельца в группе тоже
        self.assertEqual(self.session.calls, [])
        await self.feed(text_update(555, 555, "private", "/статистика"))   # в личке чужому — тишина
        self.assertEqual(self.session.calls, [])
        await self.feed(text_update(555, 555, "private", "просто текст"))
        self.assertEqual(self.session.calls, [])
        await self.feed(cb_update(555, "m:players"))
        ans = self.sent("AnswerCallbackQuery")
        self.assertTrue(ans and ans[0].show_alert)

    async def test_players_text_flow_through_dispatcher(self):
        await self.feed(text_update(OWNER, OWNER, "private", "/игрок добавить Иванов П. @ivanov"))
        self.assertIn("Иванов П.", B.load_players()["players"])
        await self.feed(cb_update(OWNER, "pl:add"))
        self.assertEqual(B.AWAITING[OWNER]["kind"], "add_player")
        await self.feed(text_update(OWNER, OWNER, "private", "Сидоров К. @sidorov"))
        self.assertIn("Сидоров К.", B.load_players()["players"])
        self.assertNotIn(OWNER, B.AWAITING)
        # обычный текст вне ожидания игнорируется
        await self.feed(text_update(OWNER, OWNER, "private", "привет"))
        self.assertEqual(self.session.calls, [])

    async def test_poll_answer_and_manual_poll_routes(self):
        await self.feed(text_update(OWNER, OWNER, "private", "/опрос"))
        self.assertEqual(len(self.sent("SendPoll")), 1)
        poll_id = B.load_json(B.POLL_STATE_FILE)["poll_id"]
        upd = Update(update_id=2, poll_answer=PollAnswer(poll_id=poll_id, user=user(9, username="lmur2000"), option_ids=[0]))
        await self.feed(upd)
        self.assertEqual(B.load_json(B.POLL_STATE_FILE)["voters"]["9"]["player"], "Ларионов М.")

    async def test_split_publish_through_dispatcher(self):
        names = list(B.DEFAULT_PLAYER_USERNAMES)[:6]
        B.save_json(B.POLL_STATE_FILE, {"poll_id": "p", "voters": {
            str(100 + i): {"username": B.DEFAULT_PLAYER_USERNAMES[n], "display": n, "player": n} for i, n in enumerate(names)}})
        await self.feed(text_update(OWNER, OWNER, "private", "/split"))
        draft = self.sent()[0]
        self.assertIn("Составы — 6 игроков", draft.text)
        pub = [b.callback_data for row in draft.reply_markup.inline_keyboard for b in row if b.callback_data.startswith("pub:")][0]
        await self.feed(cb_update(OWNER, pub))
        to_chat = [c for c in self.sent() if c.chat_id == CHAT]
        self.assertEqual(len(to_chat), 1)
        self.assertTrue(self.sent("EditMessageReplyMarkup"))
        await self.feed(cb_update(OWNER, pub))
        self.assertEqual([c for c in self.sent() if c.chat_id == CHAT], [])

    async def test_menu_setup_real_methods(self):
        report = await B.setup_menu()
        self.assertEqual(report["errors"], [])
        names = self.session.names()
        self.assertIn("DeleteMyCommands", names)
        self.assertEqual(names.count("SetMyCommands"), 1)
        self.assertEqual(names.count("SetChatMenuButton"), 2)
        setc = [c for c in self.session.calls if type(c).__name__ == "SetMyCommands"][0]
        self.assertEqual(setc.scope.type, "chat")
        self.assertEqual(setc.scope.chat_id, OWNER)
        self.assertEqual([c.command for c in setc.commands], [c for c, _ in B.OWNER_MENU_COMMANDS])
        for c, d in B.OWNER_MENU_COMMANDS:          # ограничения Telegram на команды меню
            self.assertRegex(c, r"^[a-z0-9_]{1,32}$")
            self.assertTrue(1 <= len(d) <= 256)


if __name__ == "__main__":
    unittest.main()
