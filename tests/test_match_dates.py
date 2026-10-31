"""Дата принадлежит игровому вечеру, а не моменту ввода результата."""
import copy
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_bot import Base, FakeQuery, owner_msg, cmd, all_button_data, read_bytes, B, CHAT  # noqa: E402


class MatchDateBase(Base):
    def setUp(self):
        super().setUp()
        self.write_stats({"Иванов И.": {"games": 4, "goals": 3, "assists": 2},
                          "Петров П.": {"games": 4, "goals": 1, "assists": 3}})
        B.players_add("Иванов И.")
        B.players_add("Петров П.")

    def game(self, date="2026-10-26"):
        g = B.new_game([["Иванов И."], ["Петров П."]], match_date=date)
        B.save_game(g)
        B.result_init(g)
        g["result"]["g"]["Иванов И."] = 2
        g["result"]["a"]["Петров П."] = 1
        B.save_game(g)
        return g


class RecordingDate(MatchDateBase):
    async def test_split_copies_date_from_that_poll_into_game(self):
        B.save_json(B.POLL_STATE_FILE, {"monday": "2026-10-26", "voters": {
            "1": {"player": "Иванов И."}, "2": {"player": "Петров П."}}})
        sent = []
        async def reply(text, reply_markup=None):
            sent.append((text, reply_markup))
        await B.do_split(reply)
        self.assertEqual(B.load_game()["match_date"], "2026-10-26")

    def test_low_level_new_game_requires_explicit_date(self):
        B.load_games()
        before = (read_bytes(B.STATS_FILE), read_bytes(B.GAMES_FILE))
        with self.assertRaisesRegex(ValueError, "Дата игры не указана"):
            B.record_match_full([("Иванов И.", 1, 0)], mode="new")
        self.assertEqual(before, (read_bytes(B.STATS_FILE), read_bytes(B.GAMES_FILE)))

    def test_match_date_survives_restart_file_roundtrip(self):
        self.game()
        self.assertEqual(B.load_game()["match_date"], "2026-10-26")

    async def test_preview_shows_exact_date_and_totals(self):
        self.game()
        q = FakeQuery("rs:prev")
        await B.cb_result(q)
        text, markup = q.message.edits[-1]
        self.assertIn("⚽ Игра №5", text)
        self.assertIn("Дата игры: 26.10.2026", text)
        self.assertIn("Участники: 2", text)
        self.assertIn("Голы: 2", text)
        self.assertIn("Передачи: 1", text)
        self.assertTrue(any(d == "rs:ok:" + B.load_game()["id"] + ":20261026" for d in all_button_data(markup)))

    async def test_late_entry_and_next_poll_do_not_change_match_date(self):
        self.game()
        B.save_json(B.POLL_STATE_FILE, {"monday": "2026-11-02", "voters": {}})
        q = FakeQuery("rs:prev")
        await B.cb_result(q)
        ok = next(d for d in all_button_data(q.message.edits[-1][1]) if d.startswith("rs:ok:"))
        await B.cb_result(FakeQuery(ok))
        game = B.load_games()["games"][-1]
        self.assertEqual(game["date"], "2026-10-26")

    async def test_old_confirmation_cannot_write_after_date_change(self):
        self.game()
        q = FakeQuery("rs:prev")
        await B.cb_result(q)
        old = next(d for d in all_button_data(q.message.edits[-1][1]) if d.startswith("rs:ok:"))
        g = B.load_game(); g["match_date"] = "2026-10-19"; B.save_game(g)
        stale = FakeQuery(old)
        await B.cb_result(stale)
        self.assertIn("устарело", stale.answered[-1][0])
        self.assertEqual(len(B.load_games()["games"]), 4)

    async def test_missing_legacy_date_requires_owner_input(self):
        self.game(None)
        q = FakeQuery("rs:prev")
        await B.cb_result(q)
        self.assertNotIn("rs:ok", " ".join(all_button_data(q.message.edits[-1][1])))
        self.assertIn("не указана", q.message.edits[-1][0])

    async def test_text_match_cannot_bypass_date(self):
        m = owner_msg()
        await B.cmd_match(m, cmd("матч", "Иванов И. 1+0"))
        self.assertIn("укажите дату", m.answers[-1][0])
        self.assertEqual(len(B.load_games()["games"]), 4)

    def test_duplicate_date_is_rejected_without_stats_change(self):
        B.record_match_full([("Иванов И.", 1, 0)], date="2026-10-26", mode="new")
        before = read_bytes(B.STATS_FILE)
        with self.assertRaisesRegex(ValueError, "уже записана"):
            B.record_match_full([("Петров П.", 1, 0)], date="2026-10-26", mode="new")
        self.assertEqual(before, read_bytes(B.STATS_FILE))


class ExistingGameDate(MatchDateBase):
    def setUp(self):
        super().setUp()
        B.record_match_full([("Иванов И.", 2, 1), ("Петров П.", 0, 2)],
                            teams=[["Иванов И."], ["Петров П."]], date="2026-10-31", mode="new")

    def test_correction_changes_only_date_and_creates_backup(self):
        before_game = copy.deepcopy(B.load_games()["games"][-1])
        before_stats = read_bytes(B.STATS_FILE)
        fixed = B.correct_game_date(5, "2026-10-31", "2026-10-26")
        self.assertEqual(fixed["date"], "2026-10-26")
        self.assertEqual({k: v for k, v in fixed.items() if k != "date"},
                         {k: v for k, v in before_game.items() if k != "date"})
        self.assertEqual(before_stats, read_bytes(B.STATS_FILE))
        self.assertTrue(B.list_backups("games"))

    def test_repeat_and_duplicate_corrections_are_safe(self):
        B.correct_game_date(5, "2026-10-31", "2026-10-26")
        with self.assertRaisesRegex(ValueError, "уже изменилась"):
            B.correct_game_date(5, "2026-10-31", "2026-10-26")
        B.record_match_full([("Петров П.", 0, 0)], date="2026-11-02", mode="new")
        with self.assertRaisesRegex(ValueError, "уже существует"):
            B.correct_game_date(5, "2026-10-26", "2026-11-02")

    def test_failed_post_write_verification_rolls_back(self):
        before = read_bytes(B.GAMES_FILE)
        loaded = B.load_games()
        with mock.patch.object(B, "load_games", side_effect=[loaded, {"games": []}]):
            with self.assertRaisesRegex(RuntimeError, "Проверка после изменения"):
                B.correct_game_date(5, "2026-10-31", "2026-10-26")
        self.assertEqual(read_bytes(B.GAMES_FILE), before)

    async def test_history_edit_is_owner_only(self):
        q = FakeQuery("gh:date:5", user_id=999)
        await B.cb_history(q)
        self.assertEqual(q.answered[-1], ("Недоступно", True))

    async def test_history_confirmation_changes_date_once(self):
        q = FakeQuery("gh:date:5")
        await B.cb_history(q)
        m = owner_msg("26.10.2026")
        await B.owner_text_input(m)
        confirm = next(d for d in all_button_data(m.answers[-1][1]) if d.startswith("gh:dateok:"))
        await B.cb_history(FakeQuery(confirm))
        self.assertEqual(B.load_games()["games"][-1]["date"], "2026-10-26")
        repeated = FakeQuery(confirm)
        await B.cb_history(repeated)
        self.assertTrue(repeated.answered[-1][1])


if __name__ == "__main__":
    unittest.main()
