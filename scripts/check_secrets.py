"""Secrets check script for TickTock Meals.

Scans all source code, markdown documentation, and configuration files
for leaked API keys, tokens, and credentials. Fails with exit code 1
if any candidate secret is detected.

Usage:
    python scripts/check_secrets.py [--path .]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import List, Pattern, Tuple, Union

# Directories and patterns to ignore during scan
IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    ".cache",
    "__pycache__",
    ".pytest_cache",
    ".idea",
    ".vscode",
}

IGNORED_EXTENSIONS = {
    ".pyc",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".bin",
    ".exe",
}

# Regex patterns matching potential secrets
SECRET_PATTERNS: List[Tuple[str, Pattern[str]]] = [
    # Google AI Studio / Gemini API key: AIzaSy followed by 33 URL-safe chars
    ("Google / Gemini API Key", re.compile(r"AIzaSy[A-Za-z0-9_\-]{33}")),
    # OpenAI API Key
    ("OpenAI API Key", re.compile(r"sk-[A-Za-z0-9_\-]{32,}")),
    # Generic hardcoded API key assignment
    ("Hardcoded API Key Assignment", re.compile(r"""(?:api_key|apikey|secret_key|gemini_api_key)\s*=\s*["'][A-Za-z0-9_\-]{20,}["']""", re.IGNORECASE)),
    # Private Key Headers
    ("Private Key Header", re.compile(r"-----BEGIN (?:RSA|EC|OPENSSH|DSA|PGP|PRIVATE) KEY-----")),
    # AWS Access Key ID
    ("AWS Access Key ID", re.compile(r"(?:A3T[A-Z0-9]|AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}")),
]


def scan_for_secrets(
    root_dir: Union[str, Path] = ".",
    ignore_env_file: bool = True,
) -> List[Tuple[str, int, str, str]]:
    """Scan directory recursively for potential secrets.

    Returns:
        List of tuples: (file_path, line_number, rule_name, redacted_snippet)
    """
    root = Path(root_dir).resolve()
    findings: List[Tuple[str, int, str, str]] = []

    for path in root.rglob("*"):
        # Skip directories
        if not path.is_file():
            continue

        # Skip ignored directories
        if any(part in IGNORED_DIRS for part in path.parts):
            continue

        # Skip ignored file extensions
        if path.suffix.lower() in IGNORED_EXTENSIONS:
            continue

        # Skip local .env if ignored (since it is in .gitignore)
        if ignore_env_file and path.name == ".env":
            continue

        # Do not scan this script itself for its regex definitions
        if path.name == "check_secrets.py":
            continue

        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        lines = content.splitlines()
        for line_num, line in enumerate(lines, start=1):
            for rule_name, pattern in SECRET_PATTERNS:
                match = pattern.search(line)
                if match:
                    matched_str = match.group(0)
                    # Exclude placeholders like YOUR_API_KEY, placeholder, example
                    if "YOUR_" in matched_str.upper() or "PLACEHOLDER" in matched_str.upper() or "EXAMPLE" in matched_str.upper():
                        continue
                    # Redact for safe printing
                    redacted = (
                        matched_str[:6] + "..." + matched_str[-4:]
                        if len(matched_str) > 10
                        else "***"
                    )
                    findings.append((str(path.relative_to(root)), line_num, rule_name, redacted))

    return findings


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan repository for leaked secrets and API keys.")
    parser.add_argument("--path", type=str, default=".", help="Root path to scan")
    args = parser.parse_args()

    findings = scan_for_secrets(root_dir=args.path)

    if findings:
        print("[ALERT] Potential secrets detected in repository!")
        print("-" * 70)
        for filepath, line_num, rule, redacted in findings:
            print(f"  [{rule}] {filepath}:{line_num} -> {redacted}")
        print("-" * 70)
        print("Scans failed. Please remove secrets before committing.")
        sys.exit(1)
    else:
        print("[OK] Secrets scan passed: No API keys or credentials detected in repository.")
        sys.exit(0)


if __name__ == "__main__":
    main()
