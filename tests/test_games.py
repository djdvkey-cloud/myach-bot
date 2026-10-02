"""Нумерация состоявшихся игр, История игр, «сыграно игр: N», публикация статистики, редактирование игрока
с постоянным ID. Все данные — вымышленные; Telegram подменён заглушками."""
import datetime as real_dt
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_bot as TB  # noqa: E402
import test_routing as TR  # noqa: E402
from test_bot import Base, FakeMsg, FakeQuery, owner_msg, cmd, all_button_data, all_button_text, read_bytes, read_text, B, OWNER, CHAT  # noqa: E402

DATES = {1: "2026-09-07", 2: "2026-09-14", 3: "2026-09-21", 4: "2026-09-28"}


class GamesBase(Base):
    def setup_roster(self):
        """Вымышленные игроки со старой накопленной статистикой, как у реального «4 игры»."""
        self.write_stats({"Иванов И.": {"games": 4, "goals": 9, "assists": 5},
                          "Петров П.": {"games": 4, "goals": 3, "assists": 7},
                          "Сидоров С.": {"games": 3, "goals": 1, "assists": 2}})
        data = B.load_players()
        for name in ("Иванов И.", "Петров П.", "Сидоров С."):
            B.players_add(name, None) if name not in data["players"] else None

    def record(self, teams=None, scores=None, date="2026-10-05"):
        scores = scores or {"Иванов И.": (2, 1), "Петров П.": (0, 2), "Сидоров С.": (1, 0)}
        entries = [(n, g, a) for n, (g, a) in scores.items()]
        return B.record_match_full(entries, teams=teams or [["Иванов И.", "Сидоров С."], ["Петров П."]], date=date)

    async def stats_answer(self, owner=True):
        sent = []

        async def answer(text, reply_markup=None, **kw):
            sent.append((text, reply_markup))
        await B.send_stats(answer, owner=owner)
        return sent[-1]


