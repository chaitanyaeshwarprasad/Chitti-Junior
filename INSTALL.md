# Installing Chitti Junior: the complete beginner guide

By **Chaitanya Eshwar Prasad**. This guide follows the build video on [YouTube @chaitanya.eshwar.prasad](https://www.youtube.com/@chaitanya.eshwar.prasad) step by step. It takes about **60 minutes** the first time, including wiring.

You need:
- the 7 parts from the [README](README.md#1-parts-i-used);
- a Windows 10 or 11 PC or laptop;
- 2.4 GHz Wi-Fi.

---

## Step 0: Get your own API keys

**Anthropic (required)**
1. At https://console.anthropic.com, create an account.
2. Under **Billing**, add a small amount of credit.
3. Under **Limits**, set a monthly spend limit.
4. Under **API Keys**, choose **Create Key** and copy it (`sk-ant-...`).

**Sarvam AI (optional, recommended for Telugu)**
At https://dashboard.sarvam.ai, create an API key.

Never share your keys or show them in a video or screenshot.

---

## Step 1: Build Junior

Follow **[ASSEMBLY.md](ASSEMBLY.md)** or the printable **[PDF guide](Chitti-Junior-Assembly-Guide.pdf)**. Work one device at a time, with the USB cable unplugged:

1. speaker → amplifier (2 wires);
2. amplifier → ESP32 (6 wires);
3. microphone → ESP32 (6 wires);
4. mini screen → ESP32 (4 wires);
5. check every wire.

> 📷 *photo: finished breadboard*

---

## Step 2: Download Chitti Junior

On GitHub, open **Releases**, download the latest ZIP (or choose **Code**, then **Download ZIP**), and unzip it to a simple place like `C:\ChittiJunior`. Avoid OneDrive folders.

---

## Step 3: Start Chitti Junior (`Start Chitti Junior.bat`)

1. Double-click **`Start Chitti Junior.bat`**. If Windows says "protected your PC", choose **More info**, then **Run anyway**.
2. The first time, it checks your PC in 8 steps and installs what's missing (5–10 minutes). Press **Enter** for Yes when asked. Say yes to **allowing Chitti through the firewall**.
3. The dashboard opens with the **setup wizard**. Keep the black window open while you use Junior.

---

## Step 4: Setup wizard, part 1: keys

Paste your **Claude key** and choose **Save**. Paste your **Sarvam key** too, if you have one. Tick **"I use it at my own risk"**, then choose **Next**.

---

## Step 5: Setup wizard, part 2: install Junior's software

1. Plug the ESP32's port marked **COM** into the PC with a USB-C **data** cable. The wizard shows **Board found on COM…** ✓
2. Leave **Erase the board first** ticked.
3. Choose **Install Chitti Junior** and wait for the progress bar (about 1 minute).
4. Junior restarts and shows the **screen test**: a frame and some text.
   - **Readable and complete?** Hold **BOOT** for 2 seconds.
   - **Not right?** Press **BOOT** once to try the next setting.

**If it fails:** try another data cable, close Arduino IDE or serial monitors, or hold **BOOT**, tap **RST**, release BOOT, then choose **Try again**.

---

## Step 6: Setup wizard, parts 3 and 4: Wi-Fi and say hi

1. Move the cable to the ESP32's port marked **USB** (not COM) and wait 5 seconds.
2. Type your **2.4 GHz** Wi-Fi name and password (not a guest network), then choose **Send to Chitti**.
   - Phone alternative: join the Wi-Fi network **ChittiJr-Setup**, open **192.168.4.1** and enter your Wi-Fi.
3. When the wizard says **Junior online! 🎉**, press **BOOT** and say **"హాయ్ చిట్టి!"**

Now you can power Junior from a phone charger or power bank instead of the PC.

---

## Every day after that

1. Power Junior.
2. Double-click **`Start Chitti Junior.bat`**, and keep the PC awake.
3. Press **BOOT** and talk!

---

## Still stuck?

See [Troubleshooting](README.md#9-troubleshooting), or the dashboard's **Help** section.

*Chitti Junior is free and provided as-is. The author is not responsible for any damage, data loss or API charges. See the [disclaimer](README.md#11-disclaimer-please-read).*

---

## 👤 About the Author

Created and maintained by **Chaitanya Eshwar Prasad**. Connect with me or follow my work:

[![Website](https://img.shields.io/badge/Website-chaitanyaeshwarprasad.com-005F9E?style=for-the-badge&logo=google-chrome&logoColor=white)](https://chaitanyaeshwarprasad.com)
[![GitHub](https://img.shields.io/badge/GitHub-chaitanyaeshwarprasad-181717?style=for-the-badge&logo=github&logoColor=white)](https://github.com/chaitanyaeshwarprasad)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-Chaitanya%20Eshwar%20Prasad-0A66C2?style=for-the-badge&logo=linkedin&logoColor=white)](https://linkedin.com/in/chaitanya-eshwar-prasad)
[![YouTube](https://img.shields.io/badge/YouTube-@chaitanya.eshwar.prasad-FF0000?style=for-the-badge&logo=youtube&logoColor=white)](https://www.youtube.com/@chaitanya.eshwar.prasad)
[![Instagram](https://img.shields.io/badge/Instagram-@acep.tech.in.telugu-E4405F?style=for-the-badge&logo=instagram&logoColor=white)](https://instagram.com/acep.tech.in.telugu)
