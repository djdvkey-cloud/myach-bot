"""Недельная автоматика опроса: каждую субботу 12:00 Asia/Yekaterinburg → опрос на ближайший понедельник.
Ничего не отправляется в настоящий чат: Telegram подменён заглушкой."""
import asyncio
import datetime as real_dt
import os
import sys
import types as pytypes
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_bot import Base, owner_msg, cmd, B, CHAT, OWNER  # noqa: E402


def saturdays(start, count):
    day = start
    while day.weekday() != 5:
        day += real_dt.timedelta(days=1)
    return [day + real_dt.timedelta(days=7 * i) for i in range(count)]


class PollBase(Base):
    def at(self, y, m, d, h=0, mi=0):
        self.set_now(real_dt.datetime(y, m, d, h, mi))

    def at_utc(self, y, m, d, h, mi):
        """Часы сервера в UTC: бот обязан сам перевести время в Asia/Yekaterinburg."""
        fixed = real_dt.datetime(y, m, d, h, mi, tzinfo=real_dt.timezone.utc)

        class FakeDT(real_dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)
        fake = pytypes.SimpleNamespace(datetime=FakeDT, date=real_dt.date, timedelta=real_dt.timedelta, timezone=real_dt.timezone)
        p = mock.patch.object(B, "datetime", fake)
        p.start()
        self.patches.append(p)

    def restart(self):
        """Перезапуск контейнера: память процесса теряется, файлы /data остаются."""
        B._POLL_SENT_DAY = None
        B._PUBLISHED_MONDAYS.clear()


class CurrentCycle(PollBase):
    async def test_saturday_2026_10_03_noon_publishes_for_monday_05_10_in_family_chat(self):
        self.at(2026, 10, 3, 11, 59)
        self.assertFalse(await B.maybe_send_poll())
        self.assertEqual(self.fake.polls, [])
        self.at(2026, 10, 3, 12, 0)
        self.assertTrue(await B.maybe_send_poll())
        chat, question, options = self.fake.polls[0]
        self.assertEqual(chat, CHAT)
        self.assertIn("Футбол Лестех понедельник 05.10.2026 21.30-23.00", question)
        self.assertEqual(options, ["+", "-"])
        state = B.load_json(B.POLL_STATE_FILE)
        self.assertEqual((state["date"], state["monday"], state["manual"]), ("2026-10-03", "2026-10-05", False))
        self.assertEqual(B.load_json(B.POLL_STATE_FILE)["voters"], {})

    async def test_server_in_utc_cannot_shift_publication(self):
        self.at_utc(2026, 10, 3, 6, 59)             # 11:59 в Екатеринбурге
        self.assertFalse(await B.maybe_send_poll())
        self.at_utc(2026, 10, 3, 7, 0)              # 12:00 в Екатеринбурге
        self.assertTrue(await B.maybe_send_poll())
        self.assertIn("05.10.2026", self.fake.polls[0][1])
        self.at_utc(2026, 10, 9, 19, 30)            # пятница 19:30 UTC = суббота 00:30 Екатеринбург — ещё рано (до 12:00)
        self.assertFalse(await B.maybe_send_poll())
        self.at_utc(2026, 10, 10, 6, 0)             # суббота 11:00 Екатеринбург
        self.assertFalse(await B.maybe_send_poll())
        self.assertEqual(len(self.fake.polls), 1)

    def test_next_poll_info_and_status_lines(self):
        self.at(2026, 10, 3, 0, 20)                  # сразу после полуночи в субботу — до 12:00
        info = B.next_poll_info()
        self.assertEqual((info["saturday"], info["monday"], info["status"]),
                         (real_dt.date(2026, 10, 3), real_dt.date(2026, 10, 5), "scheduled"))
        line = B.poll_status_lines()[0]
        self.assertIn("суббота 03.10.2026 12:00 (Asia/Yekaterinburg) на понедельник 05.10.2026", line)
        self.at(2026, 10, 2, 15, 0)                  # пятница
        self.assertEqual(B.next_poll_info()["saturday"], real_dt.date(2026, 10, 3))
        self.at(2026, 10, 4, 10, 0)                  # воскресенье → следующая суббота
        info = B.next_poll_info()
        self.assertEqual((info["saturday"], info["monday"]), (real_dt.date(2026, 10, 10), real_dt.date(2026, 10, 12)))
        self.at(2026, 10, 3, 12, 30)
        self.assertEqual(B.next_poll_info()["status"], "catchup")

    async def test_after_publication_next_info_points_to_next_saturday(self):
        self.at(2026, 10, 3, 12, 0)
        await B.maybe_send_poll()
        info = B.next_poll_info()
        self.assertEqual((info["status"], info["saturday"], info["monday"]), ("done", real_dt.date(2026, 10, 10), real_dt.date(2026, 10, 12)))
        self.assertIn("опрос этой субботы уже отправлен", B.poll_status_lines()[0])

