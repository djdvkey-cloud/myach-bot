"""Исторический архив игр №1–4: заполнение без повторного начисления статистики, сверка, отображение."""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_bot import Base, FakeQuery, owner_msg, read_bytes, read_text, B, OWNER, CHAT  # noqa: E402
from test_games import GamesBase  # noqa: E402,F401  (только для общих помощников)


def totals_stats():
    """Общая статистика = ровно суммы по играм №1–4 (как в production после четырёх игр)."""
    return {name: {"games": g, "goals": gl, "assists": a} for name, g, gl, a in B.archive_expected_totals().values()}


class ArchiveBase(Base):
    def prep(self, stats=None, registry=("Чичин А.", "Антон", "Антон от Миши", "Рим", "Моргун А.", "Ковалёв М.")):
        self.write_stats(stats if stats is not None else totals_stats())
        for name in registry:
            if not B.find_key(name, B.load_players()["players"]):     # часть игроков уже есть в начальном списке реестра
                B.players_add(name)
        B.load_games()

    def game(self, n):
        return next(g for g in B.load_games()["games"] if g["number"] == n)

    async def view(self, n):
        q = FakeQuery(f"gh:g:{n}")
        await B.cb_history(q)
        return q.message.edits[-1][0]


class Reconciliation(ArchiveBase):
    def test_archive_totals_match_known_snapshot_and_expectations(self):
        totals = B.archive_expected_totals()
        self.assertEqual(len(totals), 20)
        self.assertEqual(totals[B.norm_key("Расчётов А.")][1:], [4, 22, 11])
        self.assertEqual(totals[B.norm_key("Ковалёв М.")][1:], [4, 15, 17])
        self.assertEqual(totals[B.norm_key("Рим")][1:], [1, 5, 2])
        self.assertEqual(totals[B.norm_key("Антон от Миши")][1:], [1, 0, 3])
        self.assertEqual(sum(t[1] for t in totals.values()), 14 + 11 + 12 + 15)           # участий по играм №1–4
        self.assertEqual(B.archive_discrepancies(totals_stats()), [])

    def test_ye_spelling_difference_is_not_a_discrepancy(self):
        stats = totals_stats()
        stats["Ковалев М."] = stats.pop("Ковалёв М.")
        self.assertEqual(B.archive_discrepancies(stats), [])

    def test_mismatch_is_reported_and_nothing_written(self):
        stats = totals_stats()
        stats["Рим"]["goals"] += 2                      # расхождение: у Рима в статистике на 2 гола больше
        stats["Тёмка"] = {"games": 1, "goals": 3, "assists": 7}      # и «старое» имя отдельным игроком
        del stats["Чичин А."]
        self.prep(stats)
        games_before, stats_before = read_bytes(B.GAMES_FILE), read_bytes(B.STATS_FILE)
        status, lines = B.fill_historical_archive()
        self.assertEqual(status, "mismatch")
        text = "\n".join(lines)
        self.assertIn("Рим: архив И1 Г5 П2 · статистика И1 Г7 П2 · разница И+0 Г+2 П+0", text)
        self.assertIn("Чичин А.: архив И2 Г3 П10", text)
        self.assertIn("— нет игрока", text)
        self.assertIn("Тёмка: в статистике есть", text)
        self.assertEqual(games_before, read_bytes(B.GAMES_FILE))
        self.assertEqual(stats_before, read_bytes(B.STATS_FILE))
        self.assertIsNone(self.game(2)["teams"])

    async def test_startup_notice_for_mismatch_and_for_success(self):
        stats = totals_stats()
        stats["Рим"]["assists"] -= 1
        self.prep(stats)
        await B.notify_archive()
        sent = [t for c, t in self.fake.sent if c == OWNER]
        self.assertEqual(len(sent), 1)
        self.assertIn("НЕ заполнен", sent[0])
        self.assertIn("Рим:", sent[0])
        self.assertIn("Нужно решение Дмитрия", sent[0])
        self.write_stats(totals_stats())                # после разбора статистика сошлась → при следующем запуске заполнится
        self.fake.sent.clear()
        await B.notify_archive()
        self.assertIn("Архив игр №1–4 заполнен", self.fake.sent[-1][1])
        self.fake.sent.clear()
        await B.notify_archive()                        # повторный запуск — тишина
        self.assertEqual(self.fake.sent, [])


