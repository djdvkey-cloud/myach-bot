"""Канонический игрок: Моргун А. = @Footmor = прежний Alexandr (один pid), и правило версий."""
import datetime
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_bot import Base, FakeQuery, read_bytes, read_text, B, OWNER, CHAT  # noqa: E402
from test_archive import totals_stats  # noqa: E402


class CanonBase(Base):
    def sums(self):
        players = self.read_stats()[str(CHAT)]["players"]
        return tuple(sum(r[k] for r in players.values()) for k in ("games", "goals", "assists")), len(players)

    def production_after_alias_merge(self):
        """Состояние production после предыдущего шага: «Моргун А.» с pid бывшего Alexandr и его статистикой; архив №1–4 заполнен."""
        self.write_stats(totals_stats())
        data = B.load_players()
        morgun = data["players"]["Моргун А."]
        morgun["aliases"] = []                                      # алиаса ещё нет
        B.save_players(data)
        B.load_games()
        self.assertEqual(B.fill_historical_archive()[0], "done")
        return B.load_players()["players"]["Моргун А."]["pid"]

    def legacy_alexandr_state(self):
        """Старое состояние: отдельная запись Alexandr (@Footmor) со статистикой И1 Г4 П3; архив ещё не заполнен."""
        stats = totals_stats()
        stats["Alexandr"] = stats.pop("Моргун А.")
        self.write_stats(stats)
        data = B.load_players()
        pid = data["next_pid"]
        data["players"]["Alexandr"] = {"usernames": ["Footmor"], "ids": [], "aliases": [], "pid": pid}
        data["next_pid"] += 1
        del data["players"]["Моргун А."]
        B.save_players(data)
        B.load_games()
        return pid

    async def game4_line(self):
        q = FakeQuery("gh:g:4")
        await B.cb_history(q)
        return q.message.edits[-1][0]


class SeedConfig(CanonBase):
    def test_fresh_registry_has_one_morgun_with_footmor_and_alexandr_only_as_alias(self):
        players = B.load_players()["players"]                       # чистое развёртывание: файл создаётся из кода
        self.assertNotIn("Alexandr", players)
        self.assertEqual(len([n for n in players if "Моргун" in n or "alexandr" in n.lower()]), 1)
        self.assertEqual(players["Моргун А."]["usernames"], ["Footmor"])
        self.assertIn("alexandr", players["Моргун А."]["aliases"])
        self.assertEqual(B.match_player(B.load_players(), None, "", "Alexandr"), "Моргун А.")      # старое имя узнаётся

    def test_seed_in_code_cannot_create_both_names(self):
        self.assertNotIn("Alexandr", B.DEFAULT_PLAYER_USERNAMES)
        self.assertEqual(B.DEFAULT_PLAYER_USERNAMES["Моргун А."], "Footmor")
        self.assertEqual(B.DEFAULT_DISPLAY_ALIASES["alexandr"], "Моргун А.")
        seed = B._seed_players()["players"]
        self.assertEqual(sorted(n for n in seed if "Alexandr" in n or "Моргун" in n), ["Моргун А."])
        usernames = [u.lower() for rec in seed.values() for u in rec["usernames"]]
        self.assertEqual(usernames.count("footmor"), 1)

    def test_players_list_shows_single_morgun_without_separate_alexandr(self):
        text = B.players_text(B.load_players())
        rows = [l for l in text.splitlines() if l[:1].isdigit()]
        self.assertEqual(len([l for l in rows if "Моргун А." in l]), 1)
        self.assertIn("@Footmor", [l for l in rows if "Моргун А." in l][0])
        self.assertFalse(any(re.match(r"\d+\. Alexandr", l) for l in rows))

    def test_players_list_with_repeated_loading_stays_single(self):
        for _ in range(3):
            players = B.load_players()["players"]
            self.assertEqual([n for n in players if "Моргун" in n or n == "Alexandr"], ["Моргун А."])