class RecurringWeeks(PollBase):
    async def test_every_saturday_for_half_a_year_exactly_one_poll_for_next_monday(self):
        weeks = saturdays(real_dt.date(2026, 10, 3), 30)             # через конец месяца (31.10), года (26.12 → 28.12) и дальше
        for sat in weeks:
            monday = sat + real_dt.timedelta(days=2)
            self.assertEqual(monday.weekday(), 0)
            before = len(self.fake.polls)
            for hour, minute, expected in ((9, 0, False), (11, 59, False), (12, 0, True), (12, 1, False), (15, 30, False), (23, 59, False)):
                self.at(sat.year, sat.month, sat.day, hour, minute)
                self.assertEqual(await B.maybe_send_poll(), expected, (sat, hour, minute))
            self.assertEqual(len(self.fake.polls), before + 1, sat)
            self.assertIn(f"понедельник {monday:%d.%m.%Y} ", self.fake.polls[-1][1])
            for offset in (1, 2, 3, 4, 5, 6):                         # воскресенье … пятница — тишина
                day = sat + real_dt.timedelta(days=offset)
                self.at(day.year, day.month, day.day, 12, 30)
                self.assertFalse(await B.maybe_send_poll(), day)
        self.assertEqual(len(self.fake.polls), 30)
        self.assertEqual(B.load_json(B.POLL_STATE_FILE)["monday"], (weeks[-1] + real_dt.timedelta(days=2)).isoformat())   # хранится только последний

    def test_control_examples_and_month_and_year_boundaries(self):
        cases = {
            (2026, 10, 3): (2026, 10, 5), (2026, 10, 10): (2026, 10, 12), (2026, 10, 17): (2026, 10, 19),
            (2026, 10, 31): (2026, 11, 2),          # конец месяца
            (2026, 12, 26): (2026, 12, 28), (2027, 1, 2): (2027, 1, 4),
            (2022, 12, 31): (2023, 1, 2),           # суббота ровно 31 декабря → понедельник в новом году
            (2028, 2, 26): (2028, 2, 28), (2028, 2, 5): (2028, 2, 7),   # високосный год
            (2024, 3, 30): (2024, 4, 1),
        }
        for sat, mon in cases.items():
            day = real_dt.date(*sat)
            self.assertEqual(day.weekday(), 5, sat)
            self.assertEqual(B.poll_target_monday(day), real_dt.date(*mon), sat)
        self.assertEqual(B.poll_target_monday(real_dt.date(2026, 10, 5)), real_dt.date(2026, 10, 12))   # из понедельника — следующий

    async def test_no_dependency_on_the_specific_date_of_today(self):
        for start in (real_dt.date(2025, 1, 1), real_dt.date(2027, 6, 15), real_dt.date(2030, 12, 25)):
            self.fake.polls.clear()
            for p in (B.POLL_STATE_FILE, B.LAST_POLL_FILE):
                if os.path.exists(p):
                    os.unlink(p)
            sat = saturdays(start, 1)[0]
            self.at(sat.year, sat.month, sat.day, 12, 5)
            self.restart()
            self.assertTrue(await B.maybe_send_poll(), sat)
            self.assertIn(f"{sat + real_dt.timedelta(days=2):%d.%m.%Y}", self.fake.polls[0][1])

    async def test_old_poll_state_never_blocks_next_week_and_does_not_accumulate(self):
        self.at(2026, 10, 3, 12, 0)
        await B.maybe_send_poll()
        first = B.load_json(B.POLL_STATE_FILE)
        self.at(2026, 10, 10, 12, 0)
        self.assertTrue(await B.maybe_send_poll())
        second = B.load_json(B.POLL_STATE_FILE)
        self.assertNotEqual(first["poll_id"], second["poll_id"])
        self.assertEqual((second["date"], second["monday"]), ("2026-10-10", "2026-10-12"))
        self.assertEqual(set(second), {"poll_id", "message_id", "chat_id", "date", "monday", "status", "published", "published_at", "voters", "manual"})   # один файл, без накопления


