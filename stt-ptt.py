#!/usr/bin/env python3
"""
stt-ptt — Push-to-Talk Diktat fuer Linux / X11.

Halte die konfigurierte Taste, um vom Standard-Mikrofon aufzunehmen.
Beim Loslassen wird das Audio transkribiert (ElevenLabs Scribe,
Whisper auf einem entfernten Server oder Whisper lokal) und der Text
per xdotool ins fokussierte Fenster getippt.

Projekt: https://github.com/schwabischetechnik/stt-linux
Basiert auf: https://github.com/ewigerdaniel/elevenlabs-ptt (MIT)
Lizenz: MIT
"""
from __future__ import annotations

import argparse
import configparser
import io
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import requests
import sounddevice as sd
import soundfile as sf
from pynput import keyboard


__version__ = "0.1.0"

_NEW_CONFIG_PATH = Path.home() / ".config" / "stt-ptt" / "config.ini"
_LEGACY_CONFIG_PATH = Path.home() / ".config" / "elevenlabs-stt" / "config.ini"
DEFAULT_CONFIG_PATH = _NEW_CONFIG_PATH if _NEW_CONFIG_PATH.exists() or not _LEGACY_CONFIG_PATH.exists() else _LEGACY_CONFIG_PATH
ELEVENLABS_API_URL = "https://api.elevenlabs.io/v1/speech-to-text"
LOG = logging.getLogger("stt-ptt")

VALID_BACKENDS = {"elevenlabs", "whisper_remote", "whisper_local"}


def notify(summary: str, body: str = "", urgency: str = "normal") -> None:
    if urgency != "critical":
        return
    if not shutil.which("notify-send"):
        return
    try:
        subprocess.run(
            ["notify-send", "-u", urgency, "-a", "stt-ptt",
             "-i", "audio-input-microphone", summary, body],
            check=False, timeout=3,
        )
    except Exception:
        pass


def check_x11_session() -> None:
    session = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if session == "wayland":
        msg = ("Wayland-Session erkannt. xdotool funktioniert nur unter X11. "
               "Logge dich in eine X11-Session ein (am Login-Bildschirm waehlbar).")
        LOG.error(msg)
        notify("STT-PTT", msg, "critical")
        sys.exit(2)


def load_config(path: Path) -> dict:
    if not path.exists():
        LOG.error("Config nicht gefunden: %s", path)
        notify("STT-PTT", f"Config fehlt: {path}", "critical")
        sys.exit(1)
    cp = configparser.ConfigParser()
    cp.read(path)

    g = cp["general"] if cp.has_section("general") else {}
    backend = (g.get("backend", "elevenlabs") if g else "elevenlabs").strip().lower()
    if backend not in VALID_BACKENDS:
        LOG.error("Ungueltiges backend=%s (erlaubt: %s)", backend, ", ".join(VALID_BACKENDS))
        notify("STT-PTT", f"Ungueltiges backend: {backend}", "critical")
        sys.exit(1)

    # Shared settings: prefer [general], fall back to [elevenlabs] for back-compat.
    el = cp["elevenlabs"] if cp.has_section("elevenlabs") else {}

    def shared(key: str, default: str) -> str:
        if g and key in g:
            return g.get(key)
        if el and key in el:
            return el.get(key)
        return default

    cfg = {
        "backend": backend,
        "hotkey": shared("hotkey", "pause"),
        "language_code": (shared("language_code", "").strip() or None),
        "sample_rate": int(shared("sample_rate", "16000")),
        "min_seconds": float(shared("min_recording_seconds", "0.3")),
        "max_seconds": float(shared("max_recording_seconds", "120")),
        "type_delay_ms": int(shared("type_delay_ms", "8")),
    }

    if backend == "elevenlabs":
        api_key = el.get("api_key", "").strip() if el else ""
        if not api_key or api_key.startswith("YOUR_"):
            notify("STT-PTT", f"api_key fehlt in {path}", "critical")
            LOG.error("api_key fehlt in %s", path)
            sys.exit(1)
        cfg["api_key"] = api_key
        cfg["model_id"] = el.get("model_id", "scribe_v2")
    elif backend == "whisper_remote":
        if not cp.has_section("whisper_remote"):
            LOG.error("Section [whisper_remote] fehlt in %s", path)
            notify("STT-PTT", "[whisper_remote] fehlt", "critical")
            sys.exit(1)
        w = cp["whisper_remote"]
        endpoint = w.get("endpoint", "").strip()
        if not endpoint:
            LOG.error("endpoint fehlt in [whisper_remote]")
            notify("STT-PTT", "endpoint fehlt", "critical")
            sys.exit(1)
        cfg["endpoint"] = endpoint.rstrip("/")
        cfg["model"] = w.get("model", "whisper-1")
        cfg["api_key"] = w.get("api_key", "").strip()
        cfg["timeout"] = float(w.get("timeout", "60"))
    elif backend == "whisper_local":
        w = cp["whisper_local"] if cp.has_section("whisper_local") else {}
        cfg["model"] = (w.get("model", "base") if w else "base")
        cfg["device"] = (w.get("device", "auto") if w else "auto")
        cfg["compute_type"] = (w.get("compute_type", "auto") if w else "auto")

    return cfg


