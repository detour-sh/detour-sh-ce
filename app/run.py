from __future__ import annotations

import signal
import subprocess
import sys
import time


def main() -> int:
    # Migrate once, before the portal and MCP processes open the database.
    from store import init_db

    init_db()
    processes = [
        subprocess.Popen([sys.executable, "/app/server.py"]),
        subprocess.Popen([sys.executable, "/app/mcp_server.py"]),
    ]

    def stop(_signum=None, _frame=None) -> None:
        for process in processes:
            if process.poll() is None:
                process.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    try:
        while True:
            for process in processes:
                return_code = process.poll()
                if return_code is not None:
                    stop()
                    for sibling in processes:
                        try:
                            sibling.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            sibling.kill()
                    return return_code or 1
            time.sleep(0.25)
    finally:
        stop()


if __name__ == "__main__":
    raise SystemExit(main())
