#!/usr/bin/env python3
# =============================================================================
# implant_monitor.py — ESP32 Audio Implant Monitor (Python Client)
#
# Connects to an ESP32-S3 Audio Implant via Wi-Fi, receives raw PCM audio,
# plays it back in real-time, transcribes speech with Vosk (offline STT),
# and logs everything to a timestamped text file.
#
# Audio specs: Raw PCM, 16-bit signed LE, 16 kHz, Mono
# Protocol:    HTTP GET on port 81 — GET /stream?token=root
#
# Dependencies: pip install -r requirements.txt
#               + Download a Vosk model from https://alphacephei.com/vosk/models
# =============================================================================

import socket
import struct
import threading
import queue
import time
import json
import os
import sys
import logging
from datetime import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd

try:
    import customtkinter as ctk
except ImportError:
    ctk = None
    print("[WARN] customtkinter not installed. GUI will not be available.")

try:
    from vosk import Model, KaldiRecognizer, SetLogLevel
except ImportError:
    Model = None
    KaldiRecognizer = None
    SetLogLevel = None
    print("[WARN] vosk not installed. Speech-to-text will be disabled.")

# =============================================================================
# Constants
# =============================================================================
SAMPLE_RATE = 16000          # Hz
SAMPLE_WIDTH = 2             # bytes (16-bit)
CHANNELS = 1                 # mono
RECV_CHUNK_SIZE = 4096       # bytes per socket recv()
AUDIO_BLOCK_SIZE = 1024      # samples per sounddevice callback block
RECONNECT_DELAY = 3.0        # seconds between reconnection attempts
DEFAULT_IP = "192.168.4.1"
DEFAULT_PORT = 81
DEFAULT_TOKEN = "root"

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ImplantMonitor")


# =============================================================================
# TranscriptLogger — saves transcription text to a timestamped .txt file
# =============================================================================
class TranscriptLogger:
    """Appends transcription lines to a timestamped log file."""

    def __init__(self, output_dir: str = "."):
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._filepath = self._output_dir / f"implant_transcript_{ts}.txt"
        self._file = open(self._filepath, "a", encoding="utf-8", buffering=1)
        log.info("Transcript file: %s", self._filepath)

    @property
    def filepath(self) -> Path:
        return self._filepath

    def write(self, text: str):
        """Write a line of transcription with timestamp."""
        if not text.strip():
            return
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {text.strip()}\n"
        self._file.write(line)

    def close(self):
        if self._file and not self._file.closed:
            self._file.close()


