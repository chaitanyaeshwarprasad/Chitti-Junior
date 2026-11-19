// Part of Chitti Junior by Chaitanya Eshwar Prasad (https://chaitanyaeshwarprasad.com). MIT licence.
#include "board_jr.h"
#if JR_DISPLAY_OLED  // mini OLED build only
// Chitti Junior: mini 128 x 64 OLED over I2C (SSD1306 or SH1106). Same drawing interface as the 3.5" screen
// (lcd.h); any non-black colour lights the pixel.
#include <math.h>
#include <string.h>

#include "driver/i2c_master.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "font_jr_big.h"
#include "font_jr_small.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lcd.h"

static const char *TAG = "oled";

typedef enum { CHIP_SSD1306, CHIP_SH1106 } chip_t;
static const struct {
    chip_t chip;
    bool flip;
    const char *name;
} VARIANTS[] = {
    {CHIP_SSD1306, false, "SSD1306"},
    {CHIP_SH1106, false, "SH1106"},
    {CHIP_SSD1306, true, "SSD1306 flipped"},
    {CHIP_SH1106, true, "SH1106 flipped"},
};
#define NV ((int)(sizeof(VARIANTS) / sizeof(VARIANTS[0])))

static i2c_master_dev_handle_t s_dev;
static uint8_t s_fb[LCD_W * LCD_H / 8], s_shown[LCD_W * LCD_H / 8];
static int s_var;
static bool s_ok, s_first = true;

int lcd_variant_count(void) { return NV; }
const char *lcd_variant_name(int v) { return VARIANTS[(v < 0 || v >= NV) ? 0 : v].name; }

static void cmds(const uint8_t *c, int n) {
    uint8_t buf[32];
    buf[0] = 0x00;  // command stream
    memcpy(buf + 1, c, n);
    if (s_ok && i2c_master_transmit(s_dev, buf, n + 1, 50) != ESP_OK) s_ok = false;
}

static i2c_master_bus_handle_t s_bus;
static uint16_t s_addr;
static int64_t s_next_try;
static volatile int s_bl_cur = -1;

// find the screen and start it; called at start-up and again every 2 s while it's missing
// (a loose wire pushed back in brings the face back without a restart)
static bool oled_connect(void) {
    // Find the screen only once (an address "ping" after it is running upsets some OLEDs).
    // Later calls just send the start-up commands again; the bus is unjammed only after a real failure.
    static bool s_failed;
    if (!s_dev) {
        uint16_t addr = 0;
        if (i2c_master_probe(s_bus, 0x3C, 50) == ESP_OK) addr = 0x3C;
        else if (i2c_master_probe(s_bus, 0x3D, 50) == ESP_OK) addr = 0x3D;
        if (!addr) return false;
        i2c_device_config_t dc = {.dev_addr_length = I2C_ADDR_BIT_LEN_7, .device_address = addr,
                                  .scl_speed_hz = 100000};  // slower = reliable on breadboard wires
        if (i2c_master_bus_add_device(s_bus, &dc, &s_dev) != ESP_OK) return false;
        s_addr = addr;
    } else if (s_failed) {
        i2c_master_bus_reset(s_bus);  // a message failed before: free a stuck data line
    }
    uint16_t addr = s_addr;
    s_ok = true;
    bool flip = VARIANTS[s_var].flip;
    uint8_t seg = flip ? 0xA0 : 0xA1, com = flip ? 0xC0 : 0xC8;
    if (VARIANTS[s_var].chip == CHIP_SSD1306) {
        const uint8_t init[] = {0xAE, 0xD5, 0x80, 0xA8, 0x3F, 0xD3, 0x00, 0x40, 0x8D, 0x14, 0x20, 0x00,
                                seg, com, 0xDA, 0x12, 0x81, 0xCF, 0xD9, 0xF1, 0xDB, 0x40, 0xA4, 0xA6};
        cmds(init, sizeof(init));
    } else {
        const uint8_t init[] = {0xAE, 0xD5, 0x80, 0xA8, 0x3F, 0xD3, 0x00, 0x40, 0xAD, 0x8B,
                                seg, com, 0xDA, 0x12, 0x81, 0x80, 0xD9, 0x22, 0xDB, 0x35, 0xA4, 0xA6};
        cmds(init, sizeof(init));
    }
    s_first = true;     // send the whole picture again
    s_bl_cur = -1;      // re-apply brightness
    cmds((const uint8_t[]){0xAF}, 1);  // display on
    s_failed = !s_ok;
    if (s_ok) ESP_LOGI(TAG, "mini screen ready at 0x%02X: %s (setting %d)", addr, VARIANTS[s_var].name, s_var + 1);
    return s_ok;
}

