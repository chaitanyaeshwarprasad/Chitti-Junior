#include "board_jr.h"
#if !JR_DISPLAY_OLED  // 3.5" screen build only
// Chitti Junior's face on the 3.5" screen: white robot head, black visor, glowing cyan eyes,
// a mouth that moves with her voice, and subtitles / status text underneath.
#include "face.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "audio.h"
#include "esp_log.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "lcd.h"

#define C_HEAD RGB565(228, 235, 240)
#define C_HEAD_DARK RGB565(196, 206, 214)
#define C_HAIR RGB565(63, 224, 197)
#define C_VISOR RGB565(11, 14, 18)
#define C_EYE RGB565(127, 248, 255)
#define C_EYE_DIM RGB565(40, 90, 96)
#define C_TEXT RGB565(20, 26, 34)
#define C_MUTED RGB565(92, 104, 118)
#define C_OK RGB565(34, 197, 94)
#define C_WAIT RGB565(245, 158, 11)
#define C_RED RGB565(239, 68, 68)

#define VX 30
#define VY 34
#define VW 420
#define VH 196
#define EYE_L 165
#define EYE_R 315
#define EYE_Y 122
#define MOUTH_Y 194
#define BAND_Y 242

static SemaphoreHandle_t s_lock;
static volatile mood_t s_mood = MOOD_SLEEPY;
static volatile activity_t s_act = ACT_IDLE;
static char s_text[2][64];
static char s_sub[400];
static int64_t s_sub_at;
static volatile float s_look_x, s_look_y;
static volatile int64_t s_poke_at;
static volatile bool s_dim, s_rest, s_night;
static volatile int s_batt = -1;
static volatile bool s_chg;
static volatile bool s_touch_seen;  // a touch screen answered: show "tap my face" instead of "press the button"

void face_set_touch_seen(void) { s_touch_seen = true; }

static int64_t now_ms(void) { return esp_timer_get_time() / 1000; }

static volatile int64_t s_stress_until;  // sound test: redraw the whole screen non-stop
void face_stress_screen(int ms) { s_stress_until = now_ms() + ms; }

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
void face_set_battery(int pct, bool chg) { s_batt = pct, s_chg = chg; }

void face_set_text(const char *a, const char *b) {
    xSemaphoreTake(s_lock, portMAX_DELAY);
    snprintf(s_text[0], sizeof(s_text[0]), "%s", a ? a : "");
    snprintf(s_text[1], sizeof(s_text[1]), "%s", b ? b : "");
    xSemaphoreGive(s_lock);
}

void face_set_subtitle(const char *t) {
    xSemaphoreTake(s_lock, portMAX_DELAY);
    snprintf(s_sub, sizeof(s_sub), "%s", t ? t : "");
    s_sub_at = now_ms();
    xSemaphoreGive(s_lock);
}

// ---------------------------------------------------------------- eyes and mouth
// cut the top of an eye along a slanted line (sad: outer side lower, angry: inner side lower)
static void slant_cut(int cx, int top, int w, int h, bool outer_low, bool left_eye) {
    int depth = h * 45 / 100;
    for (int dx = -w / 2 - 2; dx <= w / 2 + 2; dx++) {
        float t = (dx + w / 2.0f) / (float)w;  // 0 at the eye's left edge, 1 at the right
        bool low_side_left = left_eye ? outer_low : !outer_low;
        float f = low_side_left ? 1 - t : t;   // 1 where the cut is deepest
        int cut = (int)(depth * fmaxf(0, fminf(1, f)));
        if (cut > 0) lcd_rect(cx + dx, top - 2, 1, cut + 2, C_VISOR);
    }
}

static void draw_eye(int cx, int cy, float blink, float squash, mood_t m, bool left) {
    int w = 56, h = 90;
    uint16_t c = s_dim ? C_EYE_DIM : C_EYE;
    if (m == MOOD_EXCITED) w = 64, h = 100;
    if (m == MOOD_CALM) h = 50;
    h = (int)(h * blink * squash);
    w = (int)(w * (2 - squash));

    bool happy = m == MOOD_HAPPY || m == MOOD_LAUGHING || (m == MOOD_PLAYFUL && left);
    if (happy) {  // ^ shaped, smiling eyes
        lcd_ring_arc(cx, cy + 18, 30, 14, (float)M_PI * 1.08f, (float)M_PI * 1.92f, c);
        return;
    }
    if (m == MOOD_SURPRISED) {
        lcd_circle(cx, cy, (int)(36 * fmaxf(blink, 0.15f)), c);
        return;
    }
    if (m == MOOD_SLEEPY) {
        lcd_round_rect(cx - w / 2, cy + 18, w, 12, 6, c);
        return;
    }
    if (h < 8) h = 8;
    lcd_round_rect(cx - w / 2, cy - h / 2, w, h, w / 2, c);
    if (m == MOOD_SAD) slant_cut(cx, cy - h / 2, w, h, true, left);
    if (m == MOOD_ANGRY) slant_cut(cx, cy - h / 2, w, h, false, left);
}

