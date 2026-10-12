"""Modell-Studio: lokale GGUF-Modelle öffnen, chatten und im Agenten-Modus arbeiten lassen."""

import json
import os
import queue
import secrets
import threading
import tkinter as tk
import urllib.request
from collections import deque
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, ttk
from tkinter import font as tkfont

import chats
import dialogs
import documents
import filedrop
import images
import llamacpp
from agent import Agent, describe_call, format_args, result_ok, run_conversation
from backend import LlamaServer, find_mmproj, find_models, thinking_title
from chatview import ChatView
from roomui import RoomMode
from themes import THEMES, ThemeManager, label_to_key
from widgets import ModeSwitch

APP_DIR = Path(__file__).resolve().parent.parent  # Code liegt in app/, Daten daneben
CONFIG_FILE = APP_DIR / "config.json"
MODEL_DIR = APP_DIR / "modelle"  # eigener Modellordner: wird immer durchsucht, Ziel für Downloads
MAX_ATTACHMENTS = 10

DEFAULTS = {
    "folders": [],                 # weitere Modellordner (zusätzlich zu MODEL_DIR)
    "extra_models": [],
    "server_exe": "",
    "ctx": 16384,
    "ngl": "auto",
    "temperature": 0.7,
    "max_tokens": 4096,
    "extra_args": "",
    "vision": True,                # Bildverständnis: mmproj-Datei neben dem Modell mitladen
    "image_tokens": 2048,          # höchstens so viele Tokens pro Bild
    "system_prompt": "Du bist ein hilfreicher Assistent. Antworte auf Deutsch.",
    "think_titles": True,          # kurze Überschrift für den Denkprozess vom Modell erzeugen lassen
    "agent_enabled": False,        # Dateien & Ordner (inkl. Word/Excel/PowerPoint, Projekte)
    "agent_commands": True,        # PowerShell- und Python-Ausführung
    "agent_workspace": str(Path.home() / "Desktop" / "Agent-Arbeitsordner"),
    "agent_folders": [],           # weitere freigegebene Ordner
    "agent_confirm": True,
    "web_enabled": True,
    "brave_key": "",
    "last_model": "",
    "theme": "dark",
    # WebFusedAgentMod (Gruppenchat mehrerer Rechner, siehe room.py / roomui.py)
    "mode": "chat",                # zuletzt gewählter Modus: "chat" oder "room"
    "room_name": "",               # eigener Name im Raum
    "room_ai": "",                 # Name der eigenen KI im Raum
    "room_tools": {"web": True, "files": False, "commands": False},  # was die eigene KI im Raum darf
    "client_id": "",               # feste Kennung dieser Installation (Wiederbeitritt nach Neustart)
}


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))  # tiefe Kopie, damit Listen in DEFAULTS unverändert bleiben
    try:
        cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    # Früher wurden Server und Modellordner von LM Studio mitbenutzt – jetzt nur noch eigene (server/, modelle/)
    if ".lmstudio" in cfg["server_exe"].lower():
        cfg["server_exe"] = ""
    cfg["folders"] = [f for f in cfg["folders"] if ".lmstudio" not in f.lower() and not same_path(f, MODEL_DIR)]
    cfg["extra_models"] = [f for f in cfg["extra_models"] if ".lmstudio" not in f.lower()]
    return cfg


def same_path(a, b) -> bool:
    return Path(a) == Path(b)  # unter Windows ohne Groß-/Kleinschreibung und mit / oder \