void lcd_init(int variant) {
    s_var = (variant < 0 || variant >= NV) ? 0 : variant;
    i2c_master_bus_config_t bc = {.i2c_port = I2C_NUM_0, .sda_io_num = PIN_OLED_SDA, .scl_io_num = PIN_OLED_SCL,
                                  .clk_source = I2C_CLK_SRC_DEFAULT, .glitch_ignore_cnt = 7,
                                  .flags.enable_internal_pullup = true};
    ESP_ERROR_CHECK(i2c_new_master_bus(&bc, &s_bus));
    // after power-on the OLED needs a moment before it listens; the ESP32 starts faster than that
    vTaskDelay(pdMS_TO_TICKS(250));
    for (int tries = 0; tries < 5 && !oled_connect(); tries++) vTaskDelay(pdMS_TO_TICKS(200));
    if (s_ok) {  // start it a second time: a screen still waking up can miss the first start-up commands
        vTaskDelay(pdMS_TO_TICKS(300));
        oled_connect();
    }
    if (!s_ok) ESP_LOGE(TAG, "mini screen not found: check VDD 3V3, GND, SDA -> 11, SCK -> 12 (retrying)");
    lcd_fill(0);
    lcd_flush_all();
}

void lcd_backlight(int pct) {  // OLEDs have no backlight: brightness = contrast, 0 = screen off
    if (pct == s_bl_cur || !s_ok) return;
    s_bl_cur = pct;
    if (pct <= 5) {
        cmds((const uint8_t[]){0xAE}, 1);
        return;
    }
    uint8_t c[3] = {0x81, (uint8_t)(pct * 255 / 100), 0xAF};
    cmds(c, 3);
}

static void send_page(int page) {
    uint8_t buf[1 + LCD_W];
    buf[0] = 0x40;  // data stream
    memcpy(buf + 1, s_fb + page * LCD_W, LCD_W);
    if (VARIANTS[s_var].chip == CHIP_SH1106) {
        uint8_t c[3] = {(uint8_t)(0xB0 | page), 0x02, 0x10};  // SH1106: 132 columns, picture starts at column 2
        cmds(c, 3);
    } else {
        uint8_t c[6] = {0x21, 0, LCD_W - 1, 0x22, (uint8_t)page, (uint8_t)page};
        cmds(c, 6);
    }
    if (s_ok && i2c_master_transmit(s_dev, buf, sizeof(buf), 100) != ESP_OK) s_ok = false;
    memcpy(s_shown + page * LCD_W, s_fb + page * LCD_W, LCD_W);
}

void lcd_flush_all(void) {
    for (int p = 0; p < LCD_H / 8; p++) send_page(p);
    s_first = false;
}

