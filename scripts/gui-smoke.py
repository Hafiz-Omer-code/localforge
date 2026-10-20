#!/usr/bin/env python3
"""Render the actual GUI offscreen against a disposable engine (no root/hosts edits)."""
import copy
import os
import socket
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

import localforge.gui as gui
from localforge.config import find_binary
from localforge.engine import Engine
from localforge.paths import Paths


def main():
    output = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/screenshots/overview.png")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lf-gui-") as temp:
        engine = Engine(Paths(temp))
        c = copy.deepcopy(engine.config.value)
        for key in c["ports"]:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                c["ports"][key] = sock.getsockname()[1]
        c["projects_root"] = str(Path(temp) / "www")
        for name in ("hello-php", "studio"):
            root = Path(c["projects_root"]) / name / "public"
            root.mkdir(parents=True)
            (root / "index.php").write_text('<?php echo "GUI smoke test";')
        fpm = next((find_binary(x) for x in ("php-fpm", "php-fpm8.4", "php-fpm8.3") if find_binary(x)), None)
        if fpm and find_binary("php"):
            c["php_versions"]["native"] = {"cli": find_binary("php"), "fpm": fpm}
            c["active_php"] = "native"
        engine.apply(c)
        engine.auto_scan()
        try:
            if fpm and find_binary("nginx"):
                engine.start("nginx")
            if find_binary("redis-server"):
                engine.start("redis")
            # No detached real controller: exercise GUI widgets + worker + real engine.
            gui.ensure_daemon = lambda: None
            gui.call = lambda op, **kw: engine.dispatch({"op": op, **kw})
            app = QApplication([])
            app.setStyle("Fusion")
            app.setStyleSheet(gui.STYLE)
            window = gui.Window()
            window.show()
            errors = []
            def capture():
                try:
                    window.timer.stop()
                    window.pool.waitForDone(10000)
                    assert window.snapshot is not None, "GUI did not receive its controller snapshot"
                    assert window.services.rowCount() == 6
                    assert window.project_table.rowCount() == 2
                    assert window.grab().save(str(output)), "Could not write screenshot"
                    print(f"Qt smoke passed: 6 services, 2 projects. Saved {output}")
                except Exception as exc:
                    errors.append(exc)
                finally:
                    app.quit()
            QTimer.singleShot(1800, capture)
            app.exec()
            if errors:
                raise errors[0]
        finally:
            engine.stop_all()


if __name__ == "__main__":
    main()
