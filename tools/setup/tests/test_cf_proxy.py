"""Тесты cf_proxy.py против фейкового API Cloudflare.

Запуск: python3 -m unittest discover -s tools/setup/tests
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cf_proxy  # noqa: E402

TOKEN = "cf-test-token"
SERVICES = """# буква блок service-id url
a backend srv-1 https://ws-a-backend.onrender.com
a retail srv-2 https://ws-a-retail.onrender.com/
sim simulator srv-3 https://ws-simulator.onrender.com
"""


class FakeCloudflare:
    def __init__(self, subdomain: str | None):
        self.subdomain = subdomain
        self.scripts: dict[str, bytes] = {}
        self.enabled: set[str] = set()
        self.calls: list[tuple[str, str]] = []
        self.auth_ok = True

    def handle(self, handler: BaseHTTPRequestHandler) -> None:
        path = handler.path.split("?")[0]
        self.calls.append((handler.command, path))
        length = int(handler.headers.get("Content-Length") or 0)
        body = handler.rfile.read(length) if length else b""
        if handler.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self.reply(handler, 403, {"success": False, "errors": [{"code": 10000, "message": "auth"}]})
        parts = path.strip("/").split("/")[2:]  # после client/v4
        if parts == ["accounts"]:
            return self.reply(handler, 200, {"success": True, "result": [{"id": "acc1"}]})
        if parts == ["accounts", "acc1", "workers", "subdomain"]:
            if handler.command == "PUT":
                self.subdomain = json.loads(body)["subdomain"]
                return self.reply(handler, 200, {"success": True, "result": {"subdomain": self.subdomain}})
            if not self.subdomain:
                return self.reply(handler, 404, {"success": False, "errors": [{"code": 10007, "message": "no subdomain"}]})
            return self.reply(handler, 200, {"success": True, "result": {"subdomain": self.subdomain}})
        if parts[:4] == ["accounts", "acc1", "workers", "scripts"] and len(parts) >= 5:
            name = parts[4]
            if len(parts) == 6 and parts[5] == "subdomain":
                self.enabled.add(name)
                return self.reply(handler, 200, {"success": True, "result": {}})
            if handler.command == "PUT":
                self.scripts[name] = body
                return self.reply(handler, 200, {"success": True, "result": {"id": name}})
            if handler.command == "DELETE":
                self.scripts.pop(name, None)
                return self.reply(handler, 200, {"success": True, "result": {}})
        return self.reply(handler, 404, {"success": False, "errors": [{"code": 7000, "message": "no route"}]})

    @staticmethod
    def reply(handler: BaseHTTPRequestHandler, code: int, payload: dict) -> None:
        data = json.dumps(payload).encode()
        handler.send_response(code)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)


class CfProxyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "render-services.conf").write_text(SERVICES)
        self.fake = FakeCloudflare(subdomain=None)
        fake = self.fake

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                fake.handle(self)
            do_PUT = do_POST = do_DELETE = do_GET

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{self.server.server_address[1]}/client/v4"
        for patch in (mock.patch.object(cf_proxy, "API", base),
                      mock.patch.object(cf_proxy, "SERVICES_CONF", self.tmp / "render-services.conf"),
                      mock.patch.object(cf_proxy, "PROXY_CONF", self.tmp / "proxy-urls.conf")):
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self) -> None:
        self.server.shutdown()

    def deploy(self, token: str = TOKEN) -> str:
        out = io.StringIO()
        with redirect_stdout(out):
            cf_proxy.deploy(cf_proxy.Cloudflare(token), mock.Mock(), "ws")
        return out.getvalue()

    def test_worker_names_use_team_numbers(self) -> None:
        self.assertEqual(cf_proxy.worker_name("a", "retail"), "team1-retail")
        self.assertEqual(cf_proxy.worker_name("g", "cib"), "team7-cib")
        self.assertEqual(cf_proxy.worker_name("sim", "simulator"), "simulator")

    def test_deploy_creates_subdomain_workers_and_url_file(self) -> None:
        output = self.deploy()
        self.assertEqual(self.fake.subdomain, "ws")
        self.assertEqual(sorted(self.fake.scripts), ["simulator", "team1-backend", "team1-retail"])
        self.assertEqual(self.fake.enabled, set(self.fake.scripts))
        body = self.fake.scripts["team1-retail"].decode()
        self.assertIn('"text": "https://ws-a-retail.onrender.com"', body)  # без слэша в конце
        self.assertIn("export default", body)
        rows = cf_proxy.read_proxy_conf()
        self.assertIn(("sim", "simulator", "https://simulator.ws.workers.dev",
                       "https://ws-simulator.onrender.com"), rows)
        self.assertNotIn(TOKEN, output)

    def test_existing_subdomain_is_kept(self) -> None:
        self.fake.subdomain = "someone"
        self.deploy()
        self.assertNotIn(("PUT", "/client/v4/accounts/acc1/workers/subdomain"), self.fake.calls)
        self.assertEqual(cf_proxy.read_proxy_conf()[0][2], "https://team1-backend.someone.workers.dev")

    def test_bad_token_stops_with_the_api_message(self) -> None:
        with self.assertRaises(cf_proxy.CfError) as caught:
            self.deploy(token="wrong")
        self.assertIn("HTTP 403", str(caught.exception))
        self.assertNotIn("wrong", str(caught.exception))

    def test_conf_rebuilds_url_file_without_deploying(self) -> None:
        self.deploy()
        (self.tmp / "proxy-urls.conf").unlink()
        self.fake.calls.clear()
        with redirect_stdout(io.StringIO()):
            cf_proxy.write_conf(cf_proxy.Cloudflare(TOKEN))
        self.assertTrue(all(method == "GET" for method, _path in self.fake.calls))
        self.assertEqual(len(cf_proxy.read_proxy_conf()), 3)

    def test_delete_removes_every_worker(self) -> None:
        self.deploy()
        with redirect_stdout(io.StringIO()):
            cf_proxy.delete(cf_proxy.Cloudflare(TOKEN))
        self.assertEqual(self.fake.scripts, {})


if __name__ == "__main__":
    unittest.main()
