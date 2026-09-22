"""Тесты make-bootstrap.py: один установщик на все команды.

Запуск: python3 -m unittest discover -s tools/setup/tests
"""

from __future__ import annotations

import base64
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SETUP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SETUP))
import check_no_keys  # noqa: E402
import workshop_conf  # noqa: E402

spec = importlib.util.spec_from_file_location("make_bootstrap", SETUP / "make-bootstrap.py")
mb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mb)  # type: ignore[union-attr]

CONF = """
GH_OWNER="acme"
TEAMS=(a:team_1 b:team_2 c:team_3)
WORKSHOP_ID="test-run"
RENDER_PREFIX="ws"
"""


def has_key(path: Path) -> bool:
    """Та же проверка, что гоняет CI (check_no_keys.py), для файла вне git."""
    data = path.read_bytes()
    if check_no_keys.PEM.search(data):
        return True
    text = check_no_keys.decode_text(data)
    pattern = check_no_keys.APPLESCRIPT_B64 if path.suffix == ".applescript" else check_no_keys.CMD_B64
    return any(check_no_keys.b64_has_key(m.group(1)) for m in pattern.finditer(text))


@unittest.skipUnless(shutil.which("ssh-keygen"), "нужен ssh-keygen")
class BuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp())
        conf_path = cls.tmp / "teams.conf"
        conf_path.write_text(CONF)
        cls.conf = workshop_conf.load_conf(conf_path)
        cls.out = mb.build(cls.conf, cls.tmp / "secrets")
        cls.bash = mb.payload_of(cls.out / "raif-workshop-setup.applescript")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp)

    def fingerprint(self, letter: str) -> str:
        return mb.fingerprint_of_private((self.out / "keys" / f"team_{letter}").read_text())

    def test_every_team_is_in_the_picker(self) -> None:
        text, _ = mb.read_utf16(self.out / "raif-workshop-setup.applescript")
        self.assertIn('set teamNames to {"Team 1", "Team 2", "Team 3"}', text)
        self.assertIn('set teamCodes to {"team_a", "team_b", "team_c"}', text)
        cmd = (self.out / "raif-workshop-setup.cmd").read_text(encoding="utf-8")
        self.assertIn("Code = 'team_c'; Repo = 'acme/team_3'; Label = 'Team 3'", cmd)
        self.assertIn("team_b acme/team_2 Team 2", self.bash)

    def test_payload_writes_only_the_picked_team_key(self) -> None:
        block = mb.block_re("keys").search(self.bash).group(2)  # type: ignore[union-attr]
        for letter in "abc":
            target = self.tmp / f"picked_{letter}"
            subprocess.run(["bash", "-c", block + '\nwrite_team_key "$1" "$2"', "t", f"team_{letter}", str(target)],
                           check=True)
            self.assertEqual(mb.fingerprint_of_private(target.read_text()), self.fingerprint(letter))
        unknown = subprocess.run(["bash", "-c", block + '\nwrite_team_key team_z "$1"', "t", str(self.tmp / "z")])
        self.assertNotEqual(unknown.returncode, 0)

    def test_windows_installer_carries_the_same_keys(self) -> None:
        cmd = (self.out / "raif-workshop-setup.cmd").read_bytes().decode("utf-8")
        found = {code: base64.b64decode(b64).decode() for code, b64 in mb.CMD_KEY_RE.findall(cmd)}
        self.assertEqual(sorted(found), ["team_a", "team_b", "team_c"])
        self.assertEqual(mb.fingerprint_of_private(found["team_b"]), self.fingerprint("b"))
        self.assertIn("\r\n", cmd)

    def test_guard_sees_keys_in_built_installer(self) -> None:
        for name in mb.OUT_NAMES:
            self.assertTrue(has_key(self.out / name), name)

    def test_rebuild_keeps_keys(self) -> None:
        before = {letter: self.fingerprint(letter) for letter in "abc"}
        mb.build(self.conf, self.out)
        self.assertEqual(before, {letter: self.fingerprint(letter) for letter in "abc"})

    def test_scrub_master_removes_keys(self) -> None:
        root = self.tmp / "root"
        (root / "tools/bootstrap").mkdir(parents=True)
        for name in mb.OUT_NAMES:
            shutil.copy(self.out / name, root / "tools/bootstrap" / name)
        with mock.patch.object(mb, "ROOT", root), mock.patch("sys.stdout"):
            mb.scrub_master()
        for name in mb.OUT_NAMES:
            self.assertFalse(has_key(root / "tools/bootstrap" / name), name)
        bash = mb.payload_of(root / "tools/bootstrap/raif-workshop-setup.applescript")
        self.assertIn("__TEAMS_HERE__", bash)


class MasterTest(unittest.TestCase):
    def test_master_applescript_embeds_the_bash_master(self) -> None:
        self.assertEqual(mb.payload_of(mb.MASTER_AS), mb.MASTER_SH.read_text(encoding="utf-8"),
                         "мастер-AppleScript отстал от .sh: python3 tools/setup/make-bootstrap.py --refresh-master")

    def test_masters_have_no_keys(self) -> None:
        for path in (mb.MASTER_SH, mb.MASTER_AS, mb.MASTER_CMD):
            self.assertFalse(has_key(path), path.name)

    def test_unsigned_master_stops_with_a_clear_message(self) -> None:
        env = dict(os.environ, HOME=tempfile.mkdtemp())
        proc = subprocess.run(["bash", str(mb.MASTER_SH), "team_a", "retail", "Ivan"],
                              capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Script is unsigned", proc.stderr)
        self.assertFalse((Path(env["HOME"]) / ".ssh").exists())

    def test_cyrillic_name_gets_a_latin_slug(self) -> None:
        text = mb.MASTER_SH.read_text(encoding="utf-8")
        start = text.index("ru_to_latin() {")
        end = text.index("GIT_EMAIL=", start)
        script = "set -euo pipefail\nGIT_NAME=\"$1\"\n" + text[start:end] + 'printf "%s" "$PARTICIPANT"'
        slug = subprocess.run(["bash", "-c", script, "t", "Иван Петров-Щукин"],
                              check=True, capture_output=True, text=True).stdout
        self.assertEqual(slug, "ivan-petrov-shchukin")


if __name__ == "__main__":
    unittest.main()
