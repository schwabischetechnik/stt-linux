# stt-linux (`stt-ptt`)

Push-to-Talk-Diktat für Linux unter X11. Halte eine Taste, sprich, lass los —
der erkannte Text wird ins fokussierte Fenster getippt.

Drei Transkriptions-Backends zur Auswahl:

| Backend          | Wo läuft die Erkennung?          | Vorteile                                  | Nachteile                              |
|------------------|----------------------------------|-------------------------------------------|----------------------------------------|
| `elevenlabs`     | Cloud (ElevenLabs Scribe)        | sehr hohe Qualität, kein Setup            | kostenpflichtig, sendet Audio extern   |
| `whisper_remote` | Server im LAN oder Internet      | GPU-Power eines anderen Rechners nutzbar, OpenAI-kompatibel | Server muss eingerichtet werden |
| `whisper_local`  | Im Prozess (faster-whisper)      | offline, datenschutzfreundlich            | CPU/GPU-Last lokal, größere Modelle benötigen RAM/VRAM |

## Voraussetzungen

- **X11-Session** (Wayland wird von `xdotool` nicht unterstützt)
- Debian/Ubuntu-System (auf anderen Distros analog)
- Pakete:
  ```bash
  sudo apt install xdotool libportaudio2 python3-venv
  ```

## Installation

```bash
git clone https://github.com/schwabischetechnik/stt-linux.git
cd stt-linux
./install.sh
```

Der Installer:

1. legt ein venv unter `~/.local/share/stt-ptt/venv` an,
2. installiert die Python-Abhängigkeiten,
3. kopiert die Beispielkonfiguration nach `~/.config/stt-ptt/config.ini`,
4. legt einen Launcher `~/.local/bin/stt-ptt` an.

Für das **lokale Whisper-Backend** zusätzlich:

```bash
~/.local/share/stt-ptt/venv/bin/pip install faster-whisper
```

## Konfiguration

Bearbeite `~/.config/stt-ptt/config.ini`. Wähle in `[general]` ein Backend
und fülle den entsprechenden Abschnitt aus.

### ElevenLabs

```ini
[general]
backend = elevenlabs

[elevenlabs]
api_key = sk_...
model_id = scribe_v2
```

### Whisper auf einem entfernten (oder lokalen) Server

Funktioniert mit allen OpenAI-kompatiblen STT-Endpoints:
[faster-whisper-server](https://github.com/fedirz/faster-whisper-server),
[whisper.cpp Server](https://github.com/ggerganov/whisper.cpp/tree/master/examples/server),
[LocalAI](https://localai.io/) oder der originalen OpenAI-API.

```ini
[general]
backend = whisper_remote

[whisper_remote]
endpoint = http://192.168.1.50:8000/v1
model    = Systran/faster-whisper-large-v3
# api_key = ...   # falls Server Auth verlangt
timeout  = 60
```

**Server starten (Docker, Beispiel mit GPU):**

```bash
docker run --rm -d --gpus all -p 8000:8000 \
    -v ~/.cache/huggingface:/root/.cache/huggingface \
    --name faster-whisper-server \
    fedirz/faster-whisper-server:latest-cuda
```

CPU-Variante: Image-Tag `latest-cpu`, `--gpus all` weglassen.

### Whisper lokal im Prozess

```ini
[general]
backend = whisper_local

[whisper_local]
model = base
device = auto
compute_type = auto
```

Beim ersten Start wird das Modell von Hugging Face heruntergeladen
(`~/.cache/huggingface/`). Empfehlung:

- `tiny` / `base` — schnell auf CPU, mäßige Qualität
- `small` — guter Kompromiss für CPU
- `medium` / `large-v3` — beste Qualität, GPU empfohlen

## Bedienung

Starten:

```bash
stt-ptt
```

Hotkey halten → sprechen → loslassen → Text erscheint im aktiven Fenster.

Audiogeräte auflisten:

```bash
stt-ptt --list-devices
```

Anderes Config-File:

```bash
stt-ptt --config /pfad/zur/config.ini
```

## Autostart

Für die X11-Session, z.B. mit `~/.config/autostart/stt-ptt.desktop`:

```ini
[Desktop Entry]
Type=Application
Name=stt-ptt
Exec=/home/DEINUSER/.local/bin/stt-ptt
X-GNOME-Autostart-enabled=true
```

## Lizenz & Credits

MIT — siehe [LICENSE](LICENSE).

Basiert auf [`elevenlabs-ptt`](https://github.com/ewigerdaniel/elevenlabs-ptt)
von ewigerdaniel und wurde um die Whisper-Backends erweitert.
