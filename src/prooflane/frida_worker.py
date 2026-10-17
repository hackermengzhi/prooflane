"""Disposable local Frida session; parent enforces a hard process timeout."""

import json
import sys
import time
from pathlib import Path


def main():
    import frida

    session = frida.attach(int(sys.argv[1]))
    script = None
    errors = []
    try:
        script = session.create_script(Path(sys.argv[2]).read_text(encoding="utf-8"))

        def on_message(message, data):
            print(
                json.dumps({"message": message, "data_hex": data.hex() if data else None}),
                flush=True,
            )
            if message.get("type") == "error":
                errors.append(message)

        script.on("message", on_message)
        script.load()
        time.sleep(int(sys.argv[3]))
    finally:
        if script is not None:
            script.unload()
        session.detach()
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
