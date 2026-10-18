"""Жёсткое правило «Ковалёв М. никогда не в красных» и ротация при «Пересобрать команды». Данные вымышленные, кроме набора
коэффициентов для проверки на составе игры №7 (копия, production не затрагивается); Telegram подменён заглушками."""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_bot import Base, FakeQuery, owner_msg, all_button_data, B  # noqa: E402

KOV = "Ковалёв М."
RED = B.TEAM_COLORS.index("красные")
WHITE = B.TEAM_COLORS.index("белые")
BLACK = B.TEAM_COLORS.index("чёрные")

# Набор коэффициентов, как у состава игры №7 (имена обезличены): игры, голы, передачи.
GAME7 = {
    "Игрок A1": (3, 10, 6), "Игрок A2": (6, 30, 15), "Игрок A3": (6, 10, 8), "Игрок A4": (6, 8, 10), "Игрок A5": (4, 10, 5),
    "Игрок A6": (4, 8, 7), "Игрок A7": (5, 6, 6), "Ковалёв М.": (6, 25, 18), "Игрок A8": (1, 4, 3), "Игрок A9": (3, 10, 7),
    "Игрок B1": (4, 10, 8), "Игрок B2": (6, 3, 3),
}
# 15 вымышленных игроков (3 команды по 5), среди них Ковалёв М.
FIFTEEN = [(KOV, 11.3), ("Игрок 01", 12.5), ("Игрок 02", 9.0), ("Игрок 03", 8.7), ("Игрок 04", 7.0), ("Игрок 05", 6.2), ("Игрок 06", 5.7),
           ("Игрок 07", 4.7), ("Игрок 08", 4.3), ("Игрок 09", 3.6), ("Игрок 10", 3.0), ("Игрок 11", 2.5), ("Игрок 12", 2.0),
           ("Игрок 13", 1.5), ("Игрок 14", 1.0)]


def koefs_of(stats):
    return {n: (2 * g + a) / i for n, (i, g, a) in stats.items()}


def sp_of(stats):
    return {n: {"games": i, "goals": g, "assists": a} for n, (i, g, a) in stats.items()}


def avgs(teams, koef):
    return [sum(koef[n] for n in t) / len(t) for t in teams]


def spread(teams, koef):
    a = avgs(teams, koef)
    return max(a) - min(a)


def make_game(stats, teams=None):
    names = list(stats)
    if teams is None:
        t, _ = B.split_teams([(n, koefs_of(stats)[n]) for n in names], B.team_count_for(len(names)))
        teams = [[n for n, _ in x] for x in t]
    return B.new_game(teams)


