// Chitti Junior firmware by Chaitanya Eshwar Prasad (https://chaitanyaeshwarprasad.com). MIT licence.
// Chitti Junior: ESP32-S3 N16R8 + 0.96-inch OLED + INMP441 microphone + MAX98357A amplifier + speaker.
// Talks to the same Chitti PC server as big Chitti (same protocol, docs/protocol.md).
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "audio.h"
#include "board_jr.h"
#include "cJSON.h"
#include "driver/gpio.h"
#include "driver/temperature_sensor.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_pm.h"
#include "esp_timer.h"
#include "face.h"
#include "freertos/FreeRTOS.h"
#include "freertos/stream_buffer.h"
#include "freertos/task.h"
#include "lcd.h"
#include "net.h"
#include "nvs.h"
#include "nvs_flash.h"

static const char *TAG = "junior";

static volatile activity_t s_act = ACT_IDLE;
static volatile bool s_tts_done;
static volatile int64_t s_mood_until;
static volatile int64_t s_last_input;
static volatile uint32_t s_mic_dropped;
static volatile int64_t s_sub_clear_at;
static volatile bool s_night;
static volatile int64_t s_text_clear_at;

static int64_t now_us(void) { return esp_timer_get_time(); }

// ---------------------------------------------------------------- saved settings
static int32_t nvs_get(const char *key, int32_t def) {
    nvs_handle_t h;
    int32_t v = def;
    if (nvs_open("chitti", NVS_READONLY, &h) == ESP_OK) {
        nvs_get_i32(h, key, &v);
        nvs_close(h);
    }
    return v;
}

static void nvs_put(const char *key, int32_t v) {
    nvs_handle_t h;
    if (nvs_open("chitti", NVS_READWRITE, &h) == ESP_OK) {
        nvs_set_i32(h, key, v);
        nvs_commit(h);
        nvs_close(h);
    }
}

// ---------------------------------------------------------------- volume (same 40..100 scale as big Chitti)
#define VOL_MIN 40
#define VOL_MAX 100
#define VOL_DEFAULT 88
static volatile bool s_vol_changed;
static int vol_percent(int v) { return (v - VOL_MIN) * 100 / (VOL_MAX - VOL_MIN); }

static void volume_set(int v, bool show) {
    v = v < VOL_MIN ? VOL_MIN : v > VOL_MAX ? VOL_MAX : v;
    audio_set_volume(v);
    nvs_put("vol2", v);
    s_vol_changed = true;
    if (show) {
        char line[24];
        snprintf(line, sizeof(line), "Volume %d%%", vol_percent(v));
        face_set_text(line, NULL);
        s_text_clear_at = now_us() + 1500000;
    }
    // a steady 1.2 s test tone: if this crackles, the cause is power / wiring, not the voice
    if (!audio_playing()) audio_beep(440, show ? 1200 : 90);
}

// ---------------------------------------------------------------- buttons
static bool btn_pressed(void) { return !gpio_get_level(PIN_BTN_BOOT) || !gpio_get_level(PIN_BTN_EXTRA); }

static void buttons_init(void) {
    gpio_config_t b = {.pin_bit_mask = (1ULL << PIN_BTN_BOOT) | (1ULL << PIN_BTN_EXTRA), .mode = GPIO_MODE_INPUT,
                       .pull_up_en = GPIO_PULLUP_ENABLE};
    gpio_config(&b);
}

// ---------------------------------------------------------------- screen test (pick the right screen setting)
// Shown on the very first start, or when BOOT is held for 6 s. Press = next setting, hold 2 s = keep it.
static void screen_test(int start) {
    int v = start;
    while (1) {
        // a frame round the edge + readable text = the right setting
        lcd_fill(0);
        lcd_rect(0, 0, LCD_W, 1, 0xFFFF), lcd_rect(0, LCD_H - 1, LCD_W, 1, 0xFFFF);
        lcd_rect(0, 0, 1, LCD_H, 0xFFFF), lcd_rect(LCD_W - 1, 0, 1, LCD_H, 0xFFFF);
        char t[32];
        snprintf(t, sizeof(t), "Test %d/%d", v + 1, lcd_variant_count());
        lcd_text(4, 3, t, FONT_SMALL, 0xFFFF);
        lcd_text(4, 18, "Readable+full", FONT_SMALL, 0xFFFF);
        lcd_text(4, 33, "frame? hold BOOT", FONT_SMALL, 0xFFFF);
        lcd_text(4, 48, "No: press BOOT", FONT_SMALL, 0xFFFF);
        lcd_flush_all();

        while (btn_pressed()) vTaskDelay(pdMS_TO_TICKS(20));  // wait for release
        while (!btn_pressed()) vTaskDelay(pdMS_TO_TICKS(20));
        int64_t down = now_us();
        while (btn_pressed() && now_us() - down < 2000000) vTaskDelay(pdMS_TO_TICKS(20));
        if (now_us() - down >= 2000000) {
            nvs_put("lcdv", v);
            nvs_put("lcdtest", 0);
            ESP_LOGI(TAG, "screen setting %d saved", v + 1);
            esp_restart();  // start again with the chosen setting
        }
        v = (v + 1) % lcd_variant_count();
        nvs_put("lcdv", v);
        nvs_put("lcdtest", 1);  // come back to this test after the restart
        esp_restart();  // the screen chip itself may change between settings: re-initialise cleanly
    }
}

