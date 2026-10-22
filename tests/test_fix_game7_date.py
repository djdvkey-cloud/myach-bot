"""ВРЕМЕННЫЙ one-shot: дата игры №7. Все данные вымышленные."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_bot import Base, read_bytes, B  # noqa: E402


def team(n):
    return [{"name": f"Игрок {n}", "pid": n, "goals": n % 3, "assists": n % 2}]


class Fix7(Base):
    def setUp(self):
        super().setUp()
        self.write_stats({"Иванов И.": {"games": 7, "goals": 9, "assists": 5}})
        games = [{"number": n, "date": f"2026-10-{d}", "teams": [team(n)], "split": False, "source": "result"}
                 for n, d in ((5, "05"), (6, "12"), (7, "22"))]
        B.save_json(B.GAMES_FILE, {"version": 1, "games": games, "annulled": [], "last_recorded": 7})
        B.save_json(B.GAME_FILE, {"id": "cur", "teams": [["А"]]})
        B.save_json(B.POLL_STATE_FILE, {"poll": "x", "votes": {}})
        self.files = (B.STATS_FILE, B.GAME_FILE, B.POLL_STATE_FILE)

    def games(self):
        with open(B.GAMES_FILE, encoding="utf-8") as f:
            return json.load(f)["games"]

    def test_fix(self):
        before = {p: read_bytes(p) for p in self.files}
        old = self.games()
        self.assertEqual(B.fix_game7_date(), "done")
        new = self.games()
        self.assertEqual([g["date"] for g in new], ["2026-10-05", "2026-10-12", "2026-10-19"])
        self.assertEqual(new[:2], old[:2])
        self.assertEqual({k: v for k, v in new[2].items() if k != "date"}, {k: v for k, v in old[2].items() if k != "date"})
        self.assertEqual(before, {p: read_bytes(p) for p in self.files})
        self.assertTrue(os.path.exists(os.path.join(self.tmp, B.FIX7_MARKER)))
        bdir = os.path.join(self.tmp, "backups_pre_fix_game7_date")
        sub = os.path.join(bdir, os.listdir(bdir)[0])
        self.assertIn("games.json", os.listdir(sub))
        with open(os.path.join(sub, "games.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["games"][2]["date"], "2026-10-22")

    def test_replay_protection(self):
        self.assertEqual(B.fix_game7_date(), "done")
        raw = read_bytes(B.GAMES_FILE)
        self.assertEqual(B.fix_game7_date(), "marker")
        self.assertEqual(raw, read_bytes(B.GAMES_FILE))

    def _refuses(self, mutate):
        data = json.load(open(B.GAMES_FILE, encoding="utf-8"))
        mutate(data)
        B.save_json(B.GAMES_FILE, data)
        raw = read_bytes(B.GAMES_FILE)
        self.assertEqual(B.fix_game7_date(), "error")
        self.assertEqual(raw, read_bytes(B.GAMES_FILE))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, B.FIX7_MARKER)))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "backups_pre_fix_game7_date")))

    def test_wrong_date(self):
        self._refuses(lambda d: d["games"][2].update(date="2026-10-21"))

    def test_no_game7(self):
        self._refuses(lambda d: d["games"].pop())

    def test_date_taken(self):
        self._refuses(lambda d: d["games"][1].update(date="2026-10-19"))

    def test_older_game_not_earlier(self):
        self._refuses(lambda d: d["games"][1].update(date="2026-10-20"))

    def test_no_teams(self):
        self._refuses(lambda d: d["games"][2].update(teams=None))

    def test_already(self):
        self._refuses_ok = None
        data = json.load(open(B.GAMES_FILE, encoding="utf-8"))
        data["games"][2]["date"] = "2026-10-19"
        B.save_json(B.GAMES_FILE, data)
        raw = read_bytes(B.GAMES_FILE)
        self.assertEqual(B.fix_game7_date(), "already")
        self.assertEqual(raw, read_bytes(B.GAMES_FILE))


if __name__ == "__main__":
    unittest.main()
