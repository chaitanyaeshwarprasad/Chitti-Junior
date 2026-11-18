#include "board_jr.h"
#if !JR_DISPLAY_OLED  // 3.5" screen build only
// Chitti Junior: 3.5" 480 x 320 SPI screen (ILI9488 or ST7796) + XPT2046 touch.
// The face is drawn into a frame in PSRAM; only the 32 x 16 tiles that changed are sent, so the
// animation stays smooth even though a full frame over SPI is slow (ILI9488 needs 3 bytes per pixel).
#include "lcd.h"

#include <math.h>
#include <string.h>

#include "driver/gpio.h"
#include "driver/ledc.h"
#include "driver/spi_master.h"
#include "esp_heap_caps.h"
#include "esp_lcd_panel_io.h"
#include "esp_log.h"
#include "font.h"
#include "font_small.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

static const char *TAG = "lcd";

typedef enum { CHIP_ILI9488, CHIP_ST7796 } chip_t;
typedef struct {
    chip_t chip;
    uint8_t madctl;
    bool invert;
    const char *name;
} variant_t;

// 3.5" SPI screens use one of two chips and differ in orientation / colour order. The screen test
// (first start, or hold BOOT 6 s) lets the user pick the setting that looks right.
static const variant_t VARIANTS[] = {
    {CHIP_ILI9488, 0x28, false, "ILI9488"},
    {CHIP_ILI9488, 0xE8, false, "ILI9488 flipped"},
    {CHIP_ILI9488, 0x20, false, "ILI9488 RGB"},
    {CHIP_ST7796, 0x28, true, "ST7796"},
    {CHIP_ST7796, 0xE8, true, "ST7796 flipped"},
    {CHIP_ST7796, 0x28, false, "ST7796 no-invert"},
    {CHIP_ST7796, 0x20, true, "ST7796 RGB"},
    {CHIP_ILI9488, 0x28, true, "ILI9488 inverted"},
};
#define NV ((int)(sizeof(VARIANTS) / sizeof(VARIANTS[0])))

#define TW 32
#define TH 16
#define TX (LCD_W / TW)
#define TY (LCD_H / TH)

static esp_lcd_panel_io_handle_t s_io;
static spi_device_handle_t s_touch;
static uint16_t *s_fb, *s_prev;  // current frame and what the screen shows (RGB565, PSRAM)
static uint8_t *s_line;          // DMA chunk in internal RAM
static SemaphoreHandle_t s_done;
static int s_var;
static bool s_first = true;

static bool on_done(esp_lcd_panel_io_handle_t io, esp_lcd_panel_io_event_data_t *e, void *ctx) {
    BaseType_t woken = pdFALSE;
    xSemaphoreGiveFromISR(s_done, &woken);
    return woken == pdTRUE;
}

static void cmd(uint8_t c, const uint8_t *d, int n) { esp_lcd_panel_io_tx_param(s_io, c, d, n); }

int lcd_variant_count(void) { return NV; }
const char *lcd_variant_name(int v) { return VARIANTS[(v < 0 || v >= NV) ? 0 : v].name; }

static void init_ili9488(void) {
    cmd(0xE0, (uint8_t[]){0x00, 0x03, 0x09, 0x08, 0x16, 0x0A, 0x3F, 0x78, 0x4C, 0x09, 0x0A, 0x08, 0x16, 0x1A, 0x0F}, 15);
    cmd(0xE1, (uint8_t[]){0x00, 0x16, 0x19, 0x03, 0x0F, 0x05, 0x32, 0x45, 0x46, 0x04, 0x0E, 0x0D, 0x35, 0x37, 0x0F}, 15);
    cmd(0xC0, (uint8_t[]){0x17, 0x15}, 2);
    cmd(0xC1, (uint8_t[]){0x41}, 1);
    cmd(0xC5, (uint8_t[]){0x00, 0x12, 0x80}, 3);
    cmd(0x3A, (uint8_t[]){0x66}, 1);  // 18-bit colour: the only format ILI9488 accepts over SPI
    cmd(0xB0, (uint8_t[]){0x00}, 1);
    cmd(0xB1, (uint8_t[]){0xA0}, 1);
    cmd(0xB4, (uint8_t[]){0x02}, 1);
    cmd(0xB6, (uint8_t[]){0x02, 0x02, 0x3B}, 3);
    cmd(0xE9, (uint8_t[]){0x00}, 1);
    cmd(0xF7, (uint8_t[]){0xA9, 0x51, 0x2C, 0x82}, 4);
}

