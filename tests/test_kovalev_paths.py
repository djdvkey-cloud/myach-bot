"""Правило «Ковалёв М. никогда не в красных» — для ВСЕХ способов изменения состава: деление, пересборка, добавление в команду N,
«на его место», замена с пересборкой, добавление с пересборкой. Данные вымышленные; Telegram подменён заглушками."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_bot import Base, FakeQuery, B, read_bytes  # noqa: E402

KOV = "Ковалёв М."
MSG = "Ковалёв М. не может играть в Красной команде"
WHITE, BLACK, RED = (B.TEAM_COLORS.index(c) for c in ("белые", "чёрные", "красные"))


def team(prefix, n):
    return [f"{prefix}{i}" for i in range(1, n + 1)]


class Paths(Base):
    def game(self, red=4, pending=None, kov_in=None):
        """3 команды: белые 5, чёрные 5, красные `red` (самая маленькая — именно туда наивный код поставил бы нового игрока)."""
        teams = [team("Б", 5), team("Ч", 5), team("К", red)]
        if kov_in is not None:
            teams[kov_in][0] = KOV
        g = B.new_game(teams)
        g["pending"] = pending
        B.save_game(g)
        return g

    async def press(self, g, action, *tail):
        q = FakeQuery(":".join(["sq", action, str(g["rev"]), *map(str, tail)]))
        await B.cb_squad(q)
        return q

    def shown(self, q):
        return " ".join(t for t, _ in q.message.answers) + " ".join(t for t, _ in q.message.edits)

    def add_pending(self, name=KOV):
        return {"op": "add", "in": name, "rating": None, "guest": False}

    # 1. автоделение
    async def test_auto_split_never_red(self):
        names = [KOV] + [f"Игрок {i:02d}" for i in range(1, 15)]
        voters = {str(1000 + i): {"player": n, "display": n, "username": ""} for i, n in enumerate(names)}
        B.save_json(B.POLL_STATE_FILE, {"voters": voters})
        out = []

        async def reply(text, reply_markup=None, **kw):
            out.append(text)
        await B.do_split(reply)
        g = B.load_game()
        self.assertEqual(len(g["teams"]), 3)
        self.assertNotIn(KOV, g["teams"][RED])

    # 2. пересборка
    def test_rebuild_never_red(self):
        g = self.game(kov_in=WHITE)
        g["teams"][RED].append("К5")
        for _ in range(25):
            B.squad_rebuild(g, {}, reason="manual")
            self.assertNotIn(KOV, g["teams"][RED])

    # 3. ручное добавление в команду N
    async def test_manual_add_to_red_blocked_state_untouched(self):
        g = self.game(pending=self.add_pending())
        raw = read_bytes(B.GAME_FILE)
        q = await self.press(g, "at", RED)
        self.assertIn(MSG, self.shown(q))
        self.assertEqual(read_bytes(B.GAME_FILE), raw)                                  # 8. состав не изменился
        self.assertNotIn(KOV, B.game_players(B.load_game()))

    async def test_manual_add_to_white_and_black_allowed(self):                          # 6, 7
        for idx in (WHITE, BLACK):
            g = self.game(pending=self.add_pending())
            await self.press(g, "at", idx)
            self.assertIn(KOV, B.load_game()["teams"][idx])

    async def test_normalized_name_is_blocked_too(self):
        for variant in ("ковалев м.", "  КОВАЛЁВ  М. "):
            g = self.game(pending=self.add_pending(variant))
            q = await self.press(g, "at", RED)
            self.assertIn(MSG, self.shown(q))
            self.assertNotIn(variant, B.game_players(B.load_game()))

    # 4. «на его место» (замена на том же месте = единственный путь «переноса» в красную)
    async def test_replace_in_place_into_red_blocked(self):
        g = self.game(pending={"op": "replace", "out": "К2", "in": KOV, "rating": None, "guest": False})
        raw = read_bytes(B.GAME_FILE)
        q = await self.press(g, "mp")
        self.assertIn(MSG, self.shown(q))
        self.assertEqual(read_bytes(B.GAME_FILE), raw)

    async def test_replace_in_place_into_white_and_black_allowed(self):
        for out, idx in (("Б2", WHITE), ("Ч2", BLACK)):
            g = self.game(pending={"op": "replace", "out": out, "in": KOV, "rating": None, "guest": False})
            await self.press(g, "mp")
            after = B.load_game()
            self.assertIn(KOV, after["teams"][idx])
            self.assertNotIn(out, B.game_players(after))

    # 5. замена с пересборкой и добавление с пересборкой: новичка нельзя положить в красную даже временно
    async def test_add_with_rebuild_never_red_even_if_red_is_smallest(self):
        g = self.game(red=4, pending=self.add_pending())
        await self.press(g, "ar")
        after = B.load_game()
        self.assertIn(KOV, B.game_players(after))
        self.assertNotIn(KOV, after["teams"][RED])
        self.assertEqual(sum(len(t) for t in after["teams"]), 15)

    async def test_replace_with_rebuild_never_red(self):
        g = self.game(red=5, pending={"op": "replace", "out": "К3", "in": KOV, "rating": None, "guest": False})
        await self.press(g, "mr")
        after = B.load_game()
        self.assertIn(KOV, B.game_players(after))
        self.assertNotIn(KOV, after["teams"][RED])
        self.assertNotIn("К3", B.game_players(after))
        self.assertEqual(sum(len(t) for t in after["teams"]), 15)

    def test_replace_rebuild_function_is_atomic_when_impossible(self):
        g = self.game(red=5)
        orig = dict(B.TEAM_COLOR_BANS)
        try:
            B.TEAM_COLOR_BANS[B.norm_key(KOV)] = set(B.TEAM_COLORS)                      # нет разрешённой команды вовсе
            before = [list(t) for t in g["teams"]]
            with self.assertRaises(ValueError):
                B.squad_replace_rebuild(g, "К3", KOV, {})
            self.assertEqual(g["teams"], before)                                          # ничего не успело измениться
        finally:
            B.TEAM_COLOR_BANS.clear()
            B.TEAM_COLOR_BANS.update(orig)

    # 9. остальные игроки работают как раньше
    async def test_other_players_unrestricted(self):
        g = self.game(pending=self.add_pending("Иванов И."))
        await self.press(g, "at", RED)
        self.assertIn("Иванов И.", B.load_game()["teams"][RED])
        g = self.game(pending={"op": "replace", "out": "К2", "in": "Петров П.", "rating": None, "guest": False})
        await self.press(g, "mp")
        self.assertIn("Петров П.", B.load_game()["teams"][RED])
        g = self.game(red=4, pending=self.add_pending("Сидоров С."))
        await self.press(g, "ar")
        self.assertEqual(sum(len(t) for t in B.load_game()["teams"]), 15)

    def test_check_function_messages(self):
        B.check_team_allowed(KOV, WHITE)
        B.check_team_allowed(KOV, BLACK)
        B.check_team_allowed("Иванов И.", RED)
        with self.assertRaises(ValueError) as cm:
            B.check_team_allowed(KOV, RED)
        self.assertEqual(str(cm.exception), MSG + ".")

    def test_two_team_game_has_no_red(self):
        g = B.new_game([team("Б", 6), team("Ч", 6)])
        B.squad_add(g, KOV, 0)
        self.assertIn(KOV, g["teams"][0])


if __name__ == "__main__":
    unittest.main()
