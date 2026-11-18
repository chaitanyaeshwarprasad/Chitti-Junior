#pragma once
#include <stdbool.h>
#include <stdint.h>

#include "board_jr.h"

// Colours are normal RGB565 values (e.g. 0xF800 = red).
#define RGB565(r, g, b) ((uint16_t)((((r) & 0xF8) << 8) | (((g) & 0xFC) << 3) | ((b) >> 3)))

typedef enum { FONT_SMALL, FONT_BIG } lcd_font_t;

void lcd_init(int variant);          // variant 0..lcd_variant_count()-1: screen chip / orientation / colours
int lcd_variant_count(void);
const char *lcd_variant_name(int variant);
void lcd_backlight(int percent);     // 0..100 (only if LED is wired to GPIO 21)
void lcd_flush(void);                // send what changed since the last flush
void lcd_flush_all(void);            // send the whole frame

// touch (XPT2046): true while pressed. x, y in screen pixels (approximate, uncalibrated).
bool lcd_touch_read(int *x, int *y);

// drawing into the frame (nothing shows until lcd_flush)
void lcd_fill(uint16_t c);
void lcd_rect(int x, int y, int w, int h, uint16_t c);
void lcd_round_rect(int x, int y, int w, int h, int r, uint16_t c);
void lcd_circle(int cx, int cy, int r, uint16_t c);
void lcd_ring_arc(int cx, int cy, int r, int thick, float a0, float a1, uint16_t c);  // radians, a0 < a1
int lcd_text(int x, int y, const char *s, lcd_font_t f, uint16_t c);
int lcd_text_width(const char *s, lcd_font_t f);
int lcd_font_h(lcd_font_t f);
int lcd_font_w(lcd_font_t f);
