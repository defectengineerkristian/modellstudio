"""Eigener llama.cpp-Server: findet installierte Versionen im Ordner server/ (neben ModellStudio.pyw),
erkennt die Grafikkarte und lädt die passende llama-server-Version direkt von GitHub
(github.com/ggml-org/llama.cpp). Unabhängig von LM Studio."""

import json
import platform
import re
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
RELEASES_URL = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=10"
_FOLDER_RE = re.compile(r"llama-b(\d+)-bin-win-(.+)-(?:x64|arm64)$")  # Ordnername = Name der Zip-Datei
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ---------------------------------------------------------------- Installierte Server

def _kind_rank(kind: str) -> int:
    for rank, key in enumerate(("cuda", "rocm", "vulkan")):
        if key in kind:
            return rank
    return 4 if "cpu" in kind else 3


def _kind_label(kind: str) -> str:
    name, _, ver = kind.partition("-")
    name = {"cuda": "CUDA", "rocm": "ROCm", "vulkan": "Vulkan", "cpu": "CPU", "sycl": "SYCL",
            "openvino": "OpenVINO"}.get(name, name)
    return f"{name} {ver}" if ver else name


def find_llama_servers() -> list[dict]:
    """Alle llama-server.exe in server/*/. Beste (CUDA > ROCm > Vulkan > Sonstige > CPU, neueste) zuerst."""
    found = []
    if SERVER_DIR.is_dir():
        for exe in SERVER_DIR.glob("*/llama-server.exe"):
            folder = exe.parent.name
            if folder.endswith((".part", ".alt")):
                continue
            m = _FOLDER_RE.match(folder)
            if m:  # heruntergeladene Version
                build, kind = int(m.group(1)), m.group(2)
                label = f"b{build} · {_kind_label(kind)}"
            else:  # von Hand in server/ abgelegt
                build, kind, label = 0, folder.lower(), folder
            found.append({"label": label, "exe": str(exe), "_sort": (_kind_rank(kind), -build)})
    found.sort(key=lambda s: s["_sort"])
    for s in found:
        s.pop("_sort")
    return found


# ---------------------------------------------------------------- Grafikkarte erkennen

def _run(args: list[str]) -> str:
    try:
        return subprocess.run(args, capture_output=True, encoding="oem", errors="replace", timeout=20,
                              creationflags=_NO_WINDOW).stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


def detect_gpu() -> tuple[str, str, tuple[int, int] | None]:
    """(Hersteller „nvidia“/„amd“/„intel“/„“, Name, höchste vom NVIDIA-Treiber unterstützte CUDA-Version)."""
    names = [n.strip() for n in _run(["powershell", "-NoProfile", "-Command",
                                      "(Get-CimInstance Win32_VideoController).Name"]).splitlines() if n.strip()]
    cuda = None
    # Ältere Treiber schreiben „CUDA Version: 12.8“, neuere „CUDA UMD Version: 13.4“
    m = re.search(r"CUDA (?:UMD )?Version:\s*(\d+)\.(\d+)", _run(["nvidia-smi"]))
    if m:
        cuda = (int(m.group(1)), int(m.group(2)))
    # NVIDIA vor AMD vor Intel: so gewinnt die eigene Grafikkarte gegen die im Prozessor eingebaute
    for vendor, keys in (("nvidia", ("nvidia",)), ("amd", ("amd", "radeon")), ("intel", ("intel",))):
        for name in names:
            if any(k in name.lower() for k in keys):
                return vendor, name, cuda
    return "", (names[0] if names else "unbekannt"), cuda


# ---------------------------------------------------------------- Passende Version wählen

def _pick_assets(release: dict, vendor: str, cuda: tuple[int, int] | None) -> tuple[str, list[dict]]:
    """Wählt die Zip-Dateien eines Releases: CUDA (NVIDIA, höchste vom Treiber unterstützte Version),
    sonst Vulkan (AMD/Intel, auch NVIDIA ohne passendes CUDA), sonst CPU."""
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
    tag = release["tag_name"]
    assets = {a["name"]: a for a in release.get("assets", [])}

    def main(kind):
        return assets.get(f"llama-{tag}-bin-win-{kind}-{arch}.zip")

    if vendor == "nvidia":
        pat = re.compile(rf"llama-{re.escape(tag)}-bin-win-cuda-(\d+)\.(\d+)-{arch}\.zip$")
        versions = sorted((int(m.group(1)), int(m.group(2))) for n in assets if (m := pat.match(n)))
        # Ohne nvidia-smi ist die Treiberversion unbekannt → die älteste CUDA-Version läuft am ehesten
        usable = [v for v in versions if v <= cuda] if cuda else versions[:1]
        if usable:
            ver = "%d.%d" % usable[-1]
            runtime = assets.get(f"cudart-llama-bin-win-cuda-{ver}-{arch}.zip")
            return f"CUDA {ver}", [main(f"cuda-{ver}")] + ([runtime] if runtime else [])
    if vendor and main("vulkan"):
        return "Vulkan", [main("vulkan")]
    if main("cpu"):
        return "CPU", [main("cpu")]
    return "", []