static void draw_mouth(mood_t m, activity_t a, float out, float mic, int64_t t) {
    uint16_t c = s_dim ? C_EYE_DIM : C_EYE;
    int cx = 240, cy = MOUTH_Y;
    if (a == ACT_SPEAKING) {
        int h = 10 + (int)(fminf(out * 1.6f, 1) * 40);
        lcd_round_rect(cx - 32, cy - h / 2, 64, h, h < 20 ? h / 2 : 10, c);
        return;
    }
    if (a == ACT_LISTENING) {  // sound bars follow your voice
        for (int i = 0; i < 7; i++) {
            float wave = 0.35f + 0.65f * fabsf(sinf(t / 140.0f + i * 0.9f));
            int h = 6 + (int)(fminf(mic * 2.2f, 1) * 34 * wave);
            lcd_round_rect(cx - 63 + i * 18, cy - h / 2, 10, h, 5, c);
        }
        return;
    }
    switch (m) {
        case MOOD_HAPPY:
        case MOOD_EXCITED:
        case MOOD_PLAYFUL:
            lcd_ring_arc(cx, cy - 26, 34, 8, (float)M_PI * 0.18f, (float)M_PI * 0.82f, c);
            break;
        case MOOD_LAUGHING:  // open "D" mouth
            lcd_circle(cx, cy - 8, 30, c);
            lcd_rect(cx - 32, cy - 40, 64, 32, C_VISOR);
            break;
        case MOOD_SAD:
            lcd_ring_arc(cx, cy + 22, 26, 7, (float)M_PI * 1.2f, (float)M_PI * 1.8f, c);
            break;
        case MOOD_SURPRISED:
            lcd_ring_arc(cx, cy, 13, 7, 0, 2 * (float)M_PI, c);
            break;
        case MOOD_ANGRY:
            lcd_round_rect(cx - 26, cy - 3, 52, 7, 3, c);
            break;
        case MOOD_SLEEPY:
            lcd_round_rect(cx - 14, cy - 3, 28, 6, 3, c);
            break;
        default:
            lcd_ring_arc(cx, cy - 18, 24, 6, (float)M_PI * 0.22f, (float)M_PI * 0.78f, c);
            break;
    }
}

// ---------------------------------------------------------------- bottom band: subtitle / status
static int wrap(const char *s, int cols, char out[][60], int max_lines) {
    int n = 0;
    while (*s && n < max_lines) {
        while (*s == ' ') s++;
        int len = (int)strlen(s);
        int take = len <= cols ? len : cols;
        if (len > cols) {
            int sp = take;
            while (sp > 0 && s[sp] != ' ') sp--;
            if (sp > cols / 2) take = sp;
        }
        if (take <= 0) break;
        memcpy(out[n], s, take);
        out[n][take] = 0;
        n++;
        s += take;
    }
    return n;
}

static void draw_band(activity_t a, int64_t t) {
    char text[2][64], sub[400];
    int64_t sub_at;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    memcpy(text, s_text, sizeof(text));
    memcpy(sub, s_sub, sizeof(sub));
    sub_at = s_sub_at;
    xSemaphoreGive(s_lock);

    if (text[0][0] || text[1][0]) {  // status (Wi-Fi, server, volume...)
        if (text[0][0]) lcd_text(240 - lcd_text_width(text[0], FONT_BIG) / 2, BAND_Y + 6, text[0], FONT_BIG, C_TEXT);
        if (text[1][0]) lcd_text(240 - lcd_text_width(text[1], FONT_SMALL) / 2, BAND_Y + 38, text[1], FONT_SMALL, C_MUTED);
        return;
    }
    if (sub[0]) {  // what she's saying, 2 lines at a time, paging every 3 s
        char lines[12][60];
        int n = wrap(sub, 50, lines, 12);
        int page = n > 2 ? (int)((t - sub_at) / 3000) % ((n + 1) / 2) : 0;
        for (int i = 0; i < 2 && page * 2 + i < n; i++) {
            const char *l = lines[page * 2 + i];
            lcd_text(240 - lcd_text_width(l, FONT_SMALL) / 2, BAND_Y + 12 + i * 26, l, FONT_SMALL, C_TEXT);
        }
        return;
    }
    const char *idle_hint = s_touch_seen ? "Tap my face to talk" : "Press the button to talk";
    const char *hint = a == ACT_LISTENING ? "Listening..." : a == ACT_THINKING ? "Thinking..." : idle_hint;
    lcd_text(240 - lcd_text_width(hint, FONT_SMALL) / 2, BAND_Y + 25, hint, FONT_SMALL, C_MUTED);
}

