# Modell-Studio – Projektübersicht für Claude

Lokale Windows-Desktop-App (Python + Tkinter): lokale GGUF-Sprachmodelle laden, mit ihnen
chatten und sie optional als Agent arbeiten lassen (Websuche, Dateien in freigegebenen Ordnern, Word/Excel/
PowerPoint, Programmierprojekte, PowerShell/Python).
Nutzer spricht Deutsch → **Antworten, UI-Texte und Code-Kommentare auf Deutsch.**

## Rahmenbedingungen
- Projekt mit WebFusedAgentMod (Gruppenchat mehrerer Rechner). Persönliche Daten (Einstellungen, Chats,
  Modelle, llama.cpp) liegen nur lokal und sind per `.gitignore` ausgeschlossen.
- Windows, **Python 3.13**, **nur Standardbibliothek** (keine pip-Pakete einführen).
- Start: Doppelklick auf `ModellStudio.pyw` (ohne Konsolenfenster; Fehler sieht man nur beim Start mit
  `py ModellStudio.pyw`). Der Starter hängt `app/` an `sys.path` und führt `app/modell_studio.py` aus.
- **Aufbau:** Sämtlicher Code liegt im (versteckten) Ordner `app/`; Nutzerdaten (`config.json`, `chats/`,
  `modelle/`, `raeume/`, `sicherungen/`, `server/`) liegen daneben im Hauptordner. Deshalb zeigen `APP_DIR`
  (`modell_studio.py`), `CHAT_DIR` (`chats.py`) und `SERVER_DIR` (`llamacpp.py`) auf
  `Path(__file__).resolve().parent.parent`.
- **Unabhängig von LM Studio** (bewusste Entscheidung – keine LM-Studio-Pfade/-Backends wieder
  einbauen): `llama-server.exe` kommt aus dem eigenen Ordner `server/<Zip-Name>/`, heruntergeladen über
  *Einstellungen → Laden → „llama.cpp herunterladen / aktualisieren“* direkt von github.com/ggml-org/llama.cpp,
  oder von Hand über „Eigene llama-server.exe…“. Modelle (`.gguf`) liegen im eigenen Ordner `modelle/`
  (`MODEL_DIR`, wird immer durchsucht und beim Start angelegt, auch Download-Ziel); `cfg["folders"]` enthält nur
  **weitere** Ordner. `load_config()` verwirft alte `.lmstudio`-Einträge in `server_exe`, `folders` und `extra_models`.
- `config.json` enthält Nutzereinstellungen (u. a. evtl. einen Brave-API-Key) → nicht überschreiben,
  nicht weitergeben. Neue Einstellungen über `DEFAULTS` in `modell_studio.py` einführen.

