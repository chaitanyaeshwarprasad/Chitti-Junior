#include "board_jr.h"
#if JR_DISPLAY_OLED  // mini OLED build only
// Chitti Junior's face on the mini 128 x 64 OLED: two big glowing eyes with moods, a little mouth that moves
// with her voice, and one line of scrolling text (subtitle / status) at the bottom.
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "audio.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "face.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "lcd.h"

#define ON 0xFFFF
#define OFF 0x0000
#define EYE_L 40
#define EYE_R 88
#define EYE_Y 22
#define MOUTH_Y 42
#define TEXT_Y 51  // 13-pixel text row at the bottom

static SemaphoreHandle_t s_lock;
static volatile mood_t s_mood = MOOD_SLEEPY;
static volatile activity_t s_act = ACT_IDLE;
static char s_text[2][64], s_sub[400];
static int64_t s_text_at, s_sub_at;
static volatile float s_look_x, s_look_y;
static volatile int64_t s_poke_at;
static volatile bool s_dim, s_rest, s_night;
static volatile int64_t s_stress_until;

static int64_t now_ms(void) { return esp_timer_get_time() / 1000; }

mood_t face_mood_from_name(const char *n) {
    static const char *names[] = {"neutral", "happy", "excited", "calm", "thinking", "surprised", "sad",
                                  "sleepy", "playful", "angry", "laughing", "dancing", "driving"};
    for (int i = 0; i < MOOD_COUNT; i++)
        if (n && !strcmp(n, names[i])) return (mood_t)i;
    return MOOD_HAPPY;
}

void face_set_mood(mood_t m) { s_mood = m; }
void face_set_activity(activity_t a) { s_act = a; }
void face_look_at(float x, float y) { s_look_x = x, s_look_y = y; }
void face_poke(void) { s_poke_at = now_ms(); }
void face_set_dim(bool d) { s_dim = d; }
void face_set_rest(bool r) { s_rest = r; }
void face_set_night(bool n) { s_night = n; }
void face_set_battery(int pct, bool chg) {}
void face_set_touch_seen(void) {}
void face_stress_screen(int ms) { s_stress_until = now_ms() + ms; }

void face_set_text(const char *a, const char *b) {
    xSemaphoreTake(s_lock, portMAX_DELAY);
    snprintf(s_text[0], sizeof(s_text[0]), "%s", a ? a : "");
    snprintf(s_text[1], sizeof(s_text[1]), "%s", b ? b : "");
    s_text_at = now_ms();
    xSemaphoreGive(s_lock);
}

void face_set_subtitle(const char *t) {
    xSemaphoreTake(s_lock, portMAX_DELAY);
    snprintf(s_sub, sizeof(s_sub), "%s", t ? t : "");
    s_sub_at = now_ms();
    xSemaphoreGive(s_lock);
}

// ---------------------------------------------------------------- eyes and mouth
static void draw_eye(int cx, int cy, float blink, float squash, mood_t m, bool left) {
    int w = 22, h = 28;
    if (m == MOOD_EXCITED) w = 24, h = 30;
    if (m == MOOD_CALM) h = 16;
    h = (int)(h * blink * squash);
    w = (int)(w * (2 - squash));
    bool happy = m == MOOD_HAPPY || m == MOOD_LAUGHING || (m == MOOD_PLAYFUL && left);
    if (happy) {  // ^ ^
        lcd_ring_arc(cx, cy + 8, 12, 5, (float)M_PI * 1.08f, (float)M_PI * 1.92f, ON);
        return;
    }
    if (m == MOOD_SURPRISED) {
        lcd_circle(cx, cy, (int)(13 * fmaxf(blink, 0.15f)), ON);
        return;
    }
    if (m == MOOD_SLEEPY) {
        lcd_round_rect(cx - w / 2, cy + 6, w, 5, 2, ON);
        return;
    }
    if (h < 3) h = 3;
    lcd_round_rect(cx - w / 2, cy - h / 2, w, h, 8, ON);
    if (m == MOOD_SAD || m == MOOD_ANGRY) {  // slanted cut across the top of the eye
        bool outer_low = m == MOOD_SAD;
        for (int dx = -w / 2 - 1; dx <= w / 2 + 1; dx++) {
            float t = (dx + w / 2.0f) / w;
            float f = (left == outer_low) ? 1 - t : t;
            int cut = (int)(h * 0.45f * fmaxf(0, fminf(1, f)));
            if (cut > 0) lcd_rect(cx + dx, cy - h / 2 - 1, 1, cut + 1, OFF);
        }
    }
}

