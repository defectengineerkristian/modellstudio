"""Chatverlauf im Stil moderner Chat-Apps – alles in einem tk.Text, damit Antworten markier- und kopierbar bleiben.

- Nutzer rechts in einer Sprechblase (Farbe aus dem Design), Modell links ohne Blase, Spalte mittig.
- Antworten werden als Markdown dargestellt (Überschriften, Listen, Tabellen, Code mit Hervorhebung, Links),
  auch schon während sie gestreamt werden.
- Denkprozess und Werkzeugaufrufe stehen in zugeklappten Blöcken mit Kopfzeile; ein Klick klappt sie auf.
- Während das Modell arbeitet, pulsiert die Kopfzeile; das Mausrad scrollt weich; der Verlauf folgt der
  Antwort nur, solange man ganz unten ist.

Tags mit Nummer N (Infos in self.blocks[N]): hdr:N Kopfzeile, body:N aufklappbarer Inhalt, cpy:N „Kopieren“,
ccp:N „Code kopieren“, code:N Code-Zeilen, lnk:N Link, tbl:N Tabelle (Tabstopps). Alles Schreiben läuft über
`with self._edit():`, weil das Textfeld sonst schreibgeschützt ist.
"""

import math
import os
import re
import time
import tkinter as tk
import webbrowser
from contextlib import contextmanager
from tkinter import font as tkfont

from markdown_lite import ListLevels, classify, closes_fence, inline, is_separator_row, plain, split_row
from themes import mix_hex

FAMILY, MONO = "Segoe UI", "Consolas"
MAX_COLUMN = 820          # maximale Breite der Chatspalte (Pixel bei 100 %)
PULSE_MS = 40
BULLETS = ["•", "◦", "▪", "‣"]
CLOSED, OPENED = "▸", "▾"
# Farbe des Typ-Abzeichens auf Datei-Kärtchen (sonst Akzentfarbe des Designs)
BADGE_COLORS = {"PDF": "#d0473a", "DOC": "#2b5fb4", "DOCX": "#2b5fb4", "ODT": "#2b5fb4", "RTF": "#2b5fb4",
                "XLSX": "#21804a", "XLS": "#21804a", "CSV": "#21804a", "ODS": "#21804a",
                "PPTX": "#d0542b", "PPT": "#d0542b", "ODP": "#d0542b"}

# ---------------------------------------------------------------- Code-Hervorhebung

KEYWORDS = frozenset("""
abstract and as assert async await auto bool boolean break byte case catch char class const constexpr continue
def default defer del delete do double elif else enum except export extends extern false final finally float fn
for foreach from func function go goto if impl implements import in include inline int interface internal is
lambda let long loop match mod module mut namespace new nil none not null of or override package pass private
protected pub public raise readonly ref return self short signed sizeof static string struct super switch
template this throw throws trait true try type typedef typename typeof uint union unsigned use using val var
virtual void volatile where while with yield None True False print println printf len range echo function
""".split())
HASH_LANGS = {"python", "py", "sh", "bash", "shell", "zsh", "powershell", "ps1", "ps", "pwsh", "ruby", "rb",
              "yaml", "yml", "toml", "r", "perl", "makefile", "dockerfile", "ini", "conf", "julia", "elixir", "nim"}