class KovalevRule(Base):
    def test_kovalev_never_red_in_initial_split(self):
        teams, _ = B.split_teams(FIFTEEN, 3)
        self.assertEqual(len(teams), 3)
        self.assertNotIn(KOV, [n for n, _ in teams[RED]])

    def test_color_order_matches_bot_colors(self):
        self.assertEqual(B.TEAM_COLORS, ("белые", "чёрные", "красные"))             # правило привязано к цвету, а не к «магическому индексу»
        self.assertTrue(B.team_banned(KOV, RED))
        self.assertFalse(B.team_banned(KOV, WHITE))
        self.assertFalse(B.team_banned(KOV, BLACK))
        self.assertTrue(B.team_banned("  ковалев м. ", RED))                           # штатная нормализация: регистр, ё/е, пробелы
        self.assertFalse(B.team_banned("Иванов И.", RED))

    def test_hundreds_of_generations_never_red_and_both_other_colors_happen(self):
        seen = set()
        rng = random.Random(1)
        for _ in range(300):
            teams, _, status = B.pick_split(FIFTEEN, 3, mode="rotate", rng=rng)
            self.assertEqual(status, "ok")
            idx = [i for i, t in enumerate(teams) if KOV in [n for n, _ in t]]
            self.assertEqual(len(idx), 1)
            self.assertNotEqual(idx[0], RED)
            seen.add(idx[0])
        self.assertEqual(seen, {WHITE, BLACK})                                          # бывает и в белых, и в чёрных

    def test_rule_holds_with_button_rebuild_and_players_not_lost(self):
        koef = dict(FIFTEEN)
        g = B.new_game([[n for n, _ in t] for t in B.split_teams(FIFTEEN, 3)[0]])
        sp = {n: {"games": 1, "goals": 0, "assists": 0} for n, _ in FIFTEEN}
        for n, k in FIFTEEN:                                                            # рейтинг гостей = фактический коэффициент
            g["ratings"][n] = k
        for _ in range(60):
            B.squad_rebuild(g, {}, reason="manual")
            self.assertNotIn(KOV, g["teams"][RED])
            self.assertEqual(sorted(B.game_players(g)), sorted(n for n, _ in FIFTEEN))   # никто не потерян и не продублирован
            self.assertEqual([len(t) for t in g["teams"]], [5, 5, 5])
            self.assertLessEqual(spread(g["teams"], koef), 0.16 + 0.15)

    def test_constraint_does_not_wreck_balance(self):
        banned, spread_banned = B.split_teams(FIFTEEN, 3)
        orig = dict(B.TEAM_COLOR_BANS)
        try:
            B.TEAM_COLOR_BANS.clear()
            _, spread_free, _ = B.pick_split(FIFTEEN, 3, mode="best")
        finally:
            B.TEAM_COLOR_BANS.update(orig)
        self.assertLess(spread_banned - spread_free, 0.3)                              # цена ограничения мала для сильного игрока
        self.assertLess(spread_banned, 0.35)

    def test_without_kovalev_algorithm_unchanged(self):
        players = [(n, k) for n, k in FIFTEEN if n != KOV] + [("Игрок 15", 11.3)]
        self.assertFalse(B.has_color_bans(players))
        a, sa = B.split_teams(players, 3)
        b, sb = B.exact_split(players, 3)
        self.assertEqual(a, b)
        self.assertEqual(sa, sb)

    def test_impossible_case_is_loud_not_silent(self):
        orig = dict(B.TEAM_COLOR_BANS)
        try:
            B.TEAM_COLOR_BANS[B.norm_key(KOV)] = set(B.TEAM_COLORS)                     # запрещён везде: невыполнимо
            with self.assertRaises(ValueError):
                B.split_teams(FIFTEEN, 3)
            with self.assertRaises(ValueError):
                B.pick_split(FIFTEEN, 3, mode="rotate", rng=random.Random(2))
        finally:
            B.TEAM_COLOR_BANS.clear()
            B.TEAM_COLOR_BANS.update(orig)

    def test_heuristic_branch_over_15_players_keeps_rule(self):
        many = [(f"Игрок {i:02d}", 1 + (i * 7) % 11) for i in range(1, 17)] + [(KOV, 10.0)]          # 17 игроков → эвристика, 3 команды
        for seed in range(15):
            teams, _, st = B.pick_split(many, 3, mode="rotate", rng=random.Random(seed))
            self.assertEqual(st, "ok")
            self.assertNotIn(KOV, [n for n, _ in teams[RED]])
            self.assertEqual(sorted(len(t) for t in teams), [5, 6, 6])
        teams, _ = B.split_teams(many, 3)
        self.assertNotIn(KOV, [n for n, _ in teams[RED]])