## Dateien (alle im Ordner `app/`)
| Datei | Inhalt |
|---|---|
| `modell_studio.py` | Klasse `App`: gesamte Oberfläche und Ablauf (siehe unten), `DEFAULTS`/`load_config`/`save_config` |
| `chatview.py` | Klasse `ChatView`: der komplette Chatverlauf in einem `tk.Text` – Nutzer-Sprechblasen rechts (Canvas mit kantengeglätteten Ecken, per `window_create` eingebettet), Markdown-Antworten links (auch gestreamt), aufklappbare Denkprozess-/Werkzeug-Blöcke, Code-Hervorhebung, Tabellen über Tabstopps, „Kopieren“, Pulsieren, weiches Scrollen, mittige Spalte (`MAX_COLUMN`) |
| `markdown_lite.py` | `inline()` (fett/kursiv/Code/Links → Segmente), `classify()` (Zeilenart), Tabellen-Helfer, `ListLevels` – genutzt von `chatview.py` und `office.py` |
| `llamacpp.py` | Eigener llama.cpp: `find_llama_servers()` (sucht `server/*/llama-server.exe`, sortiert CUDA > ROCm > Vulkan > Sonstige > CPU, neueste zuerst), `detect_gpu()` (Grafikkarten-Name per PowerShell/CIM, CUDA-Version aus `nvidia-smi`), `plan()` (neuestes Release `b12345` über die GitHub-API – die Builds sind dort als Vorabversion markiert, „latest“ ist etwas anderes!; wählt CUDA ≤ Treiberversion + `cudart`-Zip für NVIDIA, sonst Vulkan, sonst CPU), `install()` (lädt nach `server/<name>.part`, entpackt, benennt um, entfernt ältere Versionen derselben Art) |
| `backend.py` | `find_models()` (rglob `*.gguf`, ohne `mmproj*`), `find_mmproj(modell)` (mmproj im Ordner des Modells, bei mehreren der namensähnlichste), `LlamaServer` (freier Port, `--jinja --no-webui`, optional `--mmproj` + `--image-max-tokens`, per `_end_with_app()` an ein Windows-Job-Objekt gebunden (stirbt mit dem Programm, auch bei Absturz), wartet auf `/health`, `vision` aus `/props` → `modalities.vision`), `chat_turn()` (Streaming inkl. `reasoning_content`, `tool_calls`, `<think>`-Fallback, „stats“ = Tokens/s; gibt `(text, calls, reasoning)` zurück; Schlüssel mit `_` gehen nicht an den Server), `thinking_title()` (kurze Überschrift für einen Denkprozess, `enable_thinking: false`) |
| `agent.py` | Werkzeuge in `FILE_TOOLS` (list/read/write/edit_file, create_directory, move/copy/delete_path (Papierkorb), search_files, open_path, create_project, create_word_document, create_excel_workbook, create_presentation), `CMD_TOOLS` (run_command = PowerShell, run_python) und `WEB_TOOLS`; `Agent` (Pfade auf freigegebene Ordner `roots` beschränkt, Bestätigung „yes/no/all“); `describe_call`/`format_args`/`result_ok`/`LABELS`/`ICONS` für die Chatanzeige; `run_conversation()` (Tool-Schleife, max. 25 Schritte, speichert `_thinking`) |
| `office.py` | `write_docx` (Markdown → Word), `write_xlsx` (Blätter/Zeilen → Excel, Formeln inkl. deutscher Namen), `write_pptx` (Folien → PowerPoint 16:9), `read_text` (Text aus docx/xlsx/pptx) – reines zipfile/XML |
| `projects.py` | Projektvorlagen (python, java, cpp, c, rust, csharp, node, web, go) für `create_project` |
| `documents.py` | `read(pfad) -> Document` (Text, Art, Seiten/Folien/Blätter, `info`): PDF, Office, OpenDocument, RTF, Text/Code (Kodierung erkannt); `CONVERTED` = Formate, die `read_file` umwandelt; Bilder lehnt `read()` ab (`VISION_IMAGES` gehen über `images.py`) |
| `images.py` | Bilder: `read(pfad, vorschau, größe)` (prüft Format/Größe, PNG-Vorschau + Maße über PowerShell/System.Drawing – Tk kann kein JPG), `data_url()`, `save_clipboard()` (Screenshot → PNG), `cleanup_temp()` (alte Dateien in `TEMP_DIR` = `%TEMP%\ModellStudio`, beim Start). Formate: PNG, JPG, BMP, GIF (was llama.cpp lesen kann). PowerShell-Ausgabe immer mit `encoding="oem"` lesen |
| `pdftext.py` | Eigener PDF-Textleser (nur Standardbibliothek): Objekte/Objektströme, Flate/LZW/ASCII85, ToUnicode-CMaps, Differences, echte Zeichenbreiten für Leerzeichen-Erkennung, Form-XObjects; verschlüsselte PDFs → `PDFError` |
| `filedrop.py` | Windows: Dateien per Drag & Drop (`enable_drop`, WM_DROPFILES über ctypes) und aus der Zwischenablage (`clipboard_files`, CF_HDROP); `clipboard_has_image()` = Bild (CF_DIB) ohne Text |
| `web.py` | Suche über DuckDuckGo-HTML (ohne Schlüssel) oder Brave-API; `fetch()` holt Seiten als Text (max. 8000 Zeichen); `_check_public()` sperrt private/lokale Adressen, auch bei Weiterleitungen |
| `themes.py` | `THEMES` (Farbpaletten „dark“ Grau/Orange, „light“ Weiß/Violett, „system“ = Windows-Standard; Chatfarben je Design unter `"chat"`), `ThemeManager`, `mix_hex()` |
| `chats.py` | Chats als JSON in `chats/<id>.json` speichern/laden/löschen/auflisten, Datumsgruppen für die Seitenleiste; angehängte Bilder in `chats/bilder/<id>/` (`store_image`, `image_path`, `thumb_path`; `delete` entfernt sie mit) |
| `dialogs.py` | Meldungen im Design: `showinfo`/`showerror`/`askyesno(titel, text, parent=…)` – gleiche Aufrufe wie `tkinter.messagebox`, aber eigenes `Toplevel` mit farbigem Symbol (i/!/?), Windows-Hinweiston, Enter = OK/Ja, Escape = Nein, Strg+C kopiert den Text. `setup(root, theme, ui_scale)` in `App.__init__`; ohne setup Rückfall auf messagebox |
| `widgets.py` | Eigene Bedienelemente: `ModeSwitch` (Umschalter „Chat | WebFusedAgentMod“ mit gleitender, kantengeglätteter Markierung), `rounded()` |
| `room.py` | WebFusedAgentMod, Netzwerk: `RoomServer` (Gastgeber, `ThreadingHTTPServer`, Endpunkte `/api/join|events|post|patch|member|leave`, Long-Polling, Verlauf in `raeume/<raum>.json`), `RoomClient` (Teilnehmer, Abruf-Thread, Wiederbeitritt), HMAC-Signatur `sign()`, Einladungscode `make_invite`/`parse_invite`, `mentions()`, `local_addresses()`/`tailscale_status()` |
| `roomui.py` | WebFusedAgentMod, Oberfläche/Ablauf: `RoomMode` (Raum-Panel in der Seitenleiste, Dialoge, Darstellung in der zweiten ChatView mit Slots, eigene KI antwortet auf @Name/@alle, `MAX_CHAIN`, Kontextaufbau, `_Patcher` schickt Antworten gebündelt an den Gastgeber) |