// ---------------------------------------------------------------- activity / mood
static void set_activity(activity_t a) {
    s_act = a;
    face_set_activity(a);
    net_fast_mode(a != ACT_IDLE);
}

static void mood_for(mood_t m, int ms) {
    face_set_mood(m);
    s_mood_until = ms > 0 ? now_us() + ms * 1000LL : 0;
}

// ---------------------------------------------------------------- network callbacks
static void on_status(const char *a, const char *b) {
    face_set_text(a, b);
    if (!net_ready()) face_set_mood(MOOD_SLEEPY);
}

static void on_connected(void) {
    face_set_text(NULL, NULL);
    net_send_text("{\"type\":\"hello\",\"device\":\"chitti\",\"version\":1,\"rate\":24000,\"model\":\"junior\"}");
    mood_for(MOOD_HAPPY, 3000);
    face_poke();
}

static void on_disconnected(void) {
    audio_capture(false);
    audio_play_stop();
    set_activity(ACT_IDLE);
    face_set_mood(MOOD_SLEEPY);
}

static void on_text(const char *json, size_t len) {
    cJSON *msg = cJSON_ParseWithLength(json, len);
    if (!msg) return;
    const char *type = cJSON_GetStringValue(cJSON_GetObjectItem(msg, "type"));
    const char *state = cJSON_GetStringValue(cJSON_GetObjectItem(msg, "state"));
    if (!type) goto done;
    if (!strcmp(type, "state") && state) {
        if (!strcmp(state, "listening")) set_activity(ACT_LISTENING);
        else if (!strcmp(state, "thinking")) set_activity(ACT_THINKING);
        else if (!strcmp(state, "speaking")) set_activity(ACT_SPEAKING);
        else if (!strcmp(state, "idle") && !audio_playing() && s_act != ACT_LISTENING) set_activity(ACT_IDLE);
    } else if (!strcmp(type, "listen") && state && !strcmp(state, "stop")) {
        audio_capture(false);
        if (s_mic_dropped) {
            ESP_LOGW(TAG, "mic audio dropped: %lu ms", (unsigned long)(s_mic_dropped / (AUDIO_RATE * 2 / 1000)));
            s_mic_dropped = 0;
        }
        set_activity(ACT_THINKING);
    } else if (!strcmp(type, "mood")) {
        const char *m = cJSON_GetStringValue(cJSON_GetObjectItem(msg, "mood"));
        if (m) mood_for(face_mood_from_name(m), 0);
    } else if (!strcmp(type, "tts") && state) {
        if (!strcmp(state, "start")) {
            audio_capture(false);
            s_tts_done = false;
            audio_play_begin();
            set_activity(ACT_SPEAKING);
        } else {
            s_tts_done = true;
            audio_play_end();
        }
    } else if (!strcmp(type, "stt")) {
        ESP_LOGI(TAG, "heard: %s", cJSON_GetStringValue(cJSON_GetObjectItem(msg, "text")));
    } else if (!strcmp(type, "volume")) {  // dashboard slider, 0..100 %
        cJSON *v = cJSON_GetObjectItem(msg, "value");
        if (cJSON_IsNumber(v)) volume_set(VOL_MIN + (int)v->valuedouble * (VOL_MAX - VOL_MIN) / 100, true);
    } else if (!strcmp(type, "night")) {
        s_night = cJSON_IsTrue(cJSON_GetObjectItem(msg, "on"));
        face_set_night(s_night);
    } else if (!strcmp(type, "say")) {
        const char *en = cJSON_GetStringValue(cJSON_GetObjectItem(msg, "en"));
        face_set_subtitle(en && en[0] ? en : NULL);
        s_sub_clear_at = 0;
    }
done:
    cJSON_Delete(msg);
}

