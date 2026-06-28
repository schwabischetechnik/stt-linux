#!/usr/bin/env bash
# stt-ptt — Installer
# Legt venv an, installiert Abhängigkeiten und kopiert Beispiel-Config.

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

echo "==> Installationsverzeichnis: $INSTALL_DIR"
mkdir -p "$INSTALL_DIR" "$CONFIG_DIR" "$BIN_DIR"
cp "$SCRIPT_DIR/stt-ptt.py" "$INSTALL_DIR/stt-ptt.py"
chmod +x "$INSTALL_DIR/stt-ptt.py"

if [[ ! -d "$INSTALL_DIR/venv" ]]; then
    echo "==> venv erstellen"
    python3 -m venv "$INSTALL_DIR/venv"
fi

echo "==> Python-Abhängigkeiten installieren"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip >/dev/null
"$INSTALL_DIR/venv/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"

if [[ ! -f "$CONFIG_DIR/config.ini" ]]; then
    echo "==> Beispiel-Config nach $CONFIG_DIR/config.ini kopieren"
    cp "$SCRIPT_DIR/config.example.ini" "$CONFIG_DIR/config.ini"
    echo "    Bitte anpassen (Backend wählen, ggf. API-Key/Endpoint setzen)."
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
echo
echo "Für backend=whisper_local zusätzlich:"
echo "  $INSTALL_DIR/venv/bin/pip install faster-whisper"
