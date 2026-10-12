"""Meldungen und Ja/Nein-Fragen im Design des Programms – Ersatz für tkinter.messagebox, dessen
Windows-Meldungsfenster sich nicht einfärben lassen.

Gleiche Aufrufe wie bei messagebox: showinfo / showerror / askyesno(titel, text, parent=…). Vorher einmal
setup(root, theme, ui_scale) aufrufen (macht App.__init__); ohne setup wird auf messagebox zurückgegriffen.
"""

import tkinter as tk
from tkinter import messagebox, ttk

try:
    import winsound
except ImportError:  # nicht unter Windows
    winsound = None

_root: tk.Tk | None = None
_theme = None
_scale = 1.0

# Symbol je Art: Zeichen, Farbschlüssel in der Palette, Ersatzfarbe (Design „Windows-Standard“), Hinweiston
_KINDS = {
    "info": ("i", "accent", "#0067c0", 0x40),       # MB_ICONASTERISK
    "error": ("!", "error", "#c42b1c", 0x10),       # MB_ICONHAND
    "question": ("?", "accent", "#0067c0", 0x20),   # MB_ICONQUESTION
}


def setup(root: tk.Tk, theme, ui_scale: float = 1.0):
    global _root, _theme, _scale
    _root, _theme, _scale = root, theme, ui_scale


def showinfo(title: str, message: str, parent: tk.Misc | None = None):
    _show("info", title, message, parent)


def showerror(title: str, message: str, parent: tk.Misc | None = None):
    _show("error", title, message, parent)


def askyesno(title: str, message: str, parent: tk.Misc | None = None) -> bool:
    return _show("question", title, message, parent)


def _s(v: float) -> int:
    return round(v * _scale)


def _color(key: str, fallback: str) -> str:
    pal = _theme.pal if _theme else None
    if not pal:
        return fallback
    return pal.get(key) or pal.get("chat", {}).get(key) or fallback


def _show(kind: str, title: str, message: str, parent: tk.Misc | None) -> bool:
    if _theme is None or _root is None:
        fallback = {"info": messagebox.showinfo, "error": messagebox.showerror, "question": messagebox.askyesno}
        return bool(fallback[kind](title, message, parent=parent))
    try:
        if parent is None or not parent.winfo_exists() or not parent.winfo_viewable():
            parent = _root
    except tk.TclError:
        parent = _root
    symbol, color_key, fallback_color, sound = _KINDS[kind]
    question = kind == "question"

    win = tk.Toplevel(parent)
    win.withdraw()  # erst fertig aufbauen und platzieren, dann einblenden (theme.present)
    win.title(title)
    win.transient(parent)
    win.resizable(False, False)
    _theme.register_window(win)
    body = ttk.Frame(win, padding=(_s(18), _s(16), _s(18), _s(14)))
    body.pack(fill=tk.BOTH, expand=True)

    top = ttk.Frame(body)
    top.pack(fill=tk.X)
    size = _s(30)
    bg = ttk.Style().lookup("TFrame", "background") or "#ffffff"
    icon = tk.Canvas(top, width=size, height=size, highlightthickness=0, borderwidth=0, background=bg)
    icon.create_oval(1, 1, size - 1, size - 1, fill=_color(color_key, fallback_color), outline="")
    icon.create_text(size // 2, size // 2, text=symbol, fill="#ffffff", font=("Segoe UI", 12, "bold"))
    icon.pack(side=tk.LEFT, anchor="n", padx=(0, _s(14)))
    ttk.Label(top, text=message, wraplength=_s(420), justify=tk.LEFT).pack(side=tk.LEFT, anchor="w", fill=tk.X,
                                                                          pady=(_s(5), 0))

    result = {"v": False}

    def close(value: bool):
        result["v"] = value
        win.destroy()

    bf = ttk.Frame(body)
    bf.pack(fill=tk.X, pady=(_s(16), 0))
    if question:
        ttk.Button(bf, text="Nein", command=lambda: close(False)).pack(side=tk.RIGHT)
        default = ttk.Button(bf, text="Ja", style="Accent.TButton", command=lambda: close(True))
        default.pack(side=tk.RIGHT, padx=(0, _s(6)))
    else:
        default = ttk.Button(bf, text="OK", style="Accent.TButton", width=10, command=lambda: close(True))
        default.pack(side=tk.RIGHT)
    win.bind("<Return>", lambda e: default.invoke())
    win.bind("<Escape>", lambda e: close(False))
    win.protocol("WM_DELETE_WINDOW", lambda: close(False))
    win.bind("<Control-c>", lambda e: (win.clipboard_clear(), win.clipboard_append(message)))  # wie bei Windows

    win.update_idletasks()
    w, h = win.winfo_reqwidth(), win.winfo_reqheight()
    x = parent.winfo_rootx() + max(0, (parent.winfo_width() - w) // 2)
    y = parent.winfo_rooty() + max(0, (parent.winfo_height() - h) // 3)
    _theme.present(win, f"+{x}+{y}")
    if winsound:
        try:
            winsound.MessageBeep(sound)
        except RuntimeError:
            pass
    default.focus_set()
    try:
        win.grab_set()
    except tk.TclError:  # ein anderes Fenster hält gerade die Eingabe fest
        pass
    parent.wait_window(win)
    return result["v"]