PLAIN_LANGS = {"text", "txt", "plaintext", "output", "console", "log", "csv", "markdown", "md"}
_STR = r'"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'|`[^`\n]*`'
_REST = r"(?P<num>\b\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?\b)|(?P<id>[A-Za-z_]\w*)"
_HL = {
    "hash": re.compile(rf"(?P<com>#.*$)|(?P<str>{_STR})|{_REST}"),
    "slash": re.compile(rf"(?P<com>//.*$|/\*.*?(?:\*/|$))|(?P<str>{_STR})|{_REST}"),
    "both": re.compile(rf"(?P<com>#.*$|//.*$)|(?P<str>{_STR})|{_REST}"),
    "sql": re.compile(rf"(?P<com>--.*$)|(?P<str>{_STR})|{_REST}"),
    "html": re.compile(rf"(?P<com><!--.*?(?:-->|$))|(?P<str>{_STR})|{_REST}"),
}


def highlight(line: str, lang: str) -> list[tuple[int, int, str]]:
    """[(start, ende, art)] mit art in kw / str / com / num – bewusst einfach, Zeile für Zeile."""
    if lang in PLAIN_LANGS:
        return []
    style = ("hash" if lang in HASH_LANGS else "sql" if lang in ("sql", "lua") else
             "html" if lang in ("html", "xml", "svg", "vue") else "both" if not lang else "slash")
    spans = []
    for m in _HL[style].finditer(line):
        kind = m.lastgroup
        if kind != "id":
            spans.append((m.start(), m.end(), kind))
        elif m.group() in KEYWORDS:
            spans.append((m.start(), m.end(), "kw"))
    return spans


class ChatView:
    def __init__(self, text: tk.Text, scale: float = 1.0, register_menu=None):
        self.t = text
        self.scale = scale
        self.col: dict = {}
        self.bg = "#ffffff"
        self.blocks: dict[int, dict] = {}
        self.bubbles: list[tuple[tk.Canvas, str, object]] = []  # (Canvas, "bubble"/"files", Text bzw. Dateien)
        self._thumbs: dict = {}      # Vorschaubilder angehängter Bilder: Pfad bzw. (Pfad, Verkleinerung) → PhotoImage
        self.gen = 0                 # erhöht sich bei clear(); spätere Rückmeldungen erkennen so alte Ansichten
        self.md: dict | None = None  # Zustand der gerade gestreamten Markdown-Antwort
        self.is_placeholder = False
        self._n = 0
        self._depth = 0
        self._follow = False
        self._stick = True           # unten „kleben“ – wird nur durch eigenes Scrollen des Nutzers aufgehoben
        self._padx = None
        self._bubble_w = None
        self._layout_job = None
        self._anim_job = None
        self._live: set[int] = set()
        self._pending = False
        # Einfügeposition: normalerweise das Ende; in einem Slot (eigener Platz einer Nachricht, z. B. im
        # Gruppenchat, wo mehrere KIs gleichzeitig schreiben) dessen Marke. Siehe slot_new()/slot().
        self._at = "end-1c"
        self._pend_tag = "pending"
        self._pendings: set[str] = set()
        self._slots: dict[str, dict] = {}
        self._corners: dict = {}
        self._indent_tags: set[str] = set()
        self._scroll_left = 0.0
        self._scroll_job = None
        self._bubble_text = ""
        self._file_path = ""
        self._make_fonts()
        self._make_tags()

        t = self.t
        t.configure(state=tk.DISABLED, cursor="xterm")
        t.bind("<Configure>", self._on_configure, add="+")
        t.bind("<MouseWheel>", self._on_wheel)
        t.bind("<Button-3>", self._on_context)
        t.tag_bind("clickable", "<Enter>", self._on_enter_click)
        t.tag_bind("clickable", "<Leave>", self._on_leave_click)
        t.tag_bind("clickable", "<Button-1>", self._on_click)

        self.menu = tk.Menu(t, tearoff=0)
        self.menu.add_command(label="Kopieren", command=self._copy_selection)
        self.menu.add_command(label="Alles markieren", command=lambda: t.tag_add("sel", "1.0", "end-1c"))
        self.bubble_menu = tk.Menu(t, tearoff=0)
        self.bubble_menu.add_command(label="Text kopieren", command=lambda: self._copy(self._bubble_text))
        self.file_menu = tk.Menu(t, tearoff=0)
        self.file_menu.add_command(label="Öffnen", command=lambda: self._open_file(self._file_path))
        self.file_menu.add_command(label="Pfad kopieren", command=lambda: self._copy(self._file_path))
        if register_menu:
            register_menu(self.menu)
            register_menu(self.bubble_menu)
            register_menu(self.file_menu)

    # ================================================================ Grundlagen

    def _s(self, v: float) -> int:
        return round(v * self.scale)

    def _make_fonts(self):
        def f(size, weight="normal", slant="roman", family=FAMILY):
            return tkfont.Font(root=self.t, family=family, size=size, weight=weight, slant=slant)

        self.f_base, self.f_bold = f(11), f(11, "bold")
        self.f_italic, self.f_bi = f(11, slant="italic"), f(11, "bold", "italic")
        self.f_small, self.f_small_i, self.f_tiny = f(10), f(10, slant="italic"), f(9)
        self.f_small_b, self.f_label = f(10, "bold"), f(8, "bold")
        self.f_h1, self.f_h2, self.f_h3 = f(17, "bold"), f(14, "bold"), f(12, "bold")
        self.f_mono, self.f_mono_s = f(10, family=MONO), f(9, family=MONO)
        self.f_gap, self.f_hair = f(4), f(1)

    def _make_tags(self):
        """Reihenfolge = Priorität: später angelegte Tags gewinnen (z. B. „b“ über „p“)."""
        s, T = self._s, self.t.tag_configure
        T("p", font=self.f_base, spacing1=s(2), spacing2=s(3), spacing3=s(2))
        T("li", font=self.f_base, spacing1=s(2), spacing2=s(3), spacing3=s(2))
        T("gap", font=self.f_gap)
        T("quote", font=self.f_italic, lmargin1=s(18), lmargin2=s(18))
        T("md_h1", font=self.f_h1, spacing1=s(12), spacing3=s(4))
        T("md_h2", font=self.f_h2, spacing1=s(10), spacing3=s(3))
        T("md_h3", font=self.f_h3, spacing1=s(8), spacing3=s(2))
        T("b", font=self.f_bold)
        T("i", font=self.f_italic)
        T("bi", font=self.f_bi)
        T("s", overstrike=True)
        T("code", font=self.f_mono)
        T("link", underline=True)
        T("bullet")
        T("pre_hdr", font=self.f_tiny, lmargin1=s(12), lmargin2=s(12), spacing1=s(4), spacing3=s(4))
        T("pre", font=self.f_mono, lmargin1=s(12), lmargin2=s(12), rmargin=s(12), spacing2=s(1), wrap="char")
        T("pre_pad", font=self.f_gap)
        for k in ("c_kw", "c_str", "c_com", "c_num"):
            T(k)
        T("hr", font=self.f_hair)
        T("th", font=self.f_bold, spacing1=s(4), spacing3=s(4))
        T("td", font=self.f_base, spacing1=s(4), spacing3=s(4))
        T("trule", font=self.f_hair)
        T("trule_soft", font=self.f_hair)
        T("think_hdr", font=self.f_small, spacing1=s(6), spacing3=s(6))
        T("think_pad", font=self.f_gap)
        T("think_body", font=self.f_small, lmargin1=s(14), lmargin2=s(14), rmargin=s(14), spacing2=s(2))
        T("tool_hdr", font=self.f_small, spacing1=s(3), spacing3=s(3))
        T("tool_body", font=self.f_mono_s, lmargin1=s(14), lmargin2=s(14), rmargin=s(14), wrap="char")
        T("tool_label", font=self.f_label)
        T("st_ok")
        T("st_err")
        T("note", font=self.f_small_i, spacing1=s(4), spacing3=s(4))
        T("error", font=self.f_small, spacing1=s(4), spacing3=s(4))
        T("footer", font=self.f_tiny, spacing1=s(2), spacing3=s(10))
        T("user", justify="right", spacing1=s(20), spacing3=s(12))
        T("user_files", justify="right", spacing1=s(20), spacing3=s(6))   # Datei-Kärtchen über der Blase
        T("user_after", justify="right", spacing3=s(12))                  # Blase direkt unter den Kärtchen
        T("who", font=self.f_small_b, spacing1=s(16), spacing3=s(4))     # Absenderzeile im Gruppenchat
        T("who_sub", font=self.f_tiny)
        T("other", spacing3=s(8))                                         # Blase/Kärtchen anderer, links
        T("slot_end", elide=True)
        T("placeholder", font=(FAMILY, 15), justify="center", spacing1=s(150), spacing3=s(6))
        T("placeholder_sub", font=(FAMILY, 10), justify="center")
        T("clickable")
        T("hover")
        T("live")

    def apply_theme(self, col: dict):
        """col = THEMES[…]["chat"]. Der Hintergrund kommt vom Textfeld selbst (auch bei „Windows-Standard“)."""
        self.col = col
        r, g, b = self.t.winfo_rgb(self.t.cget("background"))
        self.bg = "#%02x%02x%02x" % (r // 257, g // 257, b // 257)
        T = self.t.tag_configure

        def band(tag, color, **kw):
            T(tag, background=color, lmargincolor=color, rmargincolor=color, **kw)

        T("bullet", foreground=col["muted"])
        T("quote", foreground=col["muted"])
        T("code", background=col["code_bg"], foreground=col["code_fg"])
        T("link", foreground=col["link"])
        band("pre_hdr", col["code_hdr"], foreground=col["muted"])
        band("pre", col["code_bg"], foreground=col["code_fg"])
        band("pre_pad", col["code_bg"])
        T("c_kw", foreground=col["kw"])
        T("c_str", foreground=col["str"])
        T("c_com", foreground=col["com"])
        T("c_num", foreground=col["num"])
        T("hr", background=col["rule"])
        T("trule", background=col["rule"])
        T("trule_soft", background=col["think_bg"])
        T("think_hdr", foreground=col["muted"])
        T("tool_hdr", foreground=col["muted"])
        band("think_pad", col["think_bg"])
        band("think_body", col["think_bg"], foreground=col["think_fg"])
        band("tool_body", col["code_bg"], foreground=col["think_fg"])
        T("tool_label", foreground=col["muted"])
        T("who_sub", foreground=col["muted"])
        T("st_ok", foreground=col["ok"])
        T("st_err", foreground=col["error"])
        T("note", foreground=col["muted"])
        T("error", foreground=col["error"])
        T("footer", foreground=col["muted"])
        T("placeholder", foreground=col["fg"])
        T("placeholder_sub", foreground=col["muted"])
        T("hover", foreground=col["fg"])
        T("live", foreground=col["muted"])
        self._corners.clear()
        self._redraw_bubbles()

    @contextmanager
    def _edit(self, follow: bool | None = None):
        """Schreibzugriff. follow=None: ans Ende scrollen, solange der Nutzer nicht selbst hochgescrollt hat."""
        if self._depth == 0:
            self._follow = self._stick if follow is None else follow
            self.t.configure(state=tk.NORMAL)
        elif follow:
            self._follow = True
        if follow:
            self._stick = True
        self._depth += 1
        try:
            yield
        finally:
            self._depth -= 1
            if self._depth == 0:
                self.t.configure(state=tk.DISABLED)
                if self._follow:
                    self.t.see("end")

    def _at_bottom(self) -> bool:
        return self.t.dlineinfo("end-1c") is not None

    def attach_scrollbar(self, sb):
        """Scrollbalken melden, damit Ziehen am Balken das „Unten-kleben“ auf- bzw. wieder einschaltet."""
        def command(*args):
            self.t.yview(*args)
            self._stick = self._at_bottom()
        sb.configure(command=command)

    def _new(self, kind: str, **info) -> int:
        self._n += 1
        self.blocks[self._n] = {"kind": kind, **info}
        return self._n

    def _ensure(self):
        if self.is_placeholder:
            self.clear()

    def scroll_end(self):
        self._stick = True
        self.t.see("end")

    # ================================================================ Leeren / Platzhalter

    def clear(self):
        self.gen += 1
        self._live.clear()
        self._pending = False
        self._pendings.clear()
        self._slots.clear()
        self._at, self._pend_tag = "end-1c", "pending"
        self.md = None
        self._stop_scroll()
        self._stick = True
        with self._edit(follow=False):
            self.t.delete("1.0", "end")
        for c, _, _ in self.bubbles:
            try:
                c.destroy()
            except tk.TclError:
                pass
        self.bubbles.clear()
        for tag in self.t.tag_names():
            if ":" in tag:
                self.t.tag_delete(tag)
        for mark in self.t.mark_names():
            if ":" in mark:
                self.t.mark_unset(mark)
        self.blocks.clear()
        self.is_placeholder = False

    def placeholder(self, title: str, sub: str):
        self.clear()
        with self._edit(follow=False):
            self.t.insert("end", title + "\n", "placeholder")
            self.t.insert("end", sub, "placeholder_sub")
        self.is_placeholder = True

    # ================================================================ Nutzer-Sprechblase

    def user(self, text: str, files: list[dict] | None = None):
        """Nutzernachricht rechts: optional Datei-Kärtchen (name, info, path) und darunter die Sprechblase."""
        self._ensure()
        self.md_finish()
        self.hide_pending()
        with self._edit(follow=True):
            if files:
                c = self._canvas("arrow")
                c.bind("<Double-Button-1>", lambda e, c=c: self._open_file(self._card_path(c)))
                c.bind("<Button-3>", lambda e, c=c: self._on_file_menu(e, self._card_path(c)))
                self.bubbles.append((c, "files", files))
                self._draw_files(c, files)
                self._embed(c, "user_files" if text else "user")
            if text or not files:
                c = self._canvas("arrow")
                c.bind("<Button-3>", lambda e, s=text: self._on_bubble_menu(e, s))
                self.bubbles.append((c, "bubble", text))
                self._draw_bubble(c, text)
                self._embed(c, "user_after" if files else "user")

    # ---------------------------------------------------------------- Gruppenchat: andere Teilnehmer (links)

    def sender(self, name: str, sub: str = "", color: str | None = None):
        """Absenderzeile über einer Nachricht anderer (Mensch oder KI), z. B. „✦ Max-KI  ·  Qwen3.8-27B“."""
        self._ensure()
        self.md_finish()
        tags = ("who",)
        if color:
            tag = "col_" + color.lstrip("#")
            self.t.tag_configure(tag, foreground=color)
            tags += (tag,)
        with self._edit():
            self.t.insert(self._at, name, tags)
            if sub:
                self.t.insert(self._at, "   " + sub, ("who", "who_sub"))
            self.t.insert(self._at, "\n", "who")

    def other(self, text: str, files: list[dict] | None = None):
        """Nachricht eines anderen Menschen: links, neutrale Blase (Kärtchen darüber)."""
        self._ensure()
        self.md_finish()
        with self._edit():
            if files:
                c = self._canvas("arrow")
                c.bind("<Double-Button-1>", lambda e, c=c: self._open_file(self._card_path(c)))
                self.bubbles.append((c, "files", files))
                self._draw_files(c, files)
                self._embed(c, "other")
            if text:
                c = self._canvas("arrow")
                c.bind("<Button-3>", lambda e, s=text: self._on_bubble_menu(e, s))
                self.bubbles.append((c, "obubble", text))
                self._draw_bubble(c, text, other=True)
                self._embed(c, "other")

    def tool_line(self, icon: str, label: str, detail: str, ok: bool | None):
        """Werkzeugaufruf einer fremden KI (nur Kopfzeile – Eingaben/Ergebnisse bleiben auf deren Rechner)."""
        self._ensure()
        self.md_finish()
        text = f"{icon}  {label}" + (f"  ·  {detail}" if detail else "")
        with self._edit():
            self.t.insert(self._at, text, "tool_hdr")
            if ok is not None:
                self.t.insert(self._at, "  ✓" if ok else "  ✗", ("tool_hdr", "st_ok" if ok else "st_err"))
            self.t.insert(self._at, "\n", "tool_hdr")

    def _canvas(self, cursor: str) -> tk.Canvas:
        c = tk.Canvas(self.t, highlightthickness=0, borderwidth=0, background=self.bg, cursor=cursor)
        c.bind("<MouseWheel>", self._on_wheel)
        return c

    def _embed(self, widget, tag: str):
        idx = self.t.index(self._at)
        self.t.window_create(self._at, window=widget)
        self.t.insert(self._at, "\n", tag)
        self.t.tag_add(tag, idx)

    def _area_width(self) -> int:
        w = self.t.winfo_width()
        if w <= 1:
            w = self._s(MAX_COLUMN) + 2 * self._s(24)
        pad = self._padx if self._padx is not None else int(self.t.cget("padx"))
        return max(self._s(200), w - 2 * pad - 4)

    def _bubble_max(self) -> int:
        return min(round(self._area_width() * 0.78), self._s(620))

    def _corner_images(self, r: int, fill: str, under: str | None = None) -> list:
        """Kantengeglättete Viertelkreise (oben links, oben rechts, unten links, unten rechts) in Farbe fill
        auf dem Untergrund under (Standard: Chat-Hintergrund)."""
        under = under or self.bg
        key = (r, fill, under)
        if key not in self._corners:
            imgs = []
            for cx, cy in ((r, r), (0, r), (r, 0), (0, 0)):   # Kreismittelpunkt je Ecke
                img = tk.PhotoImage(master=self.t, width=r, height=r)
                rows = []
                for y in range(r):
                    row = [mix_hex(under, fill, r - math.hypot(x + 0.5 - cx, y + 0.5 - cy) + 0.5) for x in range(r)]
                    rows.append("{" + " ".join(row) + "}")
                img.put(" ".join(rows))
                imgs.append(img)
            self._corners[key] = imgs
        return self._corners[key]

    def _rounded(self, c: tk.Canvas, x: int, y: int, w: int, h: int, r: int, fill: str, tags=(), under=None):
        """Rechteck mit kantengeglätteten runden Ecken (Ecken als vorberechnete Bilder)."""
        c.create_rectangle(x + r, y, x + w - r, y + h, fill=fill, outline="", tags=tags)
        c.create_rectangle(x, y + r, x + w, y + h - r, fill=fill, outline="", tags=tags)
        nw, ne, sw, se = self._corner_images(r, fill, under)
        c.create_image(x, y, image=nw, anchor="nw", tags=tags)
        c.create_image(x + w - r, y, image=ne, anchor="nw", tags=tags)
        c.create_image(x, y + h - r, image=sw, anchor="nw", tags=tags)
        c.create_image(x + w - r, y + h - r, image=se, anchor="nw", tags=tags)

    def _draw_bubble(self, c: tk.Canvas, text: str, other: bool = False):
        """Sprechblase; other=True: neutrale Farbe für Nachrichten anderer Menschen im Gruppenchat."""
        if not self.col:
            return
        c.delete("all")
        px, py, r = self._s(14), self._s(9), self._s(13)
        fg = self.col["fg"] if other else self.col["bubble_fg"]
        tid = c.create_text(px, py, text=text.replace("\t", "    "), anchor="nw", font=self.f_base,
                            width=self._bubble_max() - 2 * px, fill=fg)
        x1, y1, x2, y2 = c.bbox(tid)
        w, h = max(x2 - x1 + 2 * px, 2 * r + 4), max(y2 - y1 + 2 * py, 2 * r + 2)
        c.configure(width=w, height=h, background=self.bg)
        self._rounded(c, 0, 0, w, h, r, self.col["code_hdr"] if other else self.col["bubble"])
        c.tag_raise(tid)

    def _thumb(self, path: str | None, max_w: int) -> tk.PhotoImage | None:
        """Vorschaubild (PNG) eines angehängten Bildes, zwischengespeichert; zu breite werden verkleinert."""
        if not path or not os.path.exists(path):
            return None
        if path not in self._thumbs:
            try:
                self._thumbs[path] = tk.PhotoImage(master=self.t, file=path)
            except tk.TclError:
                self._thumbs[path] = None
        img = self._thumbs[path]
        if img is None or img.width() <= max_w:
            return img
        factor = min(8, -(-img.width() // max(1, max_w)))  # aufgerundet
        if (path, factor) not in self._thumbs:
            self._thumbs[(path, factor)] = img.subsample(factor)
        return self._thumbs[(path, factor)]

    def _draw_files(self, c: tk.Canvas, files: list[dict]):
        """Datei-Kärtchen: farbiges Typ-Abzeichen, Dateiname und Infozeile – bei Bildern stattdessen das
        Vorschaubild über Name und Infozeile; mehrere Kärtchen umbrechen."""
        if not self.col:
            return
        c.delete("all")
        s = self._s
        pad, gap, r, badge = s(9), s(6), s(10), s(34)
        line1, line2 = self.f_small_b.metrics("linespace"), self.f_tiny.metrics("linespace")
        card_h = max(badge, line1 + line2) + 2 * pad
        maxw = self._bubble_max()
        boxes, x, y, row_h = [], 0, 0, 0
        for f in files:
            name = f.get("name", "")
            name = name if len(name) <= 36 else name[:24] + "…" + name[-10:]
            info = f.get("info", "")
            text_w = max(self.f_small_b.measure(name), self.f_tiny.measure(info))
            img = self._thumb(f.get("thumb"), maxw - 2 * pad)
            if img:
                w = min(max(img.width(), text_w) + 2 * pad, maxw)
                h = pad + img.height() + s(6) + line1 + line2 + pad
            else:
                w, h = min(pad + badge + s(10) + text_w + s(14), maxw), card_h
            if x and x + w > maxw:
                x, y, row_h = 0, y + row_h + gap, 0
            boxes.append((x, y, w, h, name, info, f, img))
            x += w + gap
            row_h = max(row_h, h)
        total_w = max(bx + bw for bx, _, bw, *_ in boxes)
        c.configure(width=total_w, height=y + row_h, background=self.bg)
        row_end = {}
        for bx, by, bw, *_ in boxes:
            row_end[by] = max(row_end.get(by, 0), bx + bw)
        for i, (bx, by, bw, h, name, info, f, img) in enumerate(boxes):
            bx += total_w - row_end[by]  # jede Zeile rechtsbündig, wie die Sprechblase
            tag = f"card{i}"
            self._rounded(c, bx, by, bw, h, r, self.col["code_hdr"], tags=(tag,))
            if img:
                c.create_image(bx + pad, by + pad, image=img, anchor="nw", tags=(tag,))
                tx, ty = bx + pad, by + pad + img.height() + s(6)
            else:
                ext = (f.get("name", "").rsplit(".", 1)[-1].upper() if "." in f.get("name", "") else "DATEI")[:4]
                color = BADGE_COLORS.get(ext, self.col["accent"])
                self._rounded(c, bx + pad, by + (h - badge) // 2, badge, badge, s(7), color, tags=(tag,),
                              under=self.col["code_hdr"])
                c.create_text(bx + pad + badge // 2, by + h // 2, text=ext, fill="#ffffff", font=self.f_label,
                              tags=(tag,))
                tx, ty = bx + pad + badge + s(10), by + (h - line1 - line2) // 2
            c.create_text(tx, ty, text=name, anchor="nw", fill=self.col["fg"], font=self.f_small_b, tags=(tag,))
            c.create_text(tx, ty + line1, text=info, anchor="nw", fill=self.col["muted"], font=self.f_tiny,
                          tags=(tag,))
        c.card_paths = [f.get("path", "") for f in files]

    @staticmethod
    def _card_path(c: tk.Canvas) -> str:
        for tag in c.gettags("current"):
            if tag.startswith("card") and tag[4:].isdigit():
                paths = getattr(c, "card_paths", [])
                i = int(tag[4:])
                return paths[i] if i < len(paths) else ""
        return ""

    def _redraw_bubbles(self):
        self._bubble_w = self._bubble_max()
        for c, kind, data in self.bubbles:
            if c.winfo_exists():
                if kind == "files":
                    self._draw_files(c, data)
                else:
                    self._draw_bubble(c, data, other=kind == "obubble")

    def _on_configure(self, _e=None):
        if self._layout_job:
            self.t.after_cancel(self._layout_job)
        self._layout_job = self.t.after(40, self._relayout)

    def _relayout(self):
        """Spalte mittig halten und Sprechblasen an die neue Breite anpassen."""
        self._layout_job = None
        w = self.t.winfo_width()
        if w <= 1:
            return
        side = max(self._s(24), (w - self._s(MAX_COLUMN)) // 2)
        if side != self._padx:
            self._padx = side
            self.t.configure(padx=side)
        if self._bubble_max() != self._bubble_w:
            self._redraw_bubbles()
        if self._stick:
            self.t.see("end")

    # ================================================================ Markdown (auch gestreamt)

    def markdown(self, text: str):
        """Fertigen Text auf einmal darstellen (z. B. beim Öffnen eines gespeicherten Chats)."""
        self.md_append(text)
        self.md_finish()

    def _after_block(self) -> bool:
        """Steht direkt davor ein Denkprozess-/Werkzeug-Block? Dann etwas Abstand zum Antworttext."""
        tags = set(self.t.tag_names(f"{self._at}-1c"))
        return bool(tags & {"think_hdr", "tool_hdr", "think_pad", "pre_pad", "note", "error"})

    def _md_begin(self):
        self._n += 1
        self.md = {"line": "", "code": None, "table": None, "levels": ListLevels(), "any": False, "blank": True,
                   "mark": f"mdl:{self._n}"}  # Marke am Anfang der angefangenen (noch offenen) Zeile
        self.t.mark_set(self.md["mark"], self._at)
        self.t.mark_gravity(self.md["mark"], "left")

    def md_append(self, text: str):
        """Hängt gestreamten Text an. Fertige Zeilen werden endgültig gesetzt, die angefangene letzte Zeile
        wird bei jedem Aufruf neu gezeichnet (so wird z. B. **fett** fett, sobald es geschlossen ist)."""
        if not text:
            return
        self._ensure()
        self.hide_pending()
        new = self.md is None
        if new:
            self._md_begin()
        md = self.md
        lines = (md["line"] + text).split("\n")
        md["line"] = lines.pop()
        with self._edit():
            if new and self._after_block():
                self.t.insert(self._at, "\n", "gap")
                self.t.mark_set(self.md["mark"], self._at)
            self.t.delete(self.md["mark"], self._at)
            for line in lines:
                self._md_line(line, partial=False)
                self.t.mark_set(self.md["mark"], self._at)
            if md["line"]:
                self._md_line(md["line"], partial=True)

    def md_finish(self):
        md = self.md
        if md is None:
            return
        with self._edit():
            self.t.delete(self.md["mark"], self._at)
            if md["line"]:
                self._md_line(md["line"], partial=False)
            if md["code"] is not None:
                self._code_end()
            while self.t.get(f"{self._at}-1c") == "\n" and "gap" in self.t.tag_names(f"{self._at}-1c"):
                self.t.delete(f"{self._at}-1c")
        self.md = None

    def _md_line(self, line: str, partial: bool):
        md = self.md
        ins = self.t.insert
        if md["code"] is not None:
            if closes_fence(line, md["code"]["fence"]):
                if not partial:
                    self._code_end()
                return
            self._code_line(line)
            return
        kind, info = classify(line)
        if kind != "table" and not partial:
            md["table"] = None
        if kind == "blank":
            if not partial and md["any"] and not md["blank"]:
                ins(self._at, "\n", "gap")
                md["blank"] = True
            return
        if partial and kind in ("fence", "table", "hr", "pagebreak"):
            return
        gap = not md["blank"]  # Abstand vor Blöcken nur, wenn nicht schon eine Leerzeile davor stand
        if not partial:
            md["any"], md["blank"] = True, False
            if kind not in ("bullet", "number"):
                md["levels"].reset()
        if kind == "fence":
            self._code_start(info, gap)
        elif kind == "table":
            self._table_row(line, gap)
        elif kind == "heading":
            tag = f"md_h{min(info['level'], 3)}"
            self._inline(info["text"], (tag,), fonts=False, partial=partial)
            ins(self._at, "\n", tag)
        elif kind in ("hr", "pagebreak"):
            if gap:
                ins(self._at, "\n", "gap")
            ins(self._at, "\n", "hr")
            ins(self._at, "\n", "gap")
            md["blank"] = True
        elif kind in ("bullet", "number"):
            self._list_item(kind, info, partial)
        elif kind == "quote":
            self._inline(info["text"], ("p", "quote"), partial=partial)
            ins(self._at, "\n", ("p", "quote"))
        else:
            self._inline(info["text"], ("p",), partial=partial)
            ins(self._at, "\n", "p")

    def _inline(self, text: str, base: tuple, fonts: bool = True, partial: bool = False):
        for seg, st, url in inline(text):
            tags = list(base)
            if "code" in st:
                tags.append("code")
            elif fonts and "b" in st and "i" in st:
                tags.append("bi")
            elif fonts and "b" in st:
                tags.append("b")
            elif fonts and "i" in st:
                tags.append("i")
            if "s" in st:
                tags.append("s")
            if url:
                tags.append("link")
                if not partial:  # angefangene Zeilen werden ständig neu gezeichnet – erst fertige Links klickbar
                    tags += ["clickable", f"lnk:{self._new('link', url=url)}"]
            self.t.insert(self._at, seg, tuple(tags))

    def _list_item(self, kind: str, info: dict, partial: bool):
        levels = self.md["levels"]
        if partial:  # Zustand nicht verändern
            levels = ListLevels()
            levels.stack = list(self.md["levels"].stack)
        level = min(levels.level(info["indent"]), 4)
        if kind == "number":
            prefix, hang = info["num"] + ".", self.f_base.measure("00.") + self._s(7)
        elif info.get("task"):
            prefix, hang = info["task"], self.f_base.measure("☐") + self._s(8)
        else:
            prefix, hang = BULLETS[level % len(BULLETS)], self._s(18)
        lm1 = self._s(6) + level * self._s(24)
        lm2 = lm1 + hang
        tag = f"ind_{lm1}_{lm2}"
        if tag not in self._indent_tags:
            # Tabstopp genau beim Einzug: Text nach dem Aufzählungszeichen und Folgezeilen stehen bündig
            self.t.tag_configure(tag, lmargin1=lm1, lmargin2=lm2, tabs=(lm2,))
            self._indent_tags.add(tag)
        base = ("li", tag)
        self.t.insert(self._at, prefix, base + ("bullet",))
        self.t.insert(self._at, "\t", base)
        self._inline(info["text"], base, partial=partial)
        self.t.insert(self._at, "\n", base)

    # ---------------------------------------------------------------- Code-Blöcke

    def _code_start(self, info: dict, gap: bool):
        n = self._new("code")
        self.md["code"] = {"n": n, "fence": info["fence"], "lang": info["lang"]}
        if gap:
            self.t.insert(self._at, "\n", "gap")
        self.t.insert(self._at, (info["lang"] or "code") + "      ", "pre_hdr")
        self.t.insert(self._at, "⧉ Kopieren", ("pre_hdr", "clickable", f"ccp:{n}"))
        self.t.insert(self._at, "\n", "pre_hdr")
        self.t.insert(self._at, "\n", "pre_pad")

    def _code_line(self, line: str):
        code = self.md["code"]
        base = ("pre", f"code:{code['n']}")
        line = line.expandtabs(4)
        pos = 0
        for start, end, kind in highlight(line, code["lang"]):
            if start > pos:
                self.t.insert(self._at, line[pos:start], base)
            self.t.insert(self._at, line[start:end], base + (f"c_{kind}",))
            pos = end
        if pos < len(line):
            self.t.insert(self._at, line[pos:], base)
        self.t.insert(self._at, "\n", base)

    def _code_end(self):
        self.t.insert(self._at, "\n", "pre_pad")
        self.t.insert(self._at, "\n", "gap")
        self.md["code"] = None
        self.md["blank"] = True

    # ---------------------------------------------------------------- Tabellen

    def _table_row(self, line: str, gap: bool):
        md = self.md
        tb = md["table"]
        if tb is None:
            tb = md["table"] = {"n": self._new("table"), "rows": []}
            if gap:
                self.t.insert(self._at, "\n", "gap")
            self.t.mark_set(f"tbl:{tb['n']}", self._at)
            self.t.mark_gravity(f"tbl:{tb['n']}", "left")
        else:
            self.t.delete(f"tbl:{tb['n']}", self._at)  # Tabelle mit der neuen Zeile komplett neu setzen
        tb["rows"].append(line)
        self._render_table(tb)
        md["blank"] = True

    def _render_table(self, tb: dict):
        rows = [split_row(r) for r in tb["rows"]]
        header = None
        if len(rows) >= 2 and is_separator_row(rows[1]):
            header, body = rows[0], rows[2:]
        elif len(rows) == 1:
            header, body = rows[0], []
        else:
            body = [r for r in rows if not is_separator_row(r)]
        all_rows = ([header] if header else []) + body
        ncols = max(len(r) for r in all_rows)
        cells = [[plain(r[c]).replace("\t", " ") if c < len(r) else "" for c in range(ncols)] for r in all_rows]
        gutter = self._s(22)
        natural = [1] * ncols
        for ri, row in enumerate(cells):
            font = self.f_bold if header and ri == 0 else self.f_base
            for c, txt in enumerate(row):
                natural[c] = max(natural[c], font.measure(txt))
        avail = self._area_width() - self._s(8) - gutter * (ncols - 1)
        widths = self._fit(natural, max(avail, ncols * self._s(40)))
        stops, x = [], 0
        for w in widths[:-1]:
            x += w + gutter
            stops.append(x)
        tag = f"tbl:{tb['n']}"
        self.t.tag_configure(tag, tabs=tuple(stops) if stops else "")
        for ri, row in enumerate(cells):
            head = header is not None and ri == 0
            font = self.f_bold if head else self.f_base
            wrapped = [self._wrap(txt, widths[c], font) for c, txt in enumerate(row)]
            for k in range(max(len(w) for w in wrapped)):
                text = "\t".join(w[k] if k < len(w) else "" for w in wrapped)
                self.t.insert(self._at, text + "\n", ("th" if head else "td", tag))
            if head:
                self.t.insert(self._at, "\n", "trule")
            elif ri < len(cells) - 1:
                self.t.insert(self._at, "\n", "trule_soft")
        self.t.insert(self._at, "\n", "gap")

    @staticmethod
    def _fit(natural: list[int], avail: int) -> list[int]:
        """Spaltenbreiten: passt alles, bleibt es natürlich; sonst bekommen schmale Spalten zuerst ihren Platz."""
        if sum(natural) <= avail:
            return list(natural)
        widths, remaining = [0] * len(natural), avail
        order = sorted(range(len(natural)), key=lambda i: natural[i])
        for k, i in enumerate(order):
            widths[i] = max(1, min(natural[i], remaining // (len(natural) - k)))
            remaining -= widths[i]
        return widths

    @staticmethod
    def _wrap(text: str, width: int, font: tkfont.Font) -> list[str]:
        if not text or font.measure(text) <= width:
            return [text]
        lines, cur = [], ""
        for word in text.split():
            cand = f"{cur} {word}" if cur else word
            if font.measure(cand) <= width:
                cur = cand
                continue
            if cur:
                lines.append(cur)
            while len(word) > 1 and font.measure(word) > width:
                cut = len(word) - 1
                while cut > 1 and font.measure(word[:cut]) > width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            cur = word
        if cur:
            lines.append(cur)
        return lines or [""]

    # ================================================================ „Arbeitet …“-Anzeige

    def show_pending(self, label: str = "Arbeitet …"):
        if self._pending:
            return
        self._ensure()
        self.md_finish()
        with self._edit():
            self.t.insert(self._at, f"✻  {label}", ("think_hdr", "live", self._pend_tag))
            self.t.insert(self._at, "\n", ("think_hdr", self._pend_tag))
        self._pending = True
        self._pendings.add(self._pend_tag)
        self._animate()

    def hide_pending(self):
        if not self._pending:
            return
        self._pending = False
        self._pendings.discard(self._pend_tag)
        r = self.t.tag_ranges(self._pend_tag)
        if r:
            with self._edit():
                self.t.delete(r[0], r[-1])

    # ================================================================ Slots (Plätze für gleichzeitiges Schreiben)

    def slot_new(self) -> str:
        """Reserviert am Ende des Verlaufs einen Platz für eine Nachricht. Mit `with view.slot(id):` schreiben
        alle Methoden (md_append, think_*, tool_*, footer …) dorthin – auch wenn danach schon weitere
        Nachrichten folgen. So können mehrere KIs gleichzeitig antworten."""
        self._ensure()
        self.md_finish()
        self._n += 1
        sid = f"slot:{self._n}"
        with self._edit():
            pos = self.t.index("end-1c")
            self.t.insert("end-1c", "\n", "slot_end")  # unsichtbarer Abschluss: trennt den Platz vom Rest
            self.t.mark_set(sid, pos)
            self.t.mark_gravity(sid, "right")         # wandert hinter jeden eingefügten Text
        self._slots[sid] = {"md": None, "pending": False, "ptag": f"pend:{self._n}"}
        return sid

    @contextmanager
    def slot(self, sid: str):
        st = self._slots.get(sid)
        if st is None:  # z. B. nach clear() – dann einfach am Ende schreiben
            yield
            return
        saved = (self._at, self.md, self._pending, self._pend_tag)
        self._at, self.md, self._pending, self._pend_tag = sid, st["md"], st["pending"], st["ptag"]
        try:
            yield
        finally:
            st["md"], st["pending"] = self.md, self._pending
            self._at, self.md, self._pending, self._pend_tag = saved

    # ================================================================ Aufklappbare Blöcke

    def _header_parts(self, n: int) -> list[tuple[str, tuple]]:
        b = self.blocks[n]
        chev = OPENED if b["open"] else CLOSED
        if b["kind"] == "think":
            if b["live"]:
                secs = int(time.monotonic() - b["t0"])
                return [(f"✻  Denkt nach …  {secs} s" if secs else "✻  Denkt nach …", ())]
            secs = f"  ·  {b['secs']} s" if b.get("secs") else ""
            return [(f"✻  {b['title'] or 'Gedankengang'}{secs}   {chev}", ())]
        text = f"{b['icon']}  {b['label']}" + (f"  ·  {b['detail']}" if b["detail"] else "")
        if b["live"]:
            return [(text + "  …", ())]
        mark = ("  ✓", ("st_ok",)) if b["ok"] else ("  ✗", ("st_err",))
        return [(text, ()), mark, (f"   {chev}", ())]

    def _insert_header(self, n: int):
        b = self.blocks[n]
        base = "think_hdr" if b["kind"] == "think" else "tool_hdr"
        for text, extra in self._header_parts(n):
            tags = (base, "clickable", f"hdr:{n}") + (("live",) if b["live"] else ()) + extra
            self.t.insert(self._at, text, tags)
        self.t.insert(self._at, "\n", base)

    def _update_header(self, n: int):
        b, r = self.blocks.get(n), self.t.tag_ranges(f"hdr:{n}")
        if not b or not r:
            return
        base = "think_hdr" if b["kind"] == "think" else "tool_hdr"
        with self._edit(follow=False):
            self.t.mark_set("hdr_ins", r[0])
            self.t.mark_gravity("hdr_ins", "right")
            self.t.delete(r[0], r[-1])
            for text, extra in self._header_parts(n):
                tags = (base, "clickable", f"hdr:{n}") + (("live",) if b["live"] else ()) + extra
                self.t.insert("hdr_ins", text, tags)

    def toggle(self, n: int):
        b = self.blocks.get(n)
        if not b or b["kind"] not in ("think", "tool"):
            return
        b["open"] = not b["open"]
        self.t.tag_configure(f"body:{n}", elide=not b["open"])
        self._update_header(n)

    # ---------------------------------------------------------------- Denkprozess

    def think_begin(self) -> int:
        self._ensure()
        self.md_finish()
        self.hide_pending()
        n = self._new("think", open=False, live=True, t0=time.monotonic(), title=None, secs=None,
                      started=False, held="")
        self.t.tag_configure(f"body:{n}", elide=True)
        self._live.add(n)
        with self._edit():
            self._insert_header(n)
            self.t.insert(self._at, "\n", ("think_pad", f"body:{n}"))
        self._animate()
        return n

    def think_append(self, n: int, text: str):
        b = self.blocks.get(n)
        if not b:
            return
        if not b["started"]:
            text = text.lstrip()
            if not text:
                return
            b["started"] = True
        text = b["held"] + text
        body = text.rstrip("\n")
        b["held"] = text[len(body):]   # Zeilenumbrüche am Ende zurückhalten (sonst Leerzeile im Block)
        if body:
            with self._edit():
                self.t.insert(self._at, body, ("think_body", f"body:{n}"))

    def think_end(self, n: int) -> int | None:
        """Schließt den Block ab (muss noch am Ende stehen) und gibt die Dauer in Sekunden zurück."""
        b = self.blocks.get(n)
        if not b or not b["live"]:
            return b["secs"] if b else None
        b["live"] = False
        self._live.discard(n)
        b["secs"] = max(1, round(time.monotonic() - b["t0"]))
        with self._edit():
            if b["started"]:
                self.t.insert(self._at, "\n", ("think_body", f"body:{n}"))
            self.t.insert(self._at, "\n", ("think_pad", f"body:{n}"))
        self._update_header(n)
        return b["secs"]

    def think_static(self, text: str, title: str | None = None, secs: int | None = None) -> int:
        self._ensure()
        self.md_finish()
        n = self._new("think", open=False, live=False, t0=0, title=title, secs=secs, started=True, held="")
        self.t.tag_configure(f"body:{n}", elide=True)
        with self._edit():
            self._insert_header(n)
            self.t.insert(self._at, "\n", ("think_pad", f"body:{n}"))
            self.t.insert(self._at, text.strip() + "\n", ("think_body", f"body:{n}"))
            self.t.insert(self._at, "\n", ("think_pad", f"body:{n}"))
        return n

    def set_think_title(self, n: int, title: str):
        b = self.blocks.get(n)
        if b and b["kind"] == "think":
            b["title"] = title
            self._update_header(n)

    # ---------------------------------------------------------------- Werkzeuge

    def tool_begin(self, label: str, detail: str, args_text: str, icon: str = "⚙") -> int:
        self._ensure()
        self.md_finish()
        self.hide_pending()
        n = self._new("tool", open=False, live=True, label=label, detail=detail, icon=icon, ok=None)
        self.t.tag_configure(f"body:{n}", elide=True)
        self._live.add(n)
        with self._edit():
            self._insert_header(n)
            self._tool_section(n, "EINGABE", args_text)
        self._animate()
        return n

    def tool_end(self, n: int, result: str, ok: bool):
        b = self.blocks.get(n)
        if not b:
            return
        b["live"], b["ok"] = False, ok
        self._live.discard(n)
        with self._edit():
            self._tool_section(n, "ERGEBNIS", result, last=True)
        self._update_header(n)

    def tool_static(self, label: str, detail: str, args_text: str, result: str | None, ok: bool, icon="⚙"):
        self._ensure()
        self.md_finish()
        n = self._new("tool", open=False, live=False, label=label, detail=detail, icon=icon, ok=ok)
        self.t.tag_configure(f"body:{n}", elide=True)
        with self._edit():
            self._insert_header(n)
            self._tool_section(n, "EINGABE", args_text)
            self._tool_section(n, "ERGEBNIS", result if result is not None else "(kein Ergebnis)", last=True)

    def _tool_section(self, n: int, label: str, text: str, last: bool = False):
        body = f"body:{n}"
        text = text if len(text) <= 4000 else text[:4000] + "\n…"
        self.t.insert(self._at, "\n", ("pre_pad", body))
        self.t.insert(self._at, label + "\n", ("tool_body", "tool_label", body))
        self.t.insert(self._at, text.rstrip() + "\n", ("tool_body", body))
        if last:
            self.t.insert(self._at, "\n", ("pre_pad", body))

    # ---------------------------------------------------------------- Sonstiges

    def footer(self, raw: str, tps: float | None = None):
        """Zeile unter einer fertigen Antwort: „Kopieren“ (Markdown-Text) und ggf. Tokens pro Sekunde."""
        self.md_finish()
        n = self._new("copy", text=raw)
        with self._edit():
            self.t.insert(self._at, "⧉ Kopieren", ("footer", "clickable", f"cpy:{n}"))
            if tps:
                self.t.insert(self._at, f"      {tps:.1f} Tokens/s".replace(".", ","), "footer")
            self.t.insert(self._at, "\n", "footer")

    def note(self, text: str, kind: str = "note"):
        """kind "note" (grau, kursiv) oder "error" (rot)."""
        self._ensure()
        self.md_finish()
        self.hide_pending()
        with self._edit():
            self.t.insert(self._at, text.strip() + "\n", kind)

    # ================================================================ Animation

    def _animate(self):
        if self._anim_job is None:
            self._anim_job = self.t.after(PULSE_MS, self._anim_step)

    def _anim_step(self):
        self._anim_job = None
        if not (self._live or self._pendings) or not self.col:
            return
        now = time.monotonic()
        k = (math.sin(now * 3.4) + 1) / 2
        self.t.tag_configure("live", foreground=mix_hex(self.col["muted"], self.col["accent"], k))
        for n in list(self._live):
            b = self.blocks.get(n)
            if b and b["kind"] == "think":
                secs = int(now - b["t0"])
                if secs != b.get("shown"):
                    b["shown"] = secs
                    self._update_header(n)
        self._anim_job = self.t.after(PULSE_MS, self._anim_step)

    # ================================================================ Maus & Zwischenablage

    def _on_wheel(self, e):
        if e.delta:
            self._scroll_left -= e.delta / 120 * 3 * self.f_base.metrics("linespace")
            if self._scroll_job is None:
                self._scroll_step()
        return "break"

    def _scroll_step(self):
        """Weiches Scrollen: pro Schritt ein Teil der Reststrecke."""
        self._scroll_job = None
        left = self._scroll_left
        if abs(left) < 1:
            self._scroll_left = 0.0
            return
        move = int(left * 0.3) or (1 if left > 0 else -1)
        before = self.t.yview()
        self.t.yview_scroll(move, "pixels")
        self._scroll_left -= move
        self._stick = self._at_bottom()
        if self.t.yview() == before:  # oben oder unten angekommen
            self._scroll_left = 0.0
            return
        self._scroll_job = self.t.after(12, self._scroll_step)

    def _stop_scroll(self):
        if self._scroll_job:
            self.t.after_cancel(self._scroll_job)
            self._scroll_job = None
        self._scroll_left = 0.0

    def _on_enter_click(self, _e=None):
        self.t.configure(cursor="hand2")
        for tag in self.t.tag_names("current"):
            if tag.startswith("hdr:"):
                r = self.t.tag_ranges(tag)
                if r:
                    self.t.tag_add("hover", r[0], r[-1])

    def _on_leave_click(self, _e=None):
        self.t.configure(cursor="xterm")
        self.t.tag_remove("hover", "1.0", "end")

    def _on_click(self, _e=None):
        for tag in self.t.tag_names("current"):
            kind, _, num = tag.partition(":")
            if not num.isdigit() or int(num) not in self.blocks:
                continue
            n, b = int(num), self.blocks[int(num)]
            if kind == "hdr":
                self.toggle(n)
            elif kind == "cpy":
                self._copy(b["text"])
                self._flash(tag, "✓ Kopiert", ("footer", "clickable"))
            elif kind == "ccp":
                r = self.t.tag_ranges(f"code:{n}")
                self._copy(self.t.get(r[0], r[-1]).rstrip("\n") if r else "")
                self._flash(tag, "✓ Kopiert", ("pre_hdr", "clickable"))
            elif kind == "lnk":
                webbrowser.open(b["url"])
            else:
                continue
            return "break"

    def _flash(self, tag: str, text: str, tags: tuple):
        """Ersetzt kurz den Text eines Knopfs (z. B. „✓ Kopiert“) und stellt ihn danach wieder her."""
        r = self.t.tag_ranges(tag)
        if not r:
            return
        old, gen = self.t.get(r[0], r[-1]), self.gen
        if old == text:
            return

        def put(s):
            rr = self.t.tag_ranges(tag)
            if gen != self.gen or not rr:
                return
            with self._edit(follow=False):
                self.t.mark_set("flash", rr[0])
                self.t.mark_gravity("flash", "right")
                self.t.delete(rr[0], rr[-1])
                self.t.insert("flash", s, tags + (tag,))

        put(text)
        self.t.after(1500, lambda: put(old))

    def _copy(self, text: str):
        self.t.clipboard_clear()
        self.t.clipboard_append(text)

    def _copy_selection(self):
        if self.t.tag_ranges("sel"):
            self._copy(self.t.get("sel.first", "sel.last"))

    def _on_context(self, e):
        self.menu.entryconfigure(0, state=tk.NORMAL if self.t.tag_ranges("sel") else tk.DISABLED)
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    def _on_bubble_menu(self, e, text: str):
        self._bubble_text = text
        try:
            self.bubble_menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.bubble_menu.grab_release()

    def _on_file_menu(self, e, path: str):
        if not path:
            return
        self._file_path = path
        state = tk.NORMAL if os.path.exists(path) else tk.DISABLED
        self.file_menu.entryconfigure(0, state=state)
        try:
            self.file_menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.file_menu.grab_release()

    @staticmethod
    def _open_file(path: str):
        if path and os.path.exists(path):
            os.startfile(path)