## Aufbau der Oberfläche (`modell_studio.py`)
- **Moduswahl** ganz oben links: `self.mode_switch` (`widgets.ModeSwitch`, Farben in `set_theme`).
  `set_mode("chat"|"room")` holt per `tkraise()` die passende Ebene nach vorn: `chat_side` (Chatliste) oder
  `room_side` (Raum-Panel von `RoomMode`) sowie `chat_frame`/`view` oder `room_frame`/`room_view` (zwei getrennte
  ChatViews aus `_make_chat_view`). Beide Ebenen liegen fertig aufgebaut per `grid` in derselben Zelle – nicht auf
  pack/pack_forget umstellen (flackert). `update_title()` setzt die Kopfzeile je Modus; Modus in `cfg["mode"]`.
  `send()` leitet im Raum-Modus an `self.room.send()` weiter; der Senden-Knopf wird dort nie zu „Stopp“.
- **Seitenleiste links** (`_build_sidebar`): „+ Neuer Chat“, Chatliste als `ttk.Treeview` (Gruppen „Heute“,
  „Gestern“, „Letzte 7/30 Tage“, „Älter“; Gruppenzeilen haben IDs `grp:…`), Menü Umbenennen/Löschen
  (`_open_chat_menu`) per Rechtsklick oder Klick auf „•••“ in der Spalte `menu` (`_update_dots` zeigt sie beim
  geöffneten Chat, unter der Maus und bei offenem Menü; Klick dort wechselt den Chat nicht), F2/Entf.
  Löschen geschieht bewusst **ohne Rückfrage und endgültig** (kein Papierkorb) – so gewollt.
  Ganz unten: „⚙ Einstellungen“.
