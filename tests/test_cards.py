"""Постоянные owner-only карточки: предпросмотр и безопасная ручная публикация."""
import os
import types
from unittest import mock

from aiogram.filters import CommandObject

from test_bot import Base, FakeMsg, FakeQuery, B, OWNER, CHAT, all_button_data, read_bytes  # noqa: E402


def command(args=None):
    return CommandObject(command="карточка", args=args)


def photo_message(caption=None, html_caption=None, file_id="photo-file", unique_id="photo-unique"):
    photo = types.SimpleNamespace(file_id=file_id, file_unique_id=unique_id)
    return FakeMsg(OWNER, "private", OWNER, photo=[photo], caption=caption, html_caption=html_caption)


def card_command(reply, args=None, user_id=OWNER, chat_type="private"):
    return FakeMsg(user_id, chat_type, user_id, text="/карточка", reply_to_message=reply)


class CardDrafts(Base):
    async def create_october(self):
        message = card_command(photo_message())
        await B.cmd_card(message, command())
        store = B.load_card_store()
        return message, store, store["drafts"][0]

    async def test_october_reply_creates_persistent_preview_without_group_send(self):
        before = {}
        for path, payload in ((B.GAMES_FILE, b"games"), (B.PLAYERS_FILE, b"players"), (B.STATS_FILE, b"stats")):
            with open(path, "wb") as f:
                f.write(payload)
            before[path] = read_bytes(path)

        message, store, draft = await self.create_october()
        self.assertEqual(store["version"], 1)
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(draft["caption_text"], B.OCTOBER_CARD_TEXT)
        self.assertEqual(draft["caption_html"], B.OCTOBER_CARD_HTML)
        self.assertLessEqual(len(draft["caption_text"]), B.CARD_CAPTION_LIMIT)
        self.assertEqual(len(message.photo_answers), 1)
        preview = message.photo_answers[0]
        self.assertEqual(preview[0], "photo-file")
        self.assertIn("card:pub:", " ".join(all_button_data(preview[2])))
        self.assertEqual(self.fake.photos, [])
        self.assertEqual({path: read_bytes(path) for path in before}, before)

        # Новый процесс читает тот же JSON: черновик не зависит от AWAITING.
        B.AWAITING.clear()
        self.assertEqual(B.load_card_store()["drafts"][0]["id"], draft["id"])

    async def test_custom_future_card_uses_existing_photo_caption_and_formatting(self):
        reply = photo_message("Ноябрьский текст", "<b>Ноябрьский текст</b>", unique_id="november")
        message = card_command(reply, args="новая")
        await B.cmd_card(message, command("новая"))
        draft = B.load_card_store()["drafts"][0]
        self.assertEqual(draft["caption_text"], "Ноябрьский текст")
        self.assertEqual(draft["caption_html"], "<b>Ноябрьский текст</b>")
        self.assertEqual(draft["source"], "photo_caption")

    async def test_only_owner_private_can_create_or_publish(self):
        reply = photo_message()
        stranger = card_command(reply, user_id=777)
        await B.cmd_card(stranger, command())
        self.assertFalse(os.path.exists(B.CARD_DRAFTS_FILE))
        group = card_command(reply, chat_type="group")
        await B.cmd_card(group, command())
        self.assertFalse(os.path.exists(B.CARD_DRAFTS_FILE))

        _, _, draft = await self.create_october()
        query = FakeQuery(f"card:pub:{draft['id']}", user_id=777)
        await B.cb_card(query)
        self.assertTrue(query.answered[-1][1])
        self.assertEqual(self.fake.photos, [])

    async def test_publish_once_saves_message_id_and_blocks_repeat(self):
        _, _, draft = await self.create_october()
        data = f"card:pub:{draft['id']}"
        first = FakeQuery(data)
        await B.cb_card(first)
        self.assertEqual(len(self.fake.photos), 1)
        self.assertEqual(self.fake.photos[0][0], CHAT)
        saved = B.load_card_store()["drafts"][0]
        self.assertEqual(saved["status"], "published")
        self.assertEqual(saved["published_chat_id"], CHAT)
        self.assertEqual(saved["published_message_id"], 1)

        second = FakeQuery(data)
        await B.cb_card(second)
        self.assertEqual(len(self.fake.photos), 1)
        self.assertIn("Уже опубликовано", second.answered[-1][0])

    async def test_cancel_preserves_record_and_disables_publish(self):
        _, _, draft = await self.create_october()
        await B.cb_card(FakeQuery(f"card:cancel:{draft['id']}"))
        saved = B.load_card_store()["drafts"][0]
        self.assertEqual(saved["status"], "cancelled")
        query = FakeQuery(f"card:pub:{draft['id']}")
        await B.cb_card(query)
        self.assertEqual(self.fake.photos, [])
        self.assertTrue(query.answered[-1][1])

    async def test_telegram_error_becomes_uncertain_and_never_retries_blindly(self):
        _, _, draft = await self.create_october()
        data = f"card:pub:{draft['id']}"
        self.fake.fail_send = True
        first = FakeQuery(data)
        await B.cb_card(first)
        self.assertEqual(B.load_card_store()["drafts"][0]["status"], "uncertain")
        self.fake.fail_send = False
        second = FakeQuery(data)
        await B.cb_card(second)
        self.assertEqual(self.fake.photos, [])
        self.assertIn("Повтор заблокирован", second.answered[-1][0])

    async def test_lost_confirmation_keeps_publishing_marker_and_blocks_duplicate(self):
        _, _, draft = await self.create_october()
        data = f"card:pub:{draft['id']}"
        real_save = B.save_card_store
        calls = 0

        def fail_after_send(store):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("disk unavailable")
            return real_save(store)

        with mock.patch.object(B, "save_card_store", side_effect=fail_after_send):
            await B.cb_card(FakeQuery(data))
        self.assertEqual(len(self.fake.photos), 1)
        self.assertEqual(B.load_card_store()["drafts"][0]["status"], "publishing")

        await B.cb_card(FakeQuery(data))
        self.assertEqual(len(self.fake.photos), 1)

    async def test_same_photo_does_not_create_duplicate_draft(self):
        first, _, _ = await self.create_october()
        second = card_command(photo_message())
        await B.cmd_card(second, command())
        self.assertEqual(len(B.load_card_store()["drafts"]), 1)
        self.assertTrue(second.answers)
        self.assertEqual(len(second.photo_answers), 1)