static void init_st7796(void) {
    cmd(0xF0, (uint8_t[]){0xC3}, 1);
    cmd(0xF0, (uint8_t[]){0x96}, 1);
    cmd(0x3A, (uint8_t[]){0x55}, 1);  // 16-bit colour
    cmd(0xB4, (uint8_t[]){0x01}, 1);
    cmd(0xB6, (uint8_t[]){0x80, 0x02, 0x3B}, 3);
    cmd(0xE8, (uint8_t[]){0x40, 0x8A, 0x00, 0x00, 0x29, 0x19, 0xA5, 0x33}, 8);
    cmd(0xC1, (uint8_t[]){0x06}, 1);
    cmd(0xC2, (uint8_t[]){0xA7}, 1);
    cmd(0xC5, (uint8_t[]){0x18}, 1);
    cmd(0xE0, (uint8_t[]){0xF0, 0x09, 0x0B, 0x06, 0x04, 0x15, 0x2F, 0x54, 0x42, 0x3C, 0x17, 0x14, 0x18, 0x1B}, 14);
    cmd(0xE1, (uint8_t[]){0xE0, 0x09, 0x0B, 0x06, 0x04, 0x03, 0x2B, 0x43, 0x42, 0x3B, 0x16, 0x14, 0x17, 0x1B}, 14);
    cmd(0xF0, (uint8_t[]){0x3C}, 1);
    cmd(0xF0, (uint8_t[]){0x69}, 1);
}

void lcd_init(int variant) {
    s_var = (variant < 0 || variant >= NV) ? 0 : variant;
    const variant_t *v = &VARIANTS[s_var];
    s_done = xSemaphoreCreateBinary();
    xSemaphoreGive(s_done);

    spi_bus_config_t bus = {
        .mosi_io_num = PIN_SPI_MOSI, .miso_io_num = PIN_SPI_MISO, .sclk_io_num = PIN_SPI_SCLK,
        .quadwp_io_num = -1, .quadhd_io_num = -1, .max_transfer_sz = LCD_W * TH * 3 + 16,
    };
    ESP_ERROR_CHECK(spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_CH_AUTO));
    esp_lcd_panel_io_spi_config_t io = {
        .dc_gpio_num = PIN_LCD_DC, .cs_gpio_num = PIN_LCD_CS, .pclk_hz = 20 * 1000 * 1000,  // gentler on breadboard wires (less noise in the speaker)
        .lcd_cmd_bits = 8, .lcd_param_bits = 8, .spi_mode = 0, .trans_queue_depth = 4,
        .on_color_trans_done = on_done,
    };
    ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)SPI2_HOST, &io, &s_io));
    // weaker pin drive: less ringing / radio noise on long jumper wires
    const int spi_pins[] = {PIN_SPI_MOSI, PIN_SPI_SCLK, PIN_LCD_CS, PIN_LCD_DC};
    for (int i = 0; i < 4; i++) gpio_set_drive_capability(spi_pins[i], GPIO_DRIVE_CAP_1);

    // touch chip on the same bus, slow clock
    spi_device_interface_config_t tc = {.clock_speed_hz = 2 * 1000 * 1000, .mode = 0, .spics_io_num = PIN_TOUCH_CS, .queue_size = 1};
    spi_bus_add_device(SPI2_HOST, &tc, &s_touch);
    gpio_config_t irq = {.pin_bit_mask = 1ULL << PIN_TOUCH_IRQ, .mode = GPIO_MODE_INPUT, .pull_up_en = GPIO_PULLUP_ENABLE};
    gpio_config(&irq);

    gpio_config_t rst = {.pin_bit_mask = 1ULL << PIN_LCD_RST, .mode = GPIO_MODE_OUTPUT};
    gpio_config(&rst);
    gpio_set_level(PIN_LCD_RST, 0);
    vTaskDelay(pdMS_TO_TICKS(20));
    gpio_set_level(PIN_LCD_RST, 1);
    vTaskDelay(pdMS_TO_TICKS(150));

    cmd(0x01, NULL, 0);  // software reset
    vTaskDelay(pdMS_TO_TICKS(150));
    cmd(0x11, NULL, 0);  // wake from sleep
    vTaskDelay(pdMS_TO_TICKS(150));
    if (v->chip == CHIP_ILI9488) init_ili9488();
    else init_st7796();
    cmd(0x36, &v->madctl, 1);
    cmd(v->invert ? 0x21 : 0x20, NULL, 0);

    s_fb = heap_caps_malloc(LCD_W * LCD_H * 2, MALLOC_CAP_SPIRAM);
    s_prev = heap_caps_malloc(LCD_W * LCD_H * 2, MALLOC_CAP_SPIRAM);
    s_line = heap_caps_malloc(LCD_W * TH * 3, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    assert(s_fb && s_prev && s_line);
    lcd_fill(0);
    lcd_flush_all();
    cmd(0x29, NULL, 0);  // display on
    vTaskDelay(pdMS_TO_TICKS(20));

    ledc_timer_config_t t = {.speed_mode = LEDC_LOW_SPEED_MODE, .duty_resolution = LEDC_TIMER_10_BIT,
                             .timer_num = LEDC_TIMER_0, .freq_hz = 5000, .clk_cfg = LEDC_AUTO_CLK};
    ledc_timer_config(&t);
    ledc_channel_config_t ch = {.gpio_num = PIN_LCD_BL, .speed_mode = LEDC_LOW_SPEED_MODE, .channel = LEDC_CHANNEL_0,
                                .timer_sel = LEDC_TIMER_0, .duty = 1023};
    ledc_channel_config(&ch);
    ESP_LOGI(TAG, "screen ready: %s (setting %d)", v->name, s_var + 1);
}