class Restarts(PollBase):
    async def test_restart_on_friday_or_midweek_keeps_next_saturday(self):
        for when in ((2026, 10, 2, 18, 0), (2026, 10, 5, 9, 0), (2026, 10, 7, 14, 0), (2026, 10, 9, 23, 50)):
            self.at(*when)
            self.restart()
            self.assertFalse(await B.maybe_send_poll())
        self.assertEqual(self.fake.polls, [])
        self.restart()
        self.at(2026, 10, 10, 12, 0)
        self.assertTrue(await B.maybe_send_poll())
        self.assertIn("12.10.2026", self.fake.polls[0][1])

    async def test_restart_on_saturday_morning_then_noon(self):
        self.at(2026, 10, 3, 9, 15)
        self.restart()
        self.assertFalse(await B.maybe_send_poll())
        self.at(2026, 10, 3, 12, 0)
        self.assertTrue(await B.maybe_send_poll())
        self.assertEqual(len(self.fake.polls), 1)

    async def test_restart_after_publication_does_not_duplicate(self):
        self.at(2026, 10, 3, 12, 0)
        await B.maybe_send_poll()
        for minute in (3, 40):
            self.restart()
            self.at(2026, 10, 3, 12, minute)
            self.assertFalse(await B.maybe_send_poll())
        self.restart()
        self.at(2026, 10, 3, 18, 0)
        self.assertFalse(await B.maybe_send_poll())
        self.restart()
        self.at(2026, 10, 4, 10, 0)                              # воскресенье
        self.assertFalse(await B.maybe_send_poll())
        self.assertEqual(len(self.fake.polls), 1)

    async def test_restart_with_lost_marker_or_corrupted_state_still_no_duplicate(self):
        self.at(2026, 10, 3, 12, 0)
        await B.maybe_send_poll()
        self.restart()
        os.unlink(B.LAST_POLL_FILE)                              # маркера нет — остаётся poll_state
        self.assertFalse(await B.maybe_send_poll())
        self.restart()
        with open(B.POLL_STATE_FILE, "w") as f:                  # а теперь повреждён poll_state, маркер на месте
            f.write("{broken")
        with open(B.LAST_POLL_FILE, "w") as f:
            f.write("2026-10-03")
        self.assertFalse(await B.maybe_send_poll())
        self.assertEqual(len(self.fake.polls), 1)

    async def test_scheduler_task_is_created_on_every_start(self):
        """main() каждый раз заново запускает poll_scheduler — расписание не зависит от прошлого процесса."""
        started = []

        async def fake_scheduler():
            started.append(1)

        async def noop(*a, **kw):
            return None

        async def menu():
            return {}

        async def polling(*a, **kw):
            await asyncio.sleep(0)                      # даёт запущенной задаче расписания начать работу
        self.fake.session = pytypes.SimpleNamespace(close=noop)
        for _ in range(2):
            with mock.patch.object(B, "setup_menu", menu), mock.patch.object(B, "notify_started", noop), \
                    mock.patch.object(B, "notify_archive", noop), mock.patch.object(B, "notify_canonical", noop), \
                    mock.patch.object(B, "poll_scheduler", fake_scheduler), mock.patch.object(B.dp, "start_polling", polling):
                await B.main()
        self.assertEqual(len(started), 2)


class Missed12(PollBase):
    async def test_downtime_over_noon_is_caught_up_once_on_same_saturday(self):
        for hour, minute in ((12, 10), (13, 0), (18, 45), (23, 59)):
            for p in (B.POLL_STATE_FILE, B.LAST_POLL_FILE):
                if os.path.exists(p):
                    os.unlink(p)
            self.fake.polls.clear()
            self.restart()
            self.at(2026, 10, 3, hour, minute)                    # бот поднялся позже 12:00
            self.assertTrue(await B.maybe_send_poll(), (hour, minute))
            self.assertIn("05.10.2026", self.fake.polls[0][1])
            self.assertFalse(await B.maybe_send_poll())          # обычный планировщик в ту же минуту/позже — дубля нет
            self.assertEqual(len(self.fake.polls), 1)

    async def test_whole_saturday_down_is_not_caught_up_on_sunday(self):
        """Фактическое поведение: если бот был недоступен ВСЮ субботу (до 24:00), в воскресенье опрос не создаётся (есть /опрос)."""
        self.at(2026, 10, 4, 0, 5)
        self.assertFalse(await B.maybe_send_poll())
        self.at(2026, 10, 4, 15, 0)
        self.assertFalse(await B.maybe_send_poll())
        self.assertEqual(self.fake.polls, [])

    async def test_catch_up_and_regular_scheduler_meet_in_same_minute(self):
        self.at(2026, 10, 3, 12, 20)
        real_send = self.fake.send_poll

        async def slow_send(*args, **kwargs):
            await asyncio.sleep(0)                                # настоящая отправка занимает время: вызовы реально перемежаются
            return await real_send(*args, **kwargs)
        self.fake.send_poll = slow_send
        results = await asyncio.gather(B.maybe_send_poll(), B.maybe_send_poll(), B.maybe_send_poll())
        self.assertEqual(sorted(results), [False, False, True])
        self.assertEqual(len(self.fake.polls), 1)

    async def test_manual_button_and_auto_same_saturday_single_poll(self):
        self.at(2026, 10, 3, 11, 30)
        await B.cmd_poll(owner_msg(), cmd("опрос", None))
        self.at(2026, 10, 3, 12, 0)
        self.assertFalse(await B.maybe_send_poll())
        self.assertEqual(len(self.fake.polls), 1)