def scrolled_text(parent, **kw):
    """Textfeld mit (themebarem) ttk-Scrollbalken. Gibt (Rahmen, Textfeld) zurück."""
    frame = ttk.Frame(parent)
    text = tk.Text(frame, **kw)
    sb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview)
    text.configure(yscrollcommand=sb.set)
    sb.pack(side=tk.RIGHT, fill=tk.Y)
    text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    return frame, text


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = load_config()
        if not self.cfg["client_id"]:
            self.cfg["client_id"] = secrets.token_hex(8)
        self.app_dir = APP_DIR
        self.scrolled_text = scrolled_text
        self.mode = None                 # "chat" oder "room" (WebFusedAgentMod), gesetzt durch set_mode()
        self._chat_title = "Neuer Chat"
        self.events: queue.Queue = queue.Queue()
        self.server = LlamaServer(lambda t: self.events.put(("log", t)))
        self.servers = llamacpp.find_llama_servers()
        self.srv_cancel = threading.Event()
        threading.Thread(target=images.cleanup_temp, daemon=True).start()
        self.models: list[Path] = []
        self.sel_model: Path | None = None
        self.history: list[dict] = []
        self.chat_data: dict | None = None  # aktueller Chat (None = neuer, noch ungespeicherter Chat)
        self.stop_event = threading.Event()
        self.busy = False
        self.stream_q: deque = deque()   # Chat-Ereignisse in Reihenfolge; Text wird daraus flüssig „getippt“
        self.turn: dict = {}             # Zustand der laufenden Antwort (Schritt, offene Blöcke, Tokens/s)
        self._draining = False
        self._poll_id = None
        self.attachments: list[dict] = []  # angehängte Dokumente für die nächste Nachricht
        self._att_id = 0
        self._hover = None
        self._pointer = None      # Chat unter dem Mauszeiger
        self._menu_item = None    # Chat, dessen Menü gerade offen ist
        self._dots: set = set()   # Chats, bei denen gerade „⋯“ angezeigt wird
        self._ctx_item = None
        self.sources_win = None
        self.dl_win = None

        try:
            root.attributes("-alpha", 0.0)  # erst unsichtbar aufbauen, dann fertig gefärbt einblenden
        except tk.TclError:
            pass
        root.title("Modell-Studio")
        root.geometry("1250x800")
        root.minsize(900, 600)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.theme = ThemeManager(root)
        self.ui_scale = max(1.0, float(root.tk.call("tk", "scaling")) / 1.333)
        dialogs.setup(root, self.theme, self.ui_scale)  # Meldungen im Design statt tkinter.messagebox

        self._build_sidebar()
        self._build_main()
        self._build_settings()
        self.set_theme(self.cfg.get("theme", "dark"), save=False)
        self.room = RoomMode(self, self.room_side, self.room_view)
        self.refresh_models()
        self.refresh_chat_list()
        self.new_chat()
        self.set_mode(self.cfg.get("mode", "chat"))
        root.bind("<Control-n>", lambda e: self.new_chat())
        root.after(300, lambda: filedrop.enable_drop(root, self.add_attachments))  # Dateien aufs Fenster ziehen
        self._poll_id = self.root.after(50, self._poll_events)
        self.theme.present(root, fade_ms=220)

    # ================================================================ Oberfläche: Seitenleiste

    def _build_sidebar(self):
        side = ttk.Frame(self.root, style="Sidebar.TFrame", width=round(260 * self.ui_scale))
        side.pack(side=tk.LEFT, fill=tk.Y)
        side.pack_propagate(False)

        # Moduswahl ganz oben: normaler Chat | WebFusedAgentMod (Gruppenchat mehrerer Rechner);
        # die Markierung gleitet beim Wechsel weich zur Auswahl (widgets.ModeSwitch)
        modes = ttk.Frame(side, style="Sidebar.TFrame", padding=(8, 10, 8, 6))
        modes.pack(side=tk.TOP, fill=tk.X)
        self.mode_switch = ModeSwitch(modes, [("chat", "Chat"), ("room", "WebFusedAgentMod")], self.set_mode,
                                      self.ui_scale)
        self.mode_switch.pack(fill=tk.X)

        bottom = ttk.Frame(side, style="Sidebar.TFrame", padding=(8, 6, 8, 10))
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Button(bottom, text="⚙   Einstellungen", style="Sidebar.TButton",
                   command=self.open_settings).pack(fill=tk.X)

        # Inhalt je Modus: Chatliste (chat_side) oder Raum-Panel (room_side, gefüllt von RoomMode). Beide liegen
        # fertig aufgebaut übereinander; set_mode() holt nur das passende nach vorn → kein Flackern beim Wechsel.
        body = ttk.Frame(side, style="Sidebar.TFrame")
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.chat_side = ttk.Frame(body, style="Sidebar.TFrame")
        self.room_side = ttk.Frame(body, style="Sidebar.TFrame")
        self.chat_side.grid(row=0, column=0, sticky="nsew")
        self.room_side.grid(row=0, column=0, sticky="nsew")

        top = ttk.Frame(self.chat_side, style="Sidebar.TFrame", padding=(8, 6, 8, 6))
        top.pack(side=tk.TOP, fill=tk.X)
        ttk.Button(top, text="+   Neuer Chat", style="Sidebar.TButton", command=self.new_chat).pack(fill=tk.X)

        lf = ttk.Frame(self.chat_side, style="Sidebar.TFrame", padding=(8, 0, 2, 0))
        lf.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        # Spalte "menu" zeigt „⋯“ (Menü) beim geöffneten Chat und beim Chat unter der Maus
        tree = ttk.Treeview(lf, columns=("menu",), show="tree", selectmode="browse", style="Sidebar.Treeview")
        tree.column("#0", width=160, stretch=True)
        tree.column("menu", width=round(30 * self.ui_scale), minwidth=round(30 * self.ui_scale), stretch=False,
                    anchor="center")
        sb = ttk.Scrollbar(lf, orient=tk.VERTICAL, command=tree.yview, style="Sidebar.Vertical.TScrollbar")
        tree.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree.tag_configure("group", font=("Segoe UI", 8, "bold"))
        tree.bind("<<TreeviewSelect>>", self._on_chat_select)
        tree.bind("<Motion>", self._on_tree_hover)
        tree.bind("<Leave>", self._on_tree_leave)
        tree.bind("<Button-1>", self._on_tree_click)
        tree.bind("<Button-3>", self._on_tree_menu)
        tree.bind("<F2>", lambda e: self.rename_chat(self._current_tree_chat()))
        tree.bind("<Delete>", lambda e: self.delete_chat(self._current_tree_chat()))
        self.chat_tree = tree

        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Umbenennen…", command=lambda: self.rename_chat(self._ctx_item))
        menu.add_command(label="Löschen", command=lambda: self.delete_chat(self._ctx_item))
        self.theme.register_menu(menu)
        self.chat_menu = menu

    # ================================================================ Oberfläche: Chatbereich

    def _build_main(self):
        ttk.Separator(self.root, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y)
        main = ttk.Frame(self.root)
        main.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # ---- Kopfzeile: Chat-Titel links, Modell rechts
        top = ttk.Frame(main, style="Top.TFrame", padding=(18, 10, 14, 8))
        top.pack(side=tk.TOP, fill=tk.X)
        self.title_var = tk.StringVar(value="Neuer Chat")
        # width=1 + Gewicht: der Titel wird bei schmalem Fenster abgeschnitten statt die Modellauswahl zu verdrängen
        ttk.Label(top, textvariable=self.title_var, style="Title.TLabel", width=1).grid(
            row=0, column=0, rowspan=2, sticky="ew", padx=(0, 12))
        self.model_var = tk.StringVar()
        self.model_combo = ttk.Combobox(top, textvariable=self.model_var, state="readonly", width=34)
        self.model_combo.grid(row=0, column=2, sticky="e")
        self.model_combo.bind("<<ComboboxSelected>>", lambda e: self._select_model(self.model_combo.current()))
        self.theme.register_combo(self.model_combo)
        self.load_btn = ttk.Button(top, text="▶ Laden", style="Accent.TButton", command=self.load_model)
        self.load_btn.grid(row=0, column=3, padx=(6, 0))
        ttk.Button(top, text="■", width=3, command=self.unload_model).grid(row=0, column=4, padx=(4, 0))
        self.status_var = tk.StringVar(value="Kein Modell geladen")
        ttk.Label(top, textvariable=self.status_var, style="Muted.TLabel").grid(
            row=1, column=2, columnspan=3, sticky="e", pady=(3, 0))
        top.columnconfigure(0, weight=1)
        ttk.Separator(main).pack(side=tk.TOP, fill=tk.X)

        # ---- Unten: Eingabe (zuerst packen, damit sie beim Verkleinern sichtbar bleibt)
        inp = ttk.Frame(main, padding=(18, 8, 18, 14))
        inp.pack(side=tk.BOTTOM, fill=tk.X)
        self._inp_frame = inp
        self.send_btn = ttk.Button(inp, text="Senden", style="Accent.TButton", width=9, command=self._send_or_stop)
        self.send_btn.pack(side=tk.RIGHT, fill=tk.Y, padx=(8, 0))
        ttk.Button(inp, text="+", style="Attach.TButton", width=2, command=self.pick_attachments).pack(
            side=tk.LEFT, fill=tk.Y, padx=(0, 8))
        self.input = tk.Text(inp, height=3, width=1, wrap=tk.WORD, font=("Segoe UI", 11), padx=10, pady=8)
        self.input.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.input.bind("<Return>", self._on_enter)
        self.input.bind("<<Paste>>", self._on_paste)
        self.theme.register_text(self.input)
        # Angehängte Dokumente (wird nur eingeblendet, wenn etwas angehängt ist)
        self.attach_bar = ttk.Frame(main, padding=(18, 8, 18, 0))

        # ---- Chatverlauf (Darstellung komplett in chatview.py): normaler Chat und Gruppenchat je eine Ansicht,
        # übereinander im selben Platz; set_mode() holt die passende nach vorn
        box = ttk.Frame(main)
        box.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)
        self.chat_frame, self.chat, self.view = self._make_chat_view(box)
        self.room_frame, self.room_chat, self.room_view = self._make_chat_view(box)
        self.chat_frame.grid(row=0, column=0, sticky="nsew")
        self.room_frame.grid(row=0, column=0, sticky="nsew")

    def _make_chat_view(self, parent):
        frame, text = scrolled_text(parent, wrap=tk.WORD, font=("Segoe UI", 11), padx=28, pady=14, width=1)
        self.theme.register_text(text, role="plain")
        view = ChatView(text, self.ui_scale, register_menu=self.theme.register_menu)
        view.attach_scrollbar(next(w for w in frame.winfo_children() if isinstance(w, ttk.Scrollbar)))
        return frame, text, view

    # ================================================================ Modus: Chat | WebFusedAgentMod

    def set_mode(self, mode: str):
        mode = mode if mode in ("chat", "room") else "chat"
        if mode == self.mode:
            return
        first = self.mode is None
        self.mode = self.cfg["mode"] = mode
        room = mode == "room"
        self.mode_switch.select(mode, animate=not first)
        (self.room_side if room else self.chat_side).tkraise()
        (self.room_frame if room else self.chat_frame).tkraise()
        self.send_btn.configure(text="■ Stopp" if self.busy and not room else "Senden")
        self.update_title()
        self.input.focus_set()

    def update_title(self):
        if self.mode == "room":
            info = self.room.info
            title = f"WebFusedAgentMod  ·  {info['room']}" if info else "WebFusedAgentMod"
        else:
            title = self._chat_title
        self.title_var.set(title if len(title) <= 60 else title[:59] + "…")

    def theme_chat(self) -> dict:
        """Chatfarben des aktuellen Designs (für RoomMode, z. B. Namensfarben)."""
        return THEMES[self.theme.key]["chat"]

    # ================================================================ Oberfläche: Einstellungen

    def _build_settings(self):
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.title("Einstellungen")
        w, h = round(700 * self.ui_scale), round(580 * self.ui_scale)
        win.geometry(f"{w}x{h}")
        win.minsize(560, 460)
        win.transient(self.root)
        win.protocol("WM_DELETE_WINDOW", self.close_settings)
        win.bind("<Escape>", lambda e: self.close_settings())
        self.theme.register_window(win)
        self.settings_win = win
        self._settings_placed = False

        outer = ttk.Frame(win, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)
        bar = ttk.Frame(outer)
        bar.pack(side=tk.BOTTOM, fill=tk.X, pady=(10, 0))
        ttk.Button(bar, text="Schließen", command=self.close_settings).pack(side=tk.RIGHT)
        ttk.Label(bar, text="Änderungen werden automatisch gespeichert.", style="Muted.TLabel").pack(side=tk.LEFT)
        nb = ttk.Notebook(outer)
        nb.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.settings_nb = nb
        tabs = {}
        for key, title in [("models", "Modelle"), ("load", "Laden"), ("tools", "Werkzeuge"),
                           ("prompt", "System-Prompt"), ("look", "Darstellung"), ("log", "Server-Log")]:
            tabs[key] = ttk.Frame(nb, padding=14)
            nb.add(tabs[key], text=title)
        self.settings_tabs = tabs

        # ---- Modelle
        t = tabs["models"]
        self.models_info = tk.StringVar()
        ttk.Label(t, textvariable=self.models_info).pack(anchor="w")
        lf = ttk.Frame(t)
        lf.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        self.model_list = tk.Listbox(lf, activestyle="none", exportselection=False, font=("Segoe UI", 10))
        sb = ttk.Scrollbar(lf, command=self.model_list.yview)
        self.model_list.configure(yscrollcommand=sb.set)
        self.model_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.model_list.bind("<<ListboxSelect>>", self._on_model_list_select)
        self.model_list.bind("<Double-Button-1>",
                             lambda e: self.load_model() if self.models else self.open_model_dir())
        self.theme.register_list(self.model_list)
        bf = ttk.Frame(t)
        bf.pack(fill=tk.X, pady=(8, 0))
        for i, (txt, cmd) in enumerate([("Aktualisieren", self.refresh_models),
                                        ("Modellordner öffnen", self.open_model_dir),
                                        ("Weitere Ordner…", self.open_sources),
                                        ("Datei öffnen…", lambda: self.add_file(self.settings_win)),
                                        ("Herunterladen…", self.open_download)]):
            ttk.Button(bf, text=txt, command=cmd).grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 4, 0))
            bf.columnconfigure(i, weight=1)
        lb = ttk.Frame(t)
        lb.pack(fill=tk.X, pady=(12, 0))
        self.load_btn2 = ttk.Button(lb, text="▶ Modell laden", style="Accent.TButton", command=self.load_model)
        self.load_btn2.pack(side=tk.LEFT)
        ttk.Button(lb, text="■ Entladen", command=self.unload_model).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(lb, textvariable=self.status_var, style="Muted.TLabel").pack(side=tk.LEFT, padx=(10, 0))

        # ---- Laden
        t = tabs["load"]
        self.server_var = tk.StringVar()
        labels = [s["label"] for s in self.servers] + ["Eigene llama-server.exe…"]
        ttk.Label(t, text="Backend").grid(row=0, column=0, sticky="w", padx=(0, 12), pady=3)
        self.server_combo = ttk.Combobox(t, textvariable=self.server_var, values=labels, state="readonly")
        self.server_combo.grid(row=0, column=1, sticky="ew", pady=3)
        self.server_combo.bind("<<ComboboxSelected>>", self._on_server_selected)
        self.theme.register_combo(self.server_combo)
        if self.cfg["server_exe"]:
            match = [s["label"] for s in self.servers if s["exe"] == self.cfg["server_exe"]]
            self.server_var.set(match[0] if match else self.cfg["server_exe"])
        elif self.servers:
            self.server_var.set(self.servers[0]["label"])
        self.srv_btn = ttk.Button(t, text="llama.cpp herunterladen / aktualisieren", command=self.download_server)
        self.srv_btn.grid(row=1, column=1, sticky="w", pady=(4, 0))
        self.srv_status = tk.StringVar(value="Neueste Version von github.com/ggml-org/llama.cpp, passend zur Grafikkarte"
                                       if self.servers else "Noch nicht installiert – wird zum Laden von Modellen gebraucht")
        ttk.Label(t, textvariable=self.srv_status, style="Muted.TLabel", wraplength=round(480 * self.ui_scale)
                  ).grid(row=2, column=1, sticky="w", pady=(0, 8))

        self.ctx_var = tk.StringVar(value=str(self.cfg["ctx"]))
        self.ngl_var = tk.StringVar(value=self.cfg["ngl"])
        self.extra_var = tk.StringVar(value=self.cfg["extra_args"])
        self.temp_var = tk.StringVar(value=str(self.cfg["temperature"]))
        self.maxtok_var = tk.StringVar(value=str(self.cfg["max_tokens"]))
        self.vision_var = tk.BooleanVar(value=self.cfg["vision"])
        self.imgtok_var = tk.StringVar(value=str(self.cfg["image_tokens"]))
        rows = [("Kontext (Tokens)", self.ctx_var, "Mindestens 16384 für Websuche"),
                ("GPU-Layers", self.ngl_var, "„auto“ passt das Modell an den Grafikspeicher an"),
                ("Extra-Argumente", self.extra_var, "z. B. --n-cpu-moe 10 oder -fa on"),
                ("Temperatur", self.temp_var, "niedriger = sachlicher, höher = kreativer"),
                ("Max. Antwort-Tokens", self.maxtok_var, ""),
                ("Max. Tokens pro Bild", self.imgtok_var, "mehr = Details und kleine Schrift besser erkennbar, "
                                                          "braucht aber mehr Kontext")]
        r = 3
        for txt, var, hint in rows:
            ttk.Label(t, text=txt).grid(row=r, column=0, sticky="w", padx=(0, 12), pady=(3, 0))
            ttk.Entry(t, textvariable=var).grid(row=r, column=1, sticky="ew", pady=(3, 0))
            r += 1
            if hint:
                ttk.Label(t, text=hint, style="Muted.TLabel").grid(row=r, column=1, sticky="w")
                r += 1
        ttk.Checkbutton(t, text="Bildverständnis laden (mmproj-Datei im Ordner des Modells, ca. 1 GB Grafikspeicher)",
                        variable=self.vision_var).grid(row=r, column=0, columnspan=2, sticky="w", pady=(8, 0))
        r += 1
        ttk.Label(t, text="Änderungen gelten beim nächsten Laden eines Modells (Temperatur und "
                          "Antwort-Tokens sofort).", style="Muted.TLabel", wraplength=round(560 * self.ui_scale)
                  ).grid(row=r, column=0, columnspan=2, sticky="w", pady=(14, 0))
        t.columnconfigure(1, weight=1)

        # ---- Werkzeuge
        t = tabs["tools"]
        self.web_var = tk.BooleanVar(value=self.cfg["web_enabled"])
        self.brave_var = tk.StringVar(value=self.cfg["brave_key"])
        self.agent_var = tk.BooleanVar(value=self.cfg["agent_enabled"])
        self.cmd_var = tk.BooleanVar(value=self.cfg["agent_commands"])
        self.confirm_var = tk.BooleanVar(value=self.cfg["agent_confirm"])
        self.ws_var = tk.StringVar(value=self.cfg["agent_workspace"])
        self.extra_folders: list[str] = list(self.cfg["agent_folders"])
        ttk.Checkbutton(t, text="Internetzugriff (Websuche & Seiten lesen)", variable=self.web_var).grid(
            row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(t, text="Brave-API-Key (optional, falls DuckDuckGo blockiert)", style="Muted.TLabel").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(6, 2))
        ttk.Entry(t, textvariable=self.brave_var, show="•").grid(row=2, column=0, columnspan=2, sticky="ew")
        ttk.Separator(t).grid(row=3, column=0, columnspan=2, sticky="ew", pady=12)
        ttk.Checkbutton(t, text="Dateien & Ordner – lesen, schreiben, Word/Excel/PowerPoint, Programmierprojekte",
                        variable=self.agent_var).grid(row=4, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(t, text="Befehle & Programme ausführen (PowerShell, Python)", variable=self.cmd_var).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Checkbutton(t, text="Jede Aktion bestätigen (empfohlen)", variable=self.confirm_var).grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Label(t, text="Freigegebene Ordner – nur hier darf das Modell lesen und schreiben. "
                          "★ = Arbeitsordner (Standard für neue Dateien):", style="Muted.TLabel",
                  wraplength=round(600 * self.ui_scale)).grid(row=7, column=0, columnspan=2, sticky="w", pady=(10, 2))
        lf = ttk.Frame(t)
        lf.grid(row=8, column=0, columnspan=2, sticky="nsew")
        self.folder_list = tk.Listbox(lf, activestyle="none", exportselection=False, height=5, font=("Segoe UI", 9))
        sb = ttk.Scrollbar(lf, command=self.folder_list.yview)
        self.folder_list.configure(yscrollcommand=sb.set)
        self.folder_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.folder_list.bind("<Delete>", lambda e: self._remove_folder())
        self.theme.register_list(self.folder_list)
        bf = ttk.Frame(t)
        bf.grid(row=9, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Button(bf, text="Ordner hinzufügen…", command=self._add_folder).pack(side=tk.LEFT)
        ttk.Button(bf, text="★ Als Arbeitsordner", command=self._main_folder).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(bf, text="Entfernen", command=self._remove_folder).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(t, text="Tipp: Ein ganzes Laufwerk (z. B. C:\\) freizugeben ist möglich – dann aber unbedingt "
                          "„Jede Aktion bestätigen“ anlassen. PowerShell-Befehle selbst sind nicht auf die Ordner "
                          "beschränkt.", style="Muted.TLabel", wraplength=round(600 * self.ui_scale)
                  ).grid(row=10, column=0, columnspan=2, sticky="w", pady=(8, 0))
        t.columnconfigure(0, weight=1)
        t.rowconfigure(8, weight=1)
        self._fill_folders()

        # ---- System-Prompt
        t = tabs["prompt"]
        ttk.Label(t, text="Wird jeder Unterhaltung als Anweisung vorangestellt:").pack(anchor="w", pady=(0, 6))
        sys_frame, self.sys_text = scrolled_text(t, wrap=tk.WORD, font=("Segoe UI", 10), padx=8, pady=6)
        sys_frame.pack(fill=tk.BOTH, expand=True)
        self.sys_text.insert("1.0", self.cfg["system_prompt"])
        self.theme.register_text(self.sys_text)

        # ---- Darstellung
        t = tabs["look"]
        ttk.Label(t, text="Design").grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.theme_var = tk.StringVar()
        self.theme_combo = ttk.Combobox(t, textvariable=self.theme_var, state="readonly", width=28,
                                        values=[th["label"] for th in THEMES.values()])
        self.theme_combo.grid(row=0, column=1, sticky="w")
        self.theme_combo.bind("<<ComboboxSelected>>",
                              lambda e: self.set_theme(label_to_key(self.theme_var.get())))
        self.theme.register_combo(self.theme_combo)
        ttk.Label(t, text="Eigene Farben: in themes.py einen Eintrag in THEMES anpassen oder hinzufügen.",
                  style="Muted.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Separator(t).grid(row=2, column=0, columnspan=2, sticky="ew", pady=14)
        self.think_title_var = tk.BooleanVar(value=self.cfg["think_titles"])
        ttk.Checkbutton(t, text="Denkprozess mit kurzer Überschrift zusammenfassen",
                        variable=self.think_title_var).grid(row=3, column=0, columnspan=2, sticky="w")
        ttk.Label(t, text="Nach jeder Antwort fasst das geladene Modell seinen (zugeklappten) Denkprozess in "
                          "wenigen Wörtern zusammen – eine kleine zusätzliche Anfrage. Ausgeschaltet heißt der "
                          "Block einfach „Gedankengang“.", style="Muted.TLabel",
                  wraplength=round(560 * self.ui_scale)).grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))

        # ---- Server-Log
        t = tabs["log"]
        log_frame, self.log = scrolled_text(t, wrap=tk.NONE, state=tk.DISABLED, font=("Consolas", 9),
                                            padx=6, pady=4)
        log_frame.pack(fill=tk.BOTH, expand=True)
        self.theme.register_text(self.log)

    def open_settings(self, tab: str | None = None):
        win = self.settings_win
        if tab:
            self.settings_nb.select(self.settings_tabs[tab])
        if win.winfo_viewable():
            win.lift()
            win.focus_set()
            return
        geometry = None
        if not self._settings_placed:
            self.root.update_idletasks()
            w, h = round(700 * self.ui_scale), round(580 * self.ui_scale)
            x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - w) // 2)
            y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - h) // 3)
            geometry = f"{w}x{h}+{x}+{y}"
            self._settings_placed = True
        self.theme.present(win, geometry)
        win.focus_set()

    def close_settings(self):
        self.save_config()
        self.settings_win.withdraw()

    def set_theme(self, key: str, save: bool = True):
        theme = self.theme.apply(key)
        self.theme_var.set(theme["label"])
        self.view.apply_theme(theme["chat"])
        self.room_view.apply_theme(theme["chat"])
        p = self.theme.pal
        if p:
            self.mode_switch.set_colors(bg=p["sidebar"], track=p["side_hover"] if p["dark"] else p["side_sel"],
                                        pill=p["side_sel"] if p["dark"] else p["surface"], border=p["accent"],
                                        fg=p["fg"], muted=p["muted"])
        else:  # Windows-Standard: neutrale Grautöne auf der Fensterfarbe
            r, g, b = (v // 257 for v in self.root.winfo_rgb(self.root.cget("background")))
            self.mode_switch.set_colors(bg=f"#{r:02x}{g:02x}{b:02x}", track="#e1e1e1", pill="#ffffff",
                                        border="#0b63c5", fg="#1b1b1b", muted="#5f5f5f")
        if hasattr(self, "room"):
            self.room.refresh_panel()  # Teilnehmerliste nutzt Farben aus dem Design
        muted = theme.get("muted", "#777777")
        self.chat_tree.tag_configure("group", foreground=muted)
        self.chat_tree.tag_configure("hover", background=theme.get("side_hover", "#e8e8e8"))
        self.cfg["theme"] = self.theme.key
        if save:
            self.save_config()

    # ================================================================ Chatliste

    def refresh_chat_list(self):
        tree = self.chat_tree
        self._hover = None
        self._dots = set()
        tree.delete(*tree.get_children())
        items = chats.list_all()
        if not items:
            tree.insert("", tk.END, iid="grp:leer", text="  Noch keine Chats", tags=("group",))
        last = None
        for c in items:
            group = chats.group_label(c["updated"])
            if group != last:
                tree.insert("", tk.END, iid=f"grp:{group}", text="  " + group.upper(), tags=("group",))
                last = group
            tree.insert("", tk.END, iid=c["id"], text="  " + c["title"], tags=("chat",))
        self._sync_tree_selection()

    def _sync_tree_selection(self):
        cid = self.chat_data["id"] if self.chat_data else None
        if cid and self.chat_tree.exists(cid):
            if self.chat_tree.selection() != (cid,):
                self.chat_tree.selection_set(cid)
            self.chat_tree.see(cid)
        elif self.chat_tree.selection():
            self.chat_tree.selection_remove(*self.chat_tree.selection())
        self._update_dots()

    def _set_title(self, title: str):
        self._chat_title = title
        self.update_title()

    def _current_tree_chat(self):
        sel = self.chat_tree.selection()
        return sel[0] if sel and not sel[0].startswith("grp:") else None

    def _on_chat_select(self, _e=None):
        self._set_hover(None)
        self._update_dots()
        sel = self.chat_tree.selection()
        if not sel:
            return
        item = sel[0]
        if self.chat_data and item == self.chat_data["id"]:
            return
        if item.startswith("grp:"):
            self._sync_tree_selection()
            return
        if self.busy:
            self._sync_tree_selection()
            dialogs.showinfo("Modell-Studio", "Bitte warten, bis die Antwort fertig ist (oder „Stopp“ drücken).")
            return
        self.open_chat(item)

    def _set_hover(self, item):
        tree = self.chat_tree
        if item == self._hover:
            return
        if self._hover and tree.exists(self._hover):
            tree.item(self._hover, tags=[t for t in tree.item(self._hover, "tags") if t != "hover"])
        self._hover = None
        if item and not item.startswith("grp:") and item not in tree.selection():
            tree.item(item, tags=list(tree.item(item, "tags")) + ["hover"])
            self._hover = item

    def _on_tree_hover(self, e):
        row = self.chat_tree.identify_row(e.y) or None
        self._set_hover(row)
        if row != self._pointer:
            self._pointer = row
            self._update_dots()

    def _on_tree_leave(self, _e=None):
        self._set_hover(None)
        self._pointer = None
        self._update_dots()

    def _update_dots(self):
        """„⋯“ beim geöffneten Chat, beim Chat unter der Maus und beim Chat mit offenem Menü anzeigen."""
        tree = self.chat_tree
        want = {i for i in (self._pointer, self._menu_item, *tree.selection())
                if i and not i.startswith("grp:") and tree.exists(i)}
        for i in self._dots - want:
            if tree.exists(i):
                tree.set(i, "menu", "")
        for i in want - self._dots:
            tree.set(i, "menu", "•••")
        self._dots = want

    def _on_tree_click(self, e):
        """Klick auf „⋯“ öffnet das Menü, ohne den Chat zu wechseln."""
        tree = self.chat_tree
        item = tree.identify_row(e.y)
        if tree.identify_column(e.x) != "#1" or not item or item.startswith("grp:"):
            return None
        x, y, _w, h = tree.bbox(item, "menu")
        self._open_chat_menu(item, tree.winfo_rootx() + x, tree.winfo_rooty() + y + h)
        return "break"

    def _on_tree_menu(self, e):
        item = self.chat_tree.identify_row(e.y)
        if not item or item.startswith("grp:"):
            return
        self._open_chat_menu(item, e.x_root, e.y_root)

    def _open_chat_menu(self, item, x, y):
        self._ctx_item = self._menu_item = item
        self._update_dots()
        try:
            self.chat_menu.tk_popup(x, y)
        finally:
            self.chat_menu.grab_release()
            self._menu_item = None
            self._update_dots()

    def rename_chat(self, chat_id):
        if not chat_id:
            return
        current = self.chat_data if self.chat_data and self.chat_data["id"] == chat_id else None
        data = current or chats.load(chat_id)
        if not data:
            return
        title = self.ask_text("Chat umbenennen", "Neuer Name:", data.get("title", ""))
        if not title:
            return
        data["title"] = title
        if current is not None:
            self._set_title(title)
            if self.busy:  # wird nach der Antwort ohnehin gespeichert
                self.chat_tree.item(chat_id, text="  " + title)
                return
            data["messages"] = self.history
        chats.save(data, touch=False)
        self.refresh_chat_list()

    def delete_chat(self, chat_id):
        if not chat_id:
            return
        is_current = self.chat_data and self.chat_data["id"] == chat_id
        if is_current and self.busy:
            dialogs.showinfo("Modell-Studio", "Dieser Chat läuft gerade noch. Bitte erst „Stopp“ drücken.")
            return
        chats.delete(chat_id)
        if is_current:
            self.chat_data = None
            self.new_chat()
        self.refresh_chat_list()

    def ask_text(self, title, prompt, initial="") -> str | None:
        """Kleiner Eingabedialog im aktuellen Design."""
        win = tk.Toplevel(self.root)
        win.withdraw()  # erst fertig aufbauen und platzieren, dann einblenden (theme.present)
        win.title(title)
        win.transient(self.root)
        win.resizable(False, False)
        self.theme.register_window(win)
        body = ttk.Frame(win, padding=16)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text=prompt).pack(anchor="w")
        var = tk.StringVar(value=initial)
        entry = ttk.Entry(body, textvariable=var, width=46)
        entry.pack(fill=tk.X, pady=(6, 12))
        result = {}

        def ok(_e=None):
            result["v"] = var.get().strip()
            win.destroy()

        bf = ttk.Frame(body)
        bf.pack(fill=tk.X)
        ttk.Button(bf, text="Abbrechen", command=win.destroy).pack(side=tk.RIGHT)
        ttk.Button(bf, text="OK", style="Accent.TButton", command=ok).pack(side=tk.RIGHT, padx=(0, 6))
        entry.bind("<Return>", ok)
        win.bind("<Escape>", lambda e: win.destroy())
        self.root.update_idletasks()
        self.theme.present(win, f"+{self.root.winfo_rootx() + 300}+{self.root.winfo_rooty() + 200}")
        entry.focus_set()
        entry.select_range(0, tk.END)
        win.grab_set()
        self.root.wait_window(win)
        return result.get("v") or None

    # ================================================================ Chat öffnen / anzeigen

    def new_chat(self):
        if self.mode == "room":  # „+ Neuer Chat“/Strg+N führt zurück in den normalen Chat
            self.set_mode("chat")
        if self.busy:
            return
        self.chat_data = None
        self.history = []
        self._set_title("Neuer Chat")
        self._sync_tree_selection()
        self._show_placeholder()
        self.input.focus_set()

    def open_chat(self, chat_id):
        data = chats.load(chat_id)
        if not data:
            dialogs.showerror("Modell-Studio", "Der Chat konnte nicht geladen werden.")
            self.refresh_chat_list()
            return
        self.chat_data = data
        self.history = data.get("messages", [])
        self._set_title(data.get("title", "Chat"))
        self._render_history()
        self.input.focus_set()

    def _show_placeholder(self):
        if self.server.model:
            what = "Dokumente und Bilder" if self.server.vision else "Dokumente"
            self.view.placeholder("Womit kann ich helfen?", f"Geladen: {self.server.model.stem}\n\n{what} "
                                  "anhängen: „+“ klicken, Dateien ins Fenster ziehen oder mit Strg+V einfügen")
        else:
            self.view.placeholder("Kein Modell geladen", "Oben rechts ein Modell auswählen und „▶ Laden“ klicken.")

    def _render_history(self):
        """Gespeicherten Chat anzeigen: Nutzer rechts, Antworten links, Denkprozess/Werkzeuge zugeklappt."""
        self.view.clear()
        if not self.history:
            self._show_placeholder()
            return
        results = {m.get("tool_call_id"): m.get("content") or "" for m in self.history if m.get("role") == "tool"}
        for m in self.history:
            role, content = m.get("role"), m.get("content") or ""
            if role == "user":
                self.view.user(m.get("_text", content), self._display_files(m.get("_files")))
            elif role == "assistant":
                if m.get("_thinking"):
                    self.view.think_static(m["_thinking"], m.get("_thinking_title"), m.get("_thinking_secs"))
                if content:
                    self.view.markdown(content)
                for c in m.get("tool_calls") or []:
                    fn = c.get("function", {})
                    icon, label, detail = describe_call(fn.get("name", ""), fn.get("arguments", ""))
                    res = results.get(c.get("id"))
                    self.view.tool_static(label, detail, format_args(fn.get("arguments", "")), res,
                                          result_ok(res), icon)
                if content and not m.get("tool_calls"):
                    self.view.footer(content)
        self.view.scroll_end()

    def _save_chat(self):
        if self.chat_data is None:
            return
        self.chat_data["messages"] = self.history
        try:
            chats.save(self.chat_data)
        except OSError as e:
            self.view.note(f"Chat konnte nicht gespeichert werden: {e}", "error")
        self.refresh_chat_list()

    # ================================================================ Modelle

    def open_model_dir(self):
        MODEL_DIR.mkdir(exist_ok=True)
        os.startfile(MODEL_DIR)

    def refresh_models(self):
        MODEL_DIR.mkdir(exist_ok=True)  # damit man Modelle gleich hineinlegen kann
        self.models = find_models([str(MODEL_DIR)] + self.cfg["folders"])
        for f in self.cfg["extra_models"]:
            p = Path(f)
            if p.is_file() and p not in self.models:
                self.models.append(p)
        self.model_list.delete(0, tk.END)
        for p in self.models:
            gb = p.stat().st_size / 1024 ** 3
            self.model_list.insert(tk.END, f"  {p.stem}   ({gb:.1f} GB)")
        if self.models:
            self.models_info.set(f"{len(self.models)} Modelle gefunden  –  Doppelklick lädt ein Modell")
            self.model_combo.configure(values=[p.stem for p in self.models])
        else:
            self.models_info.set("Keine Modelle gefunden")
            self.model_list.insert(tk.END, "  Keine .gguf-Modelle gefunden.")
            self.model_list.insert(tk.END, "  Doppelklick hier öffnet den Modellordner – Modelle einfach hineinlegen")
            self.model_list.insert(tk.END, "  und „Aktualisieren“ klicken, oder über „Herunterladen…“ laden.")
            self.model_combo.configure(values=[])
            self.model_var.set("Keine Modelle – siehe ⚙ Einstellungen")

        wanted = self.sel_model or (Path(self.cfg["last_model"]) if self.cfg["last_model"] else None)
        idx = next((i for i, p in enumerate(self.models) if wanted and same_path(p, wanted)), None)
        if idx is None and wanted:  # z. B. nach dem Verschieben in einen anderen Ordner: gleicher Dateiname
            idx = next((i for i, p in enumerate(self.models) if p.name.lower() == wanted.name.lower()), None)
        if idx is None and self.models:
            idx = 0
        self.sel_model = None
        if idx is not None:
            self._select_model(idx)
        self._fill_sources()

    def _select_model(self, idx: int):
        if idx is None or not (0 <= idx < len(self.models)):
            return
        self.sel_model = self.models[idx]
        self.model_combo.current(idx)
        self.model_list.selection_clear(0, tk.END)
        self.model_list.selection_set(idx)
        self.model_list.see(idx)

    def _on_model_list_select(self, _e=None):
        sel = self.model_list.curselection()
        if sel and sel[0] < len(self.models):
            self._select_model(sel[0])

    def add_folder(self, parent=None) -> bool:
        d = filedialog.askdirectory(title="Ordner mit GGUF-Modellen wählen", parent=parent or self.root)
        if not d:
            return False
        if not any(same_path(d, f) for f in [str(MODEL_DIR)] + self.cfg["folders"]):
            self.cfg["folders"].append(str(Path(d)))
            self.save_config()
        self.refresh_models()
        return True

    def add_file(self, parent=None):
        f = filedialog.askopenfilename(title="GGUF-Modell öffnen", filetypes=[("GGUF-Modelle", "*.gguf")],
                                       parent=parent or self.root)
        if f:
            f = str(Path(f))
            if not any(same_path(f, x) for x in self.cfg["extra_models"]):
                self.cfg["extra_models"].append(f)
            self.cfg["last_model"] = f
            self.sel_model = Path(f)
            self.save_config()
            self.refresh_models()

    # ---------------------------------------------------------------- Modellordner-Dialog

    def open_sources(self):
        """Fenster zum Verwalten der Ordner (und Einzeldateien), in denen nach Modellen gesucht wird."""
        if self.sources_win and self.sources_win.winfo_exists():
            self.sources_win.lift()
            return
        parent = self.settings_win if self.settings_win.winfo_viewable() else self.root
        win = tk.Toplevel(parent)
        win.withdraw()
        win.title("Weitere Modellordner")
        win.geometry(f"{round(760 * self.ui_scale)}x{round(400 * self.ui_scale)}")
        win.minsize(520, 300)
        win.transient(parent)
        self.theme.register_window(win)
        self.sources_win = win

        body = ttk.Frame(win, padding=12)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text="In diesen Ordnern (inkl. aller Unterordner) wird nach .gguf-Modellen gesucht. "
                             "Der eigene Ordner „modelle“ ist immer dabei:").pack(anchor="w")
        lf = ttk.Frame(body)
        lf.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        lst = tk.Listbox(lf, activestyle="none", exportselection=False, font=("Segoe UI", 9))
        sb = ttk.Scrollbar(lf, command=lst.yview)
        lst.configure(yscrollcommand=sb.set)
        lst.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.theme.register_list(lst)
        self.sources_list = lst
        lst.bind("<Double-Button-1>", lambda e: self._change_source())
        lst.bind("<Delete>", lambda e: self._remove_source())

        bf = ttk.Frame(body)
        bf.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(bf, text="Ordner hinzufügen…", style="Accent.TButton",
                   command=lambda: self.add_folder(win)).pack(side=tk.LEFT)
        ttk.Button(bf, text="Datei hinzufügen…", command=lambda: self.add_file(win)).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(bf, text="Ändern…", command=self._change_source).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(bf, text="Entfernen", command=self._remove_source).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(bf, text="Schließen", command=win.destroy).pack(side=tk.RIGHT)
        ttk.Label(body, style="Muted.TLabel",
                  text="„Entfernen“ nimmt den Eintrag nur aus der Liste, die Modelldateien bleiben erhalten."
                  ).pack(anchor="w", pady=(6, 0))
        self._fill_sources()
        self.theme.present(win)

    def _sources(self) -> list[tuple[str, str]]:
        return ([("own", str(MODEL_DIR))] + [("folders", f) for f in self.cfg["folders"]] +
                [("extra_models", f) for f in self.cfg["extra_models"]])

    def _fill_sources(self):
        if not (self.sources_win and self.sources_win.winfo_exists()):
            return
        lst = self.sources_list
        sel = lst.curselection()
        lst.delete(0, tk.END)
        for kind, path in self._sources():
            p = Path(path)
            if kind in ("own", "folders"):
                if not p.is_dir():
                    info = "Ordner nicht gefunden"
                else:
                    n = sum(1 for m in self.models if m.is_relative_to(p))
                    info = f"{n} Modell" + ("" if n == 1 else "e")
                lst.insert(tk.END, f"{'Eigener Ordner' if kind == 'own' else 'Ordner'}  ·  {info}  ·  {path}")
            else:
                lst.insert(tk.END, "Datei  ·  " + ("" if p.is_file() else "nicht gefunden  ·  ") + path)
        if sel and sel[0] < lst.size():
            lst.selection_set(sel[0])

    def _selected_source(self):
        sel = self.sources_list.curselection()
        items = self._sources()
        if not sel or sel[0] >= len(items):
            dialogs.showinfo("Modellordner", "Bitte zuerst einen Eintrag in der Liste auswählen.",
                                parent=self.sources_win)
            return None
        if items[sel[0]][0] == "own":
            dialogs.showinfo("Modellordner", "Der eigene Ordner „modelle“ gehört zum Programm und wird immer "
                                "durchsucht – Modelle einfach dort hineinlegen.", parent=self.sources_win)
            return None
        return items[sel[0]]

    def _change_source(self):
        item = self._selected_source()
        if not item:
            return
        kind, old = item
        if kind == "folders":
            new = filedialog.askdirectory(title="Neuen Modellordner wählen", parent=self.sources_win,
                                          initialdir=old if Path(old).is_dir() else None)
        else:
            new = filedialog.askopenfilename(title="GGUF-Modell wählen", parent=self.sources_win,
                                             filetypes=[("GGUF-Modelle", "*.gguf")])
        if not new:
            return
        lst = self.cfg[kind]
        i = lst.index(old)
        if any(same_path(new, x) for x in lst):
            lst.pop(i)  # schon vorhanden -> alten Eintrag nur entfernen
        else:
            lst[i] = str(Path(new))
        self.save_config()
        self.refresh_models()

    def _remove_source(self):
        item = self._selected_source()
        if not item:
            return
        kind, path = item
        self.cfg[kind].remove(path)
        self.save_config()
        self.refresh_models()

    # ---------------------------------------------------------------- Laden / Entladen

    def _on_server_selected(self, _e=None):
        if self.server_var.get().startswith("Eigene"):
            f = filedialog.askopenfilename(title="llama-server.exe wählen", filetypes=[("llama-server", "*.exe")],
                                           parent=self.settings_win)
            self.server_var.set(f or (self.servers[0]["label"] if self.servers else ""))

    def _selected_server(self) -> dict | None:
        v = self.server_var.get()
        for s in self.servers:
            if s["label"] == v:
                return s
        if v and Path(v).is_file():
            return {"label": v, "exe": v}
        return None

    def _refresh_servers(self, select_exe: str | None = None):
        """Liest server/ neu ein und aktualisiert die Backend-Auswahl."""
        self.servers = llamacpp.find_llama_servers()
        labels = [s["label"] for s in self.servers]
        self.server_combo.configure(values=labels + ["Eigene llama-server.exe…"])
        match = [s["label"] for s in self.servers if select_exe and same_path(s["exe"], select_exe)]
        if match:
            self.server_var.set(match[0])
        elif self.server_var.get() not in labels and not Path(self.server_var.get()).is_file():
            self.server_var.set(labels[0] if labels else "")

    # ---------------------------------------------------------------- llama.cpp herunterladen

    def download_server(self, confirm=True):
        """Sucht auf GitHub die passende llama.cpp-Version (im Thread) – weiter in _srv_planned."""
        if self.srv_btn.instate(["disabled"]):
            return
        self.srv_btn.configure(state=tk.DISABLED)
        self.srv_status.set("Erkenne Grafikkarte und suche die neueste Version …")

        def work():
            try:
                self.events.put(("srv_plan", (llamacpp.plan(), None, confirm)))
            except Exception as e:
                self.events.put(("srv_plan", (None, str(e) or type(e).__name__, confirm)))

        threading.Thread(target=work, daemon=True).start()

    def _srv_planned(self, plan, err, confirm):
        parent = self.settings_win if self.settings_win.winfo_viewable() else self.root
        if err:
            self.srv_btn.configure(state=tk.NORMAL)
            self.srv_status.set(f"Suche fehlgeschlagen: {err}")
            return
        name = f"llama.cpp {plan['tag']} ({plan['kind']})"
        if plan["installed"]:
            self.srv_btn.configure(state=tk.NORMAL)
            self._refresh_servers(str(plan["target"] / "llama-server.exe"))
            self.srv_status.set(f"✔ {name} ist bereits die neueste Version.")
            return
        size = f"{plan['size'] / 1024 ** 2:.0f} MB"
        if confirm and not dialogs.askyesno(
                "llama.cpp herunterladen", f"Grafikkarte: {plan['gpu']}\nPassende Version: {name}\n"
                f"Download: {size}\n\nJetzt herunterladen?", parent=parent):
            self.srv_btn.configure(state=tk.NORMAL)
            self.srv_status.set("Download abgebrochen.")
            return
        self.srv_cancel = threading.Event()
        self.srv_btn.configure(state=tk.NORMAL, text="Download abbrechen", command=self.srv_cancel.set)
        self.srv_status.set(f"Lade {name} … 0 %")

        def work():
            try:
                exe = llamacpp.install(plan, lambda d, n: self.events.put(("srv_progress", (name, d, n))),
                                       self.srv_cancel)
                self.events.put(("srv_done", (name, str(exe), None)))
            except Exception as e:
                self.events.put(("srv_done", (name, None, str(e) or type(e).__name__)))

        threading.Thread(target=work, daemon=True).start()

    def _srv_finished(self, name, exe, err):
        self.srv_btn.configure(text="llama.cpp herunterladen / aktualisieren", command=self.download_server)
        if err:
            self.srv_status.set("Download abgebrochen." if self.srv_cancel.is_set()
                                else f"Download von {name} fehlgeschlagen: {err}")
            return
        self._refresh_servers(exe)
        self.save_config()
        self.srv_status.set(f"✔ {name} installiert – Modelle können jetzt geladen werden.")

    def _set_load_buttons(self, state):
        for b in (self.load_btn, self.load_btn2):
            b.configure(state=state)

    def load_model(self):
        model = self.sel_model
        if not model:
            if not self.models:
                dialogs.showinfo("Modell-Studio", "Es wurden keine Modelle gefunden. Lege deine .gguf-Modelle "
                                    "in den Ordner „modelle“ („Modellordner öffnen“) oder lade eines herunter.")
                self.open_settings("models")
            else:
                dialogs.showinfo("Modell-Studio", "Bitte zuerst ein Modell auswählen.")
            return
        server = self._selected_server()
        if not server:
            if dialogs.askyesno("Modell-Studio", "Zum Laden von Modellen wird llama.cpp gebraucht, das noch "
                                   "nicht installiert ist.\n\nJetzt die passende Version für deine Grafikkarte "
                                   "von GitHub herunterladen (einmalig, je nach Grafikkarte 20–600 MB)?"):
                self.open_settings("load")
                self.download_server(confirm=False)
            return
        try:
            ctx = int(self.ctx_var.get())
        except ValueError:
            dialogs.showerror("Modell-Studio", "Kontext muss eine Zahl sein (Einstellungen → Laden).")
            return
        mmproj = find_mmproj(model) if self.vision_var.get() else None
        try:
            image_tokens = int(self.imgtok_var.get()) if self.imgtok_var.get().strip() else None
        except ValueError:
            dialogs.showerror("Modell-Studio", "„Max. Tokens pro Bild“ muss eine Zahl sein (Einstellungen → Laden).")
            return
        self.cfg["last_model"] = str(model)
        self.save_config()
        self.load_cancel = threading.Event()
        self._set_load_buttons(tk.DISABLED)
        self.status_var.set(f"Lade {model.stem} …")

        def work():
            try:
                self.server.start(server, model, ctx, self.ngl_var.get().strip(),
                                  self.extra_var.get(), self.load_cancel, mmproj=mmproj, image_tokens=image_tokens)
                self.events.put(("loaded", model.stem))
            except Exception as e:
                self.events.put(("load_failed", str(e)))

        threading.Thread(target=work, daemon=True).start()

    def unload_model(self):
        if hasattr(self, "load_cancel"):
            self.load_cancel.set()
        self.stop_event.set()
        self.server.model = None
        threading.Thread(target=self.server.stop, daemon=True).start()
        self.status_var.set("Kein Modell geladen")
        self._set_load_buttons(tk.NORMAL)
        if self.view.is_placeholder:
            self._show_placeholder()
        self.room.model_changed()

    # ---------------------------------------------------------------- Freigegebene Ordner

    def _fill_folders(self):
        lst = self.folder_list
        lst.delete(0, tk.END)
        lst.insert(tk.END, f"★  {self.ws_var.get()}")
        for f in self.extra_folders:
            lst.insert(tk.END, f"     {f}")

    def _add_folder(self):
        d = filedialog.askdirectory(title="Ordner für das Modell freigeben", parent=self.settings_win)
        if not d:
            return
        d = str(Path(d))
        if not any(same_path(d, f) for f in [self.ws_var.get(), *self.extra_folders]):
            self.extra_folders.append(d)
        self._fill_folders()
        self.save_config()

    def _selected_folder(self) -> int | None:
        sel = self.folder_list.curselection()
        if not sel:
            dialogs.showinfo("Freigegebene Ordner", "Bitte zuerst einen Ordner in der Liste auswählen.",
                                parent=self.settings_win)
            return None
        return sel[0]

    def _main_folder(self):
        i = self._selected_folder()
        if not i:  # None oder schon der Arbeitsordner
            return
        new = self.extra_folders.pop(i - 1)
        self.extra_folders.insert(0, self.ws_var.get())
        self.ws_var.set(new)
        self._fill_folders()
        self.save_config()

    def _remove_folder(self):
        i = self._selected_folder()
        if i is None:
            return
        if i == 0:
            if not self.extra_folders:
                dialogs.showinfo("Freigegebene Ordner", "Mindestens ein Arbeitsordner wird gebraucht. "
                                    "Erst einen weiteren Ordner hinzufügen.", parent=self.settings_win)
                return
            self.ws_var.set(self.extra_folders.pop(0))
        else:
            self.extra_folders.pop(i - 1)
        self._fill_folders()
        self.save_config()

    # ================================================================ Senden

    # ================================================================ Anhänge (Dokumente)

    def pick_attachments(self):
        files = filedialog.askopenfilenames(
            title="Dokumente und Bilder anhängen", parent=self.root,
            filetypes=[("Dokumente und Bilder", "*.pdf *.docx *.xlsx *.pptx *.odt *.ods *.odp *.rtf *.txt *.md *.csv "
                                                "*.json *.html *.htm *.xml *.log *.png *.jpg *.jpeg *.bmp *.gif"),
                       ("Bilder", "*.png *.jpg *.jpeg *.bmp *.gif"), ("Alle Dateien", "*.*")])
        self.add_attachments(files)

    def add_attachments(self, paths):
        """Dateien anhängen (Knopf, Drag & Drop, Strg+V). Gelesen wird im Hintergrund."""
        skipped = 0
        for p in paths:
            p = Path(p)
            if any(same_path(a["path"], p) for a in self.attachments):
                continue
            if len(self.attachments) >= MAX_ATTACHMENTS:
                skipped += 1
                continue
            self._att_id += 1
            att = {"id": self._att_id, "path": p, "status": "loading", "doc": None, "error": ""}
            self.attachments.append(att)
            threading.Thread(target=self._read_attachment, args=(att["id"], p, round(220 * self.ui_scale)),
                             daemon=True).start()
        self._render_attachments()
        if skipped:
            dialogs.showinfo("Modell-Studio", f"Höchstens {MAX_ATTACHMENTS} Dateien pro Nachricht – "
                                                 f"{skipped} wurden nicht angehängt.")
        self.input.focus_set()

    def _read_attachment(self, att_id: int, path: Path, thumb_size: int):
        try:
            if images.is_image(path):  # Vorschaubild für die Anzeige im Chat (PNG, weil Tk kein JPG kann)
                doc = images.read(path, images.TEMP_DIR / f"vorschau-{secrets.token_hex(4)}.png", thumb_size)
            else:
                doc = documents.read(path)
            self.events.put(("attach_done", (att_id, doc, "")))
        except Exception as e:
            self.events.put(("attach_done", (att_id, None, str(e) or type(e).__name__)))

    def remove_attachment(self, att: dict):
        if att in self.attachments:
            self.attachments.remove(att)
        self._render_attachments()

    def _render_attachments(self):
        """Kärtchen über dem Eingabefeld: Name, Infozeile (bzw. Fehler) und ✕ zum Entfernen."""
        bar = self.attach_bar
        for w in bar.winfo_children():
            w.destroy()
        if not self.attachments:
            bar.pack_forget()
            return
        bar.pack(side=tk.BOTTOM, fill=tk.X, after=self._inp_frame)
        avail = max(300, self.chat.winfo_width() - round(36 * self.ui_scale))
        f_name, f_info = tkfont.Font(font=("Segoe UI", 9)), tkfont.Font(font=("Segoe UI", 8))
        row, used = None, avail
        for att in self.attachments:
            name = att["path"].name
            name = name if len(name) <= 34 else name[:22] + "…" + name[-10:]
            if att["status"] == "loading":
                label, info, style = name, "wird gelesen …", "ChipInfo.TLabel"
            elif att["status"] == "ok":
                label, info, style = name, att["doc"].info, "ChipInfo.TLabel"
            else:
                label, info, style = f"⚠  {name}", att["error"][:70], "ChipError.TLabel"
            chip = ttk.Frame(bar, style="Chip.TFrame", padding=(10, 4, 3, 4))
            ttk.Label(chip, text=label, style="Chip.TLabel").pack(side=tk.LEFT)
            ttk.Label(chip, text=info, style=style).pack(side=tk.LEFT, padx=(8, 0))
            ttk.Button(chip, text="✕", style="Chip.TButton", width=2,
                       command=lambda a=att: self.remove_attachment(a)).pack(side=tk.LEFT, padx=(6, 0))
            # Breite aus den Schriftmaßen (kein update_idletasks: das könnte hier andere Ereignisse auslösen)
            width = (f_name.measure(label) + f_info.measure(info)
                     + round((10 + 8 + 6 + 3 + 30 + 6) * self.ui_scale))
            if row is None or used + width > avail:  # neue Zeile, wenn die Kärtchen nicht mehr passen
                row = ttk.Frame(bar)
                row.pack(side=tk.TOP, fill=tk.X, pady=(0, 4))
                used = 0
            chip.pack(in_=row, side=tk.LEFT, padx=(0, 6))
            chip.lift()  # sonst läge die später erzeugte Zeile über dem Kärtchen
            used += width

    def _on_paste(self, _e=None):
        """Strg+V: im Explorer kopierte Dateien bzw. ein Bild (Screenshot) werden angehängt, sonst normal
        Text einfügen."""
        files = filedrop.clipboard_files()
        if files:
            self.add_attachments(files)
            return "break"
        if filedrop.clipboard_has_image():
            threading.Thread(target=lambda: self.events.put(("paste_image", images.save_clipboard())),
                             daemon=True).start()
            return "break"
        return None

    def _display_files(self, files: list[dict] | None) -> list[dict] | None:
        """Kärtchen-Daten für die Anzeige: bei Bildern Pfad und Vorschaubild aus chats/bilder/ ergänzen."""
        if not files:
            return files
        out = []
        for f in files:
            if f.get("image"):
                p = chats.image_path(f["image"])
                f = {**f, "path": str(p), "thumb": str(chats.thumb_path(p))}
            out.append(f)
        return out

    def _api_message(self, m: dict) -> dict:
        """Nutzernachricht mit Bildern → OpenAI-Format: Bilder als image_url (data-URL), danach der Text.
        Ohne Bildverständnis (oder wenn die Datei fehlt) bekommt das Modell nur einen Hinweis."""
        rels = m.get("_images")
        if not rels:
            return m
        parts, missing = [], []
        for rel in rels:
            p = chats.image_path(rel)
            if self.server.vision and p.is_file():
                parts.append({"type": "image_url", "image_url": {"url": images.data_url(p)}})
            else:
                missing.append(p.name.split("-", 1)[-1])
        text = m.get("content") or ""
        if missing:
            note = "[Hier war ein Bild angehängt, das gerade nicht verfügbar ist: " + ", ".join(missing) + "]"
            text = f"{text}\n\n{note}" if text else note
        if text:
            parts.append({"type": "text", "text": text})
        return {**m, "content": parts}

    def _compose_message(self, text: str, docs: list) -> tuple[str, list[dict]]:
        """Nachricht ans Modell: eigener Text + Dokumente in <dokument>-Blöcken. Zu lange Dokumente werden
        gekürzt, damit sie in den Kontext passen (grob 3 Zeichen pro Token, 60 % des Kontexts)."""
        if not docs:
            return text, []
        try:
            ctx = int(self.ctx_var.get())
        except ValueError:
            ctx = DEFAULTS["ctx"]
        per_doc = max(4000, int(ctx * 0.6 * 3) // len(docs))
        parts, meta = ([text] if text else []), []
        for doc in docs:
            body, note = doc.text, ""
            if len(body) > per_doc:
                body = body[:per_doc]
                note = (f"\n[… gekürzt: nur die ersten {per_doc:,} von {len(doc.text):,} Zeichen passen in den "
                        "Kontext]").replace(",", ".")
            parts.append(f'<dokument name="{doc.name}" art="{doc.kind}">\n{body}{note}\n</dokument>')
            meta.append({"name": doc.name, "info": doc.info + (" · gekürzt" if note else ""), "path": str(doc.path)})
        return "\n\n".join(parts), meta

    def _on_enter(self, e):
        if e.state & 0x0001:  # Shift
            return None
        self.send()
        return "break"

    def _send_or_stop(self):
        if self.busy and self.mode == "chat":
            self.stop_event.set()
        else:
            self.send()

    def _set_busy(self, busy: bool):
        self.busy = busy
        if self.mode == "chat":  # im Raum-Modus bleibt der Knopf „Senden“ (die eigene KI stoppt man links)
            self.send_btn.configure(text="■ Stopp" if busy else "Senden")

    def send(self):
        text = self.input.get("1.0", tk.END).strip()
        if self.busy and self.mode == "chat":
            return
        if any(a["status"] == "loading" for a in self.attachments):
            dialogs.showinfo("Modell-Studio", "Die angehängten Dokumente werden noch gelesen – einen Moment bitte.")
            return
        docs = [a["doc"] for a in self.attachments if a["status"] == "ok"]
        if not text and not docs:
            return
        pics = [d for d in docs if d.image]
        if pics and self.mode == "room":
            dialogs.showinfo("Modell-Studio", "Bilder können im Gruppenchat noch nicht geteilt werden – "
                                "bitte die Bilder entfernen (✕).")
            return
        if self.mode == "room":  # Gruppenchat: Nachricht geht an den Raum, Antworten kommen von den KIs dort
            content, files_meta = self._compose_message(text, docs)
            if self.room.send(text, content, files_meta):
                self.input.delete("1.0", tk.END)
                self.attachments.clear()
                self._render_attachments()
            return
        if not self.server.is_running() or not self.server.model:
            dialogs.showinfo("Modell-Studio", "Bitte zuerst oben rechts ein Modell laden.")
            return
        if pics and not self.server.vision:
            dialogs.showinfo("Modell-Studio", "Das geladene Modell kann keine Bilder ansehen. Dafür muss neben "
                                "dem Modell eine mmproj-Datei liegen und unter Einstellungen → Laden "
                                "„Bildverständnis laden“ eingeschaltet sein – dann das Modell neu laden.")
            return
        try:
            params = {"temperature": float(self.temp_var.get()), "max_tokens": int(self.maxtok_var.get())}
        except ValueError:
            dialogs.showerror("Modell-Studio", "Temperatur / Max. Tokens ungültig (Einstellungen → Laden).")
            return

        agent = None
        files, commands, web_access = self.agent_var.get(), self.cmd_var.get(), self.web_var.get()
        if files or commands or web_access:
            if files or commands:
                try:
                    Path(self.ws_var.get()).mkdir(parents=True, exist_ok=True)
                except OSError as e:
                    dialogs.showerror("Modell-Studio", f"Arbeitsordner nicht verfügbar: {e}")
                    return
            agent = Agent(self._confirm_from_worker, files=files, commands=commands, workspace=self.ws_var.get(),
                          folders=self.extra_folders, confirm_actions=self.confirm_var.get(),
                          web_access=web_access, brave_key=self.brave_var.get())

        if self.chat_data is None:
            self.chat_data = chats.new_chat()
            self.chat_data["title"] = chats.make_title(text or docs[0].name)
            self._set_title(self.chat_data["title"])

        try:  # Bilder zum Chat kopieren (chats/bilder/<id>/), damit sie auch später noch ans Modell gehen
            rels = [chats.store_image(self.chat_data["id"], d.path, d.thumb) for d in pics]
        except OSError as e:
            dialogs.showerror("Modell-Studio", f"Bild konnte nicht gespeichert werden: {e}")
            return
        self.input.delete("1.0", tk.END)
        content, files_meta = self._compose_message(text, [d for d in docs if not d.image])
        files_meta += [{"name": d.name, "info": d.info, "image": rel} for d, rel in zip(pics, rels)]
        self.attachments.clear()
        self._render_attachments()
        self.view.user(text, self._display_files(files_meta))
        msg = {"role": "user", "content": content}
        if docs:  # Anzeige: eigener Text + Kärtchen statt des ganzen Dokumenttexts
            msg.update({"_text": text, "_files": files_meta})
        if rels:
            msg["_images"] = rels
        self.history.append(msg)
        self._save_chat()
        self.turn = {"step": 0, "think": None, "thinks": [], "tool": None, "tps": None}

        system = self.sys_text.get("1.0", tk.END).strip()
        system += datetime.now().strftime("\n\nHeutiges Datum: %d.%m.%Y, %H:%M Uhr.")
        if agent:
            system = (system + "\n\n" + agent.system_prompt()).strip()
        messages = ([{"role": "system", "content": system}] if system else []) + \
            [self._api_message(m) for m in self.history]

        self._set_busy(True)
        self.stop_event.clear()
        base_url = self.server.base_url

        def work():
            n_before = len(messages)
            try:
                run_conversation(base_url, messages, params, agent, self.stop_event,
                                 lambda k, t: self.events.put((k, t)))
            except Exception as e:
                self.events.put(("error", str(e)))
            # Neue Nachrichten (Antworten, Tool-Ergebnisse) in den Verlauf übernehmen
            self.events.put(("done", messages[n_before:]))

        threading.Thread(target=work, daemon=True).start()

    def _confirm_from_worker(self, title, text) -> str:
        """Wird im Hintergrund-Thread aufgerufen und wartet auf die Antwort: "yes", "no" oder "all"."""
        done, box = threading.Event(), {}
        self.events.put(("confirm", (title, text, done, box)))
        done.wait()
        return box.get("answer", "no")

    def ask_confirm(self, title: str, text: str) -> str:
        """Bestätigungsdialog im aktuellen Design mit Vorschau (z. B. Dateiinhalt oder Befehl)."""
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.title(title)
        win.transient(self.root)
        self.theme.register_window(win)
        body = ttk.Frame(win, padding=16)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text=title, style="Title.TLabel").pack(anchor="w")
        ttk.Label(body, text="Das Modell möchte folgende Aktion ausführen:", style="Muted.TLabel").pack(
            anchor="w", pady=(2, 8))
        lines = text.count("\n") + 1
        frame, box = scrolled_text(body, wrap=tk.WORD, width=88, height=max(4, min(lines + 1, 18)),
                                   font=("Consolas", 9), padx=8, pady=6)
        frame.pack(fill=tk.BOTH, expand=True)
        box.insert("1.0", text)
        box.configure(state=tk.DISABLED)
        self.theme.register_text(box)
        result = {"answer": "no"}

        def answer(value):
            result["answer"] = value
            win.destroy()

        bf = ttk.Frame(body)
        bf.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(bf, text="Ablehnen", command=lambda: answer("no")).pack(side=tk.RIGHT)
        ttk.Button(bf, text="Alle erlauben", command=lambda: answer("all")).pack(side=tk.RIGHT, padx=(0, 6))
        ok = ttk.Button(bf, text="Erlauben", style="Accent.TButton", command=lambda: answer("yes"))
        ok.pack(side=tk.RIGHT, padx=(0, 6))
        ttk.Label(bf, text="„Alle erlauben“ gilt bis zum Ende dieser Antwort.", style="Muted.TLabel").pack(
            side=tk.LEFT)
        win.bind("<Escape>", lambda e: answer("no"))
        win.protocol("WM_DELETE_WINDOW", lambda: answer("no"))
        self.root.update_idletasks()
        win.update_idletasks()
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - win.winfo_reqwidth()) // 2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - win.winfo_reqheight()) // 3)
        self.theme.present(win, f"+{x}+{y}")
        ok.focus_set()
        win.grab_set()
        self.root.wait_window(win)
        return result["answer"]

    # ================================================================ Event-Schleife

    STREAM_EVENTS = {"assistant_start", "reasoning", "content", "tool_call", "tool_result", "stats", "info",
                     "error", "confirm", "done"}

    def _poll_events(self):
        """Holt Ereignisse aus den Threads. Chat-Ereignisse kommen in eine Warteschlange, aus der der Text
        gleichmäßig ausgegeben wird; alles andere (Log, Laden, Downloads) wird sofort verarbeitet."""
        self._poll_id = None
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind in self.STREAM_EVENTS:
                    self.stream_q.append((kind, data))
                else:
                    self._handle(kind, data)
        except queue.Empty:
            pass
        self._drain_stream()
        if self._poll_id is None:  # während eines Dialogs (verschachtelte Ereignisschleife) nicht doppelt planen
            self._poll_id = self.root.after(15 if (self.busy or self.stream_q) else 50, self._poll_events)

    def _drain_stream(self):
        """Gibt pro Durchlauf nur einen Teil des Textes aus – je größer der Rückstand, desto mehr.
        So erscheint die Antwort gleichmäßig statt in Schüben."""
        if self._draining or not self.stream_q:
            return
        self._draining = True
        try:
            q = self.stream_q
            backlog = sum(len(d) for k, d in q if k == "content")
            budget = max(3, -(-backlog // 6))
            text, reasoning = [], []

            def flush():
                if reasoning:
                    self._on_reasoning("".join(reasoning))
                    reasoning.clear()
                if text:
                    self._on_content("".join(text))
                    text.clear()

            while q:
                kind, data = q[0]
                if kind == "content":
                    if budget <= 0:
                        break
                    if reasoning:
                        flush()
                    piece = data[:budget]
                    if len(piece) < len(data):
                        q[0] = (kind, data[len(piece):])
                    else:
                        q.popleft()
                    budget -= len(piece)
                    text.append(piece)
                elif kind == "reasoning":
                    if text:
                        flush()
                    q.popleft()
                    reasoning.append(data)
                else:
                    flush()
                    q.popleft()
                    self._handle_stream(kind, data)
            flush()
        finally:
            self._draining = False

    def _on_reasoning(self, data: str):
        if self.turn.get("think") is None:
            if not data.strip():  # nur Leerzeichen/Zeilenumbrüche: (noch) keinen Block anlegen
                return
            self.turn["think"] = self.view.think_begin()
            self.turn["think_step"] = self.turn.get("step", 0)
        self.view.think_append(self.turn["think"], data)

    def _on_content(self, data: str):
        self._close_think()
        self.view.md_append(data)

    def _close_think(self):
        n = self.turn.get("think")
        if n is not None:
            secs = self.view.think_end(n)
            self.turn.setdefault("thinks", []).append((n, self.turn.get("think_step", 0), secs))
            self.turn["think"] = None

    def _handle_stream(self, kind, data):
        view = self.view
        if kind == "assistant_start":
            self._close_think()
            view.md_finish()
            self.turn["step"] = self.turn.get("step", 0) + 1
            view.show_pending()
        elif kind == "tool_call":
            self._close_think()
            name, args = data
            icon, label, detail = describe_call(name, args)
            self.turn["tool"] = view.tool_begin(label, detail, format_args(args), icon)
        elif kind == "tool_result":
            if self.turn.get("tool") is not None:
                view.tool_end(self.turn["tool"], data, result_ok(data))
                self.turn["tool"] = None
        elif kind == "stats":
            self.turn["tps"] = data
        elif kind == "info":
            self._close_think()
            view.note(data)
        elif kind == "error":
            self._close_think()
            view.note(f"Fehler: {data}", "error")
        elif kind == "confirm":
            title, text, done, box = data
            box["answer"] = self.ask_confirm(title, text)
            done.set()
        elif kind == "done":
            self._finish_turn(data)

    def _finish_turn(self, new_messages: list):
        self._close_think()
        self.view.hide_pending()
        self.view.md_finish()
        self.history.extend(new_messages)
        assistants = [m for m in new_messages if m.get("role") == "assistant"]
        jobs = []
        for n, step, secs in self.turn.get("thinks", []):
            if 0 < step <= len(assistants):
                msg = assistants[step - 1]
                msg["_thinking_secs"] = secs
                if len(msg.get("_thinking", "")) >= 40:  # für ein paar Wörter lohnt keine Überschrift
                    jobs.append((n, msg))
        if self.stop_event.is_set():
            self.view.note("[gestoppt]")
        last = assistants[-1] if assistants else None
        if last and last.get("content") and not last.get("tool_calls"):
            self.view.footer(last["content"], self.turn.get("tps"))
        self._set_busy(False)
        self._save_chat()
        if jobs and self.think_title_var.get():
            self._make_think_titles(jobs)

    def _make_think_titles(self, jobs: list):
        """Lässt das Modell im Hintergrund eine kurze Überschrift für jeden Denkprozess formulieren."""
        if not self.server.is_running():
            return
        base_url, gen, chat = self.server.base_url, self.view.gen, self.chat_data
        items = [(n, msg, msg["_thinking"]) for n, msg in jobs]

        def work():
            for n, msg, text in items:
                try:
                    title = thinking_title(base_url, text)
                except Exception:
                    title = ""
                if title:
                    self.events.put(("think_title", (gen, n, msg, chat, title)))

        threading.Thread(target=work, daemon=True).start()

    def _handle(self, kind, data):
        if kind == "log":
            self.log.configure(state=tk.NORMAL)
            self.log.insert(tk.END, data)
            if int(self.log.index("end-1c").split(".")[0]) > 5000:
                self.log.delete("1.0", "1000.0")
            self.log.see(tk.END)
            self.log.configure(state=tk.DISABLED)
        elif kind == "loaded":
            self.status_var.set(f"✔ Geladen: {data}" + ("  ·  versteht Bilder" if self.server.vision else ""))
            self._set_load_buttons(tk.NORMAL)
            if self.view.is_placeholder:
                self._show_placeholder()
            self.room.model_changed()
        elif kind == "room":            # vom Gastgeber (RoomClient-Thread)
            self.room.on_client_event(*data)
        elif kind == "room_ai":         # Antwort der eigenen KI im Raum
            self.room.on_ai_event(data)
        elif kind == "room_ready":      # Raum erstellt / beigetreten (oder Fehler)
            self.room.on_ready(data)
        elif kind == "load_failed":
            self.status_var.set("Laden fehlgeschlagen – Details unter ⚙ Einstellungen → Server-Log")
            self._set_load_buttons(tk.NORMAL)
            dialogs.showerror("Modell-Studio", data)
        elif kind == "attach_done":
            att_id, doc, error = data
            for att in self.attachments:
                if att["id"] == att_id:
                    att.update(status="ok" if doc else "error", doc=doc, error=error)
                    self._render_attachments()
                    break
        elif kind == "think_title":
            gen, n, msg, chat, title = data
            msg["_thinking_title"] = title
            if gen == self.view.gen:
                self.view.set_think_title(n, title)
            try:
                if chat is self.chat_data:
                    if not self.busy:  # sonst wird ohnehin nach der laufenden Antwort gespeichert
                        chats.save(chat, touch=False)
                elif chat and chats.exists(chat["id"]):
                    chats.save(chat, touch=False)
            except OSError:
                pass
        elif kind == "dl_progress":
            self._dl_update(*data)
        elif kind == "dl_done":
            self._dl_finished(data)
        elif kind == "paste_image":     # Screenshot aus der Zwischenablage (als PNG gespeichert)
            if data:
                self.add_attachments([data])
            else:
                dialogs.showerror("Modell-Studio", "Das Bild aus der Zwischenablage konnte nicht übernommen werden.")
        elif kind == "srv_plan":
            self._srv_planned(*data)
        elif kind == "srv_progress":
            name, done, total = data
            pct = f"{done * 100 / total:.0f} % " if total else ""
            self.srv_status.set(f"Lade {name} … {pct}({done / 1024 ** 2:.0f} / {total / 1024 ** 2:.0f} MB)")
        elif kind == "srv_done":
            self._srv_finished(*data)

    # ================================================================ Download

    def open_download(self):
        parent = self.settings_win if self.settings_win.winfo_viewable() else self.root
        win = tk.Toplevel(parent)
        win.withdraw()
        win.title("Modell herunterladen")
        win.geometry(f"{round(640 * self.ui_scale)}x{round(200 * self.ui_scale)}")
        win.transient(parent)
        self.theme.register_window(win)
        ttk.Label(win, text="Direktlink zu einer .gguf-Datei (z.B. Hugging Face → Files → Datei → Link kopieren):"
                  ).pack(anchor="w", padx=10, pady=(10, 2))
        url_var = tk.StringVar()
        ttk.Entry(win, textvariable=url_var).pack(fill=tk.X, padx=10)
        ttk.Label(win, text=f"Ziel: {MODEL_DIR}", style="Muted.TLabel").pack(anchor="w", padx=10, pady=4)
        self.dl_bar = ttk.Progressbar(win, maximum=100)
        self.dl_bar.pack(fill=tk.X, padx=10)
        self.dl_label = ttk.Label(win, text="")
        self.dl_label.pack(anchor="w", padx=10)
        cancel = threading.Event()
        bf = ttk.Frame(win)
        bf.pack(pady=6)
        start_btn = ttk.Button(bf, text="Herunterladen", style="Accent.TButton",
                               command=lambda: (start_btn.configure(state=tk.DISABLED),
                                                self._download(url_var.get().strip(), cancel)))
        start_btn.pack(side=tk.LEFT, padx=4)
        ttk.Button(bf, text="Abbrechen", command=lambda: (cancel.set(), win.destroy())).pack(side=tk.LEFT, padx=4)
        win.protocol("WM_DELETE_WINDOW", lambda: (cancel.set(), win.destroy()))
        self.dl_win = win
        self.theme.present(win)

    def _download(self, url, cancel):
        url = url.replace("/blob/", "/resolve/")
        name = url.split("?")[0].rstrip("/").split("/")[-1]
        if not name.lower().endswith(".gguf"):
            dialogs.showerror("Download", "Der Link muss auf eine .gguf-Datei zeigen.", parent=self.dl_win)
            return
        MODEL_DIR.mkdir(exist_ok=True)
        target = MODEL_DIR / name
        part = target.with_name(name + ".part")

        def work():
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Modell-Studio"})
                with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
                    total = int(r.headers.get("Content-Length") or 0)
                    done = 0
                    while chunk := r.read(1024 * 1024):
                        if cancel.is_set():
                            raise RuntimeError("abgebrochen")
                        f.write(chunk)
                        done += len(chunk)
                        self.events.put(("dl_progress", (done, total)))
                part.replace(target)
                self.events.put(("dl_done", None))
            except Exception as e:
                part.unlink(missing_ok=True)
                self.events.put(("dl_done", str(e)))

        threading.Thread(target=work, daemon=True).start()

    def _dl_update(self, done, total):
        if not (self.dl_win and self.dl_win.winfo_exists()):
            return
        if total:
            self.dl_bar["value"] = done * 100 / total
        self.dl_label.configure(text=f"{done / 1024 ** 3:.2f} / {total / 1024 ** 3:.2f} GB")

    def _dl_finished(self, err):
        if err:
            if self.dl_win and self.dl_win.winfo_exists():
                dialogs.showerror("Download", f"Fehlgeschlagen: {err}", parent=self.dl_win)
            return
        self.refresh_models()
        if self.dl_win and self.dl_win.winfo_exists():
            self.dl_win.destroy()
        dialogs.showinfo("Download", "Modell heruntergeladen und zur Liste hinzugefügt.")

    # ================================================================ Konfiguration

    def save_config(self):
        server = self._selected_server()
        try:
            ctx = int(self.ctx_var.get())
        except ValueError:
            ctx = DEFAULTS["ctx"]
        self.cfg.update({
            "server_exe": server["exe"] if server else "",
            "ctx": ctx,
            "ngl": self.ngl_var.get().strip() or "auto",
            "extra_args": self.extra_var.get(),
            "vision": self.vision_var.get(),
            "image_tokens": self.imgtok_var.get().strip(),
            "temperature": self.temp_var.get(),
            "max_tokens": self.maxtok_var.get(),
            "system_prompt": self.sys_text.get("1.0", tk.END).strip(),
            "think_titles": self.think_title_var.get(),
            "agent_enabled": self.agent_var.get(),
            "agent_commands": self.cmd_var.get(),
            "agent_workspace": self.ws_var.get(),
            "agent_folders": list(self.extra_folders),
            "agent_confirm": self.confirm_var.get(),
            "web_enabled": self.web_var.get(),
            "brave_key": self.brave_var.get().strip(),
            "theme": self.theme.key,
        })
        self.cfg.pop("show_thinking", None)  # alte Einstellung (Denkprozess ist jetzt immer aufklappbar)
        try:
            CONFIG_FILE.write_text(json.dumps(self.cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def on_close(self):
        self.save_config()
        self.stop_event.set()
        self.srv_cancel.set()
        self.room.ai_stop.set()
        self.room.shutdown()  # Raum verlassen bzw. eigenen Raum schließen (Verlauf bleibt gespeichert)
        self.server.stop()
        self.root.destroy()


if __name__ == "__main__":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()
