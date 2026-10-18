"""«Записать прошлую игру»: незаписанная игра из архива записывается, пока активна следующая игра.
Все данные вымышленные; Telegram подменён заглушками."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_bot import Base, FakeQuery, owner_msg, cmd, all_button_data, read_bytes, B  # noqa: E402

TEAMS_PAST = [["Иванов И.", "Сидоров С."], ["Петров П.", "Гость Г."]]
TEAMS_NOW = [["Иванов И.", "Петров П."], ["Сидоров С.", "Новиков Н."]]


class PastGame(Base):
    def setUp(self):
        super().setUp()
        self.write_stats({"Иванов И.": {"games": 4, "goals": 9, "assists": 5},
                          "Петров П.": {"games": 4, "goals": 3, "assists": 7},
                          "Сидоров С.": {"games": 3, "goals": 1, "assists": 2}})
        B.load_games()
        B.save_json(B.POLL_STATE_FILE, {"poll": "текущий опрос", "votes": {"1": "+"}})
        # прошлая незаписанная игра (в архиве) и текущая активная игра
        past = B.new_game(TEAMS_PAST)
        past["id"], past["created"] = "20261012180000", "2026-10-12T18:00:00+05:00"
        B.archive_game(past)
        cur = B.new_game(TEAMS_NOW)
        cur["id"], cur["created"] = "20261017120000", "2026-10-17T12:00:00+05:00"
        B.save_game(cur)

    def snap(self):
        return {p: read_bytes(p) for p in (B.GAME_FILE, B.POLL_STATE_FILE)}

    async def press(self, data):
        q = FakeQuery(data)
        await B.cb_past(q)
        return q

    async def open_past(self):
        m = owner_msg()
        await B.cmd_past(m)
        return m, all_button_data(m.answers[-1][1])

    async def fill_and_record(self):
        _, datas = await self.open_past()
        await self.press([d for d in datas if d.startswith("pg:s:")][0])
        for _ in range(2):
            await self.press("pg:g+:0")                  # Иванов И. 2 гола
        await self.press("pg:a+:2")                      # Петров П. 1 передача
        q = await self.press("pg:prev")
        return q, [d for d in all_button_data(q.message.edits[-1][1]) if d.startswith("pg:ok:")]

    async def test_list_shows_only_archived_unrecorded_game(self):
        m, datas = await self.open_past()
        self.assertEqual([d for d in datas if d.startswith("pg:s:")], ["pg:s:20261012180000"])
        self.assertIn("12.10.2026", m.answers[-1][0])
        self.assertNotIn("17.10.2026", m.answers[-1][0])        # текущая игра в списке не участвует

    async def test_record_past_game_keeps_current_game_untouched(self):
        before = self.snap()
        q, ok = await self.fill_and_record()
        preview = q.message.edits[-1][0]
        self.assertIn("Игра №5", preview)
        self.assertIn("12.10.2026", preview)
        self.assertEqual(len(ok), 1)
        q = await self.press(ok[0])
        self.assertIn("Прошлая игра записана", q.message.edits[-1][0])
        games = B.load_games()["games"]
        g5 = [g for g in games if g["number"] == 5]
        self.assertEqual(len(g5), 1)
        self.assertEqual(g5[0]["date"], "2026-10-12")
        self.assertEqual([[p["name"] for p in t] for t in g5[0]["teams"]], TEAMS_PAST)   # составы именно прошлой игры
        pl = self.read_stats()[str(B.CHAT_ID)]["players"]
        self.assertEqual(pl["Иванов И."], {"games": 5, "goals": 11, "assists": 5})
        self.assertEqual(pl["Петров П."], {"games": 5, "goals": 3, "assists": 8})
        self.assertEqual(pl["Сидоров С."], {"games": 4, "goals": 1, "assists": 2})
        self.assertEqual(pl["Гость Г."], {"games": 1, "goals": 0, "assists": 0})
        self.assertNotIn("Новиков Н.", pl)                                               # игрок текущей игры не затронут
        self.assertEqual(self.snap(), before)                                            # game.json и poll_state.json побайтно те же
        arch = json.load(open(B.GAME_ARCHIVE_FILE, encoding="utf-8"))
        self.assertTrue(arch[0]["result_recorded"] and arch[0]["game_number"] == 5)
        self.assertFalse(os.path.exists(B.PAST_DRAFT_FILE))

    async def test_second_record_is_blocked(self):
        _, ok = await self.fill_and_record()
        await self.press(ok[0])
        stats_after = read_bytes(B.STATS_FILE)
        games_after = read_bytes(B.GAMES_FILE)
        q = await self.press(ok[0])                                                      # повторное нажатие той же кнопки
        self.assertTrue(q.answered and q.answered[0][1])                                 # alert, ничего не записано
        self.assertEqual(read_bytes(B.STATS_FILE), stats_after)
        self.assertEqual(read_bytes(B.GAMES_FILE), games_after)
        _, datas = await self.open_past()                                                # и из списка игра исчезла
        self.assertFalse([d for d in datas if d.startswith("pg:s:")])

    async def test_duplicate_guard_without_archive_mark(self):
        """Даже если пометка в архиве потеряна, игра с той же датой и тем же составом не записывается второй раз."""
        _, ok = await self.fill_and_record()
        await self.press(ok[0])
        arch = json.load(open(B.GAME_ARCHIVE_FILE, encoding="utf-8"))
        arch[0].pop("result_recorded"), arch[0].pop("game_number")
        B.save_json(B.GAME_ARCHIVE_FILE, arch)
        _, datas = await self.open_past()
        self.assertFalse([d for d in datas if d.startswith("pg:s:")])

    async def test_stale_archive_entries_before_last_recorded_game_not_offered(self):
        """Старые пересборки составов (раньше последней записанной игры) в списке «незаписанных» не показываются."""
        B.record_match_full([("Иванов И.", 1, 0), ("Петров П.", 0, 1)], teams=[["Иванов И."], ["Петров П."]], date="2026-10-05")
        old = B.new_game([["Иванов И."], ["Петров П."]])
        old["id"], old["created"] = "20261003100000", "2026-10-03T10:00:00+05:00"
        B.archive_game(old)
        m, datas = await self.open_past()
        self.assertEqual([d for d in datas if d.startswith("pg:s:")], ["pg:s:20261012180000"])

    async def test_resplits_of_current_evening_not_offered_and_one_entry_per_evening(self):
        """Пересборки состава ближайшего вечера (как у текущей игры) не считаются прошлой игрой; на прошлый вечер — один, последний состав."""
        resplit = B.new_game([["Иванов И."], ["Петров П."]])
        resplit["id"], resplit["created"] = "20261017100000", "2026-10-17T10:00:00+05:00"     # суббота → вечер 19.10, как у текущей игры
        B.archive_game(resplit)
        earlier = B.new_game([["Иванов И.", "Гость Г."], ["Петров П."]])
        earlier["id"], earlier["created"] = "20261012120000", "2026-10-12T12:00:00+05:00"     # тот же вечер 12.10, раньше последнего состава
        B.archive_game(earlier)
        m, datas = await self.open_past()
        self.assertEqual([d for d in datas if d.startswith("pg:s:")], ["pg:s:20261012180000"])

    async def test_evening_date_is_next_monday_after_roster_creation(self):
        self.assertEqual(B._past_default_date({"created": "2026-10-11T23:00:00+05:00"}), "2026-10-12")   # воскресенье → понедельник
        self.assertEqual(B._past_default_date({"created": "2026-10-12T18:00:00+05:00"}), "2026-10-12")
        self.assertEqual(B._past_default_date({"created": "2026-10-13T00:30:00+05:00"}), "2026-10-12")   # после полуночи — прошедший вечер

    async def test_no_archived_game_does_not_use_current_roster(self):
        B.save_json(B.GAME_ARCHIVE_FILE, [])
        m, datas = await self.open_past()
        self.assertIn("не найдено", m.answers[-1][0])
        self.assertFalse([d for d in datas if d.startswith("pg:s:")])

    async def test_date_can_be_corrected_before_record(self):
        _, datas = await self.open_past()
        await self.press([d for d in datas if d.startswith("pg:s:")][0])
        m = owner_msg()
        await B.cmd_past_date(m, cmd("датаигры", "13.10.2026"))
        self.assertIn("13.10.2026", m.answers[-1][0])
        bad = owner_msg()
        await B.cmd_past_date(bad, cmd("датаигры", "31.02.2026"))
        self.assertIn("Формат", bad.answers[-1][0])
        q = await self.press("pg:prev")
        ok = [d for d in all_button_data(q.message.edits[-1][1]) if d.startswith("pg:ok:")][0]
        await self.press(ok)
        self.assertEqual([g["date"] for g in B.load_games()["games"] if g["number"] == 5], ["2026-10-13"])

    # ---- сценарий Б: состава в архиве нет, игра вводится вручную ----

    MANUAL = ("12.10.2026\nБелые: Иванов И. 3+1, Сидоров С. 1+0\nКрасные: Петров П. 2+2, Гость Первый 1+1\nЧёрные: Гость Второй 0+2, Гость Г. 4+0")

    async def manual_preview(self, text=None):
        m = owner_msg()
        await B.cmd_past_manual(m, cmd("прошлаяигра", text or self.MANUAL))
        return m, m.answers[-1][0], m.answers[-1][1]

    async def test_manual_game_without_archive_keeps_current_game_untouched(self):
        B.save_json(B.GAME_ARCHIVE_FILE, [])                     # архив пуст, как 12.10: бот не работал при формировании состава
        before = self.snap()
        m, text, markup = await self.manual_preview()
        self.assertIn("Игра №5 · 12.10.2026", text)
        self.assertIn("Команда 1 (белые) — ⚽ 4 · 🎯 1", text)
        self.assertIn("Команда 2 (чёрные) — ⚽ 4 · 🎯 2", text)   # цвета задают порядок: «Чёрные» — вторая команда, хотя введены третьими
        self.assertIn("Команда 3 (красные) — ⚽ 3 · 🎯 3", text)
        self.assertIn("Итого: игроков 6, голов 11, передач 6.", text)
        self.assertIn("🆕 Новые игроки", text)                    # неизвестные имена отмечены
        self.assertNotIn("Новиков", text)                         # текущий состав не подставляется
        ok = [d for d in all_button_data(markup) if d.startswith("pg:ok:")]
        self.assertEqual(len(ok), 1)
        stats_before = read_bytes(B.STATS_FILE)
        self.assertEqual(read_bytes(B.STATS_FILE), stats_before)  # предпросмотр статистику не меняет
        q = await self.press(ok[0])
        self.assertIn("Прошлая игра записана", q.message.edits[-1][0])
        g = [x for x in B.load_games()["games"] if x["number"] == 5][0]
        self.assertEqual(g["date"], "2026-10-12")
        self.assertEqual([[p["name"] for p in t] for t in g["teams"]],
                         [["Иванов И.", "Сидоров С."], ["Гость Второй", "Гость Г."], ["Петров П.", "Гость Первый"]])
        pl = self.read_stats()[str(B.CHAT_ID)]["players"]
        self.assertEqual(pl["Иванов И."], {"games": 5, "goals": 12, "assists": 6})
        self.assertEqual(pl["Гость Первый"], {"games": 1, "goals": 1, "assists": 1})
        self.assertEqual(self.snap(), before)                      # №7: game.json и poll_state.json побайтно те же
        self.assertFalse(os.path.exists(B.PAST_DRAFT_FILE))
        # стандартные коэффициенты считает штатная функция
        self.assertAlmostEqual(B.koef_of(pl["Иванов И."]), (12 * 2 + 6) / 5)

    async def test_manual_second_record_and_same_date_blocked(self):
        _, _, markup = await self.manual_preview()
        ok = [d for d in all_button_data(markup) if d.startswith("pg:ok:")][0]
        await self.press(ok)
        stats_after = read_bytes(B.STATS_FILE)
        q = await self.press(ok)                                   # та же кнопка ещё раз
        self.assertTrue(q.answered and q.answered[0][1])
        self.assertEqual(read_bytes(B.STATS_FILE), stats_after)
        m, text, _ = await self.manual_preview()                   # та же дата другим вводом
        self.assertIn("уже записана", text)
        self.assertEqual(read_bytes(B.STATS_FILE), stats_after)

    async def test_manual_input_errors_do_not_write_anything(self):
        stats_before = read_bytes(B.STATS_FILE)
        games_before = read_bytes(B.GAMES_FILE)
        for bad in ("31.02.2026\nБелые: Иванов И. 1+0\nКрасные: Петров П. 0+1",             # нет такой даты
                    "01.01.2099\nБелые: Иванов И. 1+0\nКрасные: Петров П. 0+1",             # будущее
                    "12.10.2026\nБелые: Иванов И. 1+0",                                      # одна команда
                    "12.10.2026\nБелые: Иванов И. 1+0\nКрасные: Иванов И. 0+1",             # игрок в двух командах
                    "12.10.2026\nБелые: Иванов И. 2+x\nКрасные: Петров П. 0+1",             # не разобрать
                    "12.10.2026\nБелые Иванов И. 1+0\nКрасные: Петров П. 0+1"):            # нет двоеточия
            m, text, _ = await self.manual_preview(bad)
            self.assertIn("Не записываю", text, bad)
        self.assertEqual((read_bytes(B.STATS_FILE), read_bytes(B.GAMES_FILE)), (stats_before, games_before))
        self.assertIsNone(B.past_draft_load())

    async def test_manual_typo_without_digits_is_flagged_as_new_player(self):
        _, text, _ = await self.manual_preview("12.10.2026\nБелые: Иванов И. много\nКрасные: Петров П. 0+1")
        self.assertIn("🆕 Новые игроки", text)                      # опечатка не пройдёт незамеченной: имя помечено «новый игрок»
        self.assertIn("Иванов И. Много", text)

    async def test_manual_without_text_shows_format_and_not_owner_rejected(self):
        m = owner_msg()
        await B.cmd_past_manual(m, cmd("прошлаяигра", None))
        self.assertIn("/прошлаяигра 12.10.2026", m.answers[-1][0])
        stranger = owner_msg()
        stranger.from_user.id = 999
        await B.cmd_past_manual(stranger, cmd("прошлаяигра", self.MANUAL))
        self.assertFalse(stranger.answers)

    async def test_list_offers_manual_entry_even_without_archive(self):
        B.save_json(B.GAME_ARCHIVE_FILE, [])
        m, datas = await self.open_past()
        self.assertIn("pg:man", datas)
        self.assertIn("НЕ подставляет", m.answers[-1][0])

    async def test_several_resplits_of_one_evening_use_latest(self):
        later = B.new_game([["Иванов И.", "Гость Г."], ["Петров П.", "Сидоров С."]])
        later["id"], later["created"] = "20261012190000", "2026-10-12T19:00:00+05:00"        # позже исходного (18:00), тот же вечер
        B.archive_game(later)
        _, datas = await self.open_past()
        self.assertEqual([d for d in datas if d.startswith("pg:s:")], ["pg:s:20261012190000"])

    async def test_not_owner_is_rejected(self):
        q = FakeQuery("pg:l", user_id=999)
        await B.cb_past(q)
        self.assertEqual(q.answered[-1], ("Недоступно", True))


if __name__ == "__main__":
    unittest.main()