void lcd_flush(void) {  // only the 8-pixel rows that changed
    static int64_t s_rewake_at = 3000000;  // 3 s and 8 s after start: start the screen once more, just in case
    int64_t up = esp_timer_get_time();
    if (s_ok && s_rewake_at && up > s_rewake_at) {
        s_rewake_at = s_rewake_at < 8000000 ? 8000000 : 0;
        oled_connect();
    }
    if (!s_ok) {  // screen missing or a wire came loose: try again every 2 s
        int64_t now = esp_timer_get_time();
        if (now < s_next_try) return;
        s_next_try = now + 2000000;
        if (!oled_connect()) return;
    }
    if (s_first) {
        lcd_flush_all();
        return;
    }
    for (int p = 0; p < LCD_H / 8; p++)
        if (memcmp(s_fb + p * LCD_W, s_shown + p * LCD_W, LCD_W)) send_page(p);
}

bool lcd_touch_read(int *x, int *y) { return false; }

// ---------------------------------------------------------------- drawing (1 bit per pixel)
static inline void px(int x, int y, bool on) {
    if ((unsigned)x >= LCD_W || (unsigned)y >= LCD_H) return;
    uint8_t *b = &s_fb[(y >> 3) * LCD_W + x];
    if (on) *b |= 1 << (y & 7);
    else *b &= ~(1 << (y & 7));
}

void lcd_fill(uint16_t c) { memset(s_fb, c ? 0xFF : 0x00, sizeof(s_fb)); }

void lcd_rect(int x, int y, int w, int h, uint16_t c) {
    for (int yy = y; yy < y + h; yy++)
        for (int xx = x; xx < x + w; xx++) px(xx, yy, c != 0);
}

void lcd_round_rect(int x, int y, int w, int h, int r, uint16_t c) {
    if (w <= 0 || h <= 0) return;
    if (r * 2 > w) r = w / 2;
    if (r * 2 > h) r = h / 2;
    for (int yy = 0; yy < h; yy++) {
        int inset = 0;
        float dy = yy < r ? r - yy - 0.5f : (yy >= h - r ? yy - (h - r) + 0.5f : 0);
        if (dy > 0) inset = r - (int)(sqrtf(fmaxf(0, (float)r * r - dy * dy)) + 0.5f);
        lcd_rect(x + inset, y + yy, w - 2 * inset, 1, c);
    }
}

void lcd_circle(int cx, int cy, int r, uint16_t c) {
    for (int dy = -r; dy <= r; dy++) {
        int dx = (int)(sqrtf((float)(r * r - dy * dy)) + 0.5f);
        lcd_rect(cx - dx, cy + dy, 2 * dx + 1, 1, c);
    }
}

void lcd_ring_arc(int cx, int cy, int r, int thick, float a0, float a1, uint16_t c) {
    int ro = r + thick / 2, ri = r - (thick + 1) / 2;
    if (ri < 0) ri = 0;
    for (int y = -ro; y <= ro; y++)
        for (int x = -ro; x <= ro; x++) {
            int d2 = x * x + y * y;
            if (d2 > ro * ro || d2 < ri * ri) continue;
            float a = atan2f((float)y, (float)x);
            while (a < a0) a += 2 * (float)M_PI;
            if (a <= a1) px(cx + x, cy + y, c != 0);
        }
}

int lcd_font_h(lcd_font_t f) { return f == FONT_BIG ? JRB_H : JRS_H; }
int lcd_font_w(lcd_font_t f) { return f == FONT_BIG ? JRB_W : JRS_W; }
int lcd_text_width(const char *s, lcd_font_t f) { return (int)strlen(s) * lcd_font_w(f); }

int lcd_text(int x, int y, const char *s, lcd_font_t f, uint16_t c) {
    int w = lcd_font_w(f), h = lcd_font_h(f), x0 = x;
    for (; *s; s++) {
        int ch = (unsigned char)*s;
        if (ch < 32 || ch > 126) ch = '?';
        for (int yy = 0; yy < h; yy++)
            for (int xx = 0; xx < w; xx++) {
                int a = f == FONT_BIG ? jrb_data[ch - 32][yy][xx] : jrs_data[ch - 32][yy][xx];
                if (a > 110) px(x + xx, y + yy, c != 0);
            }
        x += w;
    }
    return x - x0;
}
#endif  // JR_DISPLAY_OLED