class Rotation(Base):
    def setUp(self):
        super().setUp()
        self.koef = koefs_of(GAME7)
        self.sp = sp_of(GAME7)

    def rebuild_series(self, g, n):
        fps = [B.teams_fingerprint(g["teams"])]
        for _ in range(n):
            changed = B.squad_rebuild(g, self.sp, reason="manual")
            self.assertTrue(changed)
            fps.append(B.teams_fingerprint(g["teams"]))
        return fps

    def test_rebuild_gives_other_balanced_variants(self):
        g = make_game(GAME7)
        first = B.teams_fingerprint(g["teams"])
        self.assertEqual(g["split_history"], [first])
        best = spread(g["teams"], self.koef)
        fps = self.rebuild_series(g, 20)
        self.assertEqual(len(set(fps[:11])), 11)                                        # первые 10 пересборок не повторяют ни один из показанных
        for a, b in zip(fps, fps[1:]):
            self.assertNotEqual(a, b)                                                   # повтор текущего — не пересборка
        self.assertGreater(len(set(fps)), 15)

    def test_balance_stays_close_to_best_by_existing_metric(self):
        g = make_game(GAME7)
        best = spread(g["teams"], self.koef)
        worst = 0.0
        for _ in range(60):
            B.squad_rebuild(g, self.sp, reason="manual")
            self.assertEqual(sorted(B.game_players(g)), sorted(GAME7))                 # игроки не теряются и не дублируются
            self.assertEqual([len(t) for t in g["teams"]], [6, 6])
            worst = max(worst, spread(g["teams"], self.koef))
        self.assertLessEqual(worst, best + B.BALANCE_TOLERANCE + 1e-9)

    def test_individual_koefs_unchanged_by_rebuild(self):
        g = make_game(GAME7)
        before = B.koef_for_names(B.game_players(g), g["ratings"], self.sp)[0]
        self.rebuild_series(g, 10)
        self.assertEqual(B.koef_for_names(B.game_players(g), g["ratings"], self.sp)[0], before)

    def test_history_is_bounded_and_older_variants_return_only_after_exhaustion(self):
        g = make_game(GAME7)
        self.rebuild_series(g, 40)
        self.assertLessEqual(len(g["split_history"]), B.SPLIT_HISTORY_KEEP)
        self.assertEqual(g["split_history"][-1], B.teams_fingerprint(g["teams"]))

    def test_no_alternative_returns_false_and_keeps_state(self):
        stats = {"А": (1, 1, 0), "Б": (1, 1, 1), "В": (1, 1, 2), "Г": (1, 5, 0)}      # единственное равноценное деление
        sp = sp_of(stats)
        g = make_game(stats)
        before_teams, before_rev = [list(t) for t in g["teams"]], g["rev"]
        self.assertFalse(B.squad_rebuild(g, sp, reason="manual"))
        self.assertEqual(g["teams"], before_teams)
        self.assertEqual(g["rev"], before_rev)                                          # ничего не залогировано как пересборка

    async def test_button_changes_teams_and_says_rebuilt(self):
        B.save_json(B.STATS_FILE, {str(B.CHAT_ID): {"players": sp_of(GAME7), "last": []}})
        g = make_game(GAME7)
        B.save_game(g)
        before = B.teams_fingerprint(B.load_game()["teams"])
        q = FakeQuery(f"sq:rb:{g['rev']}")
        await B.cb_squad(q)
        after = B.teams_fingerprint(B.load_game()["teams"])
        self.assertNotEqual(before, after)
        shown = " ".join(t for t, _ in q.message.edits) + " ".join(t for t, _ in q.message.answers)
        self.assertIn("Команды пересобраны", shown)

    async def test_button_with_no_alternative_does_not_claim_rebuilt(self):
        stats = {"А": (1, 1, 0), "Б": (1, 1, 1), "В": (1, 1, 2), "Г": (1, 5, 0)}
        B.save_json(B.STATS_FILE, {str(B.CHAT_ID): {"players": sp_of(stats), "last": []}})
        g = make_game(stats)
        B.save_game(g)
        before = B.load_game()["teams"]
        q = FakeQuery(f"sq:rb:{g['rev']}")
        await B.cb_squad(q)
        shown = " ".join(t for t, _ in q.message.edits) + " ".join(t for t, _ in q.message.answers)
        self.assertNotIn("Команды пересобраны", shown)
        self.assertIn("Другого достаточно ровного варианта", shown)
        self.assertEqual(B.load_game()["teams"], before)

    def test_new_game_starts_with_fresh_history(self):
        g1 = make_game(GAME7)
        self.rebuild_series(g1, 8)
        self.assertGreater(len(g1["split_history"]), 5)
        g2 = make_game(GAME7)                                                           # новая неделя
        self.assertEqual(g2["split_history"], [B.teams_fingerprint(g2["teams"])])
        self.assertEqual(len(g2["split_history"]), 1)

    def test_add_or_replace_rebuild_resets_history_and_is_not_rotation(self):
        g = make_game(GAME7)
        self.rebuild_series(g, 5)
        g["teams"][0].append("Новичок")
        sp = dict(self.sp)
        self.assertTrue(B.squad_rebuild(g, sp, reason="add"))
        self.assertEqual(len(g["split_history"]), 1)

    def test_fingerprint_ignores_order_inside_team_and_team_order(self):
        a = [["Б", "А"], ["Г", "В"]]
        b = [["В", "Г"], ["А", "Б"]]
        self.assertEqual(B.teams_fingerprint(a), B.teams_fingerprint(b))
        self.assertNotEqual(B.teams_fingerprint(a), B.teams_fingerprint([["А", "В"], ["Б", "Г"]]))

    def test_game7_copy_report(self):
        """Реальный состав №7 (копия): много последовательных пересборок, разные варианты, разброс средних. Печатается для отчёта."""
        g = make_game(GAME7)
        rows, seen = [], []
        for _ in range(30):
            B.squad_rebuild(g, self.sp, reason="manual")
            fp = B.teams_fingerprint(g["teams"])
            seen.append(fp)
            a = avgs(g["teams"], self.koef)
            rows.append((fp, [round(x, 3) for x in a], round(max(a) - min(a), 3)))
            self.assertNotIn(KOV, g["teams"][RED] if len(g["teams"]) > RED else [])
        distinct = len(set(seen))
        spreads = [r[2] for r in rows]
        print(f"\n[№7 копия] пересборок 30, разных вариантов {distinct}, разброс средних: мин {min(spreads)}, макс {max(spreads)}")
        for fp, av, sp_ in rows[:5]:
            print(f"   {av}  разброс {sp_}  {fp[:60]}…")
        self.assertGreaterEqual(distinct, 15)                                          # всего равноценных вариантов около 26
        for i in range(len(seen)):
            self.assertNotIn(seen[i], seen[max(0, i - 8):i])                           # внутри окна истории повторов нет
        self.assertLessEqual(max(spreads), 0.011 + B.BALANCE_TOLERANCE + 0.01)


if __name__ == "__main__":
    unittest.main()
