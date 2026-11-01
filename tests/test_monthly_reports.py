"""Автоматические месячные итоги из games.json без изменения игровой статистики."""
import datetime as dt
import os

from test_bot import Base, FakeMsg, FakeQuery, B, OWNER, CHAT  # noqa: E402


NOV_MONDAYS = ["2026-11-02", "2026-11-09", "2026-11-16", "2026-11-23", "2026-11-30"]


def registry(names=("Альфа А.", "Бета Б.", "Гамма В.", "Дельта Г.")):
    return {
        "version": 1,
        "players": {name: {"usernames": [], "ids": [], "aliases": [], "pid": i}
                    for i, name in enumerate(names, 1)},
        "next_pid": len(names) + 1,
    }


def game(number, date, rows, status=None):
    result = {
        "number": number,
        "date": date,
        "teams": [[{"name": name, "pid": pid, "goals": goals, "assists": assists}
                   for name, pid, goals, assists in rows]],
        "source": "result",
        "recorded_at": "2099-01-01T00:00:00+05:00",
    }
    if status:
        result["status"] = status
        if status == "reverted":
            result["teams"] = None
    return result


def games_data(games):
    return {"version": 1, "games": games, "annulled": [], "history_filled": True}


def rows_for(index, include_delta=True):
    rows = [
        ("Альфа А.", 1, index + 1, 0),
        ("Бета Б.", 2, 0, index + 1),
        ("Гамма В.", 3, 0, 0),
    ]
    if include_delta:
        rows.append(("Дельта Г.", 4, 1, 1))
    return rows


