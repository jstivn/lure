"""Blocks a commit or push that would publish personal data (run by .githooks).

Usage: check_private.py --staged   (pre-commit: added lines of the staged diff)
       check_private.py --tree     (pre-push: every tracked file)
Your own e-mail, app password, sender name and BitBrowser ids are read from the local, git-ignored config files,
so they are never stored in the repository.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

LOCAL_FILES = ("meta-lead-gen/config.json", "whatsapp-outreach/config.json", "meta-lead-gen/data/setup.json")
PRIVATE_KEYS = {"app_password", "sender", "sender_name", "demo_base_url", "id", "email", "password", "phone"}

FORBIDDEN_PATH = re.compile(r"(^|/)(data/(?!\.gitkeep$).+|config\.json|setup\.json|\.env|[^/]+\.(db|db-wal|db-shm|sqlite|csv))$")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
SAFE_EMAIL_DOMAINS = {"example.com", "example.org", "example.net", "users.noreply.github.com", "anthropic.com"}
SAFE_EMAILS = {"name@gmail.com", "a@b.co"}
SAFE_PHONES = {"+971501234567"}
HEX32 = re.compile(r"\b[0-9a-f]{32}\b")
PHONE = re.compile(r"\+\d{1,3}[\s\-]?\(?\d{2,4}\)?[\s\-]?\d{3}[\s\-]?\d{2,4}[\s\-]?\d{0,4}")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout


def private_values() -> set:
    values = set()

    def walk(node, key=""):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str) and key in PRIVATE_KEYS and len(node.strip()) >= 4:
            value = node.strip().lower()
            values.add(value)
            values.add(value.replace(" ", ""))

    for rel in LOCAL_FILES:
        path = ROOT / rel
        if path.exists():
            try:
                walk(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
    return values


def check_line(path: str, line: str, secrets: set) -> list:
    low = line.lower()
    found = [f"your private value ({v[:2]}...)" for v in secrets if v in low]
    for m in EMAIL.finditer(line):
        addr = m.group(0).lower()
        if addr not in SAFE_EMAILS and addr.split("@")[1] not in SAFE_EMAIL_DOMAINS:
            found.append(f"e-mail address {addr}")
    if HEX32.search(low):
        found.append("32-character id (BitBrowser profile?)")
    if any(m.group(0).strip() not in SAFE_PHONES for m in PHONE.finditer(line)):
        found.append("phone number")
    return [f"{path}: {item}" for item in found]


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "--staged"
    secrets = private_values()
    problems = []

    if mode == "--staged":
        for path in git("diff", "--cached", "--name-only", "--diff-filter=AM").splitlines():
            if FORBIDDEN_PATH.search(path):
                problems.append(f"{path}: this file holds personal data and must not be committed")
        current = ""
        for line in git("diff", "--cached", "-U0", "--no-color").splitlines():
            if line.startswith("+++ b/"):
                current = line[6:]
            elif line.startswith("+") and not line.startswith("+++"):
                problems += check_line(current, line[1:], secrets)
    else:
        for path in git("ls-files").splitlines():
            if FORBIDDEN_PATH.search(path):
                problems.append(f"{path}: this file holds personal data and must not be pushed")
                continue
            try:
                text = (ROOT / path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for line in text.splitlines():
                problems += check_line(path, line, secrets)

    if problems:
        print("\nBLOCKED: personal data found, nothing was sent to GitHub:")
        for item in sorted(set(problems)):
            print("  -", item)
        print("Remove it (or move it to a git-ignored config file) and try again.\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