class Numbering(GamesBase):
    def test_first_four_games_seeded_with_dates(self):
        games = B.load_games()["games"]
        self.assertEqual([(g["number"], g["date"]) for g in games], sorted(DATES.items()))
        self.assertTrue(all(g["teams"] is None and g["source"] == "historical" for g in games))
        self.assertEqual(B.played_games_count(), 4)
        self.assertEqual(B.next_game_number(B.load_games()), 5)

    def test_next_played_game_gets_5_then_6(self):
        self.setup_roster()
        _, g5 = self.record()
        self.assertEqual(g5["number"], 5)
        _, g6 = self.record(date="2026-10-12")
        self.assertEqual(g6["number"], 6)

    def test_cancelled_evening_gets_no_number(self):
        self.setup_roster()
        B.load_games()
        # вечер отменился: ничего не записывается → счётчик не меняется
        self.assertEqual(B.played_games_count(), 4)
        _, g = self.record()
        self.assertEqual(g["number"], 5)

    def test_number_never_changes_after_corrections(self):
        self.setup_roster()
        _, g5 = self.record()
        before = {g["number"]: g["date"] for g in B.load_games()["games"]}
        pid = B.load_players()["players"]["Иванов И."]["pid"]
        B.players_rename(pid, "Иванов Иван")
        self.write_stats({"Иванов Иван": {"games": 9, "goals": 1, "assists": 1}})       # правка статистики
        self.assertEqual({g["number"]: g["date"] for g in B.load_games()["games"]}, before)

    async def test_text_match_command_also_numbers_game(self):
        self.setup_roster()
        m = owner_msg()
        await B.cmd_match(m, cmd("матч", "Иванов И. 1+0, Петров П."))
        self.assertIn("Записал игру (2 чел.)", m.answers[-1][0])
        self.assertIn("Игра №5 — ", m.answers[-1][0])

    async def test_undo_keeps_number_correction_reuses_it_next_evening_gets_6(self):
        self.setup_roster()
        # 1. Игра №5 записана (с ошибочными данными)
        wrong = {"Иванов И.": (9, 9), "Петров П.": (0, 0), "Сидоров С.": (0, 0)}
        _, g5 = self.record(scores=wrong, date="2026-10-05")
        self.assertEqual(g5["number"], 5)
        stats_with_wrong = self.read_stats()[str(CHAT)]["players"]["Иванов И."]
        self.assertEqual((stats_with_wrong["goals"], stats_with_wrong["games"]), (18, 5))
        # 2. Данные отменены (/отменить)
        m = owner_msg()
        await B.cmd_undo(m)
        self.assertIn("Игра №5 остаётся за этим вечером", m.answers[-1][0])
        self.assertIn("не освобождается", m.answers[-1][0])
        ivan = self.read_stats()[str(CHAT)]["players"]["Иванов И."]
        self.assertEqual((ivan["goals"], ivan["games"]), (9, 4))                     # статистика откатилась
        games = B.load_games()
        g = [x for x in games["games"] if x["number"] == 5][0]
        self.assertEqual((g["status"], g["date"], g["teams"]), ("reverted", "2026-10-05", None))
        self.assertIsNotNone(g["teams_before_undo"])                                   # прежние данные не потеряны (аудит)
        self.assertEqual(B.played_games_count(), 5)                                    # номер за этим вечером сохранён
        # 3. Повторная запись исправленных данных этого же вечера — снова Игра №5
        _, again = self.record(scores={"Иванов И.": (2, 1), "Петров П.": (0, 2), "Сидоров С.": (1, 0)}, date="2026-10-06")
        self.assertEqual(again["number"], 5)
        self.assertEqual(again["date"], "2026-10-05")                                  # дата вечера прежняя
        self.assertNotIn("status", again)
        games = B.load_games()
        self.assertEqual(sorted(x["number"] for x in games["games"]), [1, 2, 3, 4, 5])  # дубля игры нет
        ivan = self.read_stats()[str(CHAT)]["players"]["Иванов И."]
        self.assertEqual((ivan["goals"], ivan["games"]), (11, 5))                      # ровно одна запись игры №5
        # 4. Следующий новый состоявшийся вечер — Игра №6
        _, g6 = self.record(date="2026-10-12")
        self.assertEqual(g6["number"], 6)
        # 5. №5 никогда не переиспользуется
        self.assertEqual([x["date"] for x in B.load_games()["games"] if x["number"] == 5], ["2026-10-05"])

    async def test_number_not_reused_by_a_different_evening_after_undo(self):
        self.setup_roster()
        _, g5 = self.record(date="2026-10-05")
        await B.cmd_undo(owner_msg())                          # данные игры №5 отменены и так и не введены заново
        # через неделю — другой футбольный вечер: он обязан получить №6, а не вернуть №5
        _, other = self.record(date="2026-10-12")
        self.assertEqual(other["number"], 6)
        games = B.load_games()["games"]
        five = [x for x in games if x["number"] == 5][0]
        self.assertEqual((five["status"], five["date"]), ("reverted", "2026-10-05"))   # №5 остаётся за вечером 05.10
        self.assertEqual(B.next_game_number(B.load_games()), 7)
        numbers = [x["number"] for x in games]
        self.assertEqual(len(numbers), len(set(numbers)))

    async def test_reentry_window_is_shorter_than_a_week(self):
        self.setup_roster()
        self.record(date="2026-10-05")
        await B.cmd_undo(owner_msg())
        _, same_week = self.record(date="2026-10-08")          # 3 дня — исправление того же вечера
        self.assertEqual(same_week["number"], 5)
        await B.cmd_undo(owner_msg())
        _, next_week = self.record(date="2026-10-12")          # 7 дней — новый вечер
        self.assertEqual(next_week["number"], 6)

    async def test_undo_twice_and_historical_games_untouched(self):
        self.setup_roster()
        self.record()
        m = owner_msg()
        await B.cmd_undo(m)
        await B.cmd_undo(m)
        self.assertIn("Нечего отменять", m.answers[-1][0])
        self.assertEqual(B.played_games_count(), 5)
        # только игры №1–4 и данные «последней игры» в статистике: историческая игра остаётся без изменений
        games = B.load_games()
        for g in games["games"]:
            if g["number"] <= 4:
                self.assertIsNone(g["teams"])
                self.assertNotIn("status", g)
        self.write_stats({"Иванов И.": {"games": 4, "goals": 1, "assists": 0}}, last=[["Иванов И.", 1, 0]])
        games = B.load_games()
        games["games"] = [g for g in games["games"] if g["number"] <= 4]
        B.save_games(games)
        m2 = owner_msg()
        await B.cmd_undo(m2)
        self.assertNotIn("остаётся за этим вечером", m2.answers[-1][0])
        self.assertEqual(B.played_games_count(), 4)

    async def test_buttons_flow_can_be_reentered_after_undo_with_same_number(self):
        self.setup_roster()
        g = B.new_game([["Иванов И.", "Сидоров С."], ["Петров П."]])
        B.save_game(g)
        await B.cb_panel(FakeQuery("m:result"))
        await B.cb_result(FakeQuery("rs:g+:0"))

        async def confirm():
            q = FakeQuery("rs:prev")
            await B.cb_result(q)
            ok = [d for d in all_button_data(q.message.edits[-1][1]) if d.startswith("rs:ok")][0]
            q = FakeQuery(ok)
            await B.cb_result(q)
            return q.message.edits[-1][0]

        first = await confirm()
        self.assertIn("Игра №5", first)
        await B.cmd_undo(owner_msg())
        self.assertFalse(B.load_game()["result_recorded"])                              # можно вводить заново той же кнопкой
        await B.cb_panel(FakeQuery("m:result"))
        await B.cb_result(FakeQuery("rs:g+:1"))
        second = await confirm()
        self.assertIn("Игра №5", second)
        self.assertEqual(B.played_games_count(), 5)

    async def test_history_marks_reverted_game_and_keeps_number(self):
        self.setup_roster()
        self.record()
        await B.cmd_undo(owner_msg())
        text, markup = B.history_list_view(B.load_games(), 0)
        self.assertIn("Игра №5 — 05.10.2026 ⚠️", text)
        q = FakeQuery("gh:g:5")
        await B.cb_history(q)
        self.assertIn("Данные этой игры отменены", q.message.edits[-1][0])
        self.assertIn("Игра №5", q.message.edits[-1][0])

    def test_legacy_annulled_game_comes_back_and_number_not_reused(self):
        """Файл версии 2026-10-02: игра №5 лежала в annulled — теперь номер сохранён за ней."""
        games = B.load_games()
        games["annulled"].append({"number": 5, "date": "2026-10-05", "teams": [[{"name": "А", "pid": None, "goals": 1, "assists": 0}]],
                                  "split": True, "source": "result", "annulled_at": "2026-10-06T10:00:00"})
        B.save_json(B.GAMES_FILE, games)
        loaded = B.load_games()
        five = [g for g in loaded["games"] if g["number"] == 5][0]
        self.assertEqual((five["status"], loaded["annulled"]), ("reverted", []))
        self.assertEqual(B.next_game_number(loaded), 6)
        self.assertEqual(B.next_game_number({"games": [], "annulled": [{"number": 7}]}), 8)

    def test_game_after_midnight_belongs_to_previous_evening(self):
        self.set_now(real_dt.datetime(2026, 10, 13, 0, 40))
        self.assertEqual(B.game_date_now(), "2026-10-12")
        self.set_now(real_dt.datetime(2026, 10, 12, 23, 10))
        self.assertEqual(B.game_date_now(), "2026-10-12")

    def test_corrupted_games_file_blocks_recording_and_leaves_stats(self):
        self.setup_roster()
        B.load_games()
        with open(B.GAMES_FILE, "w") as f:
            f.write("{broken")
        stats_before = read_bytes(B.STATS_FILE)
        with self.assertRaises(B.DataCorrupted):
            self.record()
        self.assertEqual(read_bytes(B.STATS_FILE), stats_before)
        self.assertEqual(read_text(B.GAMES_FILE), "{broken")

    def test_games_write_failure_rolls_back_stats(self):
        self.setup_roster()
        B.load_games()
        stats_before = read_bytes(B.STATS_FILE)
        with mock.patch.object(B, "save_games", side_effect=OSError("диск")):
            with self.assertRaises(OSError):
                self.record()
        self.assertEqual(read_bytes(B.STATS_FILE), stats_before)
        self.assertEqual(B.played_games_count(), 4)

    def test_missing_games_file_with_backups_is_error_not_reseed(self):
        B.load_games()
        games = B.load_games()
        games["games"].append({"number": 5, "date": "2026-10-05", "teams": None, "source": "text"})
        B.save_games(games)
        B.save_games(games)
        os.unlink(B.GAMES_FILE)
        with self.assertRaises(B.DataCorrupted):
            B.load_games()