# =============================================================================
# AudioStreamReceiver — connects to ESP32 via raw TCP, reads PCM stream
# =============================================================================
class AudioStreamReceiver(threading.Thread):
    """
    Connects to the ESP32 HTTP stream endpoint via raw socket,
    reads binary PCM data, aligns to 2-byte boundaries, and
    distributes chunks to audio and STT queues.
    """

    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        audio_queue: queue.Queue,
        stt_queue: queue.Queue,
        status_callback=None,
    ):
        super().__init__(daemon=True, name="StreamReceiver")
        self.host = host
        self.port = port
        self.token = token
        self._audio_q = audio_queue
        self._stt_q = stt_queue
        self._status_cb = status_callback
        self._running = threading.Event()
        self._stop_event = threading.Event()

    def stop(self):
        """Signal the thread to stop."""
        self._stop_event.set()

    @property
    def is_streaming(self) -> bool:
        return self._running.is_set()

    def _set_status(self, msg: str):
        log.info(msg)
        if self._status_cb:
            self._status_cb(msg)

    def _connect(self) -> socket.socket:
        """Create TCP connection and send HTTP GET request."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10.0)
        self._set_status(f"Connecting to {self.host}:{self.port}...")
        sock.connect((self.host, self.port))

        # Send raw HTTP GET request
        request = (
            f"GET /stream?token={self.token} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            f"Connection: keep-alive\r\n"
            f"\r\n"
        )
        sock.sendall(request.encode("ascii"))

        # Read and discard HTTP response headers
        header_data = b""
        while b"\r\n\r\n" not in header_data:
            chunk = sock.recv(1)
            if not chunk:
                raise ConnectionError("Connection closed during header read")
            header_data += chunk

        # Verify we got a 200 OK
        header_str = header_data.decode("ascii", errors="replace")
        first_line = header_str.split("\r\n")[0]
        if "200" not in first_line:
            raise ConnectionError(f"Unexpected response: {first_line}")

        log.info("HTTP headers received: %s", first_line.strip())
        sock.settimeout(5.0)  # read timeout for stream data
        return sock

    def run(self):
        """Main loop: connect → read → distribute → reconnect on failure."""
        while not self._stop_event.is_set():
            sock = None
            try:
                sock = self._connect()
                self._running.set()
                self._set_status("🟢 Streaming")
                leftover = b""

                while not self._stop_event.is_set():
                    try:
                        raw = sock.recv(RECV_CHUNK_SIZE)
                    except socket.timeout:
                        continue  # no data yet, retry
                    if not raw:
                        raise ConnectionError("Stream ended (recv returned empty)")

                    # --------------------------------------------------------
                    # Byte alignment: PCM 16-bit = 2 bytes per sample.
                    # If total bytes are odd, hold back the last byte.
                    # --------------------------------------------------------
                    data = leftover + raw
                    leftover = b""
                    if len(data) % SAMPLE_WIDTH != 0:
                        leftover = data[-1:]
                        data = data[:-1]
                    if not data:
                        continue

                    # Fan-out: send to both audio playback and STT queues
                    try:
                        self._audio_q.put_nowait(data)
                    except queue.Full:
                        pass  # drop if audio is lagging

                    try:
                        self._stt_q.put_nowait(data)
                    except queue.Full:
                        pass  # drop if STT is lagging

            except Exception as e:
                self._running.clear()
                self._set_status(f"🔴 Disconnected: {e}")
                log.warning("Stream error: %s", e)
            finally:
                self._running.clear()
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass

            # Wait before reconnecting
            if not self._stop_event.is_set():
                self._set_status(
                    f"⏳ Reconnecting in {RECONNECT_DELAY:.0f}s..."
                )
                self._stop_event.wait(timeout=RECONNECT_DELAY)

        self._set_status("⬛ Stopped")


# =============================================================================
# AudioPlayer — plays PCM audio in real-time via sounddevice
# =============================================================================
class AudioPlayer(threading.Thread):
    """Consumes PCM chunks from a queue and plays them through speakers."""

    def __init__(self, audio_queue: queue.Queue, gain: float = 1.0):
        super().__init__(daemon=True, name="AudioPlayer")
        self._queue = audio_queue
        self._stop_event = threading.Event()
        self._gain = gain
        self._stream = None
        self._buffer = b""
        self._lock = threading.Lock()

    def stop(self):
        self._stop_event.set()

    def _audio_callback(self, outdata, frames, time_info, status):
        """sounddevice callback — fills output buffer from internal byte buffer."""
        needed = frames * SAMPLE_WIDTH * CHANNELS  # bytes needed

        with self._lock:
            # Try to fill from internal buffer
            while len(self._buffer) < needed:
                try:
                    chunk = self._queue.get_nowait()
                    self._buffer += chunk
                except queue.Empty:
                    break

            if len(self._buffer) >= needed:
                raw = self._buffer[:needed]
                self._buffer = self._buffer[needed:]
            else:
                # Not enough data — use what we have + pad with silence
                raw = self._buffer + b"\x00" * (needed - len(self._buffer))
                self._buffer = b""

        # Convert to numpy int16, apply gain, write to output
        samples = np.frombuffer(raw, dtype=np.int16).copy()
        if self._gain != 1.0:
            samples = np.clip(
                samples.astype(np.float32) * self._gain, -32768, 32767
            ).astype(np.int16)
        outdata[:] = samples.reshape(-1, CHANNELS)

    def run(self):
        """Open the audio output stream and keep it alive until stopped."""
        try:
            self._stream = sd.RawOutputStream(
                samplerate=SAMPLE_RATE,
                blocksize=AUDIO_BLOCK_SIZE,
                dtype="int16",
                channels=CHANNELS,
                callback=self._audio_callback,
            )
            self._stream.start()
            log.info("Audio playback started (gain=%.1f)", self._gain)

            # Keep thread alive while stream is running
            while not self._stop_event.is_set():
                self._stop_event.wait(timeout=0.1)

        except Exception as e:
            log.error("Audio playback error: %s", e)
        finally:
            if self._stream:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass
            log.info("Audio playback stopped")


# =============================================================================
# SpeechRecognizer — offline STT with Vosk
# =============================================================================
class SpeechRecognizer(threading.Thread):
    """Feeds PCM audio to Vosk KaldiRecognizer and emits transcription text."""

    def __init__(
        self,
        stt_queue: queue.Queue,
        model_path: str,
        on_partial=None,
        on_final=None,
    ):
        super().__init__(daemon=True, name="SpeechRecognizer")
        self._queue = stt_queue
        self._model_path = model_path
        self._on_partial = on_partial
        self._on_final = on_final
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        if Model is None:
            log.warning("Vosk not available — STT disabled")
            return

        if not os.path.isdir(self._model_path):
            log.error(
                "Vosk model not found at '%s'. Download from "
                "https://alphacephei.com/vosk/models",
                self._model_path,
            )
            if self._on_partial:
                self._on_partial(
                    f"[ERROR] Vosk model not found at '{self._model_path}'. "
                    "Download from https://alphacephei.com/vosk/models"
                )
            return

        try:
            SetLogLevel(-1)  # suppress Vosk internal logs
            model = Model(self._model_path)
            recognizer = KaldiRecognizer(model, SAMPLE_RATE)
            recognizer.SetWords(True)
            log.info("Vosk model loaded from '%s'", self._model_path)
        except Exception as e:
            log.error("Failed to load Vosk model: %s", e)
            if self._on_partial:
                self._on_partial(f"[ERROR] Vosk model load failed: {e}")
            return

        while not self._stop_event.is_set():
            try:
                data = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if recognizer.AcceptWaveform(data):
                result = json.loads(recognizer.Result())
                text = result.get("text", "")
                if text and self._on_final:
                    self._on_final(text)
            else:
                partial = json.loads(recognizer.PartialResult())
                text = partial.get("partial", "")
                if text and self._on_partial:
                    self._on_partial(text)

        # Flush final result
        try:
            result = json.loads(recognizer.FinalResult())
            text = result.get("text", "")
            if text and self._on_final:
                self._on_final(text)
        except Exception:
            pass

        log.info("Speech recognizer stopped")


# =============================================================================
# MonitorApp — CustomTkinter GUI
# =============================================================================
class MonitorApp:
    """Main application with CustomTkinter GUI."""

    def __init__(self):
        if ctk is None:
            raise RuntimeError(
                "customtkinter is required for GUI mode. "
                "Install with: pip install customtkinter"
            )

        # ---- State ----
        self._receiver = None
        self._player = None
        self._recognizer = None
        self._logger = None
        self._audio_queue = None
        self._stt_queue = None
        self._is_connected = False

        # ---- Window ----
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.root = ctk.CTk()
        self.root.title("🎙️ Audio Implant Monitor")
        self.root.geometry("780x620")
        self.root.minsize(640, 500)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()

    # ----- UI Construction -----
    def _build_ui(self):
        # Title
        title = ctk.CTkLabel(
            self.root,
            text="🎙️ Audio Implant Monitor",
            font=ctk.CTkFont(size=22, weight="bold"),
        )
        title.pack(pady=(18, 6))

        subtitle = ctk.CTkLabel(
            self.root,
            text="ESP32-S3 Real-Time Audio Stream + Speech-to-Text",
            font=ctk.CTkFont(size=12),
            text_color="gray",
        )
        subtitle.pack(pady=(0, 12))

        # ---- Connection Frame ----
        conn_frame = ctk.CTkFrame(self.root)
        conn_frame.pack(fill="x", padx=20, pady=(0, 8))

        ctk.CTkLabel(conn_frame, text="IP:", width=30).grid(
            row=0, column=0, padx=(12, 4), pady=10
        )
        self._ip_entry = ctk.CTkEntry(conn_frame, width=160, placeholder_text=DEFAULT_IP)
        self._ip_entry.grid(row=0, column=1, padx=4, pady=10)
        self._ip_entry.insert(0, DEFAULT_IP)

        ctk.CTkLabel(conn_frame, text="Port:", width=40).grid(
            row=0, column=2, padx=(12, 4), pady=10
        )
        self._port_entry = ctk.CTkEntry(conn_frame, width=70, placeholder_text=str(DEFAULT_PORT))
        self._port_entry.grid(row=0, column=3, padx=4, pady=10)
        self._port_entry.insert(0, str(DEFAULT_PORT))

        ctk.CTkLabel(conn_frame, text="Token:", width=50).grid(
            row=0, column=4, padx=(12, 4), pady=10
        )
        self._token_entry = ctk.CTkEntry(conn_frame, width=100, placeholder_text=DEFAULT_TOKEN)
        self._token_entry.grid(row=0, column=5, padx=4, pady=10)
        self._token_entry.insert(0, DEFAULT_TOKEN)

        self._connect_btn = ctk.CTkButton(
            conn_frame, text="▶  Connect", width=120, command=self._toggle_connection,
            fg_color="#1a7a2e", hover_color="#22a03a",
        )
        self._connect_btn.grid(row=0, column=6, padx=(16, 12), pady=10)
        conn_frame.columnconfigure(1, weight=1)

        # ---- Model Path Frame ----
        model_frame = ctk.CTkFrame(self.root)
        model_frame.pack(fill="x", padx=20, pady=(0, 8))

        ctk.CTkLabel(model_frame, text="Vosk Model:", width=90).grid(
            row=0, column=0, padx=(12, 4), pady=10
        )
        self._model_entry = ctk.CTkEntry(model_frame, width=400, placeholder_text="model/")
        self._model_entry.grid(row=0, column=1, padx=4, pady=10, sticky="ew")
        self._model_entry.insert(0, "model")
        model_frame.columnconfigure(1, weight=1)

        ctk.CTkLabel(model_frame, text="Gain:", width=40).grid(
            row=0, column=2, padx=(12, 4), pady=10
        )
        self._gain_entry = ctk.CTkEntry(model_frame, width=60, placeholder_text="1.0")
        self._gain_entry.grid(row=0, column=3, padx=(4, 12), pady=10)
        self._gain_entry.insert(0, "1.0")

        # ---- Status Bar ----
        self._status_var = ctk.StringVar(value="⬛ Disconnected")
        status_label = ctk.CTkLabel(
            self.root,
            textvariable=self._status_var,
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        )
        status_label.pack(fill="x", padx=24, pady=(4, 2))

        # ---- VU Meter ----
        self._vu_progress = ctk.CTkProgressBar(self.root, width=400, height=8)
        self._vu_progress.pack(fill="x", padx=24, pady=(0, 8))
        self._vu_progress.set(0)

        # ---- Transcription Area ----
        text_label = ctk.CTkLabel(
            self.root,
            text="📝 Live Transcription",
            font=ctk.CTkFont(size=13, weight="bold"),
            anchor="w",
        )
        text_label.pack(fill="x", padx=24, pady=(4, 2))

        self._text_box = ctk.CTkTextbox(
            self.root,
            font=ctk.CTkFont(family="Consolas", size=13),
            wrap="word",
            state="disabled",
        )
        self._text_box.pack(fill="both", expand=True, padx=20, pady=(0, 8))

        # ---- Footer ----
        self._file_label = ctk.CTkLabel(
            self.root,
            text="Transcript file: (none)",
            font=ctk.CTkFont(size=11),
            text_color="gray",
            anchor="w",
        )
        self._file_label.pack(fill="x", padx=24, pady=(0, 12))

    # ----- Connection Toggle -----
    def _toggle_connection(self):
        if self._is_connected:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        host = self._ip_entry.get().strip() or DEFAULT_IP
        try:
            port = int(self._port_entry.get().strip() or DEFAULT_PORT)
        except ValueError:
            port = DEFAULT_PORT
        token = self._token_entry.get().strip() or DEFAULT_TOKEN
        model_path = self._model_entry.get().strip() or "model"
        try:
            gain = float(self._gain_entry.get().strip() or "1.0")
        except ValueError:
            gain = 1.0

        # Create queues (bounded to prevent unbounded memory growth)
        self._audio_queue = queue.Queue(maxsize=200)
        self._stt_queue = queue.Queue(maxsize=200)

        # Transcript logger
        self._logger = TranscriptLogger(output_dir=".")
        self._file_label.configure(
            text=f"Transcript file: {self._logger.filepath}"
        )

        # Stream receiver
        self._receiver = AudioStreamReceiver(
            host=host,
            port=port,
            token=token,
            audio_queue=self._audio_queue,
            stt_queue=self._stt_queue,
            status_callback=self._update_status_threadsafe,
        )

        # Audio player
        self._player = AudioPlayer(audio_queue=self._audio_queue, gain=gain)

        # Speech recognizer
        self._recognizer = SpeechRecognizer(
            stt_queue=self._stt_queue,
            model_path=model_path,
            on_partial=self._on_partial_threadsafe,
            on_final=self._on_final_threadsafe,
        )

        # Start all threads
        self._receiver.start()
        self._player.start()
        self._recognizer.start()

        self._is_connected = True
        self._connect_btn.configure(
            text="⏹  Disconnect", fg_color="#a01e1e", hover_color="#cc2a2a"
        )

        # Start VU meter update loop
        self._update_vu_meter()

    def _disconnect(self):
        """Stop all threads and clean up."""
        if self._receiver:
            self._receiver.stop()
        if self._player:
            self._player.stop()
        if self._recognizer:
            self._recognizer.stop()
        if self._logger:
            self._logger.close()

        self._receiver = None
        self._player = None
        self._recognizer = None
        self._logger = None
        self._audio_queue = None
        self._stt_queue = None

        self._is_connected = False
        self._connect_btn.configure(
            text="▶  Connect", fg_color="#1a7a2e", hover_color="#22a03a"
        )
        self._status_var.set("⬛ Disconnected")
        self._vu_progress.set(0)

    # ----- Thread-safe UI Updates -----
    def _update_status_threadsafe(self, msg: str):
        """Called from receiver thread — schedules UI update on main thread."""
        self.root.after(0, lambda: self._status_var.set(msg))

    def _on_partial_threadsafe(self, text: str):
        """Called from STT thread — shows partial transcription."""
        self.root.after(0, lambda: self._show_partial(text))

    def _on_final_threadsafe(self, text: str):
        """Called from STT thread — shows final transcription and logs it."""
        self.root.after(0, lambda: self._show_final(text))

    def _show_partial(self, text: str):
        """Display partial (in-progress) transcription in italics."""
        self._text_box.configure(state="normal")
        # Remove previous partial line if exists
        try:
            self._text_box.delete("partial_start", "partial_end")
        except Exception:
            pass
        self._text_box.insert("end", f"  ⏳ {text}", "partial")
        self._text_box.mark_set("partial_start", "end-1c linestart")
        self._text_box.mark_set("partial_end", "end-1c")
        self._text_box.configure(state="disabled")
        self._text_box.see("end")

    def _show_final(self, text: str):
        """Display final transcription and log to file."""
        self._text_box.configure(state="normal")
        # Remove partial line
        try:
            self._text_box.delete("partial_start", "partial_end")
        except Exception:
            pass
        ts = datetime.now().strftime("%H:%M:%S")
        self._text_box.insert("end", f"[{ts}] {text}\n")
        self._text_box.configure(state="disabled")
        self._text_box.see("end")

        # Log to file
        if self._logger:
            self._logger.write(text)

    # ----- VU Meter -----
    def _update_vu_meter(self):
        """Periodically update the VU meter from the audio queue state."""
        if not self._is_connected:
            return
        # Use queue size as a proxy for audio level activity
        if self._audio_queue:
            level = min(self._audio_queue.qsize() / 50.0, 1.0)
            self._vu_progress.set(level)
        self.root.after(100, self._update_vu_meter)

    # ----- Close -----
    def _on_close(self):
        """Handle window close."""
        self._disconnect()
        self.root.destroy()

    def run(self):
        """Start the main Tkinter event loop."""
        self.root.mainloop()


# =============================================================================
# Console-only fallback (no GUI)
# =============================================================================
def run_console_mode():
    """Run in console mode without GUI (fallback if customtkinter unavailable)."""
    import argparse

    parser = argparse.ArgumentParser(
        description="ESP32 Audio Implant Monitor (Console Mode)"
    )
    parser.add_argument("--ip", default=DEFAULT_IP, help="ESP32 IP address")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Stream port")
    parser.add_argument("--token", default=DEFAULT_TOKEN, help="Auth token")
    parser.add_argument("--model", default="model", help="Path to Vosk model directory")
    parser.add_argument("--gain", type=float, default=1.0, help="Audio gain multiplier")
    args = parser.parse_args()

    audio_queue = queue.Queue(maxsize=200)
    stt_queue = queue.Queue(maxsize=200)
    logger = TranscriptLogger(output_dir=".")

    print(f"╔══════════════════════════════════════════════════╗")
    print(f"║     🎙️  Audio Implant Monitor — Console Mode     ║")
    print(f"╠══════════════════════════════════════════════════╣")
    print(f"║  Target : {args.ip}:{args.port:<28}  ║")
    print(f"║  Model  : {args.model:<37}  ║")
    print(f"║  Gain   : {args.gain:<37}  ║")
    print(f"║  Log    : {str(logger.filepath):<37}  ║")
    print(f"╚══════════════════════════════════════════════════╝")
    print("Press Ctrl+C to stop.\n")

    def on_status(msg):
        print(f"  [STATUS] {msg}")

    def on_partial(text):
        print(f"  ⏳ {text}", end="\r", flush=True)

    def on_final(text):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"\n  [{ts}] ✅ {text}")
        logger.write(text)

    receiver = AudioStreamReceiver(
        host=args.ip,
        port=args.port,
        token=args.token,
        audio_queue=audio_queue,
        stt_queue=stt_queue,
        status_callback=on_status,
    )
    player = AudioPlayer(audio_queue=audio_queue, gain=args.gain)
    recognizer = SpeechRecognizer(
        stt_queue=stt_queue,
        model_path=args.model,
        on_partial=on_partial,
        on_final=on_final,
    )

    receiver.start()
    player.start()
    recognizer.start()

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n\n[EXIT] Shutting down...")
    finally:
        receiver.stop()
        player.stop()
        recognizer.stop()
        logger.close()
        # Wait briefly for threads to finish
        time.sleep(0.5)
        print("[EXIT] Done. Transcript saved to:", logger.filepath)


# =============================================================================
# Main entry point
# =============================================================================
def main():
    print("=" * 55)
    print("  🎙️  ESP32 Audio Implant Monitor")
    print("  PCM 16-bit | 16 kHz | Mono | Vosk STT")
    print("=" * 55)

    if ctk is not None:
        try:
            app = MonitorApp()
            app.run()
        except Exception as e:
            log.error("GUI failed: %s — falling back to console mode", e)
            run_console_mode()
    else:
        print("[INFO] customtkinter not available. Using console mode.")
        run_console_mode()


if __name__ == "__main__":
    main()
