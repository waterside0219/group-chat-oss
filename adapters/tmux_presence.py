from __future__ import annotations

import subprocess


class TmuxPresence:
    def session_exists(self, session: str) -> bool:
        try:
            return subprocess.run(
                ["tmux", "has-session", "-t", session],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
                check=False,
            ).returncode == 0
        except Exception:
            return False