class StatsHeader(GamesBase):
    async def test_header_shows_4_then_5_cancel_does_not_increase(self):
        self.setup_roster()
        text, _ = await self.stats_answer()
        self.assertTrue(text.startswith("📊 Статистика (сыграно игр: 4)"), text[:60])
        # ввод результата отменён → число то же
        g = B.new_game([["Иванов И.", "Сидоров С."], ["Петров П."]])
        B.save_game(g)
        await B.cb_panel(FakeQuery("m:result"))
        await B.cb_result(FakeQuery("rs:g+:0"))
        await B.cb_result(FakeQuery("rs:cancel"))
        text, _ = await self.stats_answer()
        self.assertIn("(сыграно игр: 4)", text.splitlines()[0])
        # запись результата → 5
        await B.cb_panel(FakeQuery("m:result"))
        await B.cb_result(FakeQuery("rs:g+:0"))
        q = FakeQuery("rs:prev")
        await B.cb_result(q)
        ok = [d for d in all_button_data(q.message.edits[-1][1]) if d.startswith("rs:ok")][0]
        q = FakeQuery(ok)
        await B.cb_result(q)
        self.assertIn("Игра №5 — ", q.message.edits[-1][0])
        text, _ = await self.stats_answer()
        self.assertIn("(сыграно игр: 5)", text.splitlines()[0])

    async def test_number_comes_from_games_not_from_player_rows(self):
        self.write_stats({"Один": {"games": 40, "goals": 1, "assists": 1}})
        text, _ = await self.stats_answer()
        self.assertIn("(сыграно игр: 4)", text.splitlines()[0])
        self.assertIn("И:40", text)                      # колонка игр игрока не тронута

    async def test_player_rows_format_unchanged(self):
        self.setup_roster()
        text, _ = await self.stats_answer()
        self.assertIn("Иванов И. — И:4 Г:9 П:5 О:23 К:5.75", text)
        self.assertIn("И — игры, Г — голы", text)

    async def test_group_stats_same_header_no_button(self):
        self.setup_roster()
        g = FakeMsg(CHAT, "group", 555)
        await B.cmd_stats(g)
        self.assertIn("(сыграно игр: 4)", g.answers[-1][0])
        self.assertIsNone(g.answers[-1][1])


