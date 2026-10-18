"""Единое сопоставление имён (player_match_key = norm_key): регистр, ё/е, пробелы и точка после инициала не создают нового человека;
каноническое имя сохраняется; число на конце имени не превращается в рейтинг. Данные вымышленные, кроме двух имён из правил бота."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_bot import Base, FakeQuery, owner_msg, all_button_data, B  # noqa: E402

KOV = "Ковалёв М."
MYA = "Мякушко З."
RED = B.TEAM_COLORS.index("красные")
LET = "АБВГДЕЖЗИКЛМНОПРСТУФ"


def nm(i):
    return f"Игрок {LET[i - 1]}"


class Keys(unittest.TestCase):
    def test_initial_dot_case_yo_spaces_equivalent(self):
        forms = ["Мякушко З.", "Мякушко З", "мякушко з", "МЯКУШКО З.", "  мякушко   з. "]
        self.assertEqual({B.norm_key(f) for f in forms}, {B.norm_key(MYA)})
        forms = ["Ковалёв М.", "Ковалев М", "ковалёв м", "КОВАЛЁВ М.", "Ковалев М."]
        self.assertEqual({B.norm_key(f) for f in forms}, {B.norm_key(KOV)})
        self.assertIs(B.player_match_key, B.norm_key)

    def test_real_name_parts_are_kept(self):
        self.assertNotEqual(B.norm_key("Иванов-Петров И."), B.norm_key("Иванов Петров И."))      # дефис не стирается
        self.assertNotEqual(B.norm_key("О'Нил Д."), B.norm_key("Онил Д."))                       # апостроф не стирается
        self.assertNotEqual(B.norm_key("Иванов И."), B.norm_key("Иванов П."))
        self.assertEqual(B.norm_key("Иванов И.И."), B.norm_key("Иванов И. И."))                  # точки между инициалами
        self.assertNotEqual(B.norm_key(nm(1)), B.norm_key(nm(2)))

    def test_find_key_returns_canonical_display_name(self):
        reg = {MYA: {}, KOV: {}}
        self.assertEqual(B.find_key("мякушко з", reg), MYA)
        self.assertEqual(B.find_key("Ковалев М", reg), KOV)
        self.assertEqual(B.resolve_name("МЯКУШКО З", {MYA: {"games": 1}}, set()), MYA)           # статистика важнее, имя не переписывается
        self.assertEqual(B.resolve_name("новый человек", {}, set()), "Новый Человек")            # новое — по прежнему правилу

    def test_rating_parsing(self):
        self.assertEqual(B.parse_guest_text("Алексей 2.3"), ("Алексей", 2.3))                    # прежний штатный формат
        self.assertEqual(B.parse_guest_text("Алексей 2,5"), ("Алексей", 2.5))
        self.assertEqual(B.parse_guest_text("Пётр от Вадима 1.75"), ("Пётр от Вадима", 1.75))
        self.assertEqual(B.parse_guest_text("Игрок 03"), ("Игрок 03", None))                     # целый суффикс — часть имени
        self.assertEqual(B.parse_guest_text("Игрок 3"), ("Игрок 3", None))
        self.assertEqual(B.parse_guest_text("Алексей 2"), ("Алексей 2", None))
        self.assertEqual(B.parse_guest_text("  Алексей   Иванов "), ("Алексей Иванов", None))
        self.assertEqual(B.parse_guest_text("2.3"), ("2.3", None))                               # одно число — это не «имя + рейтинг»
        with self.assertRaises(ValueError):
            B.parse_guest_text("   ")
        self.assertEqual(B.split_guest_entries("Борис 1,5, Иван"), [("Борис", 1.5), ("Иван", None)])     # десятичная запятая не разделитель
        with self.assertRaises(ValueError):
            B.split_guest_entries("Иван, 5")                                                           # «голое» число — не имя
        self.assertEqual(B.split_guest_entries("Игрок 03, Алексей 2.3 ;Иван\nПётр"),
                         [("Игрок 03", None), ("Алексей", 2.3), ("Иван", None), ("Пётр", None)])


class Flows(Base):
    def setUp(self):
        super().setUp()
        st = {nm(i): {"games": 3, "goals": 4, "assists": 2} for i in range(1, 13)}
        st[MYA] = {"games": 1, "goals": 1, "assists": 2}
        st[KOV] = {"games": 5, "goals": 20, "assists": 10}
        self.write_stats(st)
        names = [nm(i) for i in range(1, 13)]
        B.save_game(B.new_game([names[:6], names[6:]]))

    def stats_players(self):
        return self.read_stats()[str(B.CHAT_ID)]["players"]

    async def batch(self, text, g=None):
        g = g or B.load_game()
        await B.cb_squad(FakeQuery(f"sq:adm:{g['rev']}"))
        await B.cb_squad(FakeQuery(f"sq:ag:{B.load_game()['rev']}"))
        m = owner_msg(text)
        await B.owner_text_input(m)
        return m

    # A. точка в инициале: существующий игрок найден, дублей-гостей нет
    async def test_known_player_without_dot_is_found_not_created(self):
        for typed in ("мякушко з", "МЯКУШКО З", "Мякушко З"):
            B.save_game(B.new_game([[nm(i) for i in range(1, 7)], [nm(i) for i in range(7, 13)]]))
            m = await self.batch(f"{typed}, Сергей")
            g = B.load_game()
            self.assertIn(MYA, B.game_players(g))                                # каноническое имя с точкой
            self.assertNotIn(typed, B.game_players(g))
            self.assertNotIn(MYA, g["guests"])
            self.assertEqual(sum(1 for n in B.game_players(g) if B.norm_key(n) == B.norm_key(MYA)), 1)
            self.assertIn(MYA, self.stats_players())
            self.assertEqual(len(self.stats_players()), 14)                       # новых записей статистики нет

    async def test_single_known_name_without_dot_uses_existing_player(self):
        m = await self.batch("мякушко з")
        g = B.load_game()
        self.assertEqual(g["pending"]["in"], MYA)
        self.assertFalse(g["pending"]["guest"])

    async def test_known_player_already_in_squad_not_added_twice(self):
        B.save_game(B.new_game([[nm(1), MYA, nm(3)], [nm(4), nm(5), nm(6)]]))
        m = await self.batch("мякушко з, Сергей")
        text = m.answers[-1][0]
        g = B.load_game()
        self.assertEqual(sum(1 for n in B.game_players(g) if B.norm_key(n) == B.norm_key(MYA)), 1)
        self.assertIn("уже в составе", text)
        self.assertIn("Сергей", B.game_players(g))

    # B. ё/е и правило цвета нельзя обойти написанием
    def test_kovalev_rule_all_spellings(self):
        for spelling in ("Ковалев М", "ковалёв м", "КОВАЛЁВ М.", "Ковалёв М", "ковалев м."):
            with self.assertRaises(ValueError):
                B.check_team_allowed(spelling, RED)
            B.check_team_allowed(spelling, 0)
            B.check_team_allowed(spelling, 1)

    async def test_kovalev_spellings_in_batch_three_teams(self):
        names = [nm(i) for i in range(1, 15)]
        for spelling in ("Ковалев М", "ковалёв м", "КОВАЛЁВ М."):
            B.save_game(B.new_game([names[:5], names[5:10], names[10:14]]))
            await self.batch(f"{spelling}, Иван, Пётр")
            g = B.load_game()
            self.assertEqual(sum(1 for n in B.game_players(g) if B.norm_key(n) == B.norm_key(KOV)), 1)
            self.assertIn(KOV, B.game_players(g))                                # каноническое «Ковалёв М.»
            self.assertNotIn(KOV, g["teams"][RED])
            for _ in range(4):
                await B.cb_squad(FakeQuery(f"sq:rb:{B.load_game()['rev']}"))
                self.assertNotIn(KOV, B.load_game()["teams"][RED])

    async def test_manual_add_to_red_with_alternative_spelling_blocked(self):
        names = [nm(i) for i in range(1, 15)]
        g = B.new_game([names[:5], names[5:10], names[10:14]])
        g["pending"] = {"op": "add", "in": "ковалев м", "rating": None, "guest": False}
        B.save_game(g)
        q = FakeQuery(f"sq:at:{g['rev']}:{RED}")
        await B.cb_squad(q)
        self.assertIn("не может играть в Красной команде", " ".join(t for t, _ in q.message.answers))
        self.assertNotIn("ковалев м", B.game_players(B.load_game()))

    def test_split_with_alternative_spelling_never_red(self):
        players = [("ковалев м", 11.0)] + [(nm(i), 12.0 - i) for i in range(1, 15)]
        teams, _ = B.split_teams(players, 3)
        self.assertNotIn("ковалев м", [n for n, _ in teams[RED]])

    # C. пакетные дубли
    async def test_batch_duplicates_collapse_to_one_known_player(self):
        m = await self.batch("Мякушко З., мякушко з, МЯКУШКО З")
        g = B.load_game()
        # три записи одного человека — это один игрок; т.к. пакет из нескольких имён, добавление идёт одной операцией
        self.assertEqual(sum(1 for n in B.game_players(g) if B.norm_key(n) == B.norm_key(MYA)), 1)
        self.assertEqual(len(B.game_players(g)), 13)
        self.assertIn(MYA, B.game_players(g))
        self.assertIn("дубль во введённом списке", m.answers[-1][0])

    async def test_batch_kovalev_duplicates_and_unknown_duplicates(self):
        m = await self.batch("Ковалёв М., Ковалев М, ковалёв м., Сергей, сергей")
        g = B.load_game()
        self.assertEqual(sum(1 for n in B.game_players(g) if B.norm_key(n) == B.norm_key(KOV)), 1)
        self.assertEqual(sum(1 for n in B.game_players(g) if B.norm_key(n) == "сергей"), 1)
        self.assertEqual(len(B.game_players(g)), 14)

    async def test_known_plus_two_new_gives_three_unique(self):
        before = len(B.game_players(B.load_game()))
        m = await self.batch("Сергей, мякушко з, Иван")
        g = B.load_game()
        self.assertEqual(len(B.game_players(g)), before + 3)
        self.assertEqual(len(set(B.norm_key(n) for n in B.game_players(g))), before + 3)
        self.assertIn(MYA, B.game_players(g))
        self.assertTrue({"Сергей", "Иван"} <= set(g["guests"]))
        self.assertNotIn(MYA, g["guests"])

    # D. цифры в имени
    async def test_digit_suffix_stays_in_name(self):
        m = await self.batch("Игрок 03, Игрок 04")
        g = B.load_game()
        self.assertIn("Игрок 03", B.game_players(g))
        self.assertIn("Игрок 04", B.game_players(g))
        self.assertNotIn("Игрок", B.game_players(g))
        self.assertNotIn("Игрок 03", g["ratings"])
        self.assertEqual(g["ratings"], {})

    async def test_explicit_decimal_rating_still_works_in_batch(self):
        m = await self.batch("Алексей 2.5, Игрок 05, Борис 1,5")
        g = B.load_game()
        self.assertEqual(g["ratings"].get("Алексей"), 2.5)
        self.assertEqual(g["ratings"].get("Борис"), 1.5)
        self.assertIn("Игрок 05", B.game_players(g))
        self.assertNotIn("Игрок 05", g["ratings"])

    async def test_single_guest_with_rating_old_flow_unchanged(self):
        m = await self.batch("Алексей 2.5")
        p = B.load_game()["pending"]
        self.assertEqual((p["in"], p["rating"], p["guest"]), ("Алексей", 2.5, True))

    # стат. записи не дробятся
    def test_statistics_not_fragmented_by_spelling(self):
        entries = [("мякушко з", 1, 1), ("ковалев м", 2, 0), ("МЯКУШКО З.", 0, 0)]
        B.record_match_full(entries[:2], teams=[["мякушко з"], ["ковалев м"]], date="2026-10-12")
        sp = self.stats_players()
        self.assertEqual(sp[MYA], {"games": 2, "goals": 2, "assists": 3})
        self.assertEqual(sp[KOV], {"games": 6, "goals": 22, "assists": 10})
        self.assertEqual(len(sp), 14)                                           # ни «Мякушко З», ни «Ковалев М» не появилось
        g = B.load_games()["games"][-1]
        self.assertEqual({p["name"] for t in g["teams"] for p in t}, {MYA, KOV})  # в журнале игр — тоже каноническое имя

    def test_manual_past_game_uses_same_resolver(self):
        day, teams, errors = B.parse_manual_past("12.10.2026\nБелые: мякушко з 1+2, ковалев м 2+0\nЧёрные: Иван 0+1")
        self.assertEqual(errors, [])
        self.assertEqual([n for n, _, _ in teams[0]], [MYA, KOV])
        _, _, errors = B.parse_manual_past("12.10.2026\nБелые: мякушко з 1+2, МЯКУШКО З. 0+0\nЧёрные: Иван 0+1")
        self.assertTrue(any("указан дважды" in e for e in errors))

    async def test_add_command_and_split_use_same_resolver(self):
        """/добавить (гости до деления): тот же разбор имени/рейтинга; при делении имя приводится к каноническому, а «Игрок 03» не режется."""
        voters = {str(100 + i): {"player": nm(i), "display": nm(i), "username": ""} for i in range(1, 12)}
        B.save_json(B.POLL_STATE_FILE, {"voters": voters})
        m = owner_msg()
        await B.cmd_add_guest(m, B.CommandObject(command="добавить", args="мякушко з, Игрок 03, Борис 2.5, ковалев м"))
        out = []

        async def reply(text, reply_markup=None, **kw):
            out.append(text)
        await B.do_split(reply)
        g = B.load_game()
        players = B.game_players(g)
        self.assertEqual(len(players), 15)
        self.assertIn(MYA, players)
        self.assertIn(KOV, players)
        self.assertNotIn("мякушко з", players)
        self.assertIn("Игрок 03", players)
        self.assertEqual(g["ratings"], {"Борис": 2.5})
        self.assertNotIn(KOV, g["teams"][RED])
        self.assertNotIn(MYA, g["guests"])

    # E. текущий UX сохранён
    async def test_menu_still_lists_known_players_outside_chat_and_hides_zero_games(self):
        st = self.stats_players()
        st["Без Игр Б"] = {"games": 0, "goals": 0, "assists": 0}
        self.write_stats(st)
        g = B.load_game()
        q = FakeQuery(f"sq:adm:{g['rev']}")
        await B.cb_squad(q)
        labels = [b.text for row in q.message.edits[-1][1].inline_keyboard for b in row if (b.callback_data or "").startswith("sq:ai:")]
        self.assertEqual(labels, sorted([MYA, KOV], key=B.norm_key))
        self.assertNotIn("Без Игр Б", labels)

    async def test_rebuild_after_batch_keeps_everyone(self):
        await self.batch("мякушко з, Иван, Пётр")
        g = B.load_game()
        everyone = sorted(B.game_players(g))
        self.assertEqual(len(everyone), 15)
        for _ in range(5):
            await B.cb_squad(FakeQuery(f"sq:rb:{B.load_game()['rev']}"))
            self.assertEqual(sorted(B.game_players(B.load_game())), everyone)


if __name__ == "__main__":
    unittest.main()
