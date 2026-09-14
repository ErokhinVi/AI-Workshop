#!/usr/bin/env python3
"""tools/setup/check_no_keys.py: упасть, если в репозитории лежит приватный ключ.

Проверяет все отслеживаемые git файлы: PEM/OpenSSH-заголовок в тексте, а в
установщиках ноутбука еще и base64-пейлоад (AppleScript `set bashB64`, .cmd
`$PrivateKeyB64`). Ключ внутри base64 обычный поиск по репозиторию не видит.

Запуск: python3 tools/setup/check_no_keys.py  (его же гоняет CI на каждый push)
"""

from __future__ import annotations

import base64
import binascii
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PEM = re.compile(rb"-----BEGIN (?:OPENSSH |RSA |EC |DSA )?PRIVATE KEY-----")
APPLESCRIPT_B64 = re.compile(r'set bashB64 to "([A-Za-z0-9+/=]+)"')
CMD_B64 = re.compile(r"\$PrivateKeyB64 = '([A-Za-z0-9+/=]{40,})'")


def decode_text(data: bytes) -> str:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    return data.decode("utf-8", errors="ignore")


def b64_has_key(payload: str) -> bool:
    try:
        return bool(PEM.search(base64.b64decode(payload)))
    except (binascii.Error, ValueError):
        return False


def main() -> int:
    listed = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True,
                            capture_output=True).stdout.split(b"\0")
    files = [ROOT / name.decode() for name in listed if name]
    findings: list[str] = []
    for path in files:
        if not path.is_file():
            continue
        data = path.read_bytes()
        rel = path.relative_to(ROOT)
        if PEM.search(data):
            findings.append(f"{rel}: приватный ключ в открытом виде")
        if path.suffix in (".applescript", ".cmd"):
            text = decode_text(data)
            pattern = APPLESCRIPT_B64 if path.suffix == ".applescript" else CMD_B64
            if any(b64_has_key(m.group(1)) for m in pattern.finditer(text)):
                findings.append(f"{rel}: приватный ключ внутри base64 установщика")
    if findings:
        print("НАЙДЕНЫ ПРИВАТНЫЕ КЛЮЧИ:\n  " + "\n  ".join(findings))
        print("Убери ключ (python3 tools/setup/make-bootstrap.py --scrub-master) и отзови его на GitHub.")
        return 1
    print(f"ok: приватных ключей в {len(files)} файлах нет")
    return 0


if __name__ == "__main__":
    sys.exit(main())