class Filled(ArchiveBase):
    def setUp(self):
        super().setUp()
        self.prep()
        self.stats_before = read_bytes(B.STATS_FILE)
        self.players_before = read_bytes(B.PLAYERS_FILE)
        self.status = B.fill_historical_archive()

    def test_done_and_stats_identical(self):
        self.assertEqual(self.status, ("done", []))
        self.assertEqual(read_bytes(B.STATS_FILE), self.stats_before)               # stats.json не изменён ни на байт
        self.assertEqual(read_bytes(B.PLAYERS_FILE), self.players_before)            # новых игроков нет
        self.assertTrue(B.load_games()["history_filled"])

    def test_idempotent(self):
        before = read_bytes(B.GAMES_FILE)
        self.assertEqual(B.fill_historical_archive(), ("already", []))
        self.assertEqual(before, read_bytes(B.GAMES_FILE))

    def test_stats_not_incremented_header_and_next_game(self):
        stats = self.read_stats()[str(CHAT)]["players"]
        self.assertEqual(stats["Расчётов А."], {"games": 4, "goals": 22, "assists": 11})
        text, _ = B.stats_snapshot()
        self.assertTrue(text.startswith("📊 Статистика (сыграно игр: 4)"))
        self.assertEqual(B.played_games_count(), 4)
        self.assertEqual(B.next_game_number(B.load_games()), 5)
        _, g5 = B.record_match_full([("Расчётов А.", 1, 0)], date="2026-10-05")
        self.assertEqual(g5["number"], 5)

    async def test_game1_participants_without_teams(self):
        g = self.game(1)
        self.assertEqual((g["date"], g["split"], len(g["teams"]), len(g["teams"][0])), ("2026-09-07", False, 1, 14))
        text = await self.view(1)
        self.assertTrue(text.startswith("⚽ Игра №1 — 07.09.2026"))
        self.assertIn("Составы команд не сохранились", text)
        self.assertNotIn("не сохранялись (игра состоялась", text)
        self.assertNotIn("Команда", text)
        for line in ("Ковалёв М. — ⚽ 2 · 🎯 6", "Лунегов А. — ⚽ 4 · 🎯 2", "Азиз — ⚽ 1 · 🎯 0", "Калабин Д. — ⚽ 0 · 🎯 0",
                     "Фаррух — ⚽ 0 · 🎯 0", "Сикач И. — ⚽ 0 · 🎯 2"):
            self.assertIn(line, text)
        self.assertEqual(len([l for l in text.splitlines() if "⚽" in l and "🎯" in l]), 14)

    async def test_game2_chichin_white_team_no_temka(self):
        g = self.game(2)
        self.assertEqual((g["date"], len(g["teams"]), [len(t) for t in g["teams"]]), ("2026-09-14", 2, [5, 6]))
        text = await self.view(2)
        white, black = text.split("⚫ Команда 2")
        self.assertIn("⚪ Команда 1 (белые)", white)
        self.assertIn("Чичин А. — ⚽ 3 · 🎯 7", white)
        self.assertIn("Чичин А.", [p["name"] for p in g["teams"][0]])
        self.assertNotIn("Чичин А.", black)
        self.assertIn("Бессмертных С. — ⚽ 6 · 🎯 2", black)
        pid = B.load_players()["players"]["Чичин А."]["pid"]
        self.assertEqual([p["pid"] for p in g["teams"][0] if p["name"] == "Чичин А."], [pid])      # существующий pid
        for blob in (read_text(B.GAMES_FILE), read_text(B.PLAYERS_FILE), read_text(B.STATS_FILE)):
            self.assertNotIn("Тёмка", blob)

    async def test_game3_two_different_antons_no_mikhail_plus_one(self):
        g = self.game(3)
        names = [p["name"] for team in g["teams"] for p in team]
        self.assertIn("Антон", names)
        self.assertIn("Антон от Миши", names)
        self.assertEqual(len(names), len(set(names)))                                   # не объединены
        anton, anton_m = (next(p for team in g["teams"] for p in team if p["name"] == n) for n in ("Антон", "Антон от Миши"))
        self.assertEqual((anton["goals"], anton["assists"]), (7, 2))
        self.assertEqual((anton_m["goals"], anton_m["assists"]), (0, 3))
        self.assertNotEqual(anton["pid"], anton_m["pid"])
        text = await self.view(3)
        self.assertIn("Антон от Миши — ⚽ 0 · 🎯 3", text)
        self.assertIn("Антон — ⚽ 7 · 🎯 2", text)
        self.assertIn("Игра №3 — 21.09.2026", text)
        for blob in (read_text(B.GAMES_FILE), read_text(B.PLAYERS_FILE), read_text(B.STATS_FILE)):
            self.assertNotIn("Михаил+1", blob)

    async def test_game4_three_teams_rim_and_morgun(self):
        g = self.game(4)
        self.assertEqual((g["date"], [len(t) for t in g["teams"]]), ("2026-09-28", [5, 5, 5]))
        text = await self.view(4)
        for head in ("⚪ Команда 1 (белые)", "⚫ Команда 2 (чёрные)", "🔴 Команда 3 (красные)"):
            self.assertIn(head, text)
        rim = next(p for team in g["teams"] for p in team if p["name"] == "Рим")
        morgun = next(p for team in g["teams"] for p in team if p["name"] == "Моргун А.")
        self.assertEqual((rim["goals"], rim["assists"], morgun["goals"], morgun["assists"]), (5, 2, 4, 3))
        self.assertEqual(rim["pid"], B.load_players()["players"]["Рим"]["pid"])
        self.assertEqual(morgun["pid"], B.load_players()["players"]["Моргун А."]["pid"])
        self.assertIn("Рим — ⚽ 5 · 🎯 2", text)
        self.assertIn("Моргун А. — ⚽ 4 · 🎯 3", text)
        for blob in (read_text(B.GAMES_FILE), read_text(B.STATS_FILE)):
            self.assertNotIn("Серёга+1", blob)
            self.assertNotIn("Alexandr", blob)
        self.assertNotIn("Серёга+1", read_text(B.PLAYERS_FILE))
        # «Alexandr» есть в начальном списке реестра (из кода) — архив его не создаёт и не использует
        self.assertEqual(read_bytes(B.PLAYERS_FILE), self.players_before)

    def test_no_new_players_registered_and_pids_used_where_exist(self):
        registry = B.load_players()["players"]
        self.assertNotIn("Тёмка", registry)
        for game in B.load_games()["games"]:
            for team in game["teams"]:
                for p in team:
                    key = B.find_key(p["name"], registry)
                    self.assertEqual(p["pid"], registry[key]["pid"] if key else None, p["name"])

    async def test_all_four_games_listed_and_old_note_gone(self):
        q = FakeQuery("m:history")
        await B.cb_panel(q)
        text = q.message.edits[-1][0]
        for line in ("Игра №4 — 28.09.2026", "Игра №3 — 21.09.2026", "Игра №2 — 14.09.2026", "Игра №1 — 07.09.2026"):
            self.assertIn(line, text)
        for n in (1, 2, 3, 4):
            self.assertNotIn("не сохранялись", await self.view(n))

    def test_backup_made_and_each_game_has_required_fields(self):
        self.assertTrue(B.list_backups("stats"))
        self.assertTrue(B.list_backups("players"))
        for g in B.load_games()["games"]:
            self.assertTrue(all(k in g for k in ("number", "date", "teams", "split", "source")))
            self.assertTrue(all(set(p) >= {"name", "pid", "goals", "assists"} for t in g["teams"] for p in t))

    def test_unrelated_features_untouched(self):
        self.assertEqual(B.GAME_TOTAL_RUB, 4500)
        _, g5 = B.record_match_full([("Чичин А.", 1, 1)], date="2026-10-05")
        games = B.load_games()
        B.revert_last_game(games)
        B.save_games(games)
        self.assertEqual([g["status"] for g in B.load_games()["games"] if g.get("status")], ["reverted"])
        self.assertTrue(B.load_games()["history_filled"])


