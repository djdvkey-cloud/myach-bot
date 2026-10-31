"""Статистический игрок без карточки получает pid и редактируется без потери данных."""
import copy
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_bot import Base, FakeQuery, all_button_data, read_bytes, B, CHAT  # noqa: E402


class AzizRegistry(Base):
    def setUp(self):
        super().setUp()
        self.write_stats({
            "Азиз": {"games": 4, "goals": 12, "assists": 6},
            "Иванов И.": {"games": 3, "goals": 2, "assists": 1},
        })
        data = B.load_players()
        if not B.find_key("Иванов И.", data["players"]):
            B.players_add("Иванов И.")
        games = B.load_games()
        games["games"][0]["teams"] = [[{"name": "Азиз", "pid": None, "goals": 1, "assists": 0}]]
        games["games"][0]["split"] = False
        B.save_games(games)

    def register(self):
        return B.register_stats_player(B.stats_player_token("Азиз"))

    def test_stats_only_player_is_detected_and_registered_without_telegram(self):
        self.assertEqual(B.unregistered_stats_players(), ["Азиз"])
        stats_before = read_bytes(B.STATS_FILE)
        others_before = copy.deepcopy(B.load_players()["players"])
        name, pid = self.register()
        self.assertEqual(name, "Азиз")
        rec = B.load_players()["players"]["Азиз"]
        self.assertEqual(rec, {"usernames": [], "ids": [], "aliases": [], "pid": pid})
        self.assertEqual(read_bytes(B.STATS_FILE), stats_before)
        for other, old in others_before.items():
            self.assertEqual(B.load_players()["players"][other], old)
        row = B.load_games()["games"][0]["teams"][0][0]
        self.assertEqual((row["name"], row["pid"], row["goals"], row["assists"]), ("Азиз", pid, 1, 0))

    def test_existing_registry_player_is_not_offered_or_duplicated(self):
        self.register()
        self.assertNotIn("Азиз", B.unregistered_stats_players())
        with self.assertRaisesRegex(ValueError, "не найден или список изменился"):
            self.register()
        self.assertEqual([n for n in B.load_players()["players"] if B.norm_key(n) == B.norm_key("Азиз")], ["Азиз"])

    async def test_edit_list_offers_stats_player_even_at_bottom_of_long_list(self):
        for i in range(35):
            B.players_add(f"Я Игрок {i:02d}")
        q = FakeQuery("pl:ed")
        await B.cb_players(q)
        callbacks = all_button_data(q.message.edits[-1][1])
        self.assertIn("pl:reg:" + B.stats_player_token("Азиз"), callbacks)
        labels = [button.text for row in q.message.edits[-1][1].inline_keyboard for button in row]
        self.assertIn("➕ Азиз · из статистики", labels)

    async def test_owner_action_creates_card_then_normal_rename_preserves_identity(self):
        stats_before = copy.deepcopy(B.stats_players_of(B.load_stats())["Азиз"])
        q = FakeQuery("pl:reg:" + B.stats_player_token("Азиз"))
        await B.cb_players(q)
        data = B.load_players()
        pid = data["players"]["Азиз"]["pid"]
        self.assertIn(f"pe:n:{pid}", all_button_data(q.message.edits[-1][1]))
        B.players_rename(pid, "Полное Имя")
        stats = B.stats_players_of(B.load_stats())
        self.assertNotIn("Азиз", stats)
        self.assertEqual(stats["Полное Имя"], stats_before)
        self.assertEqual(B.load_players()["players"]["Полное Имя"]["pid"], pid)
        self.assertNotIn("Азиз", B.load_players()["players"])
        row = B.load_games()["games"][0]["teams"][0][0]
        self.assertEqual((row["name"], row["pid"], row["goals"], row["assists"]), ("Полное Имя", pid, 1, 0))

    def test_failure_rolls_back_registry_and_history(self):
        before = (read_bytes(B.PLAYERS_FILE), read_bytes(B.GAMES_FILE), read_bytes(B.STATS_FILE))
        with mock.patch.object(B, "save_games", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.register()
        self.assertEqual(before, (read_bytes(B.PLAYERS_FILE), read_bytes(B.GAMES_FILE), read_bytes(B.STATS_FILE)))