- **Chatbereich** (`_build_main`): Kopfzeile mit Chat-Titel, Modell-Auswahl (`model_combo`), „▶ Laden“,
  „■“ (Entladen) und Status; darunter Verlauf (`self.chat` = `tk.Text`, gesteuert von `self.view` = `ChatView`),
  „+“ (Anhänge), Eingabe, Senden-Knopf (wird während einer Antwort zu „■ Stopp“). Über der Eingabe die
  Anhang-Leiste `attach_bar` (nur sichtbar, wenn `self.attachments` nicht leer; Kärtchen-Stile `Chip.*`).
- **Anhänge:** `add_attachments(pfade)` (Knopf, Drag & Drop, Strg+V via `<<Paste>>`) liest jede Datei im
  Thread (`documents.read`, Ereignis `attach_done`). `send()` baut mit `_compose_message` den Modelltext
  (eigener Text + `<dokument name=… art=…>`-Blöcke, gekürzt auf ~60 % des Kontexts) und speichert in der
  Nutzernachricht zusätzlich `_text` und `_files` (name, info, path) für die Anzeige (`view.user(text, files)`).
- **Bilder** (Bildverständnis über die `mmproj`-Datei neben dem Modell): `_read_attachment` nimmt für Bilder
  `images.read` (Document mit `image=(B, H)` und `thumb` = PNG-Vorschau im Temp-Ordner). Strg+V mit einem
  Bild ohne Text in der Zwischenablage → `images.save_clipboard()` im Thread → Ereignis `paste_image`.
  `send()` kopiert Bilder per `chats.store_image` nach `chats/bilder/<chat-id>/` und speichert in der
  Nutzernachricht `_images` (Pfade relativ zu `chats.IMAGE_DIR`) und in `_files` Einträge mit `"image"` statt
  `"path"`; `_display_files()` ergänzt für die Anzeige `path`/`thumb`. **`content` bleibt ein String** – erst
  `_api_message()` macht beim Senden daraus die OpenAI-Teile (`image_url` als data-URL + Text); ohne
  Bildverständnis oder bei fehlender Datei nur ein Hinweistext. Senden mit Bild verweigert, wenn
  `server.vision` falsch ist; im Gruppenchat noch nicht möglich. `ChatView._draw_files` zeigt bei `thumb` das
  Vorschaubild im Kärtchen (`_thumb()` mit Zwischenspeicher, zu breite per `subsample`).
- **Einstellungsfenster** (`_build_settings`): einmal erzeugtes `Toplevel`, wird nur versteckt/gezeigt
  (`open_settings(tab)` / `close_settings()` speichert die Konfiguration). Reiter: Modelle, Laden (Backend-Auswahl,
  Knopf `srv_btn` → `download_server()` → `_srv_planned` → `_srv_finished`, Status in `srv_status`;
  `_refresh_servers()` liest `server/` neu ein; `load_model()` bietet den Download an, wenn kein Server da ist;
  `vision_var` „Bildverständnis laden“ → `find_mmproj()`, `imgtok_var` → `--image-max-tokens`), Werkzeuge
  (Internet, Dateien & Ordner = `agent_var`, Befehle = `cmd_var`, Bestätigen, Liste „Freigegebene Ordner“:
  `ws_var` = ★ Arbeitsordner + `extra_folders`), System-Prompt, Darstellung (Design, `think_title_var`),
  Server-Log. **Alle Einstellungs-Variablen (`ctx_var`, `web_var`, `sys_text`, `log` …) leben dort.**
- **Bestätigungsdialog** `ask_confirm()`: Vorschau + Erlauben / Alle erlauben (bis Antwortende) / Ablehnen.
- **Reiter Modelle:** „Modellordner öffnen“ (`open_model_dir`, Explorer) und „Weitere Ordner…“ (`open_sources`):
  Ordner/Einzeldateien in `cfg["folders"]`/`cfg["extra_models"]` hinzufügen, ändern, entfernen; der eigene Ordner
  steht dort fest als erster Eintrag (Art `"own"` in `_sources()`, nicht änderbar). Pfadvergleiche immer mit
  `same_path()`. `refresh_models()` findet `last_model` notfalls über den Dateinamen wieder (nach Verschieben).
