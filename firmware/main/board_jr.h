// Chitti Junior firmware by Chaitanya Eshwar Prasad (https://chaitanyaeshwarprasad.com). MIT licence.
// Pin map: ESP32-S3 N16R8 development board + 0.96-inch OLED I2C display (4-pin) + INMP441 I2S MEMS microphone
//          + MAX98357A I2S mono amplifier + 8 ohm speaker. Must match ASSEMBLY.md.
#pragma once

#define JR_DISPLAY_OLED 1

// 0.96-inch OLED I2C display (SSD1306 or SH1106): VDD -> 3V3, GND -> GND
#define PIN_OLED_SDA 11
#define PIN_OLED_SCL 12  // printed SCK or SCL on the screen

// INMP441 microphone (I2S): VDD -> 3V3, GND -> GND, L/R -> GND
#define PIN_MIC_SCK 4
#define PIN_MIC_WS 5
#define PIN_MIC_SD 6

// MAX98357A amplifier (I2S): VIN -> 5V, GND -> GND, GAIN not connected
#define PIN_AMP_SD 7     // HIGH = amplifier on
#define PIN_AMP_DIN 8
#define PIN_AMP_BCLK 17
#define PIN_AMP_LRC 18

// Buttons: the BOOT button on the ESP32 board = talk; an optional extra button from GPIO 2 to GND
#define PIN_BTN_BOOT 0
#define PIN_BTN_EXTRA 2

#define LCD_W 128
#define LCD_H 64