// ---------------------------------------------------------------- render loop
static void render_task(void *arg) {
    float lx = 0, ly = 0;
    int64_t next_blink = now_ms() + 3000, blink_at = 0;
    int dark_frames = 0;
    while (1) {
        int64_t t = now_ms();
        mood_t m = s_mood;
        activity_t a = s_act;

        if (s_dim) {  // asleep: dark screen with a soft "z z z"
            if (dark_frames++ == 0 || dark_frames % 20 == 0) {
                lcd_fill(0);
                int k = (int)(t / 700) % 3;
                for (int i = 0; i <= k; i++) lcd_text(220 + i * 22, 150 - i * 18, "z", FONT_BIG, C_EYE_DIM);
                lcd_flush();
            }
            lcd_backlight(5);
            vTaskDelay(pdMS_TO_TICKS(50));
            continue;
        }
        dark_frames = 0;
        lcd_backlight(s_night ? 35 : s_rest ? 60 : 100);

        // eyes drift towards where she's looking; thinking = look up and to the side
        float tx = s_look_x, ty = s_look_y;
        if (a == ACT_THINKING || m == MOOD_THINKING) tx = 0.5f, ty = -0.6f;
        lx += (tx - lx) * 0.2f;
        ly += (ty - ly) * 0.2f;
        if (s_look_x != 0 || s_look_y != 0) s_look_x *= 0.97f, s_look_y *= 0.97f;  // return to centre

        float blink = 1;
        if (t > next_blink) blink_at = t, next_blink = t + 2500 + esp_random() % 3500;
        if (t - blink_at < 160) blink = 0.12f + 0.88f * fabsf((t - blink_at) / 80.0f - 1);
        float squash = 1;
        if (t - s_poke_at < 350) squash = 1 - 0.25f * sinf((t - s_poke_at) / 350.0f * (float)M_PI);
        int bob = s_rest ? 0 : (int)(3 * sinf(t / 900.0f));
        if (m == MOOD_DANCING || m == MOOD_EXCITED) bob = (int)(8 * sinf(t / 180.0f));

        lcd_fill(C_HEAD);
        lcd_round_rect(70, -40, 340, 66, 33, C_HAIR);  // hair
        lcd_rect(0, 312, LCD_W, 8, C_HEAD_DARK);
        if (a == ACT_LISTENING) {  // glowing ring around the visor while she listens
            int g = (int)(4 + 3 * sinf(t / 180.0f));
            lcd_round_rect(VX - g, VY - g + bob, VW + 2 * g, VH + 2 * g, 70 + g, C_HAIR);
        }
        lcd_round_rect(VX, VY + bob, VW, VH, 70, C_VISOR);

        int ex = (int)(lx * 26), ey = (int)(ly * 18) + bob;
        draw_eye(EYE_L + ex, EYE_Y + ey, blink, squash, m, true);
        draw_eye(EYE_R + ex, EYE_Y + ey, (m == MOOD_PLAYFUL && blink > 0.9f) ? 1 : blink, squash, m, false);
        draw_mouth(m, a, audio_out_level(), audio_mic_level(), t);

        if (a == ACT_THINKING || m == MOOD_THINKING)
            for (int i = 0; i < 3; i++) {
                int up = (int)(6 * fabsf(sinf(t / 260.0f + i * 0.8f)));
                lcd_circle(380 + i * 18, 64 - up + bob, 6, C_HAIR);
            }
        if (m == MOOD_SLEEPY) lcd_text(380, 52 + bob, "z", FONT_BIG, C_EYE_DIM);

        // connection dot, top right: green = talking to the PC, amber = not yet
        extern bool net_ready(void);
        lcd_circle(458, 18, 7, net_ready() ? C_OK : C_WAIT);

        draw_band(a, t);
        if (t < s_stress_until) {  // sound test: send the full screen every frame
            lcd_flush_all();
            continue;
        }
        lcd_flush();
        vTaskDelay(pdMS_TO_TICKS(s_rest ? 90 : 25));
    }
}

void face_init(void) {
    s_lock = xSemaphoreCreateMutex();
    xTaskCreatePinnedToCore(render_task, "face", 6144, NULL, 4, NULL, 1);
}

#endif  // !JR_DISPLAY_OLED
