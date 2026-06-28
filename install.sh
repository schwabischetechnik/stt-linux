#!/usr/bin/env bash
# stt-ptt — Installer
# Legt venv an, installiert Abhängigkeiten und kopiert Beispiel-Config.
#
# Nicht-interaktiv steuerbar per Umgebungsvariable:
#   BACKEND=elevenlabs|whisper_remote|whisper_local ./install.sh
# (sonst wird interaktiv gefragt)

set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-$HOME/.local/share/stt-ptt}"
CONFIG_DIR="$HOME/.config/stt-ptt"
BIN_DIR="$HOME/.local/bin"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Systempakete prüfen (xdotool, portaudio, python3-venv)"
missing=()
command -v xdotool >/dev/null || missing+=("xdotool")
dpkg -s libportaudio2 >/dev/null 2>&1 || missing+=("libportaudio2")
command -v python3 >/dev/null || missing+=("python3")
python3 -c "import venv" 2>/dev/null || missing+=("python3-venv")
if ((${#missing[@]})); then
    echo "Fehlende Pakete: ${missing[*]}"
    echo "Installation z.B. mit:  sudo apt install ${missing[*]}"
    exit 1
fi

# --- Backend-Auswahl ---------------------------------------------------
BACKEND="${BACKEND:-}"
if [[ -z "$BACKEND" ]]; then
    if [[ -t 0 ]]; then
        echo
        echo "Welches Transkriptions-Backend willst du nutzen?"
        echo "  1) elevenlabs     — Cloud, benötigt API-Key (kein Whisper-Download)"
        echo "  2) whisper_remote — OpenAI-kompatibler Server im LAN/Internet (kein lokaler Whisper-Download)"
        echo "  3) whisper_local  — Whisper läuft auf diesem Rechner (~1-2 GB Pakete + Modell)"
        echo
        read -r -p "Auswahl [1/2/3] (Default 1): " choice
        case "${choice:-1}" in
            1) BACKEND="elevenlabs" ;;
            2) BACKEND="whisper_remote" ;;
            3) BACKEND="whisper_local" ;;
            *) echo "Ungültige Auswahl."; exit 1 ;;
        esac
    else
        echo "Kein TTY — setze BACKEND=elevenlabs|whisper_remote|whisper_local als Umgebungsvariable."
        exit 1
    fi
fi
echo "==> Backend: $BACKEND"

echo "==> Installationsverzeichnis: $INSTALL_DIR"
mkdir -p "$INSTALL_DIR" "$CONFIG_DIR" "$BIN_DIR"
cp "$SCRIPT_DIR/stt-ptt.py" "$INSTALL_DIR/stt-ptt.py"
chmod +x "$INSTALL_DIR/stt-ptt.py"

if [[ ! -d "$INSTALL_DIR/venv" ]]; then
    echo "==> venv erstellen"
    python3 -m venv "$INSTALL_DIR/venv"
fi

echo "==> Basis-Abhängigkeiten installieren"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip >/dev/null
"$INSTALL_DIR/venv/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"

if [[ "$BACKEND" == "whisper_local" ]]; then
    echo "==> faster-whisper installieren (kann einige Minuten dauern)"
    "$INSTALL_DIR/venv/bin/pip" install faster-whisper
fi

# --- Config schreiben/anpassen ----------------------------------------
if [[ ! -f "$CONFIG_DIR/config.ini" ]]; then
    echo "==> Beispiel-Config nach $CONFIG_DIR/config.ini kopieren"
    cp "$SCRIPT_DIR/config.example.ini" "$CONFIG_DIR/config.ini"
    # backend = ... im [general]-Block auf gewähltes Backend setzen
    sed -i -E "0,/^backend\s*=.*/s//backend = $BACKEND/" "$CONFIG_DIR/config.ini"
    echo "    backend = $BACKEND wurde voreingestellt."
    case "$BACKEND" in
        elevenlabs)
            echo "    Trage deinen API-Key unter [elevenlabs] api_key ein."
            ;;
        whisper_remote)
            echo "    Setze unter [whisper_remote] endpoint und model."
            ;;
        whisper_local)
            echo "    Modell-Auswahl unter [whisper_local] (tiny/base/small/medium/large-v3)."
            echo "    Modell wird beim ersten Start nach ~/.cache/huggingface/ geladen."
            ;;
    esac
else
    echo "==> Bestehende Config bleibt unverändert: $CONFIG_DIR/config.ini"
fi

LAUNCHER="$BIN_DIR/stt-ptt"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
exec "$INSTALL_DIR/venv/bin/python" "$INSTALL_DIR/stt-ptt.py" "\$@"
EOF
chmod +x "$LAUNCHER"

echo
echo "Fertig. Starte mit:  stt-ptt"
echo "(Stelle sicher, dass $BIN_DIR in deinem PATH liegt.)"