class History(GamesBase):
    async def test_list_only_played_games_latest_first_with_dates(self):
        self.setup_roster()
        self.record()
        q = FakeQuery("m:history")
        await B.cb_panel(q)
        text, markup = q.message.edits[-1]
        self.assertEqual([t for t in all_button_text(markup) if t.startswith("Игра")],
                         ["Игра №5 — 05.10.2026", "Игра №4 — 28.09.2026", "Игра №3 — 21.09.2026",
                          "Игра №2 — 14.09.2026", "Игра №1 — 07.09.2026"])
        self.assertIn("gh:g:5", all_button_data(markup))

    async def test_game_view_shows_teams_and_per_game_goals_assists_only(self):
        self.setup_roster()
        self.record(teams=[["Иванов И.", "Сидоров С."], ["Петров П."]],
                    scores={"Иванов И.": (4, 2), "Сидоров С.": (0, 1), "Петров П.": (1, 3)})
        q = FakeQuery("gh:g:5")
        await B.cb_history(q)
        text = q.message.edits[-1][0]
        self.assertTrue(text.startswith("⚽ Игра №5 — 05.10.2026"))
        self.assertIn("⚪ Команда 1", text)
        self.assertIn("⚫ Команда 2", text)
        self.assertIn("Иванов И. — ⚽ 4 · 🎯 2", text)
        self.assertIn("Сидоров С. — ⚽ 0 · 🎯 1", text)
        self.assertIn("Петров П. — ⚽ 1 · 🎯 3", text)
        self.assertLess(text.index("Команда 1"), text.index("Иванов И."))
        self.assertLess(text.index("Иванов И."), text.index("Команда 2"))
        self.assertNotIn("И:", text)                                   # нет накопительной статистики
        self.assertNotRegex(text, r"\d+\s*[:–-]\s*\d+")               # нет счёта матчей

    async def test_per_game_numbers_not_mixed_between_games(self):
        self.setup_roster()
        self.record(scores={"Иванов И.": (4, 0), "Петров П.": (0, 0), "Сидоров С.": (0, 0)}, date="2026-10-05")
        self.record(scores={"Иванов И.": (1, 1), "Петров П.": (0, 0), "Сидоров С.": (0, 0)}, date="2026-10-12")
        q5, q6 = FakeQuery("gh:g:5"), FakeQuery("gh:g:6")
        await B.cb_history(q5)
        await B.cb_history(q6)
        self.assertIn("Иванов И. — ⚽ 4 · 🎯 0", q5.message.edits[-1][0])
        self.assertIn("Иванов И. — ⚽ 1 · 🎯 1", q6.message.edits[-1][0])
        self.assertIn("Игра №6 — 12.10.2026", q6.message.edits[-1][0])

    async def test_historical_game_has_number_date_and_honest_note(self):
        q = FakeQuery("gh:g:4")
        await B.cb_history(q)
        text = q.message.edits[-1][0]
        self.assertTrue(text.startswith("⚽ Игра №4 — 28.09.2026"))
        self.assertIn("не сохранялись", text)
        self.assertNotIn("Команда", text)

    async def test_text_match_game_listed_without_split(self):
        self.setup_roster()
        await B.cmd_match(owner_msg(), cmd("матч", "Иванов И. 2+1, Петров П."))
        q = FakeQuery("gh:g:5")
        await B.cb_history(q)
        text = q.message.edits[-1][0]
        self.assertIn("деление на команды при записи не указывалось", text)
        self.assertIn("Иванов И. — ⚽ 2 · 🎯 1", text)

    async def test_history_is_owner_private_only(self):
        for q in (FakeQuery("gh:l:0", user_id=555), FakeQuery("gh:g:4", chat_type="group", chat_id=CHAT)):
            await B.cb_history(q)
            self.assertEqual(q.answered[-1], ("Недоступно", True))
            self.assertEqual(q.message.edits, [])
        for m in (FakeMsg(CHAT, "group", 555), FakeMsg(555, "private", 555), FakeMsg(CHAT, "group", OWNER)):
            await B.cmd_history(m)
            self.assertEqual(m.answers, [])
        m = owner_msg()
        await B.cmd_history(m)
        self.assertIn("Игра №1 — 07.09.2026", m.answers[-1][0])

    async def test_history_paging(self):
        games = B.load_games()
        for n in range(5, 26):
            games["games"].append({"number": n, "date": "2026-10-05", "teams": None, "source": "text"})
        B.save_games(games)
        text, markup = B.history_list_view(B.load_games(), 0)
        self.assertEqual(len([t for t in all_button_text(markup) if t.startswith("Игра")]), 10)
        self.assertIn("gh:l:1", all_button_data(markup))
        self.assertIn("Игра №25", text)

    async def test_panel_has_history_button(self):
        m = owner_msg()
        await B.cmd_menu(m)
        self.assertIn("m:history", all_button_data(m.answers[-1][1]))


