// Chitti Junior firmware by Chaitanya Eshwar Prasad (https://chaitanyaeshwarprasad.com). MIT licence.
// Pin map: ESP32-S3-N16R8 DevKitC-1 + 3.5" SPI touch screen (ILI9488 or ST7796, XPT2046 touch)
//          + INMP441 microphone + MAX98357A amplifier. Must match ASSEMBLY.md.
#pragma once

// 3.5" screen (SPI) and its touch chip share the clock / data lines
#define PIN_SPI_MOSI 11  // screen SDI + touch T_DIN
#define PIN_SPI_SCLK 12  // screen SCK + touch T_CLK
#define PIN_SPI_MISO 13  // screen SDO + touch T_DO
#define PIN_LCD_CS 10
#define PIN_LCD_DC 9
#define PIN_LCD_RST 14
#define PIN_LCD_BL 21    // optional: only if the screen's LED pin is wired here instead of 3V3
#define PIN_TOUCH_CS 15
#define PIN_TOUCH_IRQ 16 // LOW while the screen is pressed

// INMP441 microphone (I2S)
#define PIN_MIC_SCK 4
#define PIN_MIC_WS 5
#define PIN_MIC_SD 6

// MAX98357A amplifier (I2S)
#define PIN_AMP_SD 7     // HIGH = amplifier on
#define PIN_AMP_DIN 8
#define PIN_AMP_BCLK 17
#define PIN_AMP_LRC 18

// Buttons: the BOOT button on the ESP32 board; an optional extra button from GPIO 2 to GND
#define PIN_BTN_BOOT 0
#define PIN_BTN_EXTRA 2

// Which screen Junior uses: 1 = mini 4-pin OLED (I2C, 128 x 64), 0 = 3.5" SPI screen (480 x 320)
#define JR_DISPLAY_OLED 1

// mini OLED (SSD1306 or SH1106, I2C): VDD -> 3V3, GND -> GND, SDA -> 11, SCK/SCL -> 12
#define PIN_OLED_SDA 11
#define PIN_OLED_SCL 12

#if JR_DISPLAY_OLED
#define LCD_W 128
#define LCD_H 64
#else
#define LCD_W 480  // landscape
#define LCD_H 320
#endif