static void on_audio(const uint8_t *pcm, size_t len) { audio_play_push(pcm, len); }

// Microphone -> 3 s buffer -> sender task: recording never waits for Wi-Fi
#define MIC_BUF_BYTES (AUDIO_RATE * 2 * 3)
#define MIC_SEND_BYTES (AUDIO_RATE * 2 / 10)
static StreamBufferHandle_t s_mic_sb;

static void on_mic(const int16_t *pcm, size_t samples) {
    size_t n = samples * 2;
    if (xStreamBufferSend(s_mic_sb, pcm, n, 0) != n) s_mic_dropped += n;
}

static void mic_sender_task(void *arg) {
    uint8_t *chunk = heap_caps_malloc(MIC_SEND_BYTES, MALLOC_CAP_INTERNAL);
    while (1) {
        size_t got = xStreamBufferReceive(s_mic_sb, chunk, MIC_SEND_BYTES, pdMS_TO_TICKS(100));
        if (got) net_send_audio((const int16_t *)chunk, got / 2);
    }
}

// ---------------------------------------------------------------- user actions
static void tap(bool button) {
    s_last_input = now_us();
    face_set_dim(false);
    if (!net_ready()) {
        face_poke();
        mood_for(MOOD_SAD, 2000);
        return;
    }
    switch (s_act) {
        case ACT_IDLE:
            audio_play_stop();
            face_set_subtitle(NULL);
            net_send_text("{\"type\":\"listen\",\"state\":\"start\"}");
            audio_capture(true);
            set_activity(ACT_LISTENING);
            face_set_mood(MOOD_NEUTRAL);
            break;
        case ACT_LISTENING:
            break;  // she ends the turn herself when you go quiet
        case ACT_THINKING:
            if (!button) break;
            // fall through
        default:  // speaking: interrupt her
            net_send_text("{\"type\":\"abort\"}");
            audio_capture(false);
            audio_play_stop();
            set_activity(ACT_IDLE);
            mood_for(MOOD_SURPRISED, 1200);
            break;
    }
}

static void poke(void) {
    s_last_input = now_us();
    face_poke();
    if (s_act == ACT_IDLE) mood_for(MOOD_ANGRY, 2500);
    net_send_text("{\"type\":\"event\",\"name\":\"poke\"}");
}

static float chip_temp(void) {
    static temperature_sensor_handle_t ts;
    if (!ts) {
        temperature_sensor_config_t c = TEMPERATURE_SENSOR_CONFIG_DEFAULT(10, 80);
        if (temperature_sensor_install(&c, &ts) != ESP_OK) return 0;
        temperature_sensor_enable(ts);
    }
    float t = 0;
    temperature_sensor_get_celsius(ts, &t);
    return t;
}