class SafetyAndEdgeCases(ArchiveBase):
    def test_write_failure_leaves_games_untouched(self):
        self.prep()
        before = (read_bytes(B.GAMES_FILE), read_bytes(B.STATS_FILE))
        with mock.patch.object(B, "save_games", side_effect=OSError("диск")):
            with self.assertRaises(OSError):
                B.fill_historical_archive()
        self.assertEqual(before, (read_bytes(B.GAMES_FILE), read_bytes(B.STATS_FILE)))
        self.assertEqual(B.fill_historical_archive()[0], "done")

    def test_failure_after_games_written_is_rolled_back(self):
        self.prep()
        before = read_bytes(B.GAMES_FILE)
        real_save = B.save_games

        def write_then_fail(data):
            real_save(data)
            raise OSError("сбой после записи")
        with mock.patch.object(B, "save_games", side_effect=write_then_fail):
            with self.assertRaises(OSError):
                B.fill_historical_archive()
        self.assertEqual(before, read_bytes(B.GAMES_FILE))
        self.assertFalse(B.load_games().get("history_filled"))

    def test_players_without_registry_entry_get_no_pid_and_no_new_player(self):
        self.prep(registry=())
        registry_before = read_bytes(B.PLAYERS_FILE) if os.path.exists(B.PLAYERS_FILE) else None
        self.assertEqual(B.fill_historical_archive()[0], "done")
        azis = next(p for t in self.game(2)["teams"] for p in t if p["name"] == "Азиз")
        self.assertIsNone(azis["pid"])                                                # гость без записи в реестре — pid нет
        registry = B.load_players()["players"]
        self.assertNotIn("Азиз", registry)
        self.assertNotIn("Тёмка", registry)
        self.assertNotIn("Рим", registry)

    def test_corrupted_stats_blocks_fill(self):
        self.prep()
        with open(B.STATS_FILE, "w") as f:
            f.write("{broken")
        with self.assertRaises(B.DataCorrupted):
            B.fill_historical_archive()
        self.assertIsNone(self.game(1)["teams"])

    def test_skipped_when_games_already_have_data(self):
        self.prep()
        games = B.load_games()
        games["games"][0]["teams"] = [[{"name": "Х", "pid": None, "goals": 0, "assists": 0}]]
        B.save_games(games)
        self.assertEqual(B.fill_historical_archive()[0], "skipped")

    def test_later_recorded_game_makes_stats_differ_so_archive_waits(self):
        self.prep()
        B.record_match_full([("Рим", 1, 0)], date="2026-10-05")                       # статистика уже включает игру №5
        status, lines = B.fill_historical_archive()
        self.assertEqual(status, "mismatch")
        self.assertTrue(any(l.startswith("Рим:") for l in lines))