class Recorder:
    def __init__(self, sample_rate: int, max_seconds: float):
        self.sample_rate = sample_rate
        self.max_frames = int(sample_rate * max_seconds)
        self.frames: list[np.ndarray] = []
        self.stream: sd.InputStream | None = None
        self.is_recording = False
        self.lock = threading.Lock()

    def _callback(self, indata, frames, time_info, status):
        if status:
            LOG.debug("stream status: %s", status)
        with self.lock:
            if not self.is_recording:
                return
            self.frames.append(indata.copy())
            total = sum(f.shape[0] for f in self.frames)
            if total >= self.max_frames:
                self.is_recording = False

    def start(self) -> bool:
        with self.lock:
            if self.is_recording:
                return False
            self.frames = []
            self.is_recording = True
        try:
            self.stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="int16",
                callback=self._callback,
            )
            self.stream.start()
            return True
        except Exception as e:
            LOG.exception("Aufnahme-Start fehlgeschlagen")
            notify("STT-PTT", f"Mikro-Fehler: {e}", "critical")
            with self.lock:
                self.is_recording = False
            return False

    def stop(self) -> np.ndarray | None:
        with self.lock:
            self.is_recording = False
            frames = self.frames
            self.frames = []
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        if not frames:
            return None
        return np.concatenate(frames, axis=0)


def _audio_to_wav_bytes(audio: np.ndarray, sample_rate: int) -> io.BytesIO:
    buf = io.BytesIO()
    sf.write(buf, audio, sample_rate, format="WAV", subtype="PCM_16")
    buf.seek(0)
    return buf


def transcribe_elevenlabs(audio: np.ndarray, sample_rate: int, cfg: dict) -> str | None:
    buf = _audio_to_wav_bytes(audio, sample_rate)
    data = {"model_id": cfg["model_id"]}
    if cfg["language_code"]:
        data["language_code"] = cfg["language_code"]
    files = {"file": ("audio.wav", buf, "audio/wav")}
    headers = {"xi-api-key": cfg["api_key"]}
    try:
        r = requests.post(ELEVENLABS_API_URL, headers=headers, data=data, files=files, timeout=60)
    except requests.RequestException as e:
        notify("STT-PTT", f"Netzwerkfehler: {e}", "critical")
        LOG.exception("API request failed")
        return None
    if r.status_code != 200:
        msg = f"HTTP {r.status_code}: {r.text[:200]}"
        notify("STT-PTT", msg, "critical")
        LOG.error("API error: %s", msg)
        return None
    try:
        payload = r.json()
    except ValueError:
        notify("STT-PTT", "Antwort ist kein JSON", "critical")
        return None
    return (payload.get("text") or "").strip()


