"""Узкий production Data export: только чтение, allowlist и Bearer-авторизация."""
import os
import sys
from unittest import mock

from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_bot import Base, read_bytes, B  # noqa: E402


TOKEN = "test-token-" + "x" * 40


class DataRead(Base):
    async def asyncSetUp(self):
        self.write_stats({"Азиз": {"games": 4, "goals": 12, "assists": 6}})
        B.load_players()
        B.load_games()
        self.client = TestClient(TestServer(B.create_data_read_app(TOKEN)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    def auth(self):
        return {"Authorization": "Bearer " + TOKEN}

    async def test_authorized_live_file_is_exact_and_read_only(self):
        before = read_bytes(B.GAMES_FILE)
        response = await self.client.get("/codex-data/games.json", headers=self.auth())
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.read(), before)
        self.assertEqual(read_bytes(B.GAMES_FILE), before)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertTrue(response.headers["ETag"].strip('"'))
        self.assertIn("X-Data-Mtime", response.headers)

    async def test_auth_is_required_and_token_is_not_returned(self):
        for headers in ({}, {"Authorization": "Bearer wrong"}):
            response = await self.client.get("/codex-data/stats.json", headers=headers)
            body = await response.text()
            self.assertEqual(response.status, 401)
            self.assertNotIn(TOKEN, body)

    async def test_allowlist_blocks_secrets_paths_and_writes(self):
        for name in (".env", "../stats.json", "last_offset.txt", "backups"):
            response = await self.client.get("/codex-data/" + name, headers=self.auth())
            self.assertIn(response.status, (404,))
        response = await self.client.post("/codex-data/games.json", headers=self.auth(), data=b"{}")
        self.assertEqual(response.status, 405)

    async def test_missing_and_corrupt_files_fail_closed(self):
        os.unlink(B.GAME_FILE) if os.path.exists(B.GAME_FILE) else None
        response = await self.client.get("/codex-data/game.json", headers=self.auth())
        self.assertEqual(response.status, 404)
        with open(B.GAMES_FILE, "wb") as f:
            f.write(b"{broken")
        response = await self.client.get("/codex-data/games.json", headers=self.auth())
        self.assertEqual(response.status, 503)
        self.assertNotIn(B.GAMES_FILE, await response.text())

    async def test_all_six_allowed_names_and_no_others(self):
        B.save_json(B.GAME_FILE, {})
        B.save_json(B.GAME_ARCHIVE_FILE, [])
        B.save_json(B.POLL_STATE_FILE, {})
        for name in sorted(B.DATA_READ_NAMES):
            response = await self.client.head("/codex-data/" + name, headers=self.auth())
            self.assertEqual(response.status, 200, name)


class DataReadStartup(Base):
    async def test_server_disabled_without_secret(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DATA_READ_TOKEN", None)
            with mock.patch.object(B.web, "AppRunner") as runner:
                self.assertIsNone(await B.start_data_read_server())
                runner.assert_not_called()

    def test_short_token_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "не менее 32"):
            B.create_data_read_app("short")