- Modellauswahl: `self.sel_model` ist die eine Quelle; `_select_model(idx)` hält Kopfzeilen-Combobox und
  Liste in den Einstellungen synchron. Zwei Laden-Knöpfe (`load_btn`, `load_btn2`) → `_set_load_buttons()`.

## Wichtige Regeln im Code
- **Threads:** Laden, Chatten und Downloads laufen in Hintergrund-Threads und melden sich nur über
  `self.events.put((art, daten))`. **Tk nie direkt aus einem Thread anfassen.** `_poll_events` (15 ms während
  einer Antwort, sonst 50 ms) verarbeitet `log`, `loaded`, `load_failed`, `think_title`, `attach_done`,
  `dl_progress`, `dl_done`, `srv_plan`, `srv_progress`, `srv_done`, `paste_image` sofort in `_handle`; die Chat-Ereignisse (`STREAM_EVENTS`: `assistant_start`, `reasoning`,
  `content`, `tool_call` (name, args), `tool_result`, `stats`, `info`, `error`, `confirm`, `done`) kommen in
  `self.stream_q` und werden von `_drain_stream` **in Reihenfolge** abgearbeitet – Text gedrosselt („tippt“
  flüssig, Tempo passt sich dem Rückstand an), alles andere in `_handle_stream`. `_poll_id` verhindert doppelte
  Schleifen, wenn ein Dialog eine verschachtelte Ereignisschleife startet.
- **Chat-Verlauf:** `self.history` = OpenAI-Nachrichtenliste (`user`, `assistant` mit optional `tool_calls`,
  `tool`). Assistent-Nachrichten können **Anzeige-Schlüssel mit `_`** tragen: `_thinking`, `_thinking_title`,
  `_thinking_secs` (werden gespeichert, aber nie an den Server geschickt). `self.chat_data` = aktueller Chat
  (`None` = neuer, noch ungespeicherter Chat). Gespeichert wird nach der Nutzernachricht und nach `done`
  (`_save_chat`), die Denkprozess-Überschrift später mit `touch=False`. Der Titel entsteht aus der ersten Frage;
  Umbenennen speichert mit `touch=False`, damit sich die Sortierung nicht ändert.
- **Anzeige:** nur über `self.view` (`ChatView`): `user()`, `md_append()`/`md_finish()`/`markdown()`,
  `think_begin/append/end/static`, `set_think_title`, `tool_begin/end/static`, `show_pending/hide_pending`,
  `footer`, `note`, `placeholder`, `clear`. `self.turn` hält den Zustand der laufenden Antwort (Schritt, offener
  Denk-/Werkzeugblock, Tokens/s). In `chatview.py` gilt: Schreiben nur innerhalb `with self._edit():`,
  Abstände über `spacing1/3` bzw. kleine „gap“-Zeilen, Block-Tags mit Nummer `hdr:N`/`body:N`/… (werden bei
  `clear()` gelöscht). Der Verlauf folgt der Antwort nur, solange `_stick` gilt (Nutzer nicht hochgescrollt).
- **Designs:** ttk-Widgets werden automatisch eingefärbt. **Klassische tk-Widgets und Fenster müssen
  registriert werden**, sonst bleiben sie hell: `self.theme.register_text(w, role="field"|"plain")`,
  `register_list`, `register_combo`, `register_menu`, `register_window`. Vorhandene Stile: `Accent.TButton`,
  `Muted.TLabel`, `Title.TLabel`, `Top.TFrame`, `Sidebar.TFrame/TLabel/TButton/Treeview`. Eigene Designs
  nutzen „clam“ als Basis; „system“ stellt das native „vista“ und die gemerkten Originalfarben wieder her.
  Häkchen sind selbst gezeichnete Bilder (`_draw_checkbox`), die Titelleiste wird über DWM eingefärbt
  (`set_titlebar`, Fenstergriff über `wm_frame()`; wirkt erst, wenn das Fenster angezeigt ist – daher zusätzlich
  beim `<Map>`-Ereignis).