class AlexandrIsMorgun(ArchiveBase):
    """Решение Дмитрия: Alexandr = Моргун А. — один игрок; pid Alexandr сохраняется, статистика И1 Г4 П3 остаётся."""

    def prod_like(self, with_duplicate=True):
        stats = totals_stats()
        stats["Alexandr"] = stats.pop("Моргун А.")            # как в production: статистика под именем Alexandr
        self.write_stats(stats)
        data = B.load_players()
        # Состояние production до закрепления: отдельная запись Alexandr (@Footmor, со статистикой) и, возможно, ещё одна запись
        # «Моргун А.» от прежнего начального списка (с другим username).
        legacy_pid = data["next_pid"]
        data["players"]["Alexandr"] = {"usernames": ["Footmor"], "ids": [], "aliases": [], "pid": legacy_pid}
        data["next_pid"] += 1
        data["players"]["Моргун А."]["usernames"] = ["OldDuplicate"]
        if not with_duplicate:
            del data["players"]["Моргун А."]
        B.save_players(data)
        for name in ("Рим", "Антон от Миши"):
            if not B.find_key(name, B.load_players()["players"]):
                B.players_add(name)
        B.load_games()
        return B.load_players()["players"]["Alexandr"]["pid"]

    def sums(self):
        players = self.read_stats()[str(CHAT)]["players"]
        return tuple(sum(r[k] for r in players.values()) for k in ("games", "goals", "assists")), len(players)

    def test_before_merge_archive_waits_with_exactly_two_discrepancies(self):
        self.prod_like()
        status, lines = B.fill_historical_archive()
        self.assertEqual(status, "mismatch")
        self.assertEqual(len(lines), 2)
        self.assertIn("Моргун А.: архив И1 Г4 П3 · статистика — нет игрока", lines[0])
        self.assertIn("Alexandr: в статистике есть (И1 Г4 П3)", lines[1])

    def test_merge_keeps_alexandr_pid_stats_and_totals(self):
        pid = self.prod_like()
        before = self.sums()
        done = B.apply_confirmed_aliases()
        self.assertEqual(len(done), 1)
        self.assertIn("повторная запись реестра объединена", done[0])
        registry = B.load_players()["players"]
        self.assertNotIn("Alexandr", registry)
        self.assertEqual(registry["Моргун А."]["pid"], pid)                               # pid именно Alexandr
        self.assertEqual(len([n for n in registry if "Моргун" in n or "Alexandr" in n]), 1)  # нового игрока/дубля нет
        stats = self.read_stats()[str(CHAT)]["players"]
        self.assertEqual(stats["Моргун А."], {"games": 1, "goals": 4, "assists": 3})
        self.assertNotIn("Alexandr", stats)
        self.assertEqual(self.sums(), before)                                              # суммы и число игроков не изменились

    def test_duplicate_registry_row_data_is_not_lost(self):
        self.prod_like()
        data = B.load_players()
        data["players"]["Alexandr"]["usernames"] = ["alex_main"]
        data["players"]["Alexandr"]["ids"] = [111]
        data["players"]["Моргун А."]["usernames"] = ["morgun_second", "ALEX_MAIN"]
        data["players"]["Моргун А."]["ids"] = [111, 222]
        data["players"]["Моргун А."]["aliases"] = ["саша"]
        B.save_players(data)
        B.apply_confirmed_aliases()
        rec = B.load_players()["players"]["Моргун А."]
        self.assertEqual(rec["usernames"], ["alex_main", "morgun_second"])
        self.assertEqual(rec["ids"], [111, 222])
        self.assertEqual(rec["aliases"], ["саша"])
        self.assertEqual(B.match_player(B.load_players(), 222, "", ""), "Моргун А.")      # опрос по-прежнему узнаёт обе учётные записи

    def test_works_without_duplicate_row_too(self):
        pid = self.prod_like(with_duplicate=False)
        done = B.apply_confirmed_aliases()
        self.assertNotIn("повторная запись", done[0])
        self.assertEqual(B.load_players()["players"]["Моргун А."]["pid"], pid)

    def test_idempotent_and_noop_without_alexandr(self):
        self.prod_like()
        B.apply_confirmed_aliases()
        snapshot = (read_bytes(B.PLAYERS_FILE), read_bytes(B.STATS_FILE))
        self.assertEqual(B.apply_confirmed_aliases(), [])
        self.assertEqual(snapshot, (read_bytes(B.PLAYERS_FILE), read_bytes(B.STATS_FILE)))

    def test_both_names_in_stats_is_real_conflict_nothing_touched(self):
        self.prod_like()
        stats = self.read_stats()[str(CHAT)]["players"]
        stats["Моргун А."] = {"games": 2, "goals": 0, "assists": 0}
        self.write_stats(stats)
        before = (read_bytes(B.PLAYERS_FILE), read_bytes(B.STATS_FILE))
        self.assertEqual(B.apply_confirmed_aliases(), [])
        self.assertEqual(before, (read_bytes(B.PLAYERS_FILE), read_bytes(B.STATS_FILE)))

    def test_failure_rolls_back_everything(self):
        self.prod_like()
        snapshot = (read_bytes(B.PLAYERS_FILE), read_bytes(B.STATS_FILE))
        with mock.patch.object(B, "save_players", side_effect=OSError("диск")):
            with self.assertRaises(OSError):
                B.apply_confirmed_aliases()
        self.assertEqual(snapshot, (read_bytes(B.PLAYERS_FILE), read_bytes(B.STATS_FILE)))

    async def test_full_flow_archive_filled_with_same_pid_and_no_double_count(self):
        pid = self.prod_like()
        before = self.sums()
        await B.notify_archive()
        sent = [t for c, t in self.fake.sent if c == OWNER]
        self.assertEqual(len(sent), 1)
        self.assertIn("Архив игр №1–4 заполнен", sent[0])
        self.assertIn("«Alexandr» → «Моргун А.»", sent[0])
        self.assertEqual(self.sums(), before)                                               # Г4 П3 не начислены повторно
        stats = self.read_stats()[str(CHAT)]["players"]
        self.assertEqual(stats["Моргун А."], {"games": 1, "goals": 4, "assists": 3})
        g4 = next(g for g in B.load_games()["games"] if g["number"] == 4)
        morgun = next(p for team in g4["teams"] for p in team if p["name"] == "Моргун А.")
        self.assertEqual((morgun["pid"], morgun["goals"], morgun["assists"]), (pid, 4, 3))
        text = await self.view(4)
        self.assertIn("Моргун А. — ⚽ 4 · 🎯 3", text)
        self.assertNotIn("Alexandr", read_text(B.GAMES_FILE) + read_text(B.STATS_FILE) + read_text(B.PLAYERS_FILE))
        self.assertEqual(B.played_games_count(), 4)
        self.assertTrue(B.stats_snapshot()[0].startswith("📊 Статистика (сыграно игр: 4)"))
        self.assertEqual(B.next_game_number(B.load_games()), 5)
        self.fake.sent.clear()
        await B.notify_archive()                                                             # повторный запуск — тишина
        self.assertEqual(self.fake.sent, [])

    async def test_other_discrepancy_still_blocks_and_is_reported(self):
        self.prod_like()
        stats = self.read_stats()[str(CHAT)]["players"]
        stats["Рим"]["goals"] += 1
        self.write_stats(stats)
        await B.notify_archive()
        sent = [t for c, t in self.fake.sent if c == OWNER][0]
        self.assertIn("НЕ заполнен", sent)
        self.assertIn("Рим: архив И1 Г5 П2", sent)
        self.assertNotIn("Моргун А.: архив", sent)                                           # имя уже сопоставлено — не расхождение
        self.assertIsNone(next(g for g in B.load_games()["games"] if g["number"] == 4)["teams"])


if __name__ == "__main__":
    unittest.main()