def plan() -> dict:
    """Sucht das neueste llama.cpp-Release mit passender Windows-Version (Netzwerk – im Thread aufrufen).
    Ergebnis: tag, kind, gpu, assets [(name, url, größe)], size, target (Ordner), installed (schon vorhanden?)."""
    vendor, gpu, cuda = detect_gpu()
    req = urllib.request.Request(RELEASES_URL, headers={"User-Agent": "Modell-Studio",
                                                        "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        releases = json.load(r)
    # Die fertigen Builds heißen „b12345“ (GitHub führt sie als Vorabversion); andere Releases überspringen
    for rel in releases:
        if rel.get("draft") or not re.fullmatch(r"b\d+", rel.get("tag_name", "")):
            continue
        kind, assets = _pick_assets(rel, vendor, cuda)
        if assets:
            target = SERVER_DIR / assets[0]["name"].removesuffix(".zip")
            return {"tag": rel["tag_name"], "kind": kind, "gpu": gpu,
                    "assets": [(a["name"], a["browser_download_url"], a["size"]) for a in assets],
                    "size": sum(a["size"] for a in assets), "target": target,
                    "installed": (target / "llama-server.exe").is_file()}
    raise RuntimeError("Auf GitHub wurde keine passende llama.cpp-Version für Windows gefunden.")


# ---------------------------------------------------------------- Herunterladen und entpacken

def install(p: dict, progress, cancel) -> Path:
    """Lädt die Zip-Dateien aus plan() herunter und entpackt sie nach server/<Version>/.
    progress(erledigt, gesamt) in Bytes; cancel = threading.Event. Gibt den Pfad zur llama-server.exe zurück."""
    SERVER_DIR.mkdir(exist_ok=True)
    for left in [*SERVER_DIR.glob("*.part"), *SERVER_DIR.glob("*.alt")]:  # Reste abgebrochener Vorgänge
        shutil.rmtree(left, ignore_errors=True)
    target: Path = p["target"]
    tmp = target.with_name(target.name + ".part")
    tmp.mkdir()
    try:
        done = 0
        for name, url, _size in p["assets"]:
            zpath = tmp / name
            req = urllib.request.Request(url, headers={"User-Agent": "Modell-Studio"})
            with urllib.request.urlopen(req, timeout=60) as r, open(zpath, "wb") as f:
                while chunk := r.read(1024 * 1024):
                    if cancel.is_set():
                        raise RuntimeError("abgebrochen")
                    f.write(chunk)
                    done += len(chunk)
                    progress(done, p["size"])
            with zipfile.ZipFile(zpath) as z:
                z.extractall(tmp)
            zpath.unlink()
        if not (tmp / "llama-server.exe").is_file():
            raise RuntimeError("In der heruntergeladenen Datei fehlt llama-server.exe.")
        if target.exists():
            shutil.rmtree(target)
        tmp.rename(target)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    _remove_older(target)
    return target / "llama-server.exe"


def _remove_older(target: Path):
    """Entfernt ältere heruntergeladene Versionen derselben Art (CUDA, Vulkan, …). Eine gerade laufende
    Version lässt sich nicht umbenennen und bleibt bis zum nächsten Download liegen."""
    new = _FOLDER_RE.match(target.name)
    family = new.group(2).split("-")[0] if new else None
    for old in SERVER_DIR.iterdir():
        m = _FOLDER_RE.match(old.name)
        if old == target or not m or m.group(2).split("-")[0] != family:
            continue
        try:
            moved = old.rename(old.with_name(old.name + ".alt"))
        except OSError:
            continue
        shutil.rmtree(moved, ignore_errors=True)
