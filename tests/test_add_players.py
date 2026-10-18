"""«Добавить игрока» (известные игроки из статистики кнопками) и пакетное «Добавить нового гостя». Данные вымышленные;
Telegram подменён заглушками; текущая игра — копия, production не затрагивается."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_bot import Base, FakeQuery, owner_msg, all_button_data, all_button_text, B  # noqa: E402

KOV = "Ковалёв М."
LET = "АБВГДЕЖЗИКЛМНОПРСТУФ"


def nm(i):
    """Вымышленное имя без цифр на конце (число на конце имени бот читает как рейтинг: «Алексей 2.3»)."""
    return f"Игрок {LET[i - 1]}"
RED = B.TEAM_COLORS.index("красные")


def stats_for(extra=None):
    st = {nm(i): {"games": 3, "goals": 4, "assists": 2} for i in range(1, 15)}
    st[KOV] = {"games": 5, "goals": 20, "assists": 10}
    st["Мякушко З."] = {"games": 1, "goals": 1, "assists": 2}                 # сыграл, но в чате/реестре его нет
    st["Иван От Саши"] = {"games": 2, "goals": 1, "assists": 1}               # то же
    st["Новичок Без Игр"] = {"games": 0, "goals": 0, "assists": 0}            # в статистике, но ни одной игры
    st.update(extra or {})
    return st


class AddPlayers(Base):
    def setUp(self):
        super().setUp()
        self.write_stats(stats_for())
        names = [nm(i) for i in range(1, 13)]                      # 12 игроков в составе, 2 команды
        self.g = B.new_game([names[:6], names[6:]])
        B.save_game(self.g)

    def sp(self):
        return B.stats_players_of(B.load_stats())

    async def open_add(self, g=None):
        g = g or B.load_game()
        q = FakeQuery("sq:adm:" + str(g["rev"]))
        await B.cb_squad(q)
        return q, q.message.edits[-1][1]

    def menu_names(self, markup):
        return [b.text for row in markup.inline_keyboard for b in row if b.callback_data and b.callback_data.startswith("sq:ai:")]

    # 1–4. известные игроки из статистики
    async def test_known_players_outside_chat_are_buttons(self):
        _, markup = await self.open_add()
        names = self.menu_names(markup)
        self.assertIn("Мякушко З.", names)                                    # нет в реестре игроков/чате, но сыграл
        self.assertIn("Иван От Саши", names)
        self.assertIn(KOV, names)
        self.assertIn(nm(13), names)

    async def test_players_already_in_squad_and_without_games_not_offered(self):
        _, markup = await self.open_add()
        names = self.menu_names(markup)
        for n in B.game_players(B.load_game()):
            self.assertNotIn(n, names)                                        # 2. уже в составе
        self.assertNotIn("Новичок Без Игр", names)                            # 3. games = 0
        self.assertEqual(len(names), len({B.norm_key(n) for n in names}))     # без дублей

    async def test_duplicates_after_normalization_collapse(self):
        self.write_stats(stats_for({"ковалев м.": {"games": 2, "goals": 0, "assists": 0}}))
        self.assertEqual(sum(1 for n in B.known_players(self.sp()) if B.norm_key(n) == B.norm_key(KOV)), 1)

    async def test_many_known_players_paginated_and_clickable(self):
        extra = {f"Запас {LET[i % 20]}{i}": {"games": 1, "goals": 0, "assists": 0} for i in range(1, 51)}
        self.write_stats(stats_for(extra))
        q, markup = await self.open_add()
        shown = self.menu_names(markup)
        self.assertEqual(len(shown), B.ADD_PAGE_SIZE)
        self.assertIn("sq:pg:" + str(B.load_game()["rev"]) + ":1", all_button_data(markup))
        q2 = FakeQuery(f"sq:pg:{B.load_game()['rev']}:1")
        await B.cb_squad(q2)
        shown2 = self.menu_names(q2.message.edits[-1][1])
        self.assertTrue(shown2)
        self.assertFalse(set(shown) & set(shown2))
        pick = shown2[0]
        data = [b.callback_data for row in q2.message.edits[-1][1].inline_keyboard for b in row if b.text == pick][0]
        await B.cb_squad(FakeQuery(data))                                     # кнопка со второй страницы выбирает именно этого игрока
        self.assertEqual(B.load_game()["pending"]["in"], pick)

    async def test_guest_button_present_and_prompt_text(self):
        g = B.load_game()
        _, markup = await self.open_add(g)
        self.assertIn("✍️ Добавить нового гостя", all_button_text(markup))
        q = FakeQuery(f"sq:ag:{g['rev']}")
        await B.cb_squad(q)
        self.assertIn("через запятую", q.message.edits[-1][0])
        self.assertIn("Иван, Пётр от Вадима, Сергей", q.message.edits[-1][0])

    # 5–10. пакетное добавление
    async def batch(self, text):
        g = B.load_game()
        await B.cb_squad(FakeQuery(f"sq:adm:{g['rev']}"))
        await B.cb_squad(FakeQuery(f"sq:ag:{B.load_game()['rev']}"))
        m = owner_msg(text)
        await B.owner_text_input(m)
        return m

    async def test_three_new_guests_one_operation(self):
        before = set(B.game_players(B.load_game()))
        m = await self.batch("Иван, Пётр, Сергей")
        self.assertIn("Добавлено: 3 игрока", m.answers[-1][0])
        g = B.load_game()
        added = set(B.game_players(g)) - before
        self.assertEqual(added, {"Иван", "Пётр", "Сергей"})
        self.assertEqual(len(B.game_players(g)), 15)
        self.assertEqual(len(m.answers), 1)                                   # одно сообщение, а не три
        self.assertIsNone(g["pending"])
        self.assertTrue(set(g["guests"]) >= {"Иван", "Пётр", "Сергей"})        # новые люди — гости по текущим правилам
        self.assertIn("sq:rbc:" + str(g["rev"]) if False else "sq:rbc", " ".join(all_button_data(m.answers[-1][1])))

    async def test_spaces_newlines_and_phrases(self):
        m = await self.batch("  Фёдор от Саши ,Пётр   от   Вадима ;\n Николай ,, ")
        g = B.load_game()
        players = B.game_players(g)
        for n in ("Фёдор От Саши", "Пётр От Вадима", "Николай"):
            self.assertIn(n, players)
        self.assertEqual(len(players), 15)

    async def test_duplicates_in_input_and_already_in_squad(self):
        m = await self.batch(f"Иван, иван, ИВАН, {nm(3)}, Пётр")
        text = m.answers[-1][0]
        g = B.load_game()
        players = B.game_players(g)
        self.assertEqual(sum(1 for n in players if B.norm_key(n) == "иван"), 1)
        self.assertEqual(sum(1 for n in players if B.norm_key(n) == B.norm_key(nm(3))), 1)
        self.assertIn("Добавлено: 2 игрока", text)
        self.assertIn("дубль во введённом списке", text)
        self.assertIn(f"{nm(3)} — уже в составе", text)
        self.assertEqual(len(players), len(set(players)))

    async def test_known_player_typed_as_guest_is_not_duplicated(self):
        m = await self.batch("мякушко з. , Сергей")
        g = B.load_game()
        players = B.game_players(g)
        self.assertIn("Мякушко З.", players)                                   # каноническое имя из статистики
        self.assertEqual(sum(1 for n in players if B.norm_key(n).startswith("мякушко")), 1)
        self.assertNotIn("Мякушко З.", g["guests"])                            # не гость
        self.assertIn("Сергей", g["guests"])                                   # а вот новый — гость
        self.assertIn("известный игрок", m.answers[-1][0])

    async def test_nothing_new_to_add_keeps_waiting(self):
        g = B.load_game()
        await B.cb_squad(FakeQuery(f"sq:adm:{g['rev']}"))
        await B.cb_squad(FakeQuery(f"sq:ag:{B.load_game()['rev']}"))
        m = owner_msg(f"{nm(1)}, {nm(1).lower()}")
        await B.owner_text_input(m)
        self.assertIn("Никого не добавил", m.answers[-1][0])
        self.assertIn(B.OWNER_ID, B.AWAITING)                                    # можно ввести заново
        self.assertEqual(len(B.game_players(B.load_game())), 12)

    async def test_single_name_keeps_old_flow(self):
        g = B.load_game()
        await B.cb_squad(FakeQuery(f"sq:adm:{g['rev']}"))
        await B.cb_squad(FakeQuery(f"sq:ag:{B.load_game()['rev']}"))
        m = owner_msg("Алексей 2.5")
        await B.owner_text_input(m)
        datas = all_button_data(m.answers[-1][1])
        self.assertTrue(any(d.startswith("sq:at:") for d in datas))            # выбор команды, как раньше
        self.assertEqual(len(B.game_players(B.load_game())), 12)

    async def test_batch_not_allowed_for_replacement(self):
        g = B.load_game()
        out = g["teams"][0][0]
        idx = [i for i, (_, n) in enumerate(B.squad_flat(g)) if n == out][0]
        await B.cb_squad(FakeQuery(f"sq:ro:{g['rev']}:{idx}"))
        await B.cb_squad(FakeQuery(f"sq:ag:{B.load_game()['rev']}"))
        m = owner_msg("Иван, Пётр")
        await B.owner_text_input(m)
        self.assertIn("один игрок", m.answers[-1][0])
        self.assertEqual(B.game_players(B.load_game()), B.game_players(g))

    async def test_limit_of_names(self):
        names = ", ".join(f"Гость{i}" for i in range(B.GUEST_BATCH_MAX + 1))
        m = await self.batch(names)
        self.assertIn("Слишком много имён", m.answers[-1][0])
        self.assertEqual(len(B.game_players(B.load_game())), 12)

    # 11–14. пересборка после пакета, ничего не потеряно, правило Ковалёва
    async def test_rebuild_after_batch_keeps_everyone_and_kovalev_rule(self):
        await self.batch(f"{KOV}, Иван, Пётр")                                  # Ковалёв как новый ввод и пакет вместе
        g = B.load_game()
        self.assertIn(KOV, B.game_players(g))
        self.assertEqual(len(B.game_players(g)), 15)
        self.assertEqual(len(g["teams"]), 2)                                    # 15 игроков в составе, но деление остаётся прежним по командам
        everyone = sorted(B.game_players(g))
        for _ in range(8):
            q = FakeQuery(f"sq:rb:{B.load_game()['rev']}")
            await B.cb_squad(q)
            now = B.load_game()
            self.assertEqual(sorted(B.game_players(now)), everyone)             # никто не потерян и не продублирован
            self.assertEqual(sum(len(t) for t in now["teams"]), 15)
            if len(now["teams"]) > RED:
                self.assertNotIn(KOV, now["teams"][RED])

    async def test_kovalev_never_red_in_batch_with_three_teams(self):
        names = [nm(i) for i in range(1, 15)]
        g = B.new_game([names[:5], names[5:10], names[10:14]])                 # красная команда — самая маленькая
        B.save_game(g)
        await self.batch(f"{KOV}, Иван, Пётр")
        after = B.load_game()
        self.assertIn(KOV, B.game_players(after))
        self.assertNotIn(KOV, after["teams"][RED])
        for _ in range(10):
            await B.cb_squad(FakeQuery(f"sq:rb:{B.load_game()['rev']}"))
            self.assertNotIn(KOV, B.load_game()["teams"][RED])

    async def test_known_player_button_with_kovalev_rule(self):
        names = [nm(i) for i in range(1, 15)]
        g = B.new_game([names[:5], names[5:10], names[10:14]])
        B.save_game(g)
        _, markup = await self.open_add(g)
        names_menu = self.menu_names(markup)
        data = [b.callback_data for row in markup.inline_keyboard for b in row if b.text == KOV][0]
        await B.cb_squad(FakeQuery(data))
        rev = B.load_game()["rev"]
        q = FakeQuery(f"sq:at:{rev}:{RED}")
        await B.cb_squad(q)
        self.assertIn("не может играть в Красной команде", " ".join(t for t, _ in q.message.answers))
        self.assertNotIn(KOV, B.game_players(B.load_game()))
        await B.cb_squad(FakeQuery(f"sq:ar:{rev}"))                             # «Пересобрать» при добавлении
        self.assertNotIn(KOV, B.load_game()["teams"][RED])

    # копия состава №7 + известные игроки вне состава
    async def test_game7_copy_with_known_players_outside(self):
        sp = {nm(i): {"games": 4, "goals": 6, "assists": 5} for i in range(1, 13)}
        sp.update({f"Известный {LET[i - 1]}": {"games": 1 + i, "goals": 2, "assists": 1} for i in range(1, 6)})        # сыграли, не в составе
        self.write_stats(sp)
        names = [nm(i) for i in range(1, 13)]
        B.save_game(B.new_game([names[:6], names[6:]]))
        _, markup = await self.open_add()
        menu = self.menu_names(markup)
        self.assertEqual(menu, [f"Известный {LET[i - 1]}" for i in range(1, 6)])
        m = await self.batch("Известный А, известный Б, Гость Новый")
        g = B.load_game()
        self.assertEqual(len(B.game_players(g)), 15)
        self.assertEqual(sum(1 for n in B.game_players(g) if n.startswith("Известный")), 2)
        self.assertNotIn("Известный А", g["guests"])


if __name__ == "__main__":
    unittest.main()
