# Chitti Junior: Chitti you assemble yourself

Chitti Junior is [Chitti](https://github.com/chaitanyaeshwarprasad/chitti), assembled by you. Same brain, same Telugu voice, same personalities, memory and dashboard; **the only difference is that you build the hardware yourself** from a few common parts instead of using a ready-made board. Press her button, speak in Telugu, English or a mix, and she answers out loud in natural Telugu, with glowing eyes that react on a small screen.

She's put together from **7 common parts with jumper wires, no soldering**. The board connects over your Wi-Fi to a free program on your Windows PC, which uses **your own** Claude and Sarvam API keys.

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

Windows asks once to allow Python through the firewall. Allow it on **private networks**.

---

## 3. What you need

| Item | Notes |
|---|---|
| The 7 parts | See [section 1](#1-parts-i-used) |
| Windows 10 or 11 PC or laptop | Stays on (not asleep) while you talk. Wi-Fi or LAN cable. |
| Python 3.12, ffmpeg, Visual C++ runtime | **Installed for you** if missing. Python 3.13 and newer are not supported yet. |
| About 4 GB free disk space and internet | Checked by the installer |
| 2.4 GHz Wi-Fi | The ESP32 doesn't support 5 GHz. No guest Wi-Fi. A phone hotspot works if the PC joins the same hotspot. |
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
Sign up at https://dashboard.sarvam.ai (new accounts usually get some free credits) and create an API key. Without it, Junior still works: she uses a free Microsoft Telugu voice and hears you with Whisper on your PC, which is slower and less accurate for Telugu.

**Costs:**
- Claude Haiku 5.5 (the default) costs a fraction of a rupee per reply; Sonnet 5.5 costs several times more.
- Sarvam costs about ₹0.5 per minute of listening, and ₹10–30 per 10,000 characters of speech.
- Chatting for a while every day usually costs **a few rupees a day**.
- Prices change, so check the official pages. Use the spend limits on both websites. **You are responsible for your own API bills.**

---

## 5. Build and install, step by step

Full guide: **[INSTALL.md](INSTALL.md)**.

1. **Build Junior:** follow [ASSEMBLY.md](ASSEMBLY.md) or the [PDF guide](Chitti-Junior-Assembly-Guide.pdf). Unplug the USB cable while wiring.
2. **Download** this project: get the **latest release**, or choose **Code**, then **Download ZIP**. Unzip it to a simple folder like `C:\ChittiJunior`, not inside OneDrive.
3. Double-click **`Start Chitti Junior.bat`**. The first time, it checks your PC and installs everything (5–10 minutes), then opens the dashboard. Keep the black window open while you use Junior.

   The installer checks each of these and fixes what's missing, asking you first:
   - 64-bit Windows, disk space and internet;
   - Python 3.12;
   - the Microsoft Visual C++ runtime;
   - Chitti Junior's packages;
   - ffmpeg;
   - a Windows Firewall rule so Junior can reach your PC.
4. The dashboard's **setup wizard** does the rest:
   1. **Keys:** paste your Claude key (and your Sarvam key, if you have one), then tick the own-risk box.
   2. **Install Chitti Junior on the board:** plug the ESP32's **COM** port into the PC and click **Install Chitti Junior** (progress bar, about 1 minute). On the first start, Junior shows a **screen test**: hold **BOOT** for 2 s if the text looks right.
   3. **Wi-Fi:** move the cable to the ESP32's **USB** port, then type your 2.4 GHz Wi-Fi name and password.
   4. **Say hi:** when the wizard shows **Junior online**, press **BOOT** and talk.

**Every day after that:** power Junior from USB, a charger or a power bank; double-click `Start Chitti Junior.bat`; then talk.

You can redo any step later: the dashboard's **Device** section has **Install firmware** and **Run setup again**.

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
- *"రేపు 7 కి నాకు గుర్తు చెయ్"* (remind me tomorrow at 7)

Change her speaker volume from the dashboard's **Device** section.

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
- **Your chats and memories stay on your PC**, in the `server` folder (`memory.json`, `facts.json`, `reminders.json`). The dashboard's **Reset** erases them.
- **Nothing is sent to the author.** There is no analytics, no tracking and no author server. The only services contacted:
  - **Anthropic**, which receives the conversation text to write replies;
  - **Sarvam**, which receives your voice and reply text, only if you add a Sarvam key;
  - **Microsoft Edge TTS**, which receives the reply text, only for the free voice;
  - **Open-Meteo**, which receives your city name, only when you ask about the weather.
- Read Anthropic's and Sarvam's own privacy policies to understand how they handle your data.
- Your Wi-Fi password is stored only on the board. The dashboard only accepts connections from your own PC.
- **If you fork or upload this project, never commit `server\.env`.** The included `.gitignore` already keeps keys and personal files out of git.

---

## 9. Troubleshooting

### Quick answers

- **Do I need a Wi-Fi adapter on my PC?** No. The PC just needs to be on the same router as Junior, by Wi-Fi or LAN cable. You only need a Wi-Fi adapter for the **Scan** button; without one, type the Wi-Fi name.
- **My router shows two names, like "Home" and "Home-5G".** Put Junior on the one without "5G". The PC can use either.
- **Can I use guest Wi-Fi?** Usually no. Guest networks (and routers with "AP isolation") stop devices from seeing each other.
- **Does the PC need to stay on?** Yes. Junior's brain runs on the PC. If it sleeps, she goes offline until it wakes. Keep the black **Start Chitti Junior** window open.
- **Does Junior work without internet?** No. She needs the internet to reach Claude and Sarvam.

### Problems and fixes

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
| Install stopped halfway | Fix the message it shows, then run `Start Chitti Junior.bat` again; it continues where it stopped. If it keeps failing, delete the `server\.venv` folder and try again. |
| Silent replies | Check that ffmpeg is installed: open a new window and type `ffmpeg -version`. Raise the volume in the dashboard. |
| "API key" errors | Check that your key is saved (green tick on Home), that you have credits, and that your spend limit isn't reached. |
| Wi-Fi scan shows nothing | Windows needs **Location services** turned on to scan. Or just type the Wi-Fi name. |
| She mishears Telugu | Add a Sarvam key; it's much better than Whisper for Telugu. Speak close to the mic. |
| Port already in use | Another program uses 8765, 8766 or 8768. Close it, or set the port variables in [section 10](#10-for-developers). |

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

**Environment variables** (optional):
- `CHITTI_MODEL`
- `CHITTI_PORT` (8765), `CHITTI_DISCOVERY_PORT` (8766), `CHITTI_DASHBOARD_PORT` (8768)
- `CHITTI_NO_BROWSER=1`

The board finds the PC on UDP port 8766, so change it only if you also change the firmware.

To build the firmware yourself, install ESP-IDF v5.5, put its folder in `firmware\idf_path.txt`, then run `firmware\build.bat flash`. Check the prebuilt files against `firmware\prebuilt\SHA256SUMS.txt`.

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
  - leaked or misused API keys;
  - anything Junior says, since AI replies can be wrong, rude or inappropriate;
  - any other loss or damage.
- **You use your own API keys and pay your own bills.** Set spend limits.
- Buying parts and building is **at your own risk**. Always unplug USB while wiring, and double-check 3V3 and 5V before powering on. Wrong wiring can damage the board or parts. If anything gets hot or smells, unplug immediately.
- Flashing replaces the board's original software. Use the optional backup if you may want it back.
- Junior isn't a medical, legal, financial or safety tool, and isn't meant for unsupervised young children.
- The author isn't affiliated with Espressif, Anthropic, Sarvam AI, Microsoft or any parts seller. Trademarks belong to their owners.
- You are responsible for following the terms of service of every API you use and the laws where you live.

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