- **Meldungen:** nie `tkinter.messagebox` (natives Fenster, lässt sich nicht einfärben), sondern `dialogs.showinfo/showerror/askyesno`.
- **Fenster zeigen:** neue Dialoge mit `win.withdraw()` anlegen, fertig aufbauen und dann
  `self.theme.present(win, "+x+y")` – platziert unsichtbar, färbt die Titelleiste, blendet weich ein (auch das
  Hauptfenster beim Start). **`present()` muss `lift()` aufrufen:** ein beim Start versteckt angelegtes,
  `transient`-Fenster (z. B. Einstellungen) erscheint unter Windows nach bloßem `deiconify()` sonst gar nicht.
  Testskripte daher nie nur mit `deiconify()` prüfen. Raum-Tests: `app.app_dir` auf einen Testordner setzen,
  sonst landet der Raum-Verlauf in `raeume/` des Projekts. Neue Farbschlüssel in **beiden** Paletten ergänzen; Chatfarben (`"chat"`) in **allen drei**
  Designs (auch „system“). `set_theme` reicht sie an `view.apply_theme()` weiter.
- **Agent/Sicherheit:** Dateizugriffe nur über `Agent._path()` (freigegebene Ordner, auch absolut);
  schreibende Aktionen über `_ok()` bestätigen lassen; Löschen nur in den Papierkorb; `open_path` startet
  keine Programme/Skripte. Werkzeug-Schemas: `_tool(name, beschreibung, optional=(...), **eigenschaften)`.
- **WebFusedAgentMod:** Ereignisse `room` (vom RoomClient), `room_ai` (eigene KI im Raum), `room_ready`
  (erstellt/beigetreten) laufen über `_handle` an `self.room`. Jede Nachricht im Raum-Verlauf hat einen eigenen
  Slot (`view.slot_new()`, schreiben mit `with view.slot(id):`) – nötig, weil mehrere KIs gleichzeitig
  schreiben; in `chatview.py` deshalb nie fest „end-1c“, sondern `self._at` als Einfügeposition verwenden.
  Antworten der eigenen KI werden lokal gezeichnet (Zuordnung über `nonce`), fremde aus den `patch`-Ereignissen.
  Sicherheit: Anfragen sind HMAC-signiert (Schlüssel nie im Netz); Werkzeugaktionen der eigenen KI im Raum
  immer mit Bestätigung (`confirm_actions=True` fest); an andere gehen nur Werkzeug-Kopfzeilen, nie Ergebnisse.
  Einstellungen: `room_name`, `room_ai`, `room_tools`, `client_id`, `room_hosted`/`room_last` (Wiederbeitritt).
- **Skalierung:** Fenster- und Seitenleistenbreiten mit `self.ui_scale` multiplizieren (Windows-Skalierung).
- Kein `scrolledtext` verwenden (nativer Scrollbalken lässt sich nicht einfärben) → `scrolled_text()`-Helfer.

## Testen
- Schnellcheck (im Ordner `app/`): `py -m py_compile modell_studio.py chatview.py markdown_lite.py themes.py chats.py backend.py
  agent.py office.py projects.py documents.py pdftext.py filedrop.py web.py room.py roomui.py widgets.py llamacpp.py images.py dialogs.py`.
- llama.cpp-Download ohne Oberfläche: `llamacpp.SERVER_DIR` auf einen Testordner umbiegen (als Windows-Pfad –
  ein Git-Bash-Pfad wie `/c/...` landet sonst unter `C:\c\...`), mit der kleinen CPU-Zip (~20 MB) einen Plan bauen
  und `llamacpp.install(plan, fortschritt, threading.Event())` aufrufen; `_pick_assets(release, hersteller, cuda)`
  lässt sich für AMD/Intel/ohne GPU ohne Download prüfen.
