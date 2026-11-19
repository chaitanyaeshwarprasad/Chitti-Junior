# Chitti Junior — assembly guide (no experience needed)

By **Chaitanya Eshwar Prasad** · printable version: [Chitti-Junior-Assembly-Guide.pdf](Chitti-Junior-Assembly-Guide.pdf)

**Parts:**
1. ESP32-S3 N16R8 development board
2. INMP441 I2S MEMS microphone
3. MAX98357A I2S mono amplifier
4. 0.96-inch OLED I2C display (4-pin)
5. 8 Ω, 0.5 W speaker
6. Breadboard (+ jumper wires)
7. USB cable (USB-C **data** cable)

Time: about 45 minutes. No soldering if the modules' pins are already attached.

---

## Golden rules
1. **Never wire while the USB cable is plugged in.** Unplug first, wire, check, then plug in.
2. **3V3 and 5V are different.** The screen and microphone use **3V3**. Only the amplifier uses **5V**.
3. **Every GND goes together.** All grounds must be connected to each other.
4. Read the tiny white letters printed next to each pin. Use your phone camera's zoom if needed.

---

## Step 1: Lay out and identify your parts

| Part | How to recognise it | Pins (printed labels) |
|---|---|---|
| **ESP32-S3 N16R8 board** | Metal cover, **two USB-C ports** (USB and COM) | Two long rows: numbers (4, 5, 6…) and 3V3, 5V (5VN), GND |
| **0.96" OLED I2C display** | Small black screen, 4 pins | `GND VDD SCK SDA` |
| **INMP441 microphone** | Small round board with a tiny hole | 6 pins: `VDD GND L/R WS SCK SD` |
| **MAX98357A amplifier** | Small square board | 7 pins: `LRC BCLK DIN GAIN SD GND VIN`, plus `+ −` for the speaker |
| **Speaker 8 Ω 0.5 W** | Round, red and black wires | red = +, black = − |

---

## Step 2: Put the ESP32 on the breadboard

1. Hold the breadboard so the long red (+) and blue (−) lines run left to right.
2. Push the ESP32 into the **middle**, one pin row on each side of the centre gap. Press gently and evenly.
3. The **USB-C ports face outwards** (off the edge).

> **Easier:** if your jumper wires have **holes on both ends** (female-to-female), connect the ESP32's pins
> straight to each board's pins and skip the breadboard.

---

## Step 3: Make the power lines

| From (ESP32 pin) | To |
|---|---|
| **3V3** | breadboard **red (+)** line |
| **GND** | breadboard **blue (−)** line |

---

## Step 4: Connect the mini screen (4 wires)

Junior uses a small **4-pin OLED screen** (0.96" or 1.3", pins `GND VDD SCK SDA`).

| Mini screen pin | Goes to |
|---|---|
| **VDD** (or VCC) | **3V3** |
| **GND** | **GND** |
| **SCK** (or SCL) | ESP32 **GPIO 12** |
| **SDA** | ESP32 **GPIO 11** |


---

## Step 5: Connect the microphone (6 wires)

| Microphone pin | Goes to |
|---|---|
| **VDD** | red (+) line (**3V3**) |
| **GND** | blue (−) line |
| **L/R** | blue (−) line (GND) |
| **SCK** | ESP32 **GPIO 4** |
| **WS** | ESP32 **GPIO 5** |
| **SD** | ESP32 **GPIO 6** |

Keep the microphone wires short; point the mic hole towards you.

---

## Step 6: Connect the amplifier (6 wires) and the speaker (2 wires)

| Amplifier pin | Goes to |
|---|---|
| **VIN** | ESP32 **5V** pin (⚠️ **5V**, not 3V3) |
| **GND** | blue (−) line |
| **BCLK** | ESP32 **GPIO 17** |
| **LRC** | ESP32 **GPIO 18** |
| **DIN** | ESP32 **GPIO 8** |
| **SD** | ESP32 **GPIO 7** |
| **GAIN** | leave empty |

**Speaker:** red → amplifier **+**, black → amplifier **−**.

---

## Step 7: Check everything before power

- [ ] Screen **VDD** and microphone **VDD** go to **3V3**, **not 5V**.
- [ ] Amplifier **VIN** goes to **5V**.
- [ ] Screen, microphone and amplifier **GND** all go to the blue line, and the ESP32 **GND** too.
- [ ] Microphone **L/R** goes to GND.
- [ ] No bare metal of two wires touching. Every wire pushed in firmly.

---

## Step 8: First power-on

1. Plug a USB-C cable into the ESP32 port marked **"USB"** (not "COM"/"UART") and into your PC.
2. A small light on the ESP32 turns on. The screen glows white: normal until the software is installed.
3. ⚠️ If anything gets **hot**, smells, or the PC says "USB device malfunction": **unplug immediately** and
   recheck Step 7.

Next: install Chitti Junior's software with **Start Chitti Junior.bat** and the dashboard's setup wizard (see [INSTALL.md](INSTALL.md)).
The software installs most easily through the ESP32's **COM** port; Wi-Fi setup then uses the **USB** port.

---

## All connections in one table

| ESP32 pin | Connects to |
|---|---|
| 3V3 | screen VDD, mic VDD |
| GND | blue (−) line → screen GND, mic GND, mic L/R, amp GND |
| 5V | amp VIN |
| GPIO 11 / 12 | mini screen SDA / SCK |
| GPIO 4 / 5 / 6 | mic SCK / WS / SD |
| GPIO 7 / 8 / 17 / 18 | amp SD / DIN / BCLK / LRC |
| BOOT button (on the ESP32) | **Talk button**: press to talk, press again to stop her |

*Chitti Junior by Chaitanya Eshwar Prasad ([chaitanyaeshwarprasad.com](https://chaitanyaeshwarprasad.com)). Build at your own risk; the creator is not responsible for any damage.*

---

## 👤 About the Author

Created and maintained by **Chaitanya Eshwar Prasad**. Connect with me or follow my work:

[![Website](https://img.shields.io/badge/Website-chaitanyaeshwarprasad.com-005F9E?style=for-the-badge&logo=google-chrome&logoColor=white)](https://chaitanyaeshwarprasad.com)
[![GitHub](https://img.shields.io/badge/GitHub-chaitanyaeshwarprasad-181717?style=for-the-badge&logo=github&logoColor=white)](https://github.com/chaitanyaeshwarprasad)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-Chaitanya%20Eshwar%20Prasad-0A66C2?style=for-the-badge&logo=linkedin&logoColor=white)](https://linkedin.com/in/chaitanya-eshwar-prasad)
[![YouTube](https://img.shields.io/badge/YouTube-@chaitanya.eshwar.prasad-FF0000?style=for-the-badge&logo=youtube&logoColor=white)](https://www.youtube.com/@chaitanya.eshwar.prasad)
[![Instagram](https://img.shields.io/badge/Instagram-@acep.tech.in.telugu-E4405F?style=for-the-badge&logo=instagram&logoColor=white)](https://instagram.com/acep.tech.in.telugu)