def transcribe_whisper_remote(audio: np.ndarray, sample_rate: int, cfg: dict) -> str | None:
    """OpenAI-kompatibler /v1/audio/transcriptions Endpoint
    (faster-whisper-server, whisper.cpp server, LocalAI, OpenAI)."""
    buf = _audio_to_wav_bytes(audio, sample_rate)
    url = f"{cfg['endpoint']}/audio/transcriptions"
    data = {"model": cfg["model"], "response_format": "json"}
    if cfg["language_code"]:
        data["language"] = cfg["language_code"]
    files = {"file": ("audio.wav", buf, "audio/wav")}
    headers = {}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    try:
        r = requests.post(url, headers=headers, data=data, files=files, timeout=cfg["timeout"])
    except requests.RequestException as e:
        notify("STT-PTT", f"Netzwerkfehler: {e}", "critical")
        LOG.exception("Whisper-Remote request failed")
        return None
    if r.status_code != 200:
        msg = f"HTTP {r.status_code}: {r.text[:200]}"
        notify("STT-PTT", msg, "critical")
        LOG.error("Whisper-Remote error: %s", msg)
        return None
    try:
        payload = r.json()
    except ValueError:
        # Manche Server liefern bei response_format=text plain text
        return r.text.strip()
    return (payload.get("text") or "").strip()


_LOCAL_MODEL = None
_LOCAL_MODEL_LOCK = threading.Lock()


def _get_local_model(cfg: dict):
    global _LOCAL_MODEL
    with _LOCAL_MODEL_LOCK:
        if _LOCAL_MODEL is not None:
            return _LOCAL_MODEL
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            notify("STT-PTT",
                   "faster-whisper fehlt. Im venv: pip install faster-whisper",
                   "critical")
            LOG.error("faster-whisper nicht installiert")
            return None
        device = cfg["device"]
        compute_type = cfg["compute_type"]
        if device == "auto":
            device = "cuda"
            try:
                _LOCAL_MODEL = WhisperModel(cfg["model"], device=device,
                                            compute_type=("float16" if compute_type == "auto" else compute_type))
            except Exception:
                LOG.info("CUDA nicht verfuegbar, fallback auf CPU")
                device = "cpu"
                _LOCAL_MODEL = WhisperModel(cfg["model"], device=device,
                                            compute_type=("int8" if compute_type == "auto" else compute_type))
        else:
            ct = compute_type
            if ct == "auto":
                ct = "float16" if device == "cuda" else "int8"
            _LOCAL_MODEL = WhisperModel(cfg["model"], device=device, compute_type=ct)
        return _LOCAL_MODEL


def transcribe_whisper_local(audio: np.ndarray, sample_rate: int, cfg: dict) -> str | None:
    model = _get_local_model(cfg)
    if model is None:
        return None
    # faster-whisper akzeptiert float32 mono numpy array
    if audio.ndim > 1:
        audio = audio[:, 0]
    if audio.dtype != np.float32:
        audio = audio.astype(np.float32) / 32768.0
    try:
        segments, _info = model.transcribe(
            audio,
            language=cfg["language_code"],
            beam_size=1,
        )
        text = "".join(seg.text for seg in segments).strip()
        return text
    except Exception as e:
        notify("STT-PTT", f"Whisper-Local Fehler: {e}", "critical")
        LOG.exception("local transcription failed")
        return None


def transcribe(audio: np.ndarray, sample_rate: int, cfg: dict) -> str | None:
    backend = cfg["backend"]
    if backend == "elevenlabs":
        return transcribe_elevenlabs(audio, sample_rate, cfg)
    if backend == "whisper_remote":
        return transcribe_whisper_remote(audio, sample_rate, cfg)
    if backend == "whisper_local":
        return transcribe_whisper_local(audio, sample_rate, cfg)
    LOG.error("Unbekanntes backend: %s", backend)
    return None


def type_text(text: str, delay_ms: int) -> None:
    if not text:
        return
    if not shutil.which("xdotool"):
        notify("STT-PTT",
               "xdotool fehlt. Installiere mit: sudo apt install xdotool",
               "critical")
        return
    try:
        result = subprocess.run(
            ["xdotool", "type", "--clearmodifiers", "--delay", str(delay_ms), "--", text],
            check=False, timeout=30, capture_output=True, text=True,
        )
        if result.returncode != 0:
            err = result.stderr.strip() or f"exit {result.returncode}"
            notify("STT-PTT", f"xdotool-Fehler: {err}", "critical")
    except Exception as e:
        notify("STT-PTT", f"xdotool-Fehler: {e}", "critical")