// ---------------------------------------------------------------- input loop
static void input_task(void *arg) {
    bool down = false, long_fired = false, bdown = false, blong = false;
    int64_t down_at = 0, bdown_at = 0;
    int sx = 0, sy = 0, lx = 0, ly = 0;
    bool asleep = false;
    s_last_input = now_us();

    while (1) {
        vTaskDelay(pdMS_TO_TICKS(20));
        int64_t now = now_us();

        // touch screen: tap = talk / stop, hold 1.2 s = poke, quick vertical swipe = volume
        int x, y;
        bool pressed = lcd_touch_read(&x, &y);
        if (pressed && !down) down = true, long_fired = false, down_at = now, sx = x, sy = y, face_set_touch_seen();
        if (pressed) {
            lx = x, ly = y;
            face_look_at((x - 240) / 240.0f, (y - 160) / 160.0f);
            if (!long_fired && now - down_at > 1200000 && abs(x - sx) < 40 && abs(y - sy) < 40 && s_act != ACT_LISTENING) {
                long_fired = true;
                poke();
            }
        } else if (down) {
            down = false;
            int64_t held = now - down_at;
            int dx = lx - sx, dy = ly - sy;
            bool swipe = !long_fired && held < 900000 && abs(dy) > 100 && abs(dx) < 80;
            if (swipe) {
                s_last_input = now;
                volume_set(audio_get_volume() + (dy < 0 ? 6 : -6), true);
            } else if (!long_fired && held >= 60000) {
                tap(false);
            }
        }

        // BOOT (or extra) button: press = talk / stop, hold 1.2 s = poke, hold 6 s = screen test
        bool b = btn_pressed();
        if (b && !bdown) bdown = true, blong = false, bdown_at = now;
        if (b && !blong && now - bdown_at > 1200000) blong = true, poke();
        if (b && now - bdown_at > 6000000) {
            nvs_put("lcdtest", 1);
            esp_restart();  // reopen the screen test cleanly, before the face is drawn
        }
        if (!b && bdown) {
            bdown = false;
            if (!blong) tap(true);
        }

        // rest after 20 s idle, sleep (dark screen) after 3 min. At night: 10 s and 45 s.
        int64_t quiet = now - s_last_input;
        bool should_rest = s_act == ACT_IDLE && quiet > (s_night ? 10000000 : 20000000);
        bool should_sleep = s_act == ACT_IDLE && quiet > (s_night ? 45000000LL : 180000000LL);
        face_set_rest(should_rest);
        if (should_sleep && !asleep) {
            asleep = true;
            face_set_mood(MOOD_SLEEPY);
            face_set_dim(true);
        } else if (!should_sleep && asleep) {
            asleep = false;
            face_set_dim(false);
            mood_for(MOOD_SURPRISED, 1000);
            net_send_text("{\"type\":\"event\",\"name\":\"wake\"}");
        }
        if (s_text_clear_at && now > s_text_clear_at) {
            s_text_clear_at = 0;
            face_set_text(NULL, NULL);
        }

        // status for the dashboard (no battery on Junior's USB build: pct -1)
        static int64_t next_send;
        if (net_ready() && (now > next_send || s_vol_changed)) {
            char msg[128];
            snprintf(msg, sizeof(msg), "{\"type\":\"battery\",\"pct\":-1,\"chg\":false,\"vol\":%d,\"und\":%lu,\"temp\":%.1f}",
                     vol_percent(audio_get_volume()), (unsigned long)audio_underruns(), chip_temp());
            net_send_text(msg);
            next_send = now + 30000000, s_vol_changed = false;
        }

        if (s_act == ACT_SPEAKING && s_tts_done && !audio_playing()) {
            set_activity(ACT_IDLE);
            s_last_input = now;
            if (!s_mood_until) s_mood_until = now + 5000000;
            s_sub_clear_at = now + 2500000;
        }
        if (s_sub_clear_at && now > s_sub_clear_at) {
            s_sub_clear_at = 0;
            face_set_subtitle(NULL);
        }
        if (s_mood_until && now > s_mood_until && s_act == ACT_IDLE && !asleep) {
            s_mood_until = 0;
            face_set_mood(net_ready() ? MOOD_NEUTRAL : MOOD_SLEEPY);
        }
    }
}

void app_main(void) {
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }
    esp_pm_config_t pm = {.max_freq_mhz = 240, .min_freq_mhz = 80, .light_sleep_enable = false};
    if (esp_pm_configure(&pm) != ESP_OK) ESP_LOGW(TAG, "power management not available");
    buttons_init();

    int32_t lcdv = nvs_get("lcdv", -1);
    lcd_init(lcdv < 0 ? 0 : lcdv);
    // very first start, or in the middle of choosing: show the screen test (it restarts when done)
    if (lcdv < 0 || nvs_get("lcdtest", 0)) screen_test(lcdv < 0 ? 0 : lcdv);

    face_init();
    face_set_text("Hi, I'm Chitti Junior", "Waking up...");
    s_mic_sb = xStreamBufferCreateWithCaps(MIC_BUF_BYTES, MIC_SEND_BYTES, MALLOC_CAP_SPIRAM);
    xStreamBufferSetTriggerLevel(s_mic_sb, MIC_SEND_BYTES);
    xTaskCreatePinnedToCore(mic_sender_task, "mic_send", 4096, NULL, 5, NULL, 0);
    audio_init(on_mic);
    int32_t vol = nvs_get("vol2", VOL_DEFAULT);
    audio_set_volume(vol < VOL_MIN ? VOL_MIN : vol > VOL_MAX ? VOL_MAX : vol);
    net_callbacks_t cb = {
        .on_status = on_status,
        .on_connected = on_connected,
        .on_disconnected = on_disconnected,
        .on_text = on_text,
        .on_audio = on_audio,
    };
    net_init(&cb);
    usb_setup_init();
    xTaskCreatePinnedToCore(input_task, "input", 6144, NULL, 5, NULL, 0);
    ESP_LOGI(TAG, "Chitti Junior is awake");
}
