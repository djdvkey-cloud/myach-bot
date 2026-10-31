"""Скачать разрешённые live-файлы Мяча по защищённому read-only endpoint.

URL и токен берутся только из окружения процесса и никогда не печатаются.
Каталог назначения обязан находиться вне Git-репозитория.
"""
import argparse
import hashlib
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ALLOWED = ("games.json", "players.json", "game.json", "game_archive.json", "poll_state.json", "stats.json")
REPO_ROOT = Path(__file__).resolve().parents[1]


def inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def download(name: str, output: Path, base_url: str, token: str) -> dict:
    url = base_url.rstrip("/") + "/codex-data/" + urllib.parse.quote(name)
    request = urllib.request.Request(url, headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(1024 * 1024 + 1)
            etag = response.headers.get("ETag", "").strip('"')
            mtime = response.headers.get("X-Data-Mtime", "")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{name}: production вернул HTTP {e.code}") from None
    if len(raw) > 1024 * 1024:
        raise RuntimeError(f"{name}: файл превышает безопасный лимит")
    try:
        json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise RuntimeError(f"{name}: получен повреждённый JSON: {e}") from None
    digest = hashlib.sha256(raw).hexdigest()
    if etag and etag != digest:
        raise RuntimeError(f"{name}: контрольная сумма ответа не совпала")
    output.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=name + ".", suffix=".tmp", dir=output)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, output / name)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return {"name": name, "bytes": len(raw), "sha256": digest, "production_mtime": mtime}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("files", nargs="*", choices=ALLOWED, default=list(ALLOWED))
    args = parser.parse_args()
    output = args.output.resolve()
    if inside(output, REPO_ROOT):
        parser.error("live-данные нельзя сохранять внутри публичного Git-репозитория")
    base_url = os.environ.get("MYACH_DATA_URL", "")
    token = os.environ.get("MYACH_DATA_TOKEN", "")
    if urllib.parse.urlparse(base_url).scheme != "https" or len(token) < 32:
        parser.error("нужны MYACH_DATA_URL=https://... и MYACH_DATA_TOKEN (не менее 32 символов)")
    manifest = [download(name, output, base_url, token) for name in (args.files or ALLOWED)]
    print(json.dumps({"files": manifest}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
