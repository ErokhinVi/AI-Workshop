"""Тесты tools/setup/ops-dispatch.sh: проверки до любого сетевого вызова.

Запуск: python3 -m unittest discover -s tools/setup/tests
"""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "ops-dispatch.sh"
SECRETS = ("RENDER_API_KEY", "OPENAI_API_KEY", "ADMIN_TOKEN", "RENDER_OWNER_ID",
           "CLOUDFLARE_API_TOKEN", "WORKSHOP_GH_TOKEN", "TEAM_DEPLOY_KEYS", "GH_TOKEN")


def dispatch(command: str, args: str = "", confirm: str = "", **secrets: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in SECRETS}
    env.update(OPS_COMMAND=command, OPS_ARGS=args, OPS_CONFIRM=confirm, **secrets)
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)


class OpsDispatchTest(unittest.TestCase):
    def assertStops(self, proc: subprocess.CompletedProcess, text: str) -> None:
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn(text, proc.stdout + proc.stderr)

    def test_args_reject_shell_characters(self) -> None:
        self.assertStops(dispatch("deploy", "3:cib; rm -rf /"), "args: только латиница")

    def test_team_reset_needs_confirm_and_explicit_teams(self) -> None:
        self.assertStops(dispatch("team-reset", "3"), "впиши RESET")
        self.assertStops(dispatch("team-reset", "", "RESET"), "номера команд")

    def test_repo_commands_name_the_missing_secret(self) -> None:
        self.assertStops(dispatch("installer"), "нет секрета WORKSHOP_GH_TOKEN")
        self.assertStops(dispatch("team-access", WORKSHOP_GH_TOKEN="x"), "нет секрета TEAM_DEPLOY_KEYS")
        self.assertStops(dispatch("proxy"), "нет секрета CLOUDFLARE_API_TOKEN")
        self.assertStops(dispatch("revoke", "", "DELETE"), "нет секрета WORKSHOP_GH_TOKEN")

    def test_revoke_needs_delete(self) -> None:
        self.assertStops(dispatch("revoke", WORKSHOP_GH_TOKEN="x"), "впиши DELETE")

    def test_unknown_command(self) -> None:
        self.assertStops(dispatch("format-disk"), "не знаю команду")


if __name__ == "__main__":
    unittest.main()