- WebFusedAgentMod ohne Modelle: zwei Prozesse (Gastgeber/Gast) mit `roomui.run_conversation` als Fake und
  `RoomMode.show_invite` umgebogen (schreibt den Code in eine Datei, die der Gast liest); Raum mit
  `app.room.create("Testraum", "127.0.0.1", 0, schlüssel)`, Beitritt mit `app.room.join(code)`.
- Echter Test: App starten, Modell laden, chatten, Einstellungen öffnen, Design wechseln.
- Ohne Modell: `ms.run_conversation` durch eine Fake-Funktion ersetzen (Ereignisse wie oben senden und neue
  Nachrichten an `messages` anhängen), `ms.thinking_title` ersetzen, `app.server.is_running = lambda: True`,
  `app.server.model = Path("x.gguf")`, `app.server.port = 1`. Dabei `ms.CONFIG_FILE` und `chats.CHAT_DIR` auf
  Testpfade umbiegen, damit `config.json` und echte Chats unberührt bleiben. Screenshots unter Windows per
  `PrintWindow` (ctypes) oder einfacher per PowerShell `Graphics.CopyFromScreen` über `GetWindowRect(wm_frame())`; unter Linux `python3-tk` + `Xvfb` + ImageMagick `import -window root`.
- Office-Dateien prüfen: per COM öffnen (falls Word/Excel/PowerPoint installiert sind) und als PDF exportieren.
- Agent-Werkzeuge direkt: `Agent(lambda t, x: "yes", files=True, commands=True, workspace=…).execute(name, json)`.

## Bisheriger Verlauf (Oktober 2026)
1. Modellordner nachträglich wählbar (Dialog „Modellordner…“ mit Hinzufügen/Ändern/Entfernen).
2. Designs: Dunkel (Grau/Orange), Hell (Weiß/Violett), Windows-Standard – Auswahl wird gespeichert.
3. Umbau zur klassischen Chat-Oberfläche: links nur gespeicherte Chats, alles andere im Zahnrad-Fenster
   „Einstellungen“; Modell-Auswahl oben rechts im Chat.
4. (10.10.2026) Neuer Chat (`chatview.py`): Sprechblasen rechts in Designfarbe, Markdown links, Denkprozess
   zugeklappt mit vom Modell erzeugter Überschrift, Werkzeug-Blöcke, flüssige Ausgabe, weiches Scrollen.
   Agent: freigegebene Ordner, viele Datei-Werkzeuge, Word/Excel/PowerPoint (`office.py`), Projektvorlagen
   (`projects.py`), Python-Ausführung, eigener Bestätigungsdialog. Auf dem echten PC getestet (UI per Screenshots, Office per COM,
   Werkzeug-Aufrufe mit Qwen3.6-35B-A3B über llama-server).
5. (10./11.10.2026) WebFusedAgentMod: Gastgeber-Raum, Gruppenchat mit @Name/@alle,
   KI-Kette („Schwarm“) mit Bremse, eigene Werkzeuge je Rechner mit Pflicht-Bestätigung, Tailscale empfohlen.
   Getestet mit zwei Instanzen auf einem PC (simulierte KIs), noch nicht zwischen zwei echten Rechnern.
6. (11.10.2026) Flüssigere Übergänge: gleitender Modus-Umschalter, Ebenen per `tkraise`
   statt Neuanordnen, Fenster/Dialoge blenden ein (`theme.present`), Raum-Seitenleiste aktualisiert ohne
   Neuaufbau. Fix: weiße Titelleiste des Hauptfensters (Farbe wurde gesetzt, bevor es den Fensterrahmen gab).
7. (12.10.2026) Code in versteckten Ordner `app/` verschoben, Start nur noch über
   `ModellStudio.pyw` (die .bat entfällt).
   Chats löschen ohne Rückfrage.
