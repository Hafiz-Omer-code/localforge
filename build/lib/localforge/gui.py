"""PySide6 desktop client. Blocking IPC and polkit requests run off the UI thread."""

import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .client import call, ensure_daemon
from .hosts import sync_hosts

STYLE = """
QWidget { background:#111820; color:#dce5ed; font-family:'Noto Sans','DejaVu Sans'; font-size:13px; }
QMainWindow { background:#111820; }
QLabel#brand { color:#f6fafc; font-size:22px; font-weight:700; }
QLabel#eyebrow { color:#63dbb5; font-size:11px; font-weight:700; }
QLabel#title { font-size:27px; font-weight:700; color:#f6fafc; }
QLabel#muted { color:#8b9eaf; }
QListWidget { background:#0c1218; border:0; padding:12px 6px; outline:0; }
QListWidget::item { padding:14px 18px; border-radius:7px; margin:3px; color:#8b9eaf; }
QListWidget::item:selected { color:#72e4c1; background:#17302e; }
QPushButton { background:#202c38; border:1px solid #304252; padding:9px 13px; border-radius:6px; }
QPushButton:hover { background:#2c3e4e; border-color:#5e8a9b; }
QPushButton:disabled { color:#526270; border-color:#26313b; }
QPushButton#primary { background:#62d9b2; color:#0b2521; font-weight:700; border:0; }
QPushButton#primary:hover { background:#8ce9cb; }
QTableWidget { border:1px solid #283744; background:#151f28; gridline-color:#263542; selection-background-color:#294346; }
QTableWidget::item { padding:8px; }
QHeaderView::section { background:#1b2834; color:#8da2b4; border:0; padding:11px; font-weight:600; }
QComboBox,QLineEdit,QSpinBox { background:#1b2732; border:1px solid #344655; border-radius:5px; padding:7px; }
QPlainTextEdit { background:#0d141c; color:#c5d7e5; border:1px solid #2a3b49; padding:10px; font-family:'DejaVu Sans Mono'; font-size:12px; }
QStatusBar { color:#91a6b6; background:#0c1218; }
QDialogButtonBox QPushButton { min-width:80px; }
"""


class Signals(QObject):
    done = Signal(str, object)
    error = Signal(str, str)


class Task(QRunnable):
    def __init__(self, tag, fn):
        super().__init__()
        self.tag, self.fn, self.signals = tag, fn, Signals()

    def run(self):
        try:
            self.signals.done.emit(self.tag, self.fn())
        except Exception as exc:
            self.signals.error.emit(self.tag, str(exc))


def icon():
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor("#62d9b2"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(3, 3, 58, 58, 14, 14)
    p.setPen(QColor("#102c27"))
    p.setFont(QFont("DejaVu Sans", 30, QFont.Weight.Bold))
    p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "L")
    p.end()
    return QIcon(pix)


def button(text, fn, primary=False):
    b = QPushButton(text)
    if primary:
        b.setObjectName("primary")
    b.clicked.connect(fn)
    return b