class DuplicateProtection(PollBase):
    async def test_scheduler_called_twice_one_poll(self):
        self.at(2026, 10, 3, 12, 0)
        self.assertTrue(await B.maybe_send_poll())
        self.assertFalse(await B.maybe_send_poll())
        self.assertEqual(len(self.fake.polls), 1)

    async def test_state_write_failure_after_send_does_not_cause_poll_every_minute(self):
        real_save = B.save_json

        def failing(path, data):
            if path == B.POLL_STATE_FILE:
                raise OSError("диск")
            return real_save(path, data)
        with mock.patch.object(B, "save_json", failing):
            self.at(2026, 10, 3, 12, 0)
            self.assertTrue(await B.maybe_send_poll())
            for minute in (1, 2, 3, 10):
                self.at(2026, 10, 3, 12, minute)
                self.assertFalse(await B.maybe_send_poll())
        self.assertEqual(len(self.fake.polls), 1)

    async def test_marker_write_failure_after_send_does_not_cause_duplicates(self):
        with mock.patch.object(B, "LAST_POLL_FILE", os.path.join(self.tmp, "no_such_dir", "last_poll.txt")):
            B.save_json(B.POLL_STATE_FILE, {})                  # poll_state не успел сохраниться — полагаемся на память
            self.at(2026, 10, 3, 12, 0)
            with mock.patch.object(B, "save_json", side_effect=OSError("диск")):
                self.assertTrue(await B.maybe_send_poll())
                self.at(2026, 10, 3, 12, 1)
                self.assertFalse(await B.maybe_send_poll())
        self.assertEqual(len(self.fake.polls), 1)

    async def test_failed_send_is_retried_but_sends_once_overall(self):
        self.at(2026, 10, 3, 12, 0)
        with mock.patch.object(self.fake, "send_poll", side_effect=RuntimeError("telegram down")):
            self.assertFalse(await B.maybe_send_poll())
        self.at(2026, 10, 3, 12, 1)
        self.assertTrue(await B.maybe_send_poll())
        self.at(2026, 10, 3, 12, 2)
        self.assertFalse(await B.maybe_send_poll())
        self.assertEqual(len(self.fake.polls), 1)


class SchedulerLoop(PollBase):
    async def test_unexpected_exception_does_not_stop_scheduler_and_startup_logs_next_run(self):
        self.at(2026, 10, 3, 0, 20)
        calls = []
        outcomes = [RuntimeError("неожиданно"), False, True]

        async def flaky():
            calls.append(1)
            out = outcomes[len(calls) - 1]
            if isinstance(out, Exception):
                raise out
            return out
        ticks = []

        async def fake_sleep(seconds):
            ticks.append(seconds)
            if len(ticks) >= 3:
                raise asyncio.CancelledError()
        printed = []
        with mock.patch.object(B, "maybe_send_poll", flaky), mock.patch.object(B.asyncio, "sleep", fake_sleep), \
                mock.patch("builtins.print", lambda *a, **k: printed.append(" ".join(str(x) for x in a))):
            with self.assertRaises(asyncio.CancelledError):
                await B.poll_scheduler()
        self.assertEqual(len(calls), 3)                                 # после ошибки цикл продолжил работу
        self.assertEqual(ticks, [60, 60, 60])
        text = "\n".join(printed)
        self.assertIn("планировщик опроса запущен 03.10.2026 00:20", text)
        self.assertIn("Следующий автоопрос: суббота 03.10.2026 12:00 (Asia/Yekaterinburg) на понедельник 05.10.2026", text)
        self.assertIn("[POLL] ошибка планировщика: RuntimeError", text)

    async def test_startup_message_to_owner_has_next_poll_line(self):
        self.at(2026, 10, 3, 0, 20)
        await B.notify_started(await B.setup_menu())
        text = [t for c, t in self.fake.sent if c == OWNER][0]
        self.assertIn("🗳 Следующий автоопрос: суббота 03.10.2026 12:00 (Asia/Yekaterinburg) на понедельник 05.10.2026", text)


if __name__ == "__main__":
    unittest.main()