class MonthlyReports(Base):
    def write(self, games, players=None):
        B.save_json(B.GAMES_FILE, games_data(games))
        B.save_json(B.PLAYERS_FILE, players or registry())

    def report(self, count, *, missed_delta=False):
        games = [game(i + 1, NOV_MONDAYS[i], rows_for(i, include_delta=not (missed_delta and i == 1)))
                 for i in range(count)]
        return B.calculate_monthly_report("2026-11", games_data(games), registry())

    async def test_admin_section_is_owner_private_only(self):
        stranger = FakeMsg(777, "private", 777)
        await B.cmd_monthly(stranger)
        self.assertEqual(stranger.answers, [])
        group = FakeMsg(CHAT, "group", OWNER)
        await B.cmd_monthly(group)
        self.assertEqual(group.answers, [])
        owner = FakeMsg(OWNER, "private", OWNER)
        await B.cmd_monthly(owner)
        self.assertEqual(len(owner.answers), 1)

    def test_months_with_three_four_and_five_played_games(self):
        for count in (3, 4, 5):
            report = self.report(count)
            self.assertEqual(report["game_count"], count)
            self.assertEqual(report["eligible_count"], 4)
            self.assertEqual(len(report["top3"]), 3)

    def test_attendance_requires_every_played_game(self):
        report = self.report(4, missed_delta=True)
        delta = next(row for row in report["overall"] if row["pid"] == 4)
        self.assertEqual(delta["games"], 3)
        self.assertNotIn(4, [row["pid"] for row in report["eligible"]])
        self.assertEqual(report["ineligible_count"], 1)

    def test_zero_output_player_is_kept(self):
        report = self.report(4)
        gamma = next(row for row in report["overall"] if row["pid"] == 3)
        self.assertEqual((gamma["games"], gamma["goals"], gamma["assists"], gamma["points"], gamma["k"]),
                         (4, 0, 0, 0, 0.0))

    def test_non_prize_selection_keeps_true_overall_places(self):
        report = self.report(4)
        winners = {row["identity"] for row in report["top3"]}
        self.assertTrue(all(row["identity"] not in winners for row in report["non_prize"]))
        overall_places = {row["identity"]: row["rank"] for row in report["overall"]}
        self.assertEqual([row["rank"] for row in report["non_prize"]],
                         [overall_places[row["identity"]] for row in report["non_prize"]])

    def test_recorded_at_is_ignored_but_game_date_is_used(self):
        first = game(1, "2026-11-02", rows_for(0))
        a = B.calculate_monthly_report("2026-11", games_data([first]), registry())
        first["recorded_at"] = "2027-05-01T00:00:00+05:00"
        b = B.calculate_monthly_report("2026-11", games_data([first]), registry())
        self.assertEqual(a["fingerprint"], b["fingerprint"])
        first["date"] = "2026-12-02"
        c = B.calculate_monthly_report("2026-11", games_data([first]), registry())
        self.assertEqual(c["game_count"], 0)

    def test_guest_without_pid_is_counted_and_flagged(self):
        g = game(1, NOV_MONDAYS[0], [("Гость", None, 2, 1)])
        report = B.calculate_monthly_report("2026-11", games_data([g]), registry())
        self.assertEqual(report["participants"], 1)
        self.assertEqual(report["overall"][0]["points"], 5)
        self.assertTrue(report["review"])

    def test_full_tie_on_prize_place_requests_decision(self):
        games = [game(i + 1, day, [("Альфа А.", 1, 1, 0), ("Бета Б.", 2, 1, 0),
                                    ("Гамма В.", 3, 0, 0), ("Дельта Г.", 4, 0, 0)])
                 for i, day in enumerate(NOV_MONDAYS[:4])]
        report = B.calculate_monthly_report("2026-11", games_data(games), registry())
        self.assertTrue(report["prize_ties"])
        self.assertEqual(report["top3"], [])
        self.assertIn("Нужна редакторская очередность", B.monthly_top_text(report))

    def test_reverted_result_is_not_counted(self):
        games = [game(1, NOV_MONDAYS[0], rows_for(0)), game(2, NOV_MONDAYS[1], rows_for(1), "reverted")]
        report = B.calculate_monthly_report("2026-11", games_data(games), registry())
        self.assertEqual(report["game_count"], 1)
        self.assertEqual([x["number"] for x in report["games"]], [1])

    async def test_missing_game_sends_one_owner_choice_only(self):
        games = [game(i + 1, day, rows_for(i)) for i, day in enumerate(NOV_MONDAYS[:4])]
        self.write(games)
        now = dt.datetime(2026, 12, 1, 10, tzinfo=B.YEKB_TZ)
        await B.process_monthly_reports(now)
        await B.process_monthly_reports(now)
        self.assertEqual(len(self.fake.sent), 1)
        self.assertEqual(self.fake.sent[0][0], OWNER)
        store = B.load_monthly_store()
        self.assertEqual(store["months"]["2026-11"]["status"], "open")

    async def test_later_choice_is_not_repeated_and_late_result_closes_month(self):
        games = [game(i + 1, day, rows_for(i)) for i, day in enumerate(NOV_MONDAYS[:4])]
        self.write(games)
        store = {"version": 1, "months": {"2026-11": {
            "status": "open", "missing": {NOV_MONDAYS[4]: "later"}, "missing_notice": None,
            "notifications": {}, "report": None, "created_at": B.now_iso(),
        }}}
        B.save_monthly_store(store)
        now = dt.datetime(2026, 12, 2, 10, tzinfo=B.YEKB_TZ)
        await B.process_monthly_reports(now)
        self.assertEqual(self.fake.sent, [])

        games.append(game(5, NOV_MONDAYS[4], rows_for(4)))
        self.write(games)
        await B.process_monthly_reports(now)
        entry = B.load_monthly_store()["months"]["2026-11"]
        self.assertIn(entry["status"], ("closed", "needs_tie_decision"))
        self.assertEqual(entry["report"]["game_count"], 5)
        self.assertTrue(self.fake.sent)
        self.assertTrue(all(chat_id == OWNER for chat_id, _ in self.fake.sent))

    async def test_no_game_decisions_close_without_winner_ranking(self):
        self.write([])
        entry = {"status": "open", "missing": {day: "no_game" for day in NOV_MONDAYS},
                 "missing_notice": None, "notifications": {}, "report": None, "created_at": B.now_iso()}
        B.save_monthly_store({"version": 1, "months": {"2026-11": entry}})
        await B.process_monthly_reports(dt.datetime(2026, 12, 1, 10, tzinfo=B.YEKB_TZ))
        saved = B.load_monthly_store()["months"]["2026-11"]
        self.assertEqual(saved["report"]["game_count"], 0)
        self.assertEqual(saved["report"]["top3"], [])
        self.assertIn("не было состоявшихся игр", self.fake.sent[0][1])

    async def test_restart_does_not_duplicate_reports_or_notices(self):
        games = [game(i + 1, day, rows_for(i)) for i, day in enumerate(NOV_MONDAYS)]
        self.write(games)
        now = dt.datetime(2026, 11, 30, 23, tzinfo=B.YEKB_TZ)
        await B.process_monthly_reports(now)
        count = len(self.fake.sent)
        B.AWAITING.clear()
        await B.process_monthly_reports(now)
        self.assertEqual(len(self.fake.sent), count)

    async def test_old_result_change_keeps_snapshot_until_confirmation(self):
        games = [game(i + 1, day, rows_for(i)) for i, day in enumerate(NOV_MONDAYS)]
        self.write(games)
        now = dt.datetime(2026, 11, 30, 23, tzinfo=B.YEKB_TZ)
        await B.process_monthly_reports(now)
        before = B.load_monthly_store()["months"]["2026-11"]["report"]["fingerprint"]
        self.fake.sent.clear()
        games[0]["teams"][0][0]["goals"] += 1
        self.write(games)
        await B.process_monthly_reports(now)
        entry = B.load_monthly_store()["months"]["2026-11"]
        self.assertEqual(entry["report"]["fingerprint"], before)
        self.assertEqual(len(self.fake.sent), 1)
        self.assertIn("изменилась. Пересчитать", self.fake.sent[0][1])

    async def test_rename_by_same_pid_does_not_trigger_change(self):
        games = [game(i + 1, day, rows_for(i)) for i, day in enumerate(NOV_MONDAYS)]
        players = registry()
        self.write(games, players)
        now = dt.datetime(2026, 11, 30, 23, tzinfo=B.YEKB_TZ)
        await B.process_monthly_reports(now)
        self.fake.sent.clear()
        rec = players["players"].pop("Альфа А.")
        players["players"]["Альфа Новый"] = rec
        B.save_json(B.PLAYERS_FILE, players)
        await B.process_monthly_reports(now)
        self.assertEqual(self.fake.sent, [])
        report = B.report_with_current_names(B.load_monthly_store()["months"]["2026-11"]["report"], players)
        self.assertIn("Альфа Новый", [row["name"] for row in report["overall"]])

    async def test_confirmed_recalculation_preserves_previous_revision(self):
        games = [game(i + 1, day, rows_for(i)) for i, day in enumerate(NOV_MONDAYS)]
        self.write(games)
        now = dt.datetime(2026, 11, 30, 23, tzinfo=B.YEKB_TZ)
        await B.process_monthly_reports(now)
        games[0]["teams"][0][0]["assists"] += 2
        self.write(games)
        await B.process_monthly_reports(now)
        self.fake.sent.clear()
        await B.cb_monthly(FakeQuery("mo:recalc:202611"))
        entry = B.load_monthly_store()["months"]["2026-11"]
        self.assertEqual(len(entry["revisions"]), 1)
        self.assertNotEqual(entry["revisions"][0]["fingerprint"], entry["report"]["fingerprint"])
        self.assertTrue(self.fake.sent)

    async def test_october_is_view_only_and_never_retroactively_sent(self):
        october = [game(i + 1, f"2026-10-{5 + i * 7:02d}", rows_for(i)) for i in range(4)]
        self.write(october)
        await B.process_monthly_reports(dt.datetime(2026, 11, 1, 12, tzinfo=B.YEKB_TZ))
        self.assertEqual(self.fake.sent, [])
        self.assertFalse(os.path.exists(B.MONTHLY_REPORTS_FILE))
        preview = B.calculate_monthly_report("2026-10", B.load_games(), B.load_players())
        self.assertEqual(preview["game_count"], 4)

    async def test_reports_never_send_to_group_chat(self):
        games = [game(i + 1, day, rows_for(i)) for i, day in enumerate(NOV_MONDAYS)]
        self.write(games)
        await B.process_monthly_reports(dt.datetime(2026, 11, 30, 23, tzinfo=B.YEKB_TZ))
        self.assertNotIn(CHAT, [chat_id for chat_id, _ in self.fake.sent])
