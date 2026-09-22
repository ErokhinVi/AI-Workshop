"""Тесты render_ops.py и workshop_conf.py против фейкового Render API.

Запуск: python3 -m unittest discover -s tools/setup/tests
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import render_ops  # noqa: E402
import workshop_conf  # noqa: E402

KEY = "rnd_test_key"
OPENAI = "sk-test-openai-secret"
ADMIN = "admin-test-token"

CONF = """
GH_OWNER="acme"
ORCHESTRATOR_REPO="AI-Workshop"
TEAMS=(a:team_1 b:team_2)
WORKSHOP_ID="test-run"
RENDER_PREFIX="ws"
RENDER_REGION="frankfurt"
RENDER_PLAN="starter"
RENDER_DB_PLAN="basic_256mb"
LLM_BASE_URL="https://llm.example/v1"
LLM_MODEL="model-1"
SIM_ENV=(STAGNATION_RATE_PER_MIN=0)
"""


class FakeRender:
    """Минимальная модель Render API v1 и сервисов, которые на нем крутятся."""

    def __init__(self) -> None:
        self.origin = ""
        self.owners = [{"id": "tea-1", "name": "Workshop", "type": "team"}]
        self.services: dict = {}
        self.env: dict = {}
        self.deploys: dict = {}
        self.postgres: dict = {}
        self.calls: list = []
        self.taken: set = set()      # имена, которым Render выдаст URL с суффиксом
        self.rate_limit: list = []   # Retry-After для ближайших запросов к API
        self.health: dict = {}       # хост сервиса -> код /health
        self.sim_state = {"workshop_started": False, "workshop_started_at": None,
                          "teams": {"team_a": {"client_base": 540, "delta_from_start": 40,
                                               "feature_state": "working", "releases": 2,
                                               "idle_seconds": 120}},
                          "events": [{"ts": "10:00", "team": "team_a", "delta": 40, "reason": "кредит"}]}
        self.counter = 0
        self.lock = threading.Lock()

    def next_id(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}-{self.counter}"

    def by_name(self, name: str) -> dict:
        return next(s for s in self.services.values() if s["name"] == name)


def make_handler(fake: FakeRender):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def do_PUT(self):
            self._handle("PUT")

        def do_PATCH(self):
            self._handle("PATCH")

        def do_DELETE(self):
            self._handle("DELETE")

        def _send(self, code, body=None, headers=None):
            raw = b"" if body is None else json.dumps(body).encode()
            self.send_response(code)
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            if body is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            if raw:
                self.wfile.write(raw)

        def _handle(self, method):
            parsed = urlparse(self.path)
            parts = [p for p in parsed.path.split("/") if p]
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            body = json.loads(raw) if raw else None
            with fake.lock:
                if parts[:1] == ["app"]:
                    return self._app(method, parts[1], parts[2:])
                if self.headers.get("Authorization") != f"Bearer {KEY}":
                    return self._send(401, {"message": "unauthorized"})
                fake.calls.append((method, "/" + "/".join(parts[1:]), body))
                if fake.rate_limit:
                    return self._send(429, {"message": "rate limit exceeded"},
                                      {"Retry-After": str(fake.rate_limit.pop(0))})
                return self._api(method, parts[1:], parse_qs(parsed.query), body)

        def _page(self, items, wrapper, query):
            limit = int(query.get("limit", ["20"])[0])
            start = int(query.get("cursor", ["0"])[0])
            chunk = items[start:start + limit]
            return self._send(200, [{wrapper: item, "cursor": str(start + i + 1)} for i, item in enumerate(chunk)])

        def _api(self, method, parts, query, body):
            if parts == ["owners"] and method == "GET":
                return self._page(fake.owners, "owner", query)
            if parts == ["services"] and method == "GET":
                return self._page(list(fake.services.values()), "service", query)
            if parts == ["services"] and method == "POST":
                return self._create_service(body)
            if parts[:1] == ["services"] and len(parts) >= 2:
                sid, rest = parts[1], parts[2:]
                service = fake.services.get(sid)
                if service is None:
                    return self._send(404, {"message": "service not found"})
                if not rest and method == "PATCH":
                    service["serviceDetails"].update(body["serviceDetails"])
                    return self._send(200, service)
                if not rest and method == "DELETE":
                    del fake.services[sid]
                    return self._send(204)
                if rest == ["env-vars"] and method == "GET":
                    items = [{"key": k, "value": v} for k, v in fake.env[sid].items()]
                    return self._page(items, "envVar", query)
                if rest == ["env-vars"] and method == "PUT":
                    fake.env[sid] = {e["key"]: e["value"] for e in body}
                    return self._send(200, [{"envVar": e, "cursor": "x"} for e in body])
                if rest == ["deploys"] and method == "POST":
                    deploy = {"id": fake.next_id("dep"), "status": "created", "commit": {"id": "abcdef123"}}
                    fake.deploys[sid].insert(0, deploy)
                    return self._send(201, deploy)
                if rest == ["deploys"] and method == "GET":
                    return self._page(fake.deploys[sid], "deploy", query)
                if rest in (["suspend"], ["resume"]) and method == "POST":
                    service["suspended"] = "suspended" if rest == ["suspend"] else "not_suspended"
                    return self._send(202)
            if parts == ["postgres"] and method == "GET":
                return self._page(list(fake.postgres.values()), "postgres", query)
            if parts == ["postgres"] and method == "POST":
                if not all(body.get(f) for f in ("name", "ownerId", "plan", "version")):
                    return self._send(400, {"message": "name, ownerId, plan, version required"})
                pg = {"id": fake.next_id("dpg"), "name": body["name"], "ownerId": body["ownerId"],
                      "plan": body["plan"], "status": "creating"}
                fake.postgres[pg["id"]] = pg
                return self._send(201, pg)
            if parts[:1] == ["postgres"] and len(parts) >= 2 and parts[1] in fake.postgres:
                pg = fake.postgres[parts[1]]
                if len(parts) == 2 and method == "GET":
                    pg["status"] = "available"
                    return self._send(200, pg)
                if parts[2:] == ["connection-info"]:
                    return self._send(200, {"internalConnectionString": f"postgresql://u:p@{pg['id']}/db"})
                if len(parts) == 2 and method == "DELETE":
                    del fake.postgres[parts[1]]
                    return self._send(204)
            return self._send(404, {"message": f"no route {method} {parts}"})

        def _create_service(self, body):
            details = body.get("serviceDetails") or {}
            if body.get("type") != "web_service" or not body.get("name") or not body.get("ownerId") \
                    or details.get("runtime") != "docker":
                return self._send(400, {"message": "invalid service body"})
            if not str(body.get("repo", "")).startswith("https://github.com/"):
                return self._send(400, {"message": "repo must be a public GitHub URL"})
            sid = fake.next_id("srv")
            host = body["name"] + ("-x7k2" if body["name"] in fake.taken else "")
            service = {"id": sid, "name": body["name"], "type": "web_service", "repo": body["repo"],
                       "ownerId": body["ownerId"], "autoDeploy": body.get("autoDeploy"),
                       "suspended": "not_suspended",
                       "serviceDetails": {"url": f"{fake.origin}/app/{host}", "plan": details.get("plan"),
                                          "region": details.get("region"), "runtime": "docker",
                                          "envSpecificDetails": details.get("envSpecificDetails")}}
            fake.services[sid] = service
            fake.env[sid] = {e["key"]: e["value"] for e in body.get("envVars", [])}
            fake.deploys[sid] = [{"id": fake.next_id("dep"), "status": "build_in_progress"}]
            return self._send(201, {"service": service, "deployId": fake.deploys[sid][0]["id"]})

        def _app(self, method, host, rest):
            if rest == ["health"]:
                return self._send(fake.health.get(host, 200), {"status": "ok", "db": True})
            if rest == ["state"]:
                return self._send(200, fake.sim_state)
            if rest[:1] == ["admin"] and method == "POST":
                if self.headers.get("X-Admin-Token") != ADMIN:
                    return self._send(403, {"detail": "нужен корректный admin-токен"})
                if rest[1] == "start":
                    fake.sim_state["workshop_started"] = True
                return self._send(200, {"status": rest[1]})
            return self._send(404, {"detail": "no app route"})

    return Handler


class RenderOpsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeRender()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.fake))
        self.fake.origin = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.conf_path = self.tmp / "teams.conf"
        self.conf_path.write_text(CONF)
        secrets_dir = self.tmp / "secrets"
        secrets_dir.mkdir()
        (secrets_dir / "workshop.env").write_text(
            f"RENDER_API_KEY={KEY}\nOPENAI_API_KEY={OPENAI}\nADMIN_TOKEN={ADMIN}\nRENDER_OWNER_ID=\n")
        self.services_conf = self.tmp / "render-services.conf"

        env = mock.patch.dict(os.environ, {
            "WORKSHOP_CONF": str(self.conf_path), "WORKSHOP_SECRETS": str(secrets_dir),
            "RENDER_API_BASE": self.fake.origin + "/v1", "RENDER_SERVICES_CONF": str(self.services_conf)})
        env.start()
        self.addCleanup(env.stop)
        for name in ("RENDER_API_KEY", "OPENAI_API_KEY", "ADMIN_TOKEN", "RENDER_OWNER_ID", "SIM_URL",
                     "GITHUB_STEP_SUMMARY", "GITHUB_ACTIONS"):
            os.environ.pop(name, None)
        no_sleep = mock.patch.object(render_ops.Render, "sleep", staticmethod(lambda seconds: None))
        no_sleep.start()
        self.addCleanup(no_sleep.stop)

    def run_ops(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            code = render_ops.main(list(argv))
        return code, out.getvalue()

    def writes(self) -> list:
        return [c for c in self.fake.calls if c[0] != "GET"]

    def test_provision_builds_the_whole_workshop_once(self):
        self.fake.taken.add("ws-a-backend")
        code, output = self.run_ops("provision")
        self.assertEqual(code, 0, output)

        creates = [c[2] for c in self.fake.calls if c[:2] == ("POST", "/services")]
        self.assertEqual([b["name"] for b in creates],
                         ["ws-a-backend", "ws-a-cib", "ws-a-retail", "ws-b-backend", "ws-b-cib", "ws-b-retail",
                          "ws-simulator"])
        first = creates[0]
        self.assertEqual(first["repo"], "https://github.com/acme/team_1")
        self.assertEqual(first["autoDeploy"], "no")
        self.assertEqual(first["serviceDetails"]["plan"], "starter")
        self.assertEqual(first["serviceDetails"]["region"], "frankfurt")
        self.assertEqual(first["serviceDetails"]["envSpecificDetails"],
                         {"dockerfilePath": "./backend/Dockerfile", "dockerContext": "."})
        self.assertEqual(creates[-1]["repo"], "https://github.com/acme/AI-Workshop")
        self.assertEqual(creates[-1]["serviceDetails"]["envSpecificDetails"]["dockerfilePath"],
                         "./simulator/Dockerfile")

        def env(name):
            return self.fake.env[self.fake.by_name(name)["id"]]

        backend_url = self.fake.by_name("ws-a-backend")["serviceDetails"]["url"]
        self.assertTrue(backend_url.endswith("/ws-a-backend-x7k2"))
        self.assertEqual(env("ws-a-cib")["BACKEND_URL"], backend_url)
        self.assertEqual(env("ws-a-retail")["CIB_URL"], self.fake.by_name("ws-a-cib")["serviceDetails"]["url"])
        self.assertEqual(env("ws-a-cib")["OPENAI_API_KEY"], OPENAI)
        self.assertEqual(env("ws-a-cib")["OPENAI_MODEL"], "model-1")
        self.assertNotIn("OPENAI_API_KEY", env("ws-a-retail"))
        self.assertEqual(env("ws-b-backend"), {"TEAM_NAME": "team_b"})
        sim = env("ws-simulator")
        self.assertEqual(sim["TEAM_NAMES"], "team_a,team_b")
        self.assertEqual(sim["A_BACKEND_URL"], backend_url)
        self.assertEqual(sim["B_REPO"], "acme/team_2")
        self.assertEqual(sim["STAGNATION_RATE_PER_MIN"], "0")
        self.assertEqual(sim["ADMIN_TOKEN"], ADMIN)
        self.assertTrue(sim["DATABASE_URL"].startswith("postgresql://"))
        self.assertEqual([p["plan"] for p in self.fake.postgres.values()], ["basic_256mb"])

        lines = self.services_conf.read_text().splitlines()[1:]
        self.assertIn(f"a backend {self.fake.by_name('ws-a-backend')['id']} {backend_url}", lines)
        self.assertTrue(any(line.startswith("sim simulator srv-") for line in lines))
        self.assertNotIn(OPENAI, output)
        self.assertNotIn(ADMIN, output)

        self.fake.calls.clear()
        code, output = self.run_ops("provision")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.writes(), [], output)

    def test_dry_run_only_reads(self):
        code, output = self.run_ops("provision", "--dry-run")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.writes(), [])
        self.assertIn("+ ws-simulator", output)

    def test_env_follows_conf_and_redeploys_only_changed_services(self):
        self.run_ops("provision")
        self.conf_path.write_text(CONF.replace('LLM_MODEL="model-1"', 'LLM_MODEL="model-2"'))
        ids = {s["name"]: s["id"] for s in self.fake.services.values()}

        self.fake.calls.clear()
        code, output = self.run_ops("env", "--dry-run")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.writes(), [])
        self.assertIn("OPENAI_MODEL", output)

        code, output = self.run_ops("env")
        self.assertEqual(code, 0, output)
        changed = ("ws-a-cib", "ws-b-cib", "ws-simulator")
        self.assertEqual(sorted(c[1] for c in self.fake.calls if c[0] == "PUT"),
                         sorted(f"/services/{ids[n]}/env-vars" for n in changed))
        self.assertEqual(sorted(c[1] for c in self.fake.calls if c[0] == "POST"),
                         sorted(f"/services/{ids[n]}/deploys" for n in changed))
        sim_env = self.fake.env[ids["ws-simulator"]]
        self.assertEqual(sim_env["OPENAI_MODEL"], "model-2")
        self.assertIn("DATABASE_URL", sim_env)
        self.assertNotIn(OPENAI, output)

    def test_pagination_does_not_duplicate_services(self):
        with mock.patch.object(render_ops, "PAGE", 2):
            self.run_ops("provision")
            self.fake.calls.clear()
            code, output = self.run_ops("provision")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.writes(), [], output)

    def test_short_rate_limit_is_waited_out(self):
        self.fake.rate_limit = [2]
        code, output = self.run_ops("check")
        self.assertEqual(code, 0, output)
        self.assertIn("лимит запросов", output)

    def test_long_rate_limit_stops_with_tempfail(self):
        self.fake.rate_limit = [3600]
        code, output = self.run_ops("provision")
        self.assertEqual(code, 75, output)
        self.assertIn("повтори", output)

    def test_check_lists_foreign_services_that_eat_the_limit(self):
        self.fake.services["srv-old"] = {"id": "srv-old", "name": "raif-simulator", "ownerId": "tea-1",
                                         "type": "web_service", "suspended": "suspended",
                                         "createdAt": "2026-06-02T10:00:00Z", "serviceDetails": {}}
        code, output = self.run_ops("check")
        self.assertEqual(code, 0, output)
        self.assertIn("чужих сервисов и баз в workspace: 1", output)
        self.assertIn("raif-simulator  web_service  suspended  создан 2026-06-02", output)

    def test_drop_removes_only_named_foreign_services(self):
        self.run_ops("provision")
        self.fake.services["srv-old"] = {"id": "srv-old", "name": "raif-simulator", "ownerId": "tea-1",
                                         "serviceDetails": {}}
        self.fake.postgres["dpg-old"] = {"id": "dpg-old", "name": "raif-workshop-db", "ownerId": "tea-1",
                                         "status": "suspended"}
        code, output = self.run_ops("drop", "raif-simulator", "raif-workshop-db")
        self.assertEqual(code, 1, output)
        self.assertIn("srv-old", self.fake.services)
        code, output = self.run_ops("drop", "ws-a-cib", "--confirm", "DELETE")
        self.assertEqual(code, 1, output)
        self.assertIn("teardown", output)
        code, output = self.run_ops("drop", "raif-simulator", "raif-workshop-db", "nope", "--confirm", "DELETE")
        self.assertEqual(code, 0, output)
        self.assertNotIn("srv-old", self.fake.services)
        self.assertNotIn("dpg-old", self.fake.postgres)
        self.assertEqual(len(self.fake.services), 7)
        self.assertIn("? nope", output)

    def test_several_workspaces_need_owner_id(self):
        self.fake.owners.append({"id": "usr-2", "name": "Personal", "type": "user"})
        code, output = self.run_ops("check")
        self.assertEqual(code, 1)
        self.assertIn("RENDER_OWNER_ID", output)
        with mock.patch.dict(os.environ, {"RENDER_OWNER_ID": "tea-1"}):
            code, output = self.run_ops("check")
        self.assertEqual(code, 0, output)

    def test_deploy_selected_targets(self):
        self.run_ops("provision")
        ids = {s["name"]: s["id"] for s in self.fake.services.values()}
        self.fake.calls.clear()
        code, output = self.run_ops("deploy", "a:cib", "sim")
        self.assertEqual(code, 0, output)
        self.assertEqual([c[1] for c in self.writes()],
                         [f"/services/{ids['ws-a-cib']}/deploys", f"/services/{ids['ws-simulator']}/deploys"])
        self.fake.calls.clear()
        self.run_ops("deploy", "b")
        self.assertEqual(len(self.writes()), 3)
        code, output = self.run_ops("deploy", "z")
        self.assertEqual(code, 1, output)

    def test_deploy_skip_missing_before_provision(self):
        code, output = self.run_ops("deploy", "sim", "--skip-missing")
        self.assertEqual(code, 0, output)
        code, output = self.run_ops("deploy", "sim")
        self.assertEqual(code, 1, output)

    def test_status_fails_when_a_block_is_down(self):
        self.run_ops("provision")
        code, output = self.run_ops("status")
        self.assertEqual(code, 0, output)
        self.fake.health["ws-b-retail"] = 503
        code, output = self.run_ops("status")
        self.assertEqual(code, 1, output)
        self.assertIn("503", output)

    def test_plan_and_suspend_are_idempotent(self):
        self.run_ops("provision")
        self.assertEqual(self.run_ops("plan", "free")[0], 0)
        self.assertTrue(all(s["serviceDetails"]["plan"] == "free" for s in self.fake.services.values()))
        self.assertEqual(self.run_ops("suspend")[0], 0)
        self.assertTrue(all(s["suspended"] == "suspended" for s in self.fake.services.values()))
        self.fake.calls.clear()
        self.run_ops("suspend")
        self.run_ops("plan", "free")
        self.assertEqual(self.writes(), [])

    def test_teardown_needs_confirm_and_spares_foreign_services(self):
        self.run_ops("provision")
        self.fake.services["srv-foreign"] = {"id": "srv-foreign", "name": "someone-else", "ownerId": "tea-1",
                                             "serviceDetails": {}}
        code, output = self.run_ops("teardown")
        self.assertEqual(code, 1, output)
        self.assertEqual(len(self.fake.services), 8)
        code, output = self.run_ops("teardown", "--confirm", "DELETE")
        self.assertEqual(code, 0, output)
        self.assertEqual(list(self.fake.services), ["srv-foreign"])
        self.assertEqual(self.fake.postgres, {})
        self.assertFalse(self.services_conf.exists())

    def test_sim_admin_uses_token_and_state_prints_board(self):
        self.run_ops("provision")
        code, output = self.run_ops("sim", "start")
        self.assertEqual(code, 0, output)
        self.assertTrue(self.fake.sim_state["workshop_started"])
        code, output = self.run_ops("sim", "state")
        self.assertEqual(code, 0, output)
        self.assertIn("team_a", output)
        self.assertIn("+40", output)
        with mock.patch.dict(os.environ, {"ADMIN_TOKEN": "wrong"}):
            code, output = self.run_ops("sim", "stop")
        self.assertEqual(code, 1, output)

    def test_missing_secrets_are_named(self):
        (Path(os.environ["WORKSHOP_SECRETS"]) / "workshop.env").write_text(f"RENDER_API_KEY={KEY}\n")
        code, output = self.run_ops("provision")
        self.assertEqual(code, 1)
        self.assertIn("ADMIN_TOKEN", output)

    def test_llm_key_can_arrive_after_provision(self):
        env_file = Path(os.environ["WORKSHOP_SECRETS"]) / "workshop.env"
        env_file.write_text(f"RENDER_API_KEY={KEY}\nADMIN_TOKEN={ADMIN}\n")
        code, output = self.run_ops("provision")
        self.assertEqual(code, 0, output)
        self.assertIn("OPENAI_API_KEY пуст", output)
        ids = {s["name"]: s["id"] for s in self.fake.services.values()}
        self.assertNotIn("OPENAI_API_KEY", self.fake.env[ids["ws-a-cib"]])
        self.assertEqual(self.fake.env[ids["ws-a-cib"]]["OPENAI_MODEL"], "model-1")

        env_file.write_text(f"RENDER_API_KEY={KEY}\nOPENAI_API_KEY={OPENAI}\nADMIN_TOKEN={ADMIN}\n")
        self.fake.calls.clear()
        code, output = self.run_ops("env")
        self.assertEqual(code, 0, output)
        changed = ("ws-a-cib", "ws-b-cib", "ws-simulator")
        self.assertEqual(sorted(c[1] for c in self.fake.calls if c[0] == "POST"),
                         sorted(f"/services/{ids[n]}/deploys" for n in changed))
        self.assertEqual(self.fake.env[ids["ws-simulator"]]["OPENAI_API_KEY"], OPENAI)
        self.assertNotIn(OPENAI, output)

    def test_init_secrets_creates_private_file_once(self):
        target = self.tmp / "fresh"
        with mock.patch.dict(os.environ, {"WORKSHOP_SECRETS": str(target)}):
            code, output = self.run_ops("init-secrets")
            self.assertEqual(code, 0, output)
            path = target / "workshop.env"
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            token = next(line for line in path.read_text().splitlines() if line.startswith("ADMIN_TOKEN="))
            self.assertGreater(len(token), 40)
            before = path.read_text()
            self.run_ops("init-secrets")
            self.assertEqual(path.read_text(), before)


class ConfTest(unittest.TestCase):
    def write(self, text: str) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "teams.conf"
        path.write_text(text)
        return path

    def test_repository_conf_is_valid(self):
        conf = workshop_conf.load_conf(workshop_conf.DEFAULT_CONF)
        self.assertGreaterEqual(len(conf.teams), 1)
        self.assertTrue(conf.prefix)

    def test_defaults_fill_optional_fields(self):
        conf = workshop_conf.load_conf(self.write('GH_OWNER="o"\nTEAMS=(a:r1)\nWORKSHOP_ID="w"\nRENDER_PREFIX="px"\n'))
        self.assertEqual((conf.region, conf.plan, conf.db_plan, conf.orchestrator_repo),
                         ("frankfurt", "starter", "basic_256mb", "AI-Workshop"))
        self.assertEqual(conf.sim_env, {})

    def test_rejects_bad_team_entry_and_duplicates(self):
        base = 'GH_OWNER="o"\nWORKSHOP_ID="w"\nRENDER_PREFIX="px"\n'
        with self.assertRaises(workshop_conf.ConfError):
            workshop_conf.load_conf(self.write(base + "TEAMS=(ab:r1)\n"))
        with self.assertRaises(workshop_conf.ConfError):
            workshop_conf.load_conf(self.write(base + "TEAMS=(a:r1 a:r2)\n"))

    def test_environment_overrides_secret_file(self):
        conf = workshop_conf.load_conf(self.write('GH_OWNER="o"\nTEAMS=(a:r1)\nWORKSHOP_ID="w"\nRENDER_PREFIX="px"\n'))
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "workshop.env").write_text('RENDER_API_KEY="from-file"\nADMIN_TOKEN=file-token\nOTHER=x\n')
            with mock.patch.dict(os.environ, {"WORKSHOP_SECRETS": tmp, "ADMIN_TOKEN": "from-env"}):
                os.environ.pop("RENDER_API_KEY", None)
                secrets = workshop_conf.load_secrets(conf)
        self.assertEqual(secrets, {"RENDER_API_KEY": "from-file", "ADMIN_TOKEN": "from-env"})


if __name__ == "__main__":
    unittest.main()