class PublishStats(GamesBase):
    async def press(self, data, **kw):
        q = FakeQuery(data, **kw)
        await B.cb_stats_publish(q)
        return q

    async def test_private_stats_has_publish_button_and_publishes_actual_stats(self):
        self.setup_roster()
        text, markup = await self.stats_answer()
        data = [d for d in all_button_data(markup) if d.startswith("sp:")]
        self.assertEqual(len(data), 1)
        self.assertIn("📢 Опубликовать в общий чат", all_button_text(markup))
        q = await self.press(data[0])
        self.assertEqual(self.fake.sent, [(CHAT, text)])
        self.assertIn("(сыграно игр: 4)", self.fake.sent[0][1])
        self.assertEqual(q.answered[-1], ("Опубликовано", False))
        self.assertIn("✅ Опубликовано в общем чате", all_button_text(q.message.markup_edits[-1]))

    async def test_second_press_does_not_duplicate_and_state_ok(self):
        self.setup_roster()
        _, markup = await self.stats_answer()
        data = [d for d in all_button_data(markup) if d.startswith("sp:")][0]
        await self.press(data)
        q2 = await self.press(data)
        self.assertEqual(len(self.fake.sent), 1)
        self.assertEqual(q2.answered[-1][0], "Уже опубликовано")
        _, markup2 = await self.stats_answer()                     # снова открыли ту же статистику → кнопка уже «опубликовано»
        self.assertIn("noop", all_button_data(markup2))

    async def test_changed_stats_require_reopen_and_can_be_published_again(self):
        self.setup_roster()
        _, markup = await self.stats_answer()
        old = [d for d in all_button_data(markup) if d.startswith("sp:")][0]
        self.record()                                               # статистика изменилась (игра №5)
        q = await self.press(old)
        self.assertEqual(self.fake.sent, [])
        self.assertTrue(q.answered[-1][1])
        text, markup = await self.stats_answer()
        new = [d for d in all_button_data(markup) if d.startswith("sp:")][0]
        self.assertNotEqual(new, old)
        await self.press(new)
        self.assertIn("(сыграно игр: 5)", self.fake.sent[0][1])

    async def test_failed_send_keeps_button_for_retry(self):
        self.setup_roster()
        _, markup = await self.stats_answer()
        data = [d for d in all_button_data(markup) if d.startswith("sp:")][0]
        self.fake.fail_send = True
        q = await self.press(data)
        self.assertTrue(q.answered[-1][1])
        self.assertEqual(q.message.markup_edits, [])
        self.fake.fail_send = False
        await self.press(data)
        self.assertEqual(len(self.fake.sent), 1)

    async def test_only_owner_in_private_can_publish(self):
        self.setup_roster()
        _, markup = await self.stats_answer()
        data = [d for d in all_button_data(markup) if d.startswith("sp:")][0]
        for kw in ({"user_id": 555}, {"chat_type": "group", "chat_id": CHAT}):
            q = await self.press(data, **kw)
            self.assertEqual(q.answered[-1], ("Недоступно", True))
        self.assertEqual(self.fake.sent, [])

    async def test_empty_stats_no_button(self):
        sent = []

        async def answer(text, reply_markup=None, **kw):
            sent.append((text, reply_markup))
        await B.send_stats(answer, owner=True)
        self.assertIn("Статистики пока нет", sent[-1][0])
        self.assertIsNone(sent[-1][1])