class Canonicalize(CanonBase):
    async def test_production_state_gets_alias_footmor_stays_pid_stats_same(self):
        pid = self.production_after_alias_merge()
        sums_before = self.sums()
        status, lines = B.canonicalize_players()
        self.assertEqual(status, "changed")
        rec = B.load_players()["players"]["Моргун А."]
        self.assertEqual((rec["pid"], rec["usernames"]), (pid, ["Footmor"]))
        self.assertIn("alexandr", rec["aliases"])
        self.assertNotIn("Alexandr", B.load_players()["players"])
        self.assertEqual(self.read_stats()[str(CHAT)]["players"]["Моргун А."], {"games": 1, "goals": 4, "assists": 3})
        self.assertEqual(self.sums(), sums_before)                                      # суммы всех игроков те же
        self.assertIn("Моргун А. — ⚽ 4 · 🎯 3", await self.game4_line())
        g4 = next(g for g in B.load_games()["games"] if g["number"] == 4)
        self.assertEqual([p["pid"] for t in g4["teams"] for p in t if p["name"] == "Моргун А."], [pid])

    def test_idempotent_and_restart_does_not_bring_back_duplicate(self):
        self.production_after_alias_merge()
        B.canonicalize_players()
        snapshot = (read_bytes(B.PLAYERS_FILE), read_bytes(B.STATS_FILE), read_bytes(B.GAMES_FILE))
        for _ in range(3):                                           # «перезапуски»
            self.assertEqual(B.canonicalize_players(), ("ok", []))
            self.assertEqual(B.apply_confirmed_aliases(), [])
            B.load_players()
        self.assertEqual(snapshot, (read_bytes(B.PLAYERS_FILE), read_bytes(B.STATS_FILE), read_bytes(B.GAMES_FILE)))
        self.assertEqual([n for n in B.load_players()["players"] if n == "Alexandr"], [])

    async def test_legacy_alexandr_row_is_renamed_keeping_pid_and_stats(self):
        pid = self.legacy_alexandr_state()
        sums_before = self.sums()
        status, lines = B.canonicalize_players()
        self.assertEqual(status, "changed")
        registry = B.load_players()["players"]
        self.assertNotIn("Alexandr", registry)
        self.assertEqual((registry["Моргун А."]["pid"], registry["Моргун А."]["usernames"]), (pid, ["Footmor"]))
        self.assertEqual(self.read_stats()[str(CHAT)]["players"]["Моргун А."], {"games": 1, "goals": 4, "assists": 3})
        self.assertEqual(self.sums(), sums_before)
        self.assertEqual(B.fill_historical_archive()[0], "done")
        self.assertIn("Моргун А. — ⚽ 4 · 🎯 3", await self.game4_line())

    def test_missing_username_is_set_and_other_usernames_kept(self):
        self.production_after_alias_merge()
        data = B.load_players()
        data["players"]["Моргун А."]["usernames"] = ["second_account"]
        B.save_players(data)
        B.canonicalize_players()
        self.assertEqual(B.load_players()["players"]["Моргун А."]["usernames"], ["Footmor", "second_account"])

    def test_conflict_footmor_belongs_to_another_player(self):
        self.production_after_alias_merge()
        data = B.load_players()
        data["players"]["Ларионов М."]["usernames"].append("footmor")
        data["players"]["Моргун А."]["usernames"] = []
        B.save_players(data)
        before = (read_bytes(B.PLAYERS_FILE), read_bytes(B.STATS_FILE))
        status, lines = B.canonicalize_players()
        self.assertEqual(status, "conflict")
        self.assertIn("@Footmor уже принадлежит другому игроку «Ларионов М.»", lines[0])
        self.assertEqual(before, (read_bytes(B.PLAYERS_FILE), read_bytes(B.STATS_FILE)))

    def test_conflict_stats_differ(self):
        self.production_after_alias_merge()
        stats = self.read_stats()[str(CHAT)]["players"]
        stats["Моргун А."]["games"] = 2
        self.write_stats(stats)
        before = read_bytes(B.PLAYERS_FILE)
        status, lines = B.canonicalize_players()
        self.assertEqual(status, "conflict")
        self.assertIn("статистика «Моргун А.»", lines[0])
        self.assertEqual(before, read_bytes(B.PLAYERS_FILE))

    def test_conflict_two_active_rows_not_merged_automatically(self):
        self.production_after_alias_merge()
        data = B.load_players()
        data["players"]["Alexandr"] = {"usernames": [], "ids": [], "aliases": [], "pid": data["next_pid"]}
        data["next_pid"] += 1
        B.save_players(data)
        before = read_bytes(B.PLAYERS_FILE)
        status, lines = B.canonicalize_players()
        self.assertEqual(status, "conflict")
        self.assertIn("одновременно", lines[0])
        self.assertEqual(before, read_bytes(B.PLAYERS_FILE))

    async def test_startup_notice_only_when_something_changed_or_conflict(self):
        self.production_after_alias_merge()
        await B.notify_canonical()
        sent = [t for c, t in self.fake.sent if c == OWNER]
        self.assertEqual(len(sent), 1)
        self.assertIn("Игрок закреплён", sent[0])
        self.fake.sent.clear()
        await B.notify_canonical()
        self.assertEqual(self.fake.sent, [])
        data = B.load_players()
        data["players"]["Ларионов М."]["usernames"].append("Footmor2")                 # другой username — не конфликт
        B.save_players(data)
        await B.notify_canonical()
        self.assertEqual(self.fake.sent, [])

    async def test_conflict_is_reported_to_owner(self):
        self.production_after_alias_merge()
        data = B.load_players()
        data["players"]["Ларионов М."]["usernames"].append("Footmor")
        data["players"]["Моргун А."]["usernames"] = []
        B.save_players(data)
        await B.notify_canonical()
        text = [t for c, t in self.fake.sent if c == OWNER][0]
        self.assertIn("НЕ закреплён", text)
        self.assertIn("Ларионов М.", text)
        self.assertIn("Нужно решение Дмитрия", text)


