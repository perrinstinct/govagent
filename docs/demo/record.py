"""Record the CLI demo as an asciinema cast, then render it: agg demo.cast demo.gif

Runs each command in a real pseudo-terminal (colors and prompts as a user sees them), answers
the interactive prompts, and timestamps all output. Waits longer than MAX_GAP (model calls) are
shortened and announced on screen, never cut silently.

    uv run python docs/demo/record.py docs/demo/demo.cast   # calls Amazon Bedrock (~$0.02)

Needs Spectral on PATH, the model settings in .env and ../govagent-demo checked out.
"""

import codecs
import fcntl
import json
import os
import pty
import re
import select
import shutil
import struct
import subprocess
import sys
import tempfile
import termios
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SPEC = REPO.parent / "govagent-demo" / "openapi.yaml"
COLS, ROWS = 140, 34
MAX_GAP = 2.5  # seconds of silence kept as is; longer waits are shortened to 1 s
PROMPT = re.compile(r"Apply this fix\? \[y/N\]: $")
DIM, CYAN, RESET = "\x1b[2m", "\x1b[1;36m", "\x1b[0m"

# (command, answers to the interactive prompts, pause after it)
SCENARIO: list[tuple[list[str], list[str], float]] = [
    (["lint", "openapi.yaml"], [], 3.5),
    (["fix", "openapi.yaml", "--interactive"], ["y", "y", "y", "y", "n", "n", "y"], 3.5),
    (["lint", "openapi.yaml"], [], 5.0),
]


class Cast:
    def __init__(self) -> None:
        self.events: list[tuple[float, str]] = []
        self.now = 0.0

    def out(self, data: str, delay: float = 0.0) -> None:
        self.now += delay
        self.events.append((round(self.now, 3), data))

    def type(self, text: str) -> None:
        for char in text:
            self.out(char, 0.045)

    def save(self, path: Path) -> None:
        header = {"version": 2, "width": COLS, "height": ROWS, "env": {"TERM": "xterm-256color"}}
        lines = [json.dumps(header)] + [json.dumps([t, "o", d]) for t, d in self.events]
        path.write_text("\n".join(lines) + "\n")


def run(cast: Cast, argv: list[str], answers: list[str], cwd: Path, env: dict[str, str]) -> None:
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
    process = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, cwd=cwd, env=env)  # noqa: S603
    os.close(slave)
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    pending = list(answers)
    screen_tail = ""
    last = started = time.monotonic()
    while True:
        if time.monotonic() - started > 180:
            process.kill()
            raise TimeoutError(f"{argv} still running after 3 minutes")
        ready, _, _ = select.select([master], [], [], 0.05)
        if ready:
            try:
                chunk = os.read(master, 4096)
            except OSError:  # the child closed the terminal
                break
            if not chunk:
                break
            now = time.monotonic()
            gap = now - last
            last = now
            if gap > MAX_GAP:
                cast.out(f"{DIM}>> {gap:.0f} s shortened: waiting for the model{RESET}\r\n", 1.0)
                gap = 0.3
            text = decoder.decode(chunk)
            cast.out(text, gap)
            screen_tail = (screen_tail + text)[-200:]
            if PROMPT.search(screen_tail):
                cast.now += 2.6  # time to read the diff
                answer = pending.pop(0) if pending else "n"  # unexpected prompt: decline
                os.write(master, f"{answer}\r".encode())
                screen_tail = ""
                last = time.monotonic()
        elif process.poll() is not None:
            break
    os.close(master)
    process.wait()


def main(output: Path) -> None:
    workdir = Path(tempfile.mkdtemp(prefix="govagent-demo-"))
    shutil.copy(SPEC, workdir / "openapi.yaml")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GOVAGENT_")}
    for line in (REPO / ".env").read_text().splitlines():
        if re.match(r"GOVAGENT_(MODEL|AWS|PRICE)", line):
            key, _, value = line.partition("=")
            env[key] = value
    env |= {
        "GOVAGENT_RULESET_PATH": str(REPO / "rulesets" / "governance.spectral.yaml"),
        "GOVAGENT_RULES_META_PATH": str(REPO / "rulesets" / "rules_meta.yaml"),
        "COLUMNS": str(COLS),
        "TERM": "xterm-256color",
    }
    cast = Cast()
    cast.out("", 0.5)
    for args, answers, pause in SCENARIO:
        cast.out(f"{CYAN}${RESET} ", 0.3)
        cast.type(f"govagent {' '.join(args)}")
        cast.out("\r\n", 0.4)
        command = ["uv", "run", "--quiet", "--project", str(REPO), "govagent", *args]
        run(cast, command, answers, workdir, env)
        cast.now += pause
    cast.out(f"{CYAN}${RESET} ", 0.0)
    cast.out("", 2.0)
    cast.save(output)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
