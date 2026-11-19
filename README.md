# Chitti Junior: a low-budget Telugu AI friend you build on a breadboard

Chitti Junior is a do-it-yourself, low-budget version of [Chitti](https://github.com/chaitanyaeshwarprasad/chitti). Press her button, speak in Telugu, English or a mix, and she answers out loud in natural Telugu, with glowing eyes that react on a tiny screen. She has a personality you choose: a friendly roaster, a funny one, a chill one or a caring one.

She's built from **7 cheap parts on a breadboard, with no soldering**. The board connects over your Wi-Fi to a free program on your Windows PC, which uses **your own** Claude and Sarvam API keys.

> **Free and open source.** You only pay for your own API use, usually a few rupees a day.
> **Created by [Chaitanya Eshwar Prasad](https://chaitanyaeshwarprasad.com)**. Watch the build video and more on [YouTube](https://www.youtube.com/@chaitanya.eshwar.prasad) and [Instagram @acep.tech.in.telugu](https://instagram.com/acep.tech.in.telugu).

[![Website](https://img.shields.io/badge/Website-chaitanyaeshwarprasad.com-005F9E?style=for-the-badge&logo=google-chrome&logoColor=white)](https://chaitanyaeshwarprasad.com)
[![GitHub](https://img.shields.io/badge/GitHub-chaitanyaeshwarprasad-181717?style=for-the-badge&logo=github&logoColor=white)](https://github.com/chaitanyaeshwarprasad)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-Chaitanya%20Eshwar%20Prasad-0A66C2?style=for-the-badge&logo=linkedin&logoColor=white)](https://linkedin.com/in/chaitanya-eshwar-prasad)
[![YouTube](https://img.shields.io/badge/YouTube-@chaitanya.eshwar.prasad-FF0000?style=for-the-badge&logo=youtube&logoColor=white)](https://www.youtube.com/@chaitanya.eshwar.prasad)
[![Instagram](https://img.shields.io/badge/Instagram-@acep.tech.in.telugu-E4405F?style=for-the-badge&logo=instagram&logoColor=white)](https://instagram.com/acep.tech.in.telugu)

---

## Contents

1. [Parts I used](#1-parts-i-used)
2. [How Chitti Junior works](#2-how-chitti-junior-works)
3. [What you need](#3-what-you-need)
4. [Buy your API keys](#4-buy-your-api-keys-you-must-use-your-own)
5. [Build and install, step by step](#5-build-and-install-step-by-step)
6. [Using Chitti Junior](#6-using-chitti-junior)
7. [The dashboard](#7-the-dashboard)
8. [Privacy and safety](#8-privacy-and-safety)
9. [Troubleshooting](#9-troubleshooting)
10. [For developers](#10-for-developers)
11. [Disclaimer](#11-disclaimer-please-read)
12. [Credits and licence](#12-credits-and-licence)
13. [About the author](#-about-the-author)

---

## 1. Parts I used

| # | Part | What it does |
|---|---|---|
| 1 | **ESP32-S3 N16R8 development board** | Junior's brain: Wi-Fi, face, microphone and speaker. Must be **N16R8** (16 MB flash, 8 MB PSRAM). |
| 2 | **INMP441 I2S MEMS microphone** | Her ears |
| 3 | **MAX98357A I2S mono amplifier** | Drives the speaker |
| 4 | **0.96-inch OLED I2C display (4-pin)** | Her glowing-eyes face and scrolling text (SSD1306 or SH1106) |
| 5 | **8 Ω, 0.5 W speaker** | Her voice |
| 6 | **Breadboard** + jumper wires | Connections, no soldering |
| 7 | **USB cable** (USB-C **data** cable) | Power, software install and Wi-Fi setup |

Optional:
- a **5 V 2 A phone charger or power bank**, for cleaner sound once Wi-Fi is set up;
- a **470 µF capacitor**, to allow a louder 4 Ω 3 W speaker.

Buy the parts from any shop or seller; just check the names match. If the modules come with loose pins, they need soldering once (any electronics or mobile repair shop can do it in minutes).

**Wiring:** see **[ASSEMBLY.md](ASSEMBLY.md)** or the printable **[PDF guide](Chitti-Junior-Assembly-Guide.pdf)**. There are about 18 wires, one device at a time.

---

## 2. How Chitti Junior works

```
  You speak ─► Junior (ESP32 board) ──Wi-Fi──► Your PC (Chitti Junior server) ──Internet──► Claude (brain)
                    ▲                                 │                             └────► Sarvam (Telugu voice)
                    └─────────── her voice ◄──────────┘
```

1. **You press BOOT and talk.** The board records your voice and streams it over your home Wi-Fi to your PC.
2. **Your PC does the thinking.** The server:
   - turns your voice into text with **Sarvam** (or free **Whisper** on your PC);
   - asks **Claude** for a reply in Junior's personality and Telugu style;
   - turns the reply into Telugu speech.
3. **She answers** through her speaker, with moving eyes and scrolling subtitles.

**How the board finds your PC:** you never type an IP address. Junior asks "where are you?" on your Wi-Fi, and the server answers. Both must be on the **same Wi-Fi**.

| Port on your PC | Used for |
|---|---|
| 8765 (TCP, WebSocket) | Voice and replies between Junior and the PC |
| 8766 (UDP) | Auto-discovery |
| 8768 (HTTP, this PC only) | The dashboard at http://localhost:8768 |

---

## 3. What you need

| Item | Notes |
|---|---|
| The 7 parts | See [section 1](#1-parts-i-used) |
| Windows 10 or 11 PC or laptop | Stays on (not asleep) while you talk. Wi-Fi or LAN cable. |
| Python 3.12, ffmpeg, Visual C++ runtime | **Installed for you** if missing |
| About 4 GB free disk space and internet | Checked by the installer |
| 2.4 GHz Wi-Fi | The ESP32 doesn't support 5 GHz. No guest Wi-Fi. |
| **Your own Anthropic API key** | Required |
| Your own Sarvam API key | Optional, recommended for Telugu |

---

## 4. Buy your API keys (you must use your own)

Chitti Junior doesn't come with any API keys. Every user creates their own and pays for their own usage. **Never share your keys.**

### Anthropic (Claude): required
1. Go to https://console.anthropic.com and sign up.
2. Add a small amount of credit under **Billing**.
3. **Set a monthly spend limit** under **Limits**.
4. Under **API Keys**, choose **Create Key** and copy it. It starts with `sk-ant-`.

### Sarvam AI: optional (natural Telugu voice and hearing)
Sign up at https://dashboard.sarvam.ai and create an API key. Without it, Junior uses a free Microsoft voice and Whisper.

**Costs:**
- Claude Haiku 5.5 (the default) costs a fraction of a rupee per reply; Sonnet 5.5 costs several times more.
- Sarvam costs about ₹0.5 per minute of listening, and ₹10–30 per 10,000 characters of speech.
- Prices change, so check the official pages. Use spend limits.

---

## 5. Build and install, step by step

Full guide: **[INSTALL.md](INSTALL.md)**.

1. **Build Junior:** follow [ASSEMBLY.md](ASSEMBLY.md) or the [PDF guide](Chitti-Junior-Assembly-Guide.pdf). Unplug the USB cable while wiring.
2. **Download** this project: get the **latest release**, or choose **Code**, then **Download ZIP**. Unzip it to a simple folder like `C:\ChittiJunior`.
3. Double-click **`Start Chitti Junior.bat`**. The first time, it checks your PC and installs everything (5–10 minutes), then opens the dashboard.
4. The dashboard's **setup wizard** does the rest:
   1. **Keys:** paste your Claude key (and your Sarvam key, if you have one), then tick the own-risk box.
   2. **Install Chitti Junior on the board:** plug the ESP32's **COM** port into the PC and click **Install Chitti Junior** (progress bar, about 1 minute). On the first start, Junior shows a **screen test**: hold **BOOT** for 2 s if the text looks right.
   3. **Wi-Fi:** move the cable to the ESP32's **USB** port, then type your 2.4 GHz Wi-Fi name and password.
   4. **Say hi:** when the wizard shows **Junior online**, press **BOOT** and talk.

**Every day after that:** power Junior from USB, a charger or a power bank; double-click `Start Chitti Junior.bat`; then talk.

The `tools` folder has manual versions of the wizard's steps:
- `Flash Chitti Junior.bat`
- `Connect Chitti Junior to Wi-Fi.bat`
- `Save API keys.bat`
- `Allow Chitti in Firewall.bat`
- `Open Dashboard.bat`

---

## 6. Using Chitti Junior

| Do this | Junior does |
|---|---|
| **Press BOOT** | Starts listening. Talk, then stop; she notices when you're done. |
| **Press BOOT while she talks** | Stops her |
| **Hold BOOT 1.2 s** | Poke: she reacts |
| **Hold BOOT 6 s** | Screen test (pick the right screen setting) |
| Leave her alone 3 minutes | She sleeps (screen off). Press BOOT to wake her. |

Things to try:
- *"నన్ను రోస్ట్ చెయ్"* (roast me)
- *"ఈరోజు వాతావరణం ఎలా ఉంది?"* (what's the weather today?)
- *"నా పేరు ___, గుర్తుపెట్టుకో"* (my name is ___, remember it)

---

## 7. The dashboard

The dashboard runs at **http://localhost:8768**, on this PC only. It's the same dashboard as Chitti:
- **Setup wizard**
- **Home:** animated robot, status, chat and setup checklist
- **Settings:** API keys, Haiku or Sonnet, voice, personality, Telugu style, words to use or avoid, and your city
- **Device:** volume, Wi-Fi setup, install firmware, run setup again and reset
- **Help**, **Hardware** (the parts list), **Disclaimer** and **Author**

---

## 8. Privacy and safety

- **Your keys stay on your PC** (in `server\.env` and your Windows settings), and are only sent to their own service. Entering a new key deletes the old one, and a wrong Claude key is refused.
- **Your chats and memories stay on your PC.** The dashboard's **Reset** erases them.
- **Nothing is sent to the author.** There are no analytics. The only services contacted are Anthropic, Sarvam (only with a Sarvam key), Microsoft Edge TTS (only for the free voice) and Open-Meteo (only for weather).
- Your Wi-Fi password is stored only on the board. The dashboard only accepts connections from your own PC.

---

## 9. Troubleshooting

| Problem | Fix |
|---|---|
| Install on the board fails | Use the ESP32's **COM** port. Close Arduino IDE or serial monitors. Try another USB-C **data** cable, or hold BOOT, tap RST, release BOOT and try again. |
| Wi-Fi setup can't find the board | Wi-Fi setup needs the ESP32's port marked **USB**, not COM. |
| Screen blank | Press BOOT (she may be asleep). Check the screen wires: VDD 3V3, GND, SDA 11, SCK 12. She finds the screen again within 2 s. |
| Screen garbled or shifted | Hold BOOT 6 s for the screen test and pick the next setting. |
| She can't hear you | Check the microphone: VDD 3V3, L/R GND, SCK 4, WS 5, SD 6. Speak close to the mic. |
| No sound | Check the amplifier: VIN on **5V** (5VN), SD 7, DIN 8, BCLK 17, LRC 18, and the speaker on + and −. |
| Crackle or rattle | Power from a 2 A charger or power bank, volume 60–80%, keep the speaker standing (not face-down), keep the VIN and GND wires short. |
| "Junior offline" | Same 2.4 GHz Wi-Fi as the PC, server running, Python allowed through the firewall (`tools\Allow Chitti in Firewall.bat`). |
| "Python 3.12 is needed" | Install Python 3.12 with **Add python.exe to PATH** ticked, then run `Start Chitti Junior.bat` again. |

The black **Start Chitti Junior** window shows what's happening; there's also a log in `server\chitti.log`.

---

## 10. For developers

```
chitti-junior/
├─ Start Chitti Junior.bat   installs on first run, then starts the server and dashboard
├─ tools/                    manual flash / Wi-Fi / key / firewall scripts
├─ server/                   PC server (Python): speech → Claude → speech, dashboard
├─ firmware/                 ESP-IDF v5.5 firmware source (C)
│  ├─ main/board_jr.h        pin map (must match ASSEMBLY.md)
│  └─ prebuilt/              ready-made .bin files the dashboard installs
├─ docs/protocol.md          board ↔ PC messages (same as Chitti)
├─ ASSEMBLY.md               wiring guide
└─ Chitti-Junior-Assembly-Guide.pdf
```

To build the firmware yourself, install ESP-IDF v5.5, put its folder in `firmware\idf_path.txt`, then run `firmware\build.bat flash`.

**Flash layout** (DIO, 80 MHz, 16 MB):

| Offset | File |
|---|---|
| `0x0` | bootloader.bin |
| `0x8000` | partition-table.bin |
| `0x10000` | chitti_junior.bin |

**Audio:** the microphone and speaker run at 24 kHz to and from the PC, and the speaker is driven at 48 kHz (up-sampled on the board for the MAX98357A).

---

## 11. Disclaimer (please read)

Chitti Junior is shared **free, for learning and fun, "as is", with no warranty of any kind.** By downloading, building, flashing or using it, you agree that:

- **The author, Chaitanya Eshwar Prasad, is not responsible for anything** that happens from using this project. That includes:
  - damaged boards, parts, PCs or other hardware;
  - data loss, overheating, fire, injury or electrical problems;
  - **API charges** from Anthropic, Sarvam or any other service;
  - leaked keys;
  - anything the AI says;
  - any other loss.
- **You use your own API keys and pay your own bills.** Set spend limits.
- Buying parts and building is **at your own risk**. Double-check 3V3 and 5V before powering on.
- Junior isn't a medical, legal, financial or safety tool, and isn't meant for unsupervised young children.
- The author isn't affiliated with Espressif, Anthropic, Sarvam AI, Microsoft or any parts seller. Trademarks belong to their owners.

---

## 12. Credits and licence

**Chitti Junior was created by Chaitanya Eshwar Prasad.** If you make a video or project with it, please credit **Chaitanya Eshwar Prasad** and link back to this repository.

Released under the [MIT Licence](LICENSE). Built with Claude by Anthropic, Sarvam AI, ESP-IDF by Espressif, faster-whisper, edge-tts and Open-Meteo.

---

## 👤 About the Author

Created and maintained by **Chaitanya Eshwar Prasad**. Connect with me or follow my work:

[![Website](https://img.shields.io/badge/Website-chaitanyaeshwarprasad.com-005F9E?style=for-the-badge&logo=google-chrome&logoColor=white)](https://chaitanyaeshwarprasad.com)
[![GitHub](https://img.shields.io/badge/GitHub-chaitanyaeshwarprasad-181717?style=for-the-badge&logo=github&logoColor=white)](https://github.com/chaitanyaeshwarprasad)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-Chaitanya%20Eshwar%20Prasad-0A66C2?style=for-the-badge&logo=linkedin&logoColor=white)](https://linkedin.com/in/chaitanya-eshwar-prasad)
[![YouTube](https://img.shields.io/badge/YouTube-@chaitanya.eshwar.prasad-FF0000?style=for-the-badge&logo=youtube&logoColor=white)](https://www.youtube.com/@chaitanya.eshwar.prasad)
[![Instagram](https://img.shields.io/badge/Instagram-@acep.tech.in.telugu-E4405F?style=for-the-badge&logo=instagram&logoColor=white)](https://instagram.com/acep.tech.in.telugu)