static void draw_mouth(mood_t m, activity_t a, float out, float mic, int64_t t) {
    int cx = 64, cy = MOUTH_Y;
    if (a == ACT_SPEAKING) {
        int h = 2 + (int)(fminf(out * 1.6f, 1) * 8);
        lcd_round_rect(cx - 8, cy - h / 2, 16, h, 2, ON);
        return;
    }
    if (a == ACT_LISTENING) {  // little sound bars
        for (int i = 0; i < 5; i++) {
            float wave = 0.35f + 0.65f * fabsf(sinf(t / 140.0f + i * 0.9f));
            int h = 2 + (int)(fminf(mic * 2.2f, 1) * 8 * wave);
            lcd_rect(cx - 12 + i * 5, cy - h / 2, 3, h, ON);
        }
        return;
    }
    switch (m) {
        case MOOD_HAPPY:
        case MOOD_EXCITED:
        case MOOD_PLAYFUL:
            lcd_ring_arc(cx, cy - 6, 8, 2, (float)M_PI * 0.2f, (float)M_PI * 0.8f, ON);
            break;
        case MOOD_LAUGHING:
            lcd_circle(cx, cy - 1, 6, ON);
            lcd_rect(cx - 7, cy - 8, 15, 7, OFF);
            break;
        case MOOD_SAD:
            lcd_ring_arc(cx, cy + 6, 7, 2, (float)M_PI * 1.2f, (float)M_PI * 1.8f, ON);
            break;
        case MOOD_SURPRISED:
            lcd_ring_arc(cx, cy, 4, 2, 0, 2 * (float)M_PI, ON);
            break;
        case MOOD_ANGRY:
        case MOOD_SLEEPY:
            lcd_rect(cx - 6, cy, 12, 2, ON);
            break;
        default:
            lcd_ring_arc(cx, cy - 5, 6, 2, (float)M_PI * 0.25f, (float)M_PI * 0.75f, ON);
            break;
    }
}

// ---------------------------------------------------------------- bottom text: scrolls when too long
static void draw_line(const char *s, int64_t since, int64_t t) {
    int w = lcd_text_width(s, FONT_SMALL);
    int x;
    if (w <= LCD_W) {
        x = (LCD_W - w) / 2;
    } else {  // marquee: pause 1 s, scroll at ~40 px/s, pause at the end
        int span = w - LCD_W + 4;
        int64_t e = t - since - 1000;
        int pos = e < 0 ? 0 : (int)(e * 40 / 1000);
        if (pos > span) pos = span;
        x = 2 - pos;
    }
    lcd_text(x, TEXT_Y, s, FONT_SMALL, ON);
}

static void draw_bottom(activity_t a, int64_t t) {
    char text[2][64], sub[400];
    int64_t text_at, sub_at;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    memcpy(text, s_text, sizeof(text));
    memcpy(sub, s_sub, sizeof(sub));
    text_at = s_text_at, sub_at = s_sub_at;
    xSemaphoreGive(s_lock);
    if (text[0][0] || text[1][0]) {  // status: alternate the two lines every 2.5 s
        int which = (text[1][0] && ((t - text_at) / 2500) % 2) ? 1 : 0;
        if (!text[which][0]) which = 0;
        draw_line(text[which], text_at + (which ? 2500 : 0), t);
        return;
    }
    if (sub[0]) {
        draw_line(sub, sub_at, t);
        return;
    }
    if (a == ACT_LISTENING) draw_line("Listening...", 0, t);
    else if (a == ACT_THINKING) draw_line("Thinking...", 0, t);
}

// ---------------------------------------------------------------- render loop
static void render_task(void *arg) {
    float lx = 0, ly = 0;
    int64_t next_blink = now_ms() + 3000, blink_at = 0;
    while (1) {
        int64_t t = now_ms();
        mood_t m = s_mood;
        activity_t a = s_act;
        if (s_dim) {  // asleep: screen off (saves the OLED too)
            lcd_backlight(0);
            vTaskDelay(pdMS_TO_TICKS(100));
            continue;
        }
        lcd_backlight(s_night ? 25 : s_rest ? 50 : 100);

        float tx = s_look_x, ty = s_look_y;
        if (a == ACT_THINKING || m == MOOD_THINKING) tx = 0.5f, ty = -0.6f;
        lx += (tx - lx) * 0.25f;
        ly += (ty - ly) * 0.25f;
        float blink = 1;
        if (t > next_blink) blink_at = t, next_blink = t + 2500 + esp_random() % 3500;
        if (t - blink_at < 160) blink = 0.12f + 0.88f * fabsf((t - blink_at) / 80.0f - 1);
        float squash = 1;
        if (t - s_poke_at < 350) squash = 1 - 0.25f * sinf((t - s_poke_at) / 350.0f * (float)M_PI);
        int bob = s_rest ? 0 : (int)roundf(1.5f * sinf(t / 900.0f));

        lcd_fill(OFF);
        int ex = (int)(lx * 8), ey = (int)(ly * 5) + bob;
        draw_eye(EYE_L + ex, EYE_Y + ey, blink, squash, m, true);
        draw_eye(EYE_R + ex, EYE_Y + ey, (m == MOOD_PLAYFUL && blink > 0.9f) ? 1 : blink, squash, m, false);
        draw_mouth(m, a, audio_out_level(), audio_mic_level(), t);
        if (a == ACT_THINKING || m == MOOD_THINKING)
            for (int i = 0; i < 3; i++) lcd_circle(104 + i * 7, 6 - (int)(2 * fabsf(sinf(t / 260.0f + i * 0.8f))), 2, ON);
        extern bool net_ready(void);
        if (!net_ready()) lcd_rect(LCD_W - 4, 0, 4, 4, ON);  // small square = not connected to the PC yet
        draw_bottom(a, t);

        if (t < s_stress_until) lcd_flush_all();
        else lcd_flush();
        vTaskDelay(pdMS_TO_TICKS(s_rest ? 90 : 30));
    }
}

void face_init(void) {
    s_lock = xSemaphoreCreateMutex();
    xTaskCreatePinnedToCore(render_task, "face", 6144, NULL, 4, NULL, 1);
}
#endif  // JR_DISPLAY_OLED
