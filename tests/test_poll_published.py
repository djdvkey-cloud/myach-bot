"""Черновик ≠ опубликован: автоопрос блокирует только ПОДТВЕРЖДЁННАЯ публикация на этот понедельник в общем чате.
Настоящий Telegram не затрагивается: заглушка бота."""
import asyncio
import datetime as real_dt
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_bot import Base, FakeQuery, owner_msg, cmd, all_button_data, all_button_text, B, CHAT, OWNER  # noqa: E402
from test_poll_schedule import PollBase  # noqa: E402

PROD_LEGACY_STATE = {  # как в production после 01.10: старый формат, без published/chat_id; message_id есть
    "poll_id": "p-prod", "message_id": 777, "date": "2026-10-01", "voters": {}, "manual": True}


class DraftVsPublished(PollBase):
    def write_state(self, **fields):
        base = {"poll_id": "p", "message_id": None, "chat_id": None, "date": "2026-10-02", "monday": "2026-10-05",
                "status": "draft", "published": False, "voters": {}, "manual": True}
        base.update(fields)
        B.save_json(B.POLL_STATE_FILE, base)

    async def test_1_preview_in_private_chat_published_false_saturday_noon_sends(self):
        self.write_state()                                          # подготовлен/просмотрен в личке, в общий чат не уходил
        self.at(2026, 10, 3, 12, 0)
        self.assertTrue(await B.maybe_send_poll())
        self.assertEqual(len(self.fake.polls), 1)
        self.assertEqual(self.fake.polls[0][0], CHAT)
        state = B.load_json(B.POLL_STATE_FILE)
        self.assertTrue(state["published"])
        self.assertEqual(state["status"], "published")

    async def test_first_screen_of_create_poll_publishes_nothing_and_says_so(self):
        self.at(2026, 10, 1, 1, 15)
        q = FakeQuery("m:poll")
        await B.cb_panel(q)
        text, markup = q.message.edits[-1]
        self.assertIn("Опубликовать опрос в ОБЩЕМ чате на понедельник 05.10.2026", text)
        self.assertIn("Предпросмотра нет", text)
        self.assertEqual(self.fake.polls, [])                       # просто экран подтверждения — ничего не опубликовано
        self.assertFalse(os.path.exists(B.POLL_STATE_FILE))
        self.assertEqual(all_button_data(markup), ["m:pollgo", "m:home"])
        self.assertIn("📢 Опубликовать сейчас", all_button_text(markup))
        self.at(2026, 10, 3, 12, 0)
        self.assertTrue(await B.maybe_send_poll())                  # и субботний автоопрос выходит

    async def test_2_real_manual_publication_blocks_auto_for_same_monday(self):
        self.at(2026, 10, 1, 1, 18)
        q = FakeQuery("m:pollgo")
        await B.cb_panel(q)
        self.assertIn("ОПУБЛИКОВАН в общем чате", q.message.edits[-1][0])
        state = B.load_json(B.POLL_STATE_FILE)
        self.assertEqual((state["published"], state["status"], state["chat_id"], state["monday"]), (True, "published", CHAT, "2026-10-05"))
        self.assertEqual(state["message_id"], 1)                    # message_id — из ответа Telegram
        self.assertIn("published_at", state)
        self.at(2026, 10, 3, 12, 0)
        self.assertFalse(await B.maybe_send_poll())
        self.assertEqual(len(self.fake.polls), 1)

    async def test_3_auto_poll_marks_published_only_after_send_success(self):
        seen_during_send = {}
        real_send = self.fake.send_poll

        async def checking_send(*a, **kw):
            seen_during_send["state"] = B.load_json(B.POLL_STATE_FILE, {})
            seen_during_send["published_set"] = set(B._PUBLISHED_MONDAYS)
            return await real_send(*a, **kw)
        self.fake.send_poll = checking_send
        self.at(2026, 10, 3, 12, 0)
        self.assertTrue(await B.maybe_send_poll())
        self.assertEqual(seen_during_send["state"], {})             # на момент вызова send ничего «опубликованным» не записано
        self.assertEqual(seen_during_send["published_set"], set())
        state = B.load_json(B.POLL_STATE_FILE)
        self.assertEqual((state["published"], state["chat_id"], state["message_id"], state["monday"]), (True, CHAT, 1, "2026-10-05"))
        self.assertIn(real_dt.date(2026, 10, 5), B._PUBLISHED_MONDAYS)

    async def test_message_id_and_chat_id_come_from_telegram_response(self):
        async def send(chat_id, question, options, **kw):
            return type("M", (), {"poll": type("P", (), {"id": "px"})(), "message_id": 4242,
                                  "chat": type("C", (), {"id": CHAT})()})()
        self.fake.send_poll = send
        self.at(2026, 10, 3, 12, 0)
        await B.maybe_send_poll()
        state = B.load_json(B.POLL_STATE_FILE)
        self.assertEqual((state["message_id"], state["chat_id"], state["poll_id"]), (4242, CHAT, "px"))

    async def test_4_telegram_error_published_not_set_retry_allowed(self):
        self.write_state()
        before = B.load_json(B.POLL_STATE_FILE)
        self.at(2026, 10, 3, 12, 0)
        with mock.patch.object(self.fake, "send_poll", side_effect=RuntimeError("telegram error")):
            self.assertFalse(await B.maybe_send_poll())
        self.assertEqual(B.load_json(B.POLL_STATE_FILE), before)    # черновик остался черновиком
        self.assertEqual(B._PUBLISHED_MONDAYS, set())
        self.assertFalse(os.path.exists(B.LAST_POLL_FILE))
        self.at(2026, 10, 3, 12, 1)
        self.assertTrue(await B.maybe_send_poll())                  # повторная попытка допустима
        self.assertTrue(B.load_json(B.POLL_STATE_FILE)["published"])

    async def test_5_restart_after_success_no_duplicate(self):
        self.at(2026, 10, 3, 12, 0)
        await B.maybe_send_poll()
        for minute in (2, 30):
            self.restart()
            self.at(2026, 10, 3, 12, minute)
            self.assertFalse(await B.maybe_send_poll())
        self.assertEqual(len(self.fake.polls), 1)

    async def test_6_draft_survived_restart_does_not_block(self):
        self.write_state()
        self.restart()
        self.at(2026, 10, 3, 12, 0)
        self.assertTrue(await B.maybe_send_poll())
        self.restart()
        self.fake.extra_messages.add((CHAT, 5))                      # сообщение с таким id существует, но запись — черновик
        self.write_state(status="draft", published=False, message_id=5, chat_id=CHAT, monday="2026-10-12")    # даже черновик с message_id (личный чат) не блокирует
        B._PUBLISHED_MONDAYS.clear()
        self.at(2026, 10, 10, 12, 0)
        self.assertTrue(await B.maybe_send_poll())

    async def test_7_old_published_poll_for_previous_monday_does_not_block_next_week(self):
        B.save_json(B.POLL_STATE_FILE, {"poll_id": "old", "message_id": 1, "chat_id": CHAT, "date": "2026-09-26", "monday": "2026-09-28",
                                        "status": "published", "published": True, "published_at": "2026-09-26T12:00:06+05:00",
                                        "voters": {}, "manual": False})
        self.fake.extra_messages.add((CHAT, 1))
        self.at(2026, 10, 3, 12, 0)
        self.assertTrue(await B.maybe_send_poll())
        self.assertEqual(B.load_json(B.POLL_STATE_FILE)["monday"], "2026-10-05")

    async def test_8_catch_up_after_noon_draft_sends_published_does_not(self):
        self.write_state()
        self.at(2026, 10, 3, 12, 40)
        self.assertTrue(await B.maybe_send_poll())
        self.assertEqual(len(self.fake.polls), 1)
        # теперь — опубликованный на этот понедельник (после рестарта) — догон не отправляет
        self.restart()
        if os.path.exists(B.LAST_POLL_FILE):
            os.unlink(B.LAST_POLL_FILE)
        self.at(2026, 10, 3, 18, 0)
        self.assertFalse(await B.maybe_send_poll())
        # другой чистый день с уже опубликованным вручную опросом на этот же понедельник
        for p in (B.POLL_STATE_FILE, B.LAST_POLL_FILE):
            if os.path.exists(p):
                os.unlink(p)
        self.fake.polls.clear()
        self.restart()
        self.at(2026, 10, 2, 20, 0)
        await B.cmd_poll(owner_msg(), cmd("опрос", None))
        self.restart()
        self.at(2026, 10, 3, 13, 5)
        self.assertFalse(await B.maybe_send_poll())
        self.assertEqual(len(self.fake.polls), 1)

    # ---- сегодняшний production-кейс (poll_state старого формата от 01.10)

    async def test_9_production_state_does_not_block_todays_poll_when_message_not_in_chat(self):
        B.save_json(B.POLL_STATE_FILE, dict(PROD_LEGACY_STATE))     # старый формат: нет published, message_id=777
        self.fake.chat_polls_only = True                            # такого сообщения в общем чате нет (Дмитрий: опроса нет)
        self.at(2026, 10, 3, 0, 20)
        line = await B.poll_state_line()
        self.assertIn("сообщения в общем чате нет", line)
        self.assertIn("автоопрос выйдет", line)
        self.at(2026, 10, 3, 12, 0)
        self.assertTrue(await B.maybe_send_poll())
        self.assertEqual(len(self.fake.polls), 1)
        self.assertIn("05.10.2026", self.fake.polls[0][1])
        self.assertIn((CHAT, 777), self.fake.probes)                # проверка у Telegram, а не по poll_state

    async def test_9b_legacy_state_with_really_existing_message_blocks_and_is_reported(self):
        B.save_json(B.POLL_STATE_FILE, dict(PROD_LEGACY_STATE))
        self.fake.extra_messages.add((CHAT, 777))                   # Telegram подтверждает: сообщение есть в общем чате
        self.at(2026, 10, 3, 0, 20)
        self.assertIn("СУЩЕСТВУЕТ опрос на 05.10.2026 (message_id 777", await B.poll_state_line())
        self.at(2026, 10, 3, 12, 0)
        self.assertFalse(await B.maybe_send_poll())
        self.assertEqual(self.fake.polls, [])

    async def test_9c_legacy_state_unverifiable_does_not_block(self):
        B.save_json(B.POLL_STATE_FILE, dict(PROD_LEGACY_STATE))
        self.fake.probe_error = True
        self.at(2026, 10, 3, 0, 20)
        self.assertIn("проверить не удалось", await B.poll_state_line())
        self.at(2026, 10, 3, 12, 0)
        self.assertTrue(await B.maybe_send_poll())

    async def test_9d_recorded_publication_unverifiable_still_blocks(self):
        B.save_json(B.POLL_STATE_FILE, {"poll_id": "p", "message_id": 9, "chat_id": CHAT, "date": "2026-10-02", "monday": "2026-10-05",
                                        "status": "published", "published": True, "voters": {}, "manual": True})
        self.fake.probe_error = True                                # сеть сбоит, но успех отправки записан нашим кодом
        self.at(2026, 10, 3, 12, 0)
        self.assertFalse(await B.maybe_send_poll())

    async def test_9e_poll_in_another_chat_is_not_the_general_chat_publication(self):
        B.save_json(B.POLL_STATE_FILE, {"poll_id": "p", "message_id": 9, "chat_id": -111, "date": "2026-10-02", "monday": "2026-10-05",
                                        "status": "published", "published": True, "voters": {}, "manual": True})
        self.fake.extra_messages.add((CHAT, 9))                      # такой id существует именно в общем чате, но запись про другой чат
        self.at(2026, 10, 3, 12, 0)
        self.assertTrue(await B.maybe_send_poll())

    async def test_recorded_publication_whose_message_is_gone_does_not_block(self):
        """Опрос был опубликован, но сообщение удалено из чата: подтверждения нет — автоопрос должен выйти."""
        B.save_json(B.POLL_STATE_FILE, {"poll_id": "p", "message_id": 9, "chat_id": CHAT, "date": "2026-10-02", "monday": "2026-10-05",
                                        "status": "published", "published": True, "voters": {}, "manual": True})
        self.fake.deleted.add((CHAT, 9))
        self.at(2026, 10, 3, 12, 0)
        self.assertTrue(await B.maybe_send_poll())

    async def test_poll_message_exists_semantics(self):
        self.fake.extra_messages.add((CHAT, 5))
        self.assertTrue(await B.poll_message_exists(CHAT, 5))
        self.assertFalse(await B.poll_message_exists(CHAT, 6))
        self.fake.probe_error = True
        self.assertIsNone(await B.poll_message_exists(CHAT, 5))

    async def test_draft_state_line_says_draft(self):
        self.write_state(message_id=3)
        self.at(2026, 10, 3, 0, 20)
        self.assertIn("только черновик", await B.poll_state_line())

    async def test_startup_message_to_owner_has_poll_state_verdict(self):
        B.save_json(B.POLL_STATE_FILE, dict(PROD_LEGACY_STATE))
        self.at(2026, 10, 3, 0, 20)
        await B.notify_started(await B.setup_menu())
        text = [t for c, t in self.fake.sent if c == OWNER][0]
        self.assertIn("🗳 Следующий автоопрос: суббота 03.10.2026 12:00", text)
        self.assertIn("автоопрос выйдет", text)

    # ---- E: успех отправки, но запись на диск не удалась

    async def test_E_state_write_failure_current_process_guard_and_no_dup_after_marker_restart(self):
        real_save = B.save_json

        def failing(path, data):
            if path == B.POLL_STATE_FILE:
                raise OSError("диск")
            return real_save(path, data)
        self.at(2026, 10, 3, 12, 0)
        with mock.patch.object(B, "save_json", failing):
            self.assertTrue(await B.maybe_send_poll())
            for minute in (1, 2, 5):
                self.at(2026, 10, 3, 12, minute)
                self.assertFalse(await B.maybe_send_poll())
        self.assertEqual(len(self.fake.polls), 1)
        self.restart()                                              # после рестарта: маркер субботы (last_poll.txt) записан отдельно
        self.at(2026, 10, 3, 14, 0)
        self.assertFalse(await B.maybe_send_poll())
        self.assertEqual(len(self.fake.polls), 1)

    async def test_E_both_files_lost_means_in_process_guard_only_documented_limit(self):
        """Граница защиты: если при успешной отправке не записалось НИЧЕГО на диск и процесс перезапущен, повтор возможен (в логе есть
        message_id отправленного опроса). Тест фиксирует это поведение, чтобы оно не менялось незаметно."""
        self.at(2026, 10, 3, 12, 0)
        with mock.patch.object(B, "save_json", side_effect=OSError("диск")), \
                mock.patch.object(B, "LAST_POLL_FILE", os.path.join(self.tmp, "no_dir", "last.txt")):
            self.assertTrue(await B.maybe_send_poll())
            self.assertFalse(await B.maybe_send_poll())             # в этом процессе — защита работает
            self.restart()
        self.assertEqual(len(self.fake.polls), 1)


class HelpAndHandoffMentions(Base):
    def test_help_describes_publish_semantics(self):
        text = B.HELP_SECTIONS["poll"]
        self.assertIn("опубликован", text.lower())


if __name__ == "__main__":
    unittest.main()