class PlayerEdit(GamesBase):
    def prep(self):
        """Игрок с накопленной статистикой, участием в прошлых играх, голами и передачами."""
        self.write_stats({"Иванов И.": {"games": 4, "goals": 9, "assists": 5},
                          "Петров П.": {"games": 4, "goals": 3, "assists": 7}},
                         last=[["Иванов И.", 2, 1], ["Петров П.", 0, 2]])
        B.players_add("Иванов И.", "ivanov")
        B.players_add("Петров П.")
        self.record(teams=[["Иванов И."], ["Петров П."]], scores={"Иванов И.": (2, 1), "Петров П.": (0, 2)}, date="2026-10-05")
        self.record(teams=[["Петров П."], ["Иванов И."]], scores={"Иванов И.": (3, 0), "Петров П.": (1, 1)}, date="2026-10-12")
        return B.load_players()["players"]["Иванов И."]["pid"]

    async def edit_via_buttons(self, pid, field, text):
        q = FakeQuery(f"pe:{field}:{pid}")
        await B.cb_player_edit(q)
        m = owner_msg(text)
        await B.owner_text_input(m)
        return q, m

    async def test_rename_keeps_pid_stats_games_and_history(self):
        pid = self.prep()
        stats_before = self.read_stats()[str(CHAT)]["players"]["Иванов И."]
        games_before = [(g["number"], g["date"]) for g in B.load_games()["games"]]
        _, m = await self.edit_via_buttons(pid, "n", "Иванов Иван")
        self.assertIn("Иванов Иван", m.answers[-1][0])
        data = B.load_players()
        self.assertNotIn("Иванов И.", data["players"])
        self.assertEqual(data["players"]["Иванов Иван"]["pid"], pid)                   # internal ID прежний
        self.assertEqual(len([n for n in data["players"] if "Иванов" in n]), 1)         # дубля нет
        st = self.read_stats()[str(CHAT)]
        self.assertEqual(st["players"]["Иванов Иван"], stats_before)                    # общая статистика прежняя
        self.assertNotIn("Иванов И.", st["players"])
        self.assertEqual(len(st["players"]), 2)
        self.assertEqual(st["last"][0][0], "Иванов Иван")
        self.assertEqual([(g["number"], g["date"]) for g in B.load_games()["games"]], games_before)
        for n, goals, assists in ((5, 2, 1), (6, 3, 0)):                                # прошлые игры, голы и передачи на месте
            q = FakeQuery(f"gh:g:{n}")
            await B.cb_history(q)
            self.assertIn(f"Иванов Иван — ⚽ {goals} · 🎯 {assists}", q.message.edits[-1][0])
            self.assertNotIn("Иванов И.", q.message.edits[-1][0])
        stored = [p["name"] for g in B.load_games()["games"] if g["number"] in (5, 6) for team in g["teams"] for p in team
                  if p["pid"] == pid]
        self.assertEqual(stored, ["Иванов Иван", "Иванов Иван"])                        # и сохранённые имена в архиве обновлены
        self.assertTrue(all(any(p["pid"] == pid for team in g["teams"] for p in team)
                            for g in B.load_games()["games"] if g["number"] in (5, 6)))

    async def test_rename_updates_current_and_archived_squads(self):
        pid = self.prep()
        g = B.new_game([["Иванов И.", "Петров П."], ["Гость"]], guests=["Гость"])
        B.save_game(g)
        B.save_json(B.GAME_ARCHIVE_FILE, [B.new_game([["Иванов И."], ["Петров П."]])])
        await self.edit_via_buttons(pid, "n", "Иванов Иван")
        self.assertEqual(B.load_game()["teams"][0], ["Иванов Иван", "Петров П."])
        self.assertEqual(B.load_game()["initial"][0], ["Иванов Иван", "Петров П."])
        self.assertEqual(json.loads(read_text(B.GAME_ARCHIVE_FILE))[0]["teams"][0], ["Иванов Иван"])

    async def test_rename_to_existing_name_is_refused_and_changes_nothing(self):
        pid = self.prep()
        snapshot = {p: read_bytes(p) for p in (B.PLAYERS_FILE, B.STATS_FILE, B.GAMES_FILE)}
        _, m = await self.edit_via_buttons(pid, "n", "петров п.")
        self.assertIn("уже есть", m.answers[-1][0])
        self.assertEqual(snapshot, {p: read_bytes(p) for p in snapshot})

    async def test_rename_to_name_of_registered_player_without_stats_is_refused(self):
        pid = self.prep()
        B.players_add("Новичок Н.")
        before = read_bytes(B.PLAYERS_FILE)
        _, m = await self.edit_via_buttons(pid, "n", "Новичок Н.")
        self.assertIn("уже есть в списке", m.answers[-1][0])
        self.assertEqual(before, read_bytes(B.PLAYERS_FILE))

    async def test_rename_failure_midway_restores_everything(self):
        pid = self.prep()
        snapshot = {p: read_bytes(p) for p in (B.PLAYERS_FILE, B.STATS_FILE, B.GAMES_FILE)}
        with mock.patch.object(B, "save_players", side_effect=OSError("диск")):
            with self.assertRaises(OSError):
                B.players_rename(pid, "Иванов Иван")
        self.assertEqual(snapshot, {p: read_bytes(p) for p in snapshot})
        self.assertEqual(B.player_by_pid(B.load_players(), pid)[0], "Иванов И.")

    def test_case_and_yo_change_is_same_person(self):
        self.write_stats({"Семенов Т.": {"games": 2, "goals": 1, "assists": 1}})
        B.players_add("Семенов Т.")
        pid = B.load_players()["players"]["Семенов Т."]["pid"]
        B.players_rename(pid, "Семёнов Т.")
        self.assertEqual(list(self.read_stats()[str(CHAT)]["players"]), ["Семёнов Т."])
        self.assertEqual(B.load_players()["players"]["Семёнов Т."]["pid"], pid)

    async def test_username_and_alias_edit_keep_everything(self):
        pid = self.prep()
        stats_before = read_bytes(B.STATS_FILE)
        games_before = read_bytes(B.GAMES_FILE)
        _, m = await self.edit_via_buttons(pid, "u", "@ivanov2")
        self.assertEqual(B.load_players()["players"]["Иванов И."]["usernames"], ["ivanov2"])
        _, m = await self.edit_via_buttons(pid, "a", "Иван Иванов, Ваня")
        self.assertEqual(B.load_players()["players"]["Иванов И."]["aliases"], ["иван иванов", "ваня"])
        self.assertEqual(B.load_players()["players"]["Иванов И."]["pid"], pid)
        self.assertEqual(read_bytes(B.STATS_FILE), stats_before)
        self.assertEqual(read_bytes(B.GAMES_FILE), games_before)
        _, m = await self.edit_via_buttons(pid, "u", "-")
        self.assertEqual(B.load_players()["players"]["Иванов И."]["usernames"], [])

    async def test_username_taken_by_other_player_is_refused(self):
        pid = self.prep()
        petrov = B.load_players()["players"]["Петров П."]["pid"]
        await self.edit_via_buttons(petrov, "u", "@petrov1")
        _, m = await self.edit_via_buttons(pid, "u", "@PETROV1")
        self.assertIn("уже привязан", m.answers[-1][0])
        self.assertEqual(B.load_players()["players"]["Иванов И."]["usernames"], ["ivanov"])

    async def test_edit_screens_show_format_hints_and_card(self):
        pid = self.prep()
        q = FakeQuery("pl:ed")
        await B.cb_players(q)
        self.assertIn(f"pe:c:{pid}", all_button_data(q.message.edits[-1][1]))
        q = FakeQuery(f"pe:c:{pid}")
        await B.cb_player_edit(q)
        card = q.message.edits[-1][0]
        self.assertIn("Иванов И.", card)
        self.assertIn("Статистика: игр 6, голов 14, передач 6", card)
        self.assertEqual(sorted(d for d in all_button_data(q.message.edits[-1][1]) if d.startswith("pe:")),
                         sorted([f"pe:n:{pid}", f"pe:u:{pid}", f"pe:a:{pid}"]))
        for field, hint in (("n", "Например: Иванов П."), ("u", "Например: @ivanov"), ("a", "Например: Миша В.")):
            q = FakeQuery(f"pe:{field}:{pid}")
            await B.cb_player_edit(q)
            self.assertIn(hint, q.message.edits[-1][0])
            self.assertEqual(B.AWAITING[OWNER]["pid"], pid)

    async def test_edit_owner_private_only(self):
        pid = self.prep()
        for q in (FakeQuery(f"pe:n:{pid}", user_id=555), FakeQuery(f"pe:n:{pid}", chat_type="group", chat_id=CHAT)):
            await B.cb_player_edit(q)
            self.assertEqual(q.answered[-1], ("Недоступно", True))
        self.assertNotIn(OWNER, B.AWAITING)

    def test_legacy_players_file_gets_pids_without_changing_data(self):
        legacy = {"version": 1, "players": {"Иванов И.": {"usernames": ["ivanov"], "ids": [5], "aliases": ["ваня"]},
                                            "Петров П.": {"usernames": [], "ids": [], "aliases": []}}}
        B.save_json(B.PLAYERS_FILE, legacy)
        stats = {str(CHAT): {"players": {"Иванов И.": {"games": 4, "goals": 9, "assists": 5}}, "last": []}}
        B.save_json(B.STATS_FILE, stats)
        stats_bytes = read_bytes(B.STATS_FILE)
        data = B.load_players()
        self.assertEqual(sorted(r["pid"] for r in data["players"].values()), [1, 2])
        self.assertEqual(data["players"]["Иванов И."]["usernames"], ["ivanov"])
        self.assertEqual(data["players"]["Иванов И."]["ids"], [5])
        self.assertEqual(read_bytes(B.STATS_FILE), stats_bytes)
        pids = {n: r["pid"] for n, r in B.load_players()["players"].items()}            # повторное чтение стабильно
        self.assertEqual(pids, {n: r["pid"] for n, r in data["players"].items()})
        B.players_add("Новичок Н.")
        self.assertEqual(B.load_players()["players"]["Новичок Н."]["pid"], 3)

    def test_removed_player_pid_not_reused(self):
        B.players_add("А А.")
        B.players_add("Б Б.")
        pid_b = B.load_players()["players"]["Б Б."]["pid"]
        B.players_remove("Б Б.")
        B.players_add("В В.")
        self.assertGreater(B.load_players()["players"]["В В."]["pid"], pid_b)


