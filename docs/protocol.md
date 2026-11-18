# Chitti protocol v1 (board ⇄ PC server)

One WebSocket connection: `ws://<pc-ip>:8765/chitti`. Text frames are JSON, binary frames are audio.

Audio, both directions: raw PCM, signed 16-bit little-endian, mono, 16 000 Hz.

## Board → server

| Message | Meaning |
|---|---|
| `{"type":"hello","device":"chitti","version":1}` | First message after connecting |
| `{"type":"listen","state":"start"}` | User tapped / woke Chitti; microphone audio follows as binary frames |
| `{"type":"listen","state":"stop"}` | User ended the turn early (optional: the server also detects silence) |
| `{"type":"abort"}` | Stop speaking now (user interrupted) |
| `{"type":"event","name":"poke"}` etc. | Local events: `poke`, `shake`, `pat`, `pickup` (spoken reaction), `wake` (morning greeting) |
| `{"type":"battery","pct":68,"chg":true}` | Battery level, on change and every 30 s |
| binary | Microphone PCM while listening |

## Server → board

| Message | Meaning |
|---|---|
| `{"type":"state","state":"idle\|listening\|thinking\|speaking"}` | Drives the face |
| `{"type":"listen","state":"stop"}` | Server heard the end of speech; stop sending audio |
| `{"type":"stt","text":"...","lang":"te"}` | What Chitti heard |
| `{"type":"mood","mood":"happy"}` | Expression for the reply (happy, excited, calm, thinking, surprised, sad, sleepy, playful, angry, laughing) |
| `{"type":"say","text":"..."}` | Subtitle for the sentence being spoken |
| `{"type":"tts","state":"start\|stop"}` | Speech audio begins / ends |
| `{"type":"night","on":true}` | Late night (23:00-06:00): dimmer screen, sleeps sooner |
| binary | Speech PCM: first ~0.8 s at once, then ~1.25x real time; the board waits for 250 ms before playing |