def parse_hotkey(spec: str):
    """Resolve a config hotkey string to a pynput Key or KeyCode."""
    s = spec.strip().lower().replace("-", "_")
    if s.startswith("<") and s.endswith(">"):
        s = s[1:-1]
    aliases = {
        "rctrl": "ctrl_r", "right_ctrl": "ctrl_r",
        "lctrl": "ctrl_l", "left_ctrl": "ctrl_l",
        "ralt": "alt_r", "right_alt": "alt_r",
        "lalt": "alt_l", "left_alt": "alt_l",
        "rshift": "shift_r", "right_shift": "shift_r",
        "lshift": "shift_l", "left_shift": "shift_l",
    }
    s = aliases.get(s, s)
    if hasattr(keyboard.Key, s):
        return getattr(keyboard.Key, s)
    if len(s) == 1:
        return keyboard.KeyCode.from_char(s)
    raise ValueError(f"Unbekannter Hotkey: {spec}")


class App:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.hotkey = parse_hotkey(cfg["hotkey"])
        self.recorder = Recorder(cfg["sample_rate"], cfg["max_seconds"])
        self.key_held = False
        self.t_start = 0.0
        self.worker_lock = threading.Lock()

    def on_press(self, key):
        if key != self.hotkey or self.key_held:
            return
        self.key_held = True
        if self.recorder.start():
            self.t_start = time.monotonic()
            notify("Aufnahme laeuft", f"Hotkey: {self.cfg['hotkey']}", "low")

    def on_release(self, key):
        if key != self.hotkey or not self.key_held:
            return
        self.key_held = False
        duration = time.monotonic() - self.t_start
        audio = self.recorder.stop()
        if audio is None or duration < self.cfg["min_seconds"]:
            notify("Zu kurz", "Aufnahme verworfen", "low")
            return
        threading.Thread(target=self._process, args=(audio,), daemon=True).start()

    def _process(self, audio: np.ndarray) -> None:
        with self.worker_lock:
            notify("Transkribiere ...", "", "low")
            text = transcribe(audio, self.cfg["sample_rate"], self.cfg)
            if not text:
                notify("Kein Text erkannt", "", "normal")
                return
            type_text(text, self.cfg["type_delay_ms"])

    def run(self) -> None:
        notify("STT-PTT bereit",
               f"Halte [{self.cfg['hotkey']}] zum Diktieren", "low")
        model_label = self.cfg.get("model_id") or self.cfg.get("model") or "?"
        LOG.info("Listener gestartet, Backend=%s, Hotkey=%s, Modell=%s",
                 self.cfg["backend"], self.cfg["hotkey"], model_label)
        with keyboard.Listener(on_press=self.on_press, on_release=self.on_release) as listener:
            listener.join()


def cmd_list_devices() -> int:
    print("Audio-Eingangsgeraete:")
    for i, dev in enumerate(sd.query_devices()):
        if dev.get("max_input_channels", 0) > 0:
            default = " (default)" if i == sd.default.device[0] else ""
            print(f"  [{i}] {dev['name']}  {int(dev['default_samplerate'])} Hz"
                  f"  in={dev['max_input_channels']}{default}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="stt-ptt",
        description="Push-to-Talk dictation for Linux X11, powered by ElevenLabs Scribe.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--config", "-c", type=Path, default=DEFAULT_CONFIG_PATH,
                   help=f"Pfad zur Config-Datei (Default: {DEFAULT_CONFIG_PATH})")
    p.add_argument("--list-devices", action="store_true",
                   help="Audio-Eingangsgeraete auflisten und beenden")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=os.environ.get("ELEVENLABS_PTT_LOG", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.list_devices:
        return cmd_list_devices()

    check_x11_session()
    cfg = load_config(args.config)

    def _term(*_):
        LOG.info("shutting down")
        os._exit(0)

    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)

    App(cfg).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