8. (12.10.2026) Unabhängig von LM Studio: eigener llama.cpp-Download (`llamacpp.py`,
   Ordner `server/`), LM-Studio-Backend-Suche entfernt. Getestet: Grafikkarten-Erkennung, Auswahl für
   NVIDIA/AMD/Intel/CPU, Installation der CPU-Version in einen Testordner, Startparameter der neuen
   `llama-server.exe`. Die CUDA-Version (550 MB) und die Oberfläche noch nicht. Danach auch der LM-Studio-Modellordner entfernt → eigener
   Ordner `modelle/`. Download der CUDA-Version über die Oberfläche später erfolgreich ausprobiert.
9. (12.10.2026) Bildfunktion: Bilder anhängen (Knopf, Drag & Drop, Strg+V auch für
   Screenshots), Vorschaubild im Chat, Bilder werden zum Chat kopiert, mmproj wird automatisch mitgeladen.
   Getestet im echten Fenster mit Qwen3.6-35B-A3B Q4_K_M: Bild erkannt, Screenshot eingefügt, Folgefrage
   zum früheren Bild richtig beantwortet, gespeicherter Chat zeigt Vorschau wieder.
10. (12.10.2026) Fix: Drag & Drop ließ das Programm abstürzen (jede Datei, schon vor der Bildfunktion). Die
    Fensterprozedur in `filedrop.py` rief `window.after_idle` auf → „Fatal Python error: PyEval_RestoreThread“.
    **Regel: aus einer ctypes-Fensterprozedur nie Tkinter aufrufen** – sie legt die Pfade nur in eine Liste,
    `poll()` (alle 100 ms per `after`) übergibt sie an den Callback.
11. (12.10.2026) Verwaiste Server verhindert: Nach dem Absturz lief llama-server mit 14 GB weiter. Jetzt
    hängt `LlamaServer.start` den Prozess an ein Job-Objekt mit KILL_ON_JOB_CLOSE (`backend._end_with_app`).
    Getestet: Programmprozess hart beendet → llama-server (mit geladenem Modell) sofort weg; Gegenprobe ohne
    Job → Kindprozess überlebt (der `py`/`pyw`-Starter beendet Kinder also nicht von selbst).
12. (12.10.2026) Meldungen im Design: `dialogs.py` ersetzt alle 33 `messagebox`-Aufrufe (modell_studio.py, roomui.py).
    Getestet mit Screenshots in Dunkel/Hell/Windows-Standard, auch über dem Einstellungsfenster.

## Offen / bekannte Grenzen
- Der Chat-Verlauf wird nie gekürzt → sehr lange Chats laufen irgendwann in das Kontextlimit
  (Fehler vom Server).
- Markdown-Anzeige ist bewusst einfach: keine verschachtelten Zitate, keine Formeln (LaTeX), Tabellenzellen
  ohne Fettdruck; Code-Hervorhebung zeilenweise (mehrzeilige Strings/Kommentare werden nicht erkannt).
- PDF: Lesen geht (`pdftext.py`), aber nicht bei eingescannten (nur Bilder) oder verschlüsselten PDFs;
  Erzeugen nur über Word → PDF per PowerShell/COM.
- Bilder: nicht im Gruppenchat (WebFusedAgentMod); der Agent kann Bilddateien nicht selbst ansehen; WebP/TIFF/
  HEIC werden nicht angenommen (llama.cpp liest sie nicht); EXIF-Drehung von Handyfotos wird nicht beachtet.
- Drag & Drop ist mit einem nachgestellten Drop getestet (echtes HDROP per `GlobalAlloc`, aus einem anderen
  Thread desselben Prozesses mit `PostMessageW(root.winfo_id(), WM_DROPFILES, …)` – so kommt auch ein echter
  Drop an, und damit ließ sich der Absturz nachstellen); echtes Ziehen aus dem Explorer nicht automatisiert geprüft.
- WebFusedAgentMod: noch nicht über zwei echte Rechner/Tailscale getestet und nicht mit echten Modellen im
  Raum. Raum-Verlauf wird nur beim Gastgeber gespeichert; Denkprozess fremder KIs wird nicht übertragen.
  Ohne VPN ist der Verkehr unverschlüsselt (nur signiert) – im Heimnetz okay, sonst Tailscale/WireGuard nutzen.