class StartupWiring(Base):
    async def test_main_runs_archive_then_canonicalization_at_startup(self):
        import types as pytypes
        from unittest import mock
        calls = []

        async def spy(name):
            calls.append(name)

        async def noop(*a, **kw):
            return None

        async def menu():
            return {}
        self.fake.session = pytypes.SimpleNamespace(close=noop)
        with mock.patch.object(B, "setup_menu", menu),                 mock.patch.object(B, "notify_started", noop),                 mock.patch.object(B, "notify_archive", lambda: spy("archive")),                 mock.patch.object(B, "notify_canonical", lambda: spy("canonical")),                 mock.patch.object(B, "poll_scheduler", noop),                 mock.patch.object(B.dp, "start_polling", noop):
            await B.main()
        self.assertEqual(calls, ["archive", "canonical"])


class Versioning(Base):
    def test_version_is_dated_with_suffix_and_not_in_the_future(self):
        m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})(?:\.(\d+))?", B.BOT_VERSION)
        self.assertIsNotNone(m, B.BOT_VERSION)
        day = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        self.assertLessEqual(day, datetime.date.today())
        self.assertEqual(B.BOT_VERSION, "2026-10-03.2")

    async def test_startup_message_shows_version(self):
        report = await B.setup_menu()
        await B.notify_started(report)
        self.assertIn(f"версия {B.BOT_VERSION}", self.fake.sent[0][1])

    def test_handoff_documents_version_rule(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "HANDOFF.md")
        if os.path.exists(path):                                    # в репозитории Amvera HANDOFF нет (он в зеркале)
            self.assertIn("реальной дате deploy", read_text(path))


if __name__ == "__main__":
    unittest.main()