def label(text, name=None):
    w = QLabel(text)
    if name:
        w.setObjectName(name)
    return w


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LocalForge · Linux development environments")
        self.setWindowIcon(icon())
        self.resize(1160, 780)
        self.setMinimumSize(950, 650)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.busy, self.snapshot, self.loaded_config = False, None, None
        self.tray = None
        root = QWidget()
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        side = QWidget()
        side.setFixedWidth(205)
        side.setStyleSheet("background:#0c1218;")
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(16, 28, 12, 20)
        side_layout.addWidget(label("LocalForge", "brand"))
        side_layout.addWidget(label("YOUR STACK. YOUR USER.", "eyebrow"))
        self.nav = QListWidget()
        self.nav.addItems(
            ["Overview", "Projects", "Configuration", "Logs & ports", "Settings"]
        )
        side_layout.addWidget(self.nav)
        side_layout.addWidget(label("LOCAL ONLY\n127.0.0.1 · no root GUI", "muted"))
        outer.addWidget(side)
        self.pages = QStackedWidget()
        outer.addWidget(self.pages, 1)
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.build_overview()
        self.build_projects()
        self.build_editor()
        self.build_logs()
        self.build_settings()
        self.nav.setCurrentRow(0)
        self.setCentralWidget(root)
        self.statusBar().showMessage("Connecting to your user controller…")
        self.setup_tray()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2500)
        self.work("connect", lambda: (ensure_daemon(), call("snapshot"))[1])

    def page(self, title, subtitle):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(30, 26, 30, 24)
        layout.setSpacing(16)
        layout.addWidget(label(title, "title"))
        sub = label(subtitle, "muted")
        sub.setWordWrap(True)
        layout.addWidget(sub)
        self.pages.addWidget(w)
        return layout

    def work(self, tag, fn):
        if self.busy:
            if tag != "snapshot":
                self.statusBar().showMessage(
                    "A controller operation is still running. Please wait."
                )
            return
        self.busy = True
        task = Task(tag, fn)
        task.signals.done.connect(self.completed)
        task.signals.error.connect(self.failed)
        self.statusBar().showMessage("Working… " + tag)
        self.pool.start(task)

    def rpc(self, op, **kwargs):
        self.work(op, lambda: call(op, **kwargs))

    def refresh(self):
        if not self.busy:
            self.work("snapshot", lambda: call("snapshot"))

    def completed(self, tag, result):
        self.busy = False
        if tag in ("connect", "snapshot"):
            self.update_snapshot(result)
            self.statusBar().showMessage(
                "Controller connected · user-owned processes · refreshed now"
            )
        elif tag == "read_config":
            self.editor.setPlainText(result)
            self.loaded_config = self.requested_config
            self.editor.setReadOnly(self.loaded_config == "hosts")
            self.save_config.setEnabled(self.loaded_config != "hosts")
            self.statusBar().showMessage("Loaded " + self.loaded_config)
        elif tag == "tail":
            self.log_text.setPlainText(result)
            self.log_text.verticalScrollBar().setValue(
                self.log_text.verticalScrollBar().maximum()
            )
        else:
            self.statusBar().showMessage(str(result)[:350], 20000)
            if tag in (
                "init_db",
                "sync_hosts",
                "start_all",
                "reload_all",
                "restart_all",
            ):
                QMessageBox.information(self, "LocalForge", str(result))
            self.refresh()

    def failed(self, tag, message):
        self.busy = False
        self.statusBar().showMessage(message[:300])
        if tag != "snapshot":
            QMessageBox.warning(self, "LocalForge · " + tag, message)

    def build_overview(self):
        layout = self.page(
            "Your development stack",
            "An isolated workspace for local web development. Package binaries, private configs and private data.",
        )
        row = QHBoxLayout()
        row.addWidget(button("▶  Start all", lambda: self.rpc("start_all"), True))
        row.addWidget(button("■  Stop all", lambda: self.rpc("stop_all")))
        row.addWidget(button("Restart all", lambda: self.rpc("restart_all")))
        row.addWidget(button("Reload all", lambda: self.rpc("reload_all")))
        row.addStretch()
        row.addWidget(button("Refresh", self.refresh))
        layout.addLayout(row)
        self.summary = label("Connecting…", "eyebrow")
        layout.addWidget(self.summary)
        self.services = QTableWidget(0, 5)
        self.services.setHorizontalHeaderLabels(
            ["SERVICE", "STATUS", "LISTENER", "PID", "CONTROLS"]
        )
        self.services.verticalHeader().hide()
        self.services.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.services.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.services.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.services.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.ResizeToContents
        )
        layout.addWidget(self.services, 1)
        php_row = QHBoxLayout()
        php_row.addWidget(label("ACTIVE PHP", "eyebrow"))
        self.php_choice = QComboBox()
        self.php_choice.setMinimumWidth(140)
        php_row.addWidget(self.php_choice)
        php_row.addWidget(
            button(
                "Switch CLI + web",
                lambda: self.rpc("switch_php", version=self.php_choice.currentText()),
            )
        )
        php_row.addStretch()
        layout.addLayout(php_row)
        row = QHBoxLayout()
        row.addWidget(button("Initialize database", self.init_database))
        row.addWidget(button("Open Adminer", self.open_adminer))
        row.addWidget(button("Open DBeaver", self.open_database))
        row.addWidget(button("Copy DB connection", self.copy_db))
        row.addStretch()
        layout.addLayout(row)
        note = label(
            "Start all skips Node until configured. Reload is supported by Nginx, Apache and PHP; other engines need a restart.\nRedis and database TCP ports are loopback-only. Node apps must honor HOST and PORT.",
            "muted",
        )
        note.setWordWrap(True)
        layout.addWidget(note)

    def update_snapshot(self, snap):
        first = self.snapshot is None
        self.snapshot = snap
        c = snap["settings"]
        names = {
            "nginx": "Nginx",
            "apache": "Apache",
            "php": "PHP-FPM",
            "database": "MariaDB / MySQL",
            "redis": "Redis",
            "node": "Node.js",
        }
        self.services.setRowCount(len(snap["services"]))
        for row, service in enumerate(snap["services"]):
            name = service["name"]
            text = "● Running" if service["running"] else "○ Stopped"
            if service["disabled"]:
                text = "Not selected"
            elif service["conflict"]:
                text = "Port occupied"
            elif service["error"] and not service["running"]:
                text = "Needs attention"
            items = [
                names[name],
                text,
                str(service["port"] or "Unix socket"),
                str(service["pid"] or "—"),
            ]
            for col, value in enumerate(items):
                item = QTableWidgetItem(value)
                item.setToolTip(service["error"] or value)
                if col == 1:
                    item.setForeground(
                        QColor(
                            "#63dbb5"
                            if service["running"]
                            else "#e5ba76"
                            if service["conflict"] or service["error"]
                            else "#8b9eaf"
                        )
                    )
                self.services.setItem(row, col, item)
            controls = QWidget()
            controls_layout = QHBoxLayout(controls)
            controls_layout.setContentsMargins(4, 3, 4, 3)
            for title, op in [
                (
                    "Stop" if service["running"] else "Start",
                    "stop" if service["running"] else "start",
                ),
                ("↻", "restart"),
                ("Reload", "reload"),
            ]:
                b = button(
                    title, lambda checked=False, o=op, n=name: self.rpc(o, service=n)
                )
                b.setEnabled(not service["disabled"])
                controls_layout.addWidget(b)
            self.services.setCellWidget(row, 4, controls)
            self.services.setRowHeight(row, 61)
        running = sum(s["running"] for s in snap["services"])
        self.summary.setText(
            f"{running} SERVICES RUNNING     /     HTTP :{c['ports']['http']}     /     {len(c['projects'])} PROJECTS"
        )
        previous = self.php_choice.currentText()
        pools = snap.get("php_pools", [])
        self.php_choice.setToolTip(
            "Project FPM overrides:\n"
            + "\n".join(
                f"{p['version']}: {'running' if p['running'] else 'stopped'} — {p['socket']} {p['error']}"
                for p in pools
            )
            if pools
            else "No project PHP overrides"
        )
        self.php_choice.clear()
        self.php_choice.addItems(list(c["php_versions"]))
        self.php_choice.setCurrentText(
            c["active_php"] if first or previous not in c["php_versions"] else previous
        )
        self.project_table.setRowCount(len(c["projects"]))
        for row, project in enumerate(c["projects"]):
            for col, value in enumerate(
                [
                    project["name"],
                    f"http://{project['name']}.test:{c['ports']['http']}",
                    project["path"],
                ]
            ):
                self.project_table.setItem(row, col, QTableWidgetItem(value))
        self.listeners.setPlainText(snap["listeners"])
        old = self.log_choice.currentText()
        self.log_choice.clear()
        self.log_choice.addItems(snap["logs"])
        if old in snap["logs"]:
            self.log_choice.setCurrentText(old)
        if first:
            self.load_settings()
        self.path_label.setText(
            "CONFIG  "
            + snap["paths"]["config"]
            + "\nDATA      "
            + snap["paths"]["data"]
        )

    def build_projects(self):
        layout = self.page(
            "Projects & pretty URLs",
            "New directories are discovered every 5 seconds and become name.test virtual hosts. A public/ directory is used when present.",
        )
        row = QHBoxLayout()
        row.addWidget(button("Scan project root", lambda: self.rpc("scan"), True))
        row.addWidget(button("New project", self.new_project))
        row.addWidget(button("Sync /etc/hosts…", self.sync))
        row.addStretch()
        layout.addLayout(row)
        self.project_table = QTableWidget(0, 3)
        self.project_table.setHorizontalHeaderLabels(
            ["PROJECT", "LOCAL URL", "DIRECTORY"]
        )
        self.project_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.project_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.project_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.project_table.verticalHeader().hide()
        self.project_table.cellDoubleClicked.connect(
            lambda row, col: self.open_project(row)
        )
        layout.addWidget(self.project_table, 1)
        row = QHBoxLayout()
        row.addWidget(
            button(
                "Open selected URL",
                lambda: self.open_project(self.project_table.currentRow()),
            )
        )
        row.addWidget(
            button(
                "Open project root",
                lambda: (
                    self.open_folder(self.snapshot["settings"]["projects_root"])
                    if self.snapshot
                    else None
                ),
            )
        )
        row.addStretch()
        layout.addLayout(row)
        note = label(
            "Automatic discovery reloads vhost includes. Manual scan requires stopped services and regenerates configs. Host sync asks for administrator approval and edits only your managed block.\nURLs include :8080 (or your selected HTTP port). No wildcard DNS, privileged binds or automatic TLS are assumed.",
            "muted",
        )
        note.setWordWrap(True)
        layout.addWidget(note)

    def new_project(self):
        if not self.snapshot:
            return
        name, ok = QInputDialog.getText(
            self, "New project", "Lowercase DNS name (for example: my-app)"
        )
        if not ok:
            return
        from .config import NAME

        if not NAME.fullmatch(name):
            QMessageBox.warning(
                self,
                "Invalid name",
                "Use a lowercase DNS label, 1–63 letters, digits or internal hyphens.",
            )
            return
        if any(s["running"] for s in self.snapshot["services"]):
            QMessageBox.warning(
                self,
                "Stop services",
                "Stop all services before creating and scanning projects.",
            )
            return
        root = Path(self.snapshot["settings"]["projects_root"]).expanduser() / name
        try:
            root.mkdir(parents=True, exist_ok=False)
            (root / "index.php").write_text(
                '<?php echo "<h1>Welcome to LocalForge</h1>";\n'
            )
            self.rpc("scan")
        except OSError as exc:
            QMessageBox.warning(self, "Project creation", str(exc))

    def open_project(self, row):
        if self.snapshot and row >= 0:
            c = self.snapshot["settings"]
            if row < len(c["projects"]):
                QDesktopServices.openUrl(
                    QUrl(
                        f"http://{c['projects'][row]['name']}.test:{c['ports']['http']}"
                    )
                )

    def sync(self):
        if self.snapshot:
            projects = copy.deepcopy(self.snapshot["settings"]["projects"])
            self.work("sync_hosts", lambda: sync_hosts(projects))

    def build_editor(self):
        layout = self.page(
            "Configuration editor",
            "Stop services before saving. Use persistent *-extra.conf files for customizations that survive regeneration.",
        )
        row = QHBoxLayout()
        self.config_choice = QComboBox()
        self.config_choice.addItems(
            [
                "nginx.conf",
                "httpd.conf",
                "php.ini",
                "my.cnf",
                "redis.conf",
                "nginx-http.conf",
                "apache-extra.conf",
                "my-extra.cnf",
                "redis-extra.conf",
                "hosts",
            ]
        )
        row.addWidget(self.config_choice, 1)
        row.addWidget(button("Load file", self.read_config))
        self.save_config = button("Save file", self.write_config, True)
        row.addWidget(self.save_config)
        row.addWidget(
            button(
                "Check web config",
                lambda: (
                    self.rpc("check", service=self.snapshot["settings"]["web_server"])
                    if self.snapshot
                    else None
                ),
            )
        )
        layout.addLayout(row)
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(
            "Select a file and choose Load file. /etc/hosts is read-only here; use Sync hosts to request changes."
        )
        layout.addWidget(self.editor, 1)
        layout.addWidget(
            label(
                "Managed files are overwritten by Apply settings, Scan projects and PHP switching. Back up manual edits first.",
                "muted",
            )
        )

    def read_config(self):
        if not self.busy:
            self.requested_config = self.config_choice.currentText()
            self.rpc("read_config", name=self.requested_config)

    def write_config(self):
        if (
            not self.loaded_config
            or self.loaded_config != self.config_choice.currentText()
        ):
            QMessageBox.warning(
                self, "Load file first", "Load the selected file before saving."
            )
            return
        self.rpc(
            "write_config", name=self.loaded_config, text=self.editor.toPlainText()
        )

    def build_logs(self):
        layout = self.page(
            "Logs & active ports",
            "Live port inventory comes from ss. Startup always performs a real bind probe before spawning a service.",
        )
        split = QSplitter(Qt.Orientation.Vertical)
        log_panel = QWidget()
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.log_choice = QComboBox()
        row.addWidget(self.log_choice, 1)
        row.addWidget(
            button(
                "Refresh log",
                lambda: (
                    self.rpc("tail", name=self.log_choice.currentText())
                    if self.log_choice.currentText()
                    else None
                ),
            )
        )
        row.addWidget(
            button(
                "Open log folder",
                lambda: (
                    self.open_folder(self.snapshot["paths"]["logs"])
                    if self.snapshot
                    else None
                ),
            )
        )
        log_layout.addLayout(row)
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text)
        split.addWidget(log_panel)
        self.listeners = QPlainTextEdit()
        self.listeners.setReadOnly(True)
        split.addWidget(self.listeners)
        split.setSizes([330, 160])
        layout.addWidget(split, 1)

    def build_settings(self):
        layout = self.page(
            "Workspace settings",
            "Apply requires all services stopped. Each version uses a private php.ini; runtime executables stay outside system alternatives.",
        )
        row = QHBoxLayout()
        row.addWidget(button("Reload from controller", self.load_settings))
        row.addWidget(button("Discover runtimes", lambda: self.rpc("discover")))
        row.addWidget(button("Register PHP…", self.register_php))
        row.addWidget(button("Edit ports…", self.edit_ports))
        row.addStretch()
        row.addWidget(button("Apply settings", self.apply_settings, True))
        layout.addLayout(row)
        self.settings_editor = QPlainTextEdit()
        self.settings_editor.setPlaceholderText(
            "Complete JSON settings will appear when connected."
        )
        layout.addWidget(self.settings_editor, 1)
        self.path_label = label("", "muted")
        self.path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.path_label)
        note = label(
            "Edit ports, web_server (nginx/apache), projects_root, runtime paths and Node cwd/entry here.\nPHP 7.4 / 8.1 are legacy runtimes: use only for trusted local projects, never expose them to the network.",
            "muted",
        )
        note.setWordWrap(True)
        layout.addWidget(note)

    def load_settings(self):
        if self.snapshot:
            self.settings_editor.setPlainText(
                json.dumps(self.snapshot["settings"], indent=2)
            )

    def apply_settings(self):
        try:
            c = json.loads(self.settings_editor.toPlainText())
            self.rpc("apply", settings=c)
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid JSON", str(exc))

    def edit_ports(self):
        try:
            c = json.loads(self.settings_editor.toPlainText())
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid settings draft", str(exc))
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Private workspace listeners")
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        fields = {}
        for name, value in c["ports"].items():
            field = QSpinBox()
            field.setRange(1024, 65535)
            field.setValue(value)
            fields[name] = field
            form.addRow(name.capitalize(), field)
        server = QComboBox()
        server.addItems(["nginx", "apache"])
        server.setCurrentText(c["web_server"])
        form.addRow("Web server", server)
        layout.addLayout(form)
        layout.addWidget(
            label(
                "Updates the draft. Apply settings to save.\nPort conflicts are checked before each service starts.",
                "muted",
            )
        )
        controls = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        controls.accepted.connect(dialog.accept)
        controls.rejected.connect(dialog.reject)
        layout.addWidget(controls)
        if dialog.exec():
            c["ports"] = {k: v.value() for k, v in fields.items()}
            c["web_server"] = server.currentText()
            self.settings_editor.setPlainText(json.dumps(c, indent=2))

    def register_php(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Register isolated PHP runtime")
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        fields = [QLineEdit() for _ in range(3)]
        for title, field, placeholder in zip(
            ["Label", "CLI binary", "FPM binary"],
            fields,
            [
                "8.3",
                "/home/you/.local/share/localforge/runtimes/php/8.3/bin/php",
                "/home/you/.local/share/localforge/runtimes/php/8.3/sbin/php-fpm",
            ],
        ):
            field.setPlaceholderText(placeholder)
            field.setMinimumWidth(440)
            form.addRow(title, field)
        layout.addLayout(form)
        controls = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        controls.accepted.connect(dialog.accept)
        controls.rejected.connect(dialog.reject)
        layout.addWidget(controls)
        if dialog.exec():
            try:
                c = json.loads(self.settings_editor.toPlainText())
                name, cli, fpm = [f.text().strip() for f in fields]
                c["php_versions"][name] = {"cli": cli, "fpm": fpm}
                if not c["active_php"]:
                    c["active_php"] = name
                self.settings_editor.setPlainText(json.dumps(c, indent=2))
                self.statusBar().showMessage(
                    "Runtime added to draft. Stop services and Apply settings to save."
                )
            except Exception as exc:
                QMessageBox.warning(self, "Runtime", str(exc))

    def init_database(self):
        if (
            QMessageBox.question(
                self,
                "Initialize private database?",
                "Only LocalForge's empty private database directory will be initialized.\n\nMariaDB uses Unix-socket authentication as your OS username. MySQL starts with an empty root password; set it immediately. Continue?",
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.rpc("init_db")

    def open_adminer(self):
        if self.snapshot:
            QDesktopServices.openUrl(QUrl(self.snapshot["adminer_url"]))

    def open_database(self):
        binary = shutil.which("dbeaver") or shutil.which("dbeaver-ce")
        if binary:
            subprocess.Popen(
                [binary],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.copy_db()
        elif (
            shutil.which("flatpak")
            and subprocess.run(
                ["flatpak", "info", "io.dbeaver.DBeaverCommunity"], capture_output=True
            ).returncode
            == 0
        ):
            subprocess.Popen(
                ["flatpak", "run", "io.dbeaver.DBeaverCommunity"],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.copy_db()
        else:
            QMessageBox.information(
                self,
                "Database client",
                "Install DBeaver Community using your distro or Flatpak, then retry.\nA desktop database GUI is not bundled with this source distribution.",
            )

    def copy_db(self):
        if self.snapshot:
            s = self.snapshot
            text = f"Host: 127.0.0.1\nPort: {s['settings']['ports']['database']}\nSocket: {s['paths']['mysql_socket']}\nUser: create a password-protected non-root account"
            QApplication.clipboard().setText(text)
            self.statusBar().showMessage(
                "Database connection details copied (no credentials).", 10000
            )

    def open_folder(self, path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).expanduser())))

    def setup_tray(self):
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(icon(), self)
            menu = QMenu(self)
            for title, callback in [
                ("Show LocalForge", self.showNormal),
                ("Start all", lambda: self.rpc("start_all")),
                ("Stop all", lambda: self.rpc("stop_all")),
                ("Quit GUI (keep services)", QApplication.instance().quit),
            ]:
                action = QAction(title, self)
                action.triggered.connect(callback)
                menu.addAction(action)
            self.tray.setContextMenu(menu)
            self.tray.setToolTip("LocalForge · user development services")
            self.tray.activated.connect(
                lambda reason: (
                    self.showNormal()
                    if reason == QSystemTrayIcon.ActivationReason.Trigger
                    else None
                )
            )
            self.tray.show()

    def closeEvent(self, event):
        if self.tray:
            self.hide()
            event.ignore()
        else:
            event.accept()


def main():
    if os.geteuid() == 0:
        raise SystemExit(
            "Run the LocalForge GUI as your regular desktop user, not root"
        )
    app = QApplication(sys.argv)
    app.setApplicationName("LocalForge")
    app.setOrganizationName("LocalForge")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    window = Window()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