void lcd_backlight(int pct) {
    pct = pct < 0 ? 0 : pct > 100 ? 100 : pct;
    ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0, pct * 1023 / 100);
    ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0);
}

// send one rectangle (x0..x0+w-1, y0..y0+h-1, h <= TH) from the frame
static void send_rect(int x0, int y0, int w, int h) {
    uint8_t ca[4] = {(uint8_t)(x0 >> 8), (uint8_t)x0, (uint8_t)((x0 + w - 1) >> 8), (uint8_t)(x0 + w - 1)};
    uint8_t ra[4] = {(uint8_t)(y0 >> 8), (uint8_t)y0, (uint8_t)((y0 + h - 1) >> 8), (uint8_t)(y0 + h - 1)};
    xSemaphoreTake(s_done, portMAX_DELAY);  // the DMA chunk is free again
    cmd(0x2A, ca, 4);
    cmd(0x2B, ra, 4);
    bool rgb666 = VARIANTS[s_var].chip == CHIP_ILI9488;
    uint8_t *o = s_line;
    for (int y = y0; y < y0 + h; y++) {
        const uint16_t *p = s_fb + y * LCD_W + x0;
        for (int x = 0; x < w; x++) {
            uint16_t c = p[x];
            if (rgb666) {
                *o++ = (c >> 8) & 0xF8;
                *o++ = (c >> 3) & 0xFC;
                *o++ = (c << 3) & 0xF8;
            } else {
                *o++ = c >> 8;
                *o++ = c & 0xFF;
            }
        }
        memcpy(s_prev + y * LCD_W + x0, p, w * 2);
    }
    esp_lcd_panel_io_tx_color(s_io, 0x2C, s_line, o - s_line);
}

static void wait_idle(void) {
    xSemaphoreTake(s_done, portMAX_DELAY);
    xSemaphoreGive(s_done);
}

void lcd_flush_all(void) {
    for (int ty = 0; ty < TY; ty++) send_rect(0, ty * TH, LCD_W, TH);
    wait_idle();
    s_first = false;
}

static bool tile_changed(int tx, int ty) {
    for (int y = ty * TH; y < ty * TH + TH; y++)
        if (memcmp(s_fb + y * LCD_W + tx * TW, s_prev + y * LCD_W + tx * TW, TW * 2)) return true;
    return false;
}

void lcd_flush(void) {
    if (s_first) {
        lcd_flush_all();
        return;
    }
    for (int ty = 0; ty < TY; ty++) {
        int run = -1;
        for (int tx = 0; tx <= TX; tx++) {
            bool ch = tx < TX && tile_changed(tx, ty);
            if (ch && run < 0) run = tx;
            if (!ch && run >= 0) {
                send_rect(run * TW, ty * TH, (tx - run) * TW, TH);
                run = -1;
            }
        }
    }
    wait_idle();
}