class HelpAndMisc(GamesBase):
    def test_help_covers_new_features_and_commands(self):
        for needle in ("/история", "Игра №5", "сыграно игр: N", "Опубликовать в общий чат", "Редактировать игрока",
                       "постоянный внутренний номер", "games.json"):
            self.assertIn(needle, B.HELP_ALL_TEXT)
        self.assertIn("history", B.HELP_SECTIONS)
        self.assertTrue(all(len(t) < 3800 for t in B.HELP_SECTIONS.values()))
        self.assertIn("history", [c for c, _ in B.OWNER_MENU_COMMANDS])

    def test_startup_report_has_games_line(self):
        lines = B.storage_report()
        self.assertTrue(any("Игры: сыграно 4, последняя Игра №4 — 28.09.2026" in l for l in lines))

    def test_reminder_logic_and_poll_untouched(self):
        self.assertEqual(B.GOAL_POINTS, 2.0)
        self.assertEqual(B.ASSIST_POINTS, 1.0)
        self.assertEqual(B.GAME_TOTAL_RUB, 4500)


class RoutedThroughDispatcher(unittest.IsolatedAsyncioTestCase):
    setUp = TR.RoutingTests.setUp
    tearDown = TR.RoutingTests.tearDown
    feed = TR.RoutingTests.feed
    sent = TR.RoutingTests.sent

    async def test_history_stats_publish_and_edit_routes(self):
        B.save_json(B.STATS_FILE, {str(B.CHAT_ID): {"players": {"Иванов И.": {"games": 4, "goals": 1, "assists": 1}}, "last": []}})
        B.players_add("Иванов И.")
        await self.feed(TR.text_update(OWNER, OWNER, "private", "/история"))
        self.assertIn("Игра №4 — 28.09.2026", self.sent()[0].text)
        await self.feed(TR.cb_update(OWNER, "gh:g:3"))
        self.assertIn("Игра №3 — 21.09.2026", self.sent("EditMessageText")[0].text)
        await self.feed(TR.text_update(OWNER, OWNER, "private", "/статистика"))
        out = self.sent()[0]
        self.assertIn("(сыграно игр: 4)", out.text)
        token = [b.callback_data for row in out.reply_markup.inline_keyboard for b in row if (b.callback_data or "").startswith("sp:")][0]
        await self.feed(TR.cb_update(OWNER, token))
        to_chat = [c for c in self.sent() if c.chat_id == B.CHAT_ID]
        self.assertEqual(len(to_chat), 1)
        self.assertIn("(сыграно игр: 4)", to_chat[0].text)
        # повтор не дублирует
        await self.feed(TR.cb_update(OWNER, token))
        self.assertEqual([c for c in self.sent() if c.chat_id == B.CHAT_ID], [])
        # редактирование игрока через кнопки и текст
        pid = B.load_players()["players"]["Иванов И."]["pid"]
        await self.feed(TR.cb_update(OWNER, f"pe:n:{pid}"))
        await self.feed(TR.text_update(OWNER, OWNER, "private", "Иванов Иван"))
        self.assertIn("Иванов Иван", B.load_players()["players"])
        self.assertIn("Иванов Иван", B.load_json(B.STATS_FILE)[str(B.CHAT_ID)]["players"])

    async def test_group_and_strangers_cannot_use_new_features(self):
        B.save_json(B.STATS_FILE, {str(B.CHAT_ID): {"players": {"Иванов И.": {"games": 4, "goals": 1, "assists": 1}}, "last": []}})
        for upd in (TR.text_update(555, CHAT, "group", "/история"), TR.text_update(OWNER, CHAT, "group", "/history"),
                    TR.text_update(555, 555, "private", "/история")):
            await self.feed(upd)
            self.assertEqual(self.session.calls, [])
        for data in ("gh:l:0", "gh:g:1", "sp:pub:abc", "pe:n:1", "pl:ed"):
            await self.feed(TR.cb_update(555, data))
            self.assertTrue(all(type(c).__name__ == "AnswerCallbackQuery" for c in self.session.calls), data)
            await self.feed(TR.cb_update(OWNER, data, chat_type="group", chat_id=CHAT))
            self.assertTrue(all(type(c).__name__ == "AnswerCallbackQuery" for c in self.session.calls), data)
        await self.feed(TR.text_update(555, CHAT, "group", "/статистика"))
        text = self.sent()[0].text
        self.assertIn("(сыграно игр: 4)", text)
        self.assertIsNone(self.sent()[0].reply_markup)                        # в общем чате кнопок нет
        self.assertEqual(B.load_games()["annulled"], [])


if __name__ == "__main__":
    unittest.main()