// ---------------------------------------------------------------- touch (XPT2046)
static int touch_axis(uint8_t c) {
    uint8_t tx[3] = {c, 0, 0}, rx[3] = {0};
    spi_transaction_t t = {.length = 24, .tx_buffer = tx, .rx_buffer = rx};
    if (spi_device_polling_transmit(s_touch, &t) != ESP_OK) return -1;
    return ((rx[1] << 8) | rx[2]) >> 3;  // 12-bit
}

bool lcd_touch_read(int *x, int *y) {
    if (gpio_get_level(PIN_TOUCH_IRQ)) return false;  // HIGH = nothing pressing
    wait_idle();                                      // don't talk to the touch chip mid-frame
    int z1 = touch_axis(0xB1), rx = touch_axis(0xD1), ry = touch_axis(0x91);
    if (z1 < 80) return false;  // too light: noise
    // rough mapping, good enough to know where the face was pressed
    int px = (rx - 250) * LCD_W / 3600, py = (ry - 250) * LCD_H / 3600;
    *x = px < 0 ? 0 : px >= LCD_W ? LCD_W - 1 : px;
    *y = py < 0 ? 0 : py >= LCD_H ? LCD_H - 1 : py;
    return true;
}

// ---------------------------------------------------------------- drawing
void lcd_fill(uint16_t c) {
    for (int i = 0; i < LCD_W * LCD_H; i++) s_fb[i] = c;
}

void lcd_rect(int x, int y, int w, int h, uint16_t c) {
    int x0 = x < 0 ? 0 : x, y0 = y < 0 ? 0 : y;
    int x1 = x + w > LCD_W ? LCD_W : x + w, y1 = y + h > LCD_H ? LCD_H : y + h;
    for (int yy = y0; yy < y1; yy++) {
        uint16_t *p = s_fb + yy * LCD_W;
        for (int xx = x0; xx < x1; xx++) p[xx] = c;
    }
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
    for (int y = -ro; y <= ro; y++) {
        int Y = cy + y;
        if ((unsigned)Y >= LCD_H) continue;
        for (int x = -ro; x <= ro; x++) {
            int d2 = x * x + y * y;
            if (d2 > ro * ro || d2 < ri * ri) continue;
            float a = atan2f((float)y, (float)x);
            while (a < a0) a += 2 * (float)M_PI;
            int X = cx + x;
            if (a <= a1 && (unsigned)X < LCD_W) s_fb[Y * LCD_W + X] = c;
        }
    }
}

static uint16_t blend(uint16_t bg, uint16_t fg, int a) {
    int r = (((fg >> 11) & 31) * a + ((bg >> 11) & 31) * (255 - a)) / 255;
    int g = (((fg >> 5) & 63) * a + ((bg >> 5) & 63) * (255 - a)) / 255;
    int b = ((fg & 31) * a + (bg & 31) * (255 - a)) / 255;
    return (uint16_t)((r << 11) | (g << 5) | b);
}

int lcd_font_h(lcd_font_t f) { return f == FONT_BIG ? FONT_H : SUB_H; }
int lcd_font_w(lcd_font_t f) { return f == FONT_BIG ? FONT_W : SUB_W; }
int lcd_text_width(const char *s, lcd_font_t f) { return (int)strlen(s) * lcd_font_w(f); }

int lcd_text(int x, int y, const char *s, lcd_font_t f, uint16_t c) {
    int w = lcd_font_w(f), h = lcd_font_h(f), x0 = x;
    for (; *s; s++) {
        int ch = (unsigned char)*s;
        if (ch < 32 || ch > 126) ch = '?';
        for (int yy = 0; yy < h; yy++) {
            int Y = y + yy;
            if ((unsigned)Y >= LCD_H) continue;
            for (int xx = 0; xx < w; xx++) {
                int a = f == FONT_BIG ? font_data[ch - 32][yy][xx] : sub_data[ch - 32][yy][xx];
                int X = x + xx;
                if (a && (unsigned)X < LCD_W) s_fb[Y * LCD_W + X] = blend(s_fb[Y * LCD_W + X], c, a);
            }
        }
        x += w;
    }
    return x - x0;
}

#endif  // !JR_DISPLAY_OLED
