// Part of Chitti Junior by Chaitanya Eshwar Prasad (https://chaitanyaeshwarprasad.com). MIT licence.
// Chitti Junior audio: INMP441 microphone (I2S 0, in) and MAX98357A amplifier (I2S 1, out).
// Same interface as big Chitti (audio.h): 24 kHz mono PCM both ways.
#include "audio.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "board_jr.h"
#include "driver/gpio.h"
#include "driver/i2s_std.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/stream_buffer.h"
#include "freertos/task.h"

static const char *TAG = "audio";

#define FRAME (AUDIO_RATE / 50)                 // 20 ms
#define PLAY_BUF (1536 * 1024)  // ~32 s of voice (PSRAM): room to hold a whole reply
#define PRIME_BYTES (AUDIO_RATE * 2 * 7 / 10)   // 700 ms in hand before speaking: no gaps if Wi-Fi hiccups
#define MIC_SHIFT 14                            // INMP441: 32-bit sample >> 14 (= x4 over 16-bit)
#define MIC_HPF 0.975f                          // high-pass ~95 Hz: removes the mic's start-up drift and hum
#define MAX_GAIN 0.5f                           // speaker loudness cap: big peaks dip a USB supply and crackle
#define SPEAKER_HPF_HZ 250.0f                   // bass below this is removed before the speaker (stops rattling)

static i2s_chan_handle_t s_rx, s_tx;
static audio_mic_cb_t s_mic_cb;
static volatile bool s_capture, s_play_active, s_play_eof, s_primed;
static volatile uint32_t s_underruns;
static StreamBufferHandle_t s_play;
static volatile float s_mic_level, s_out_level;
static volatile int s_volume = 88;
static volatile float s_gain = 0.3f;
static volatile bool s_buffer_all;  // test mode: play a reply only after all of it has arrived

static void i2s_setup(void) {
    // microphone: 32-bit slots, left channel (INMP441 L/R pin to GND)
    i2s_chan_config_t rxc = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    rxc.dma_desc_num = 8;
    rxc.dma_frame_num = 240;
    ESP_ERROR_CHECK(i2s_new_channel(&rxc, NULL, &s_rx));
    i2s_std_config_t rx = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(AUDIO_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO),
        .gpio_cfg = {.mclk = I2S_GPIO_UNUSED, .bclk = PIN_MIC_SCK, .ws = PIN_MIC_WS, .dout = I2S_GPIO_UNUSED, .din = PIN_MIC_SD},
    };
    rx.slot_cfg.slot_mask = I2S_STD_SLOT_LEFT;
    ESP_ERROR_CHECK(i2s_channel_init_std_mode(s_rx, &rx));

    // amplifier: 16-bit, same sound on both channels (the MAX98357A plays (L+R)/2)
    i2s_chan_config_t txc = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_1, I2S_ROLE_MASTER);
    txc.dma_desc_num = 8;
    txc.dma_frame_num = 240;
    txc.auto_clear_after_cb = true;  // silence, not a buzz, if the buffer runs dry
    ESP_ERROR_CHECK(i2s_new_channel(&txc, &s_tx, NULL));
    i2s_std_config_t tx = {
        // 48 kHz: a rate the MAX98357A supports (24 kHz isn't on its list and made complex sounds like a voice crackle)
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(AUDIO_RATE * 2),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO),
        .gpio_cfg = {.mclk = I2S_GPIO_UNUSED, .bclk = PIN_AMP_BCLK, .ws = PIN_AMP_LRC, .dout = PIN_AMP_DIN, .din = I2S_GPIO_UNUSED},
    };
    tx.slot_cfg.slot_mask = I2S_STD_SLOT_BOTH;
    ESP_ERROR_CHECK(i2s_channel_init_std_mode(s_tx, &tx));
    ESP_ERROR_CHECK(i2s_channel_enable(s_tx));
    // gentle edges on the amplifier's clock / data wires: sharp edges ring on jumper wires and crackle
    gpio_set_drive_capability(PIN_AMP_BCLK, GPIO_DRIVE_CAP_0);
    gpio_set_drive_capability(PIN_AMP_LRC, GPIO_DRIVE_CAP_0);
    gpio_set_drive_capability(PIN_AMP_DIN, GPIO_DRIVE_CAP_0);
    gpio_set_drive_capability(PIN_MIC_SCK, GPIO_DRIVE_CAP_0);
    gpio_set_drive_capability(PIN_MIC_WS, GPIO_DRIVE_CAP_0);

    gpio_config_t sd = {.pin_bit_mask = 1ULL << PIN_AMP_SD, .mode = GPIO_MODE_OUTPUT};
    gpio_config(&sd);
    gpio_set_level(PIN_AMP_SD, 0);  // amplifier off until she speaks (no hiss)
}

static float rms01(const int16_t *x, int n) {
    double acc = 0;
    for (int i = 0; i < n; i++) acc += (double)x[i] * x[i];
    float r = sqrtf(acc / n) / 6000.0f;
    return r > 1 ? 1 : r;
}

static void mic_task(void *arg) {
    int32_t *raw = heap_caps_malloc(FRAME * sizeof(int32_t), MALLOC_CAP_INTERNAL);
    int16_t *pcm = heap_caps_malloc(FRAME * sizeof(int16_t), MALLOC_CAP_INTERNAL);
    bool on = false;
    int settle = 0;
    float hp_x = 0, hp_y = 0;  // high-pass filter state
    int32_t prev = 0;
    while (1) {
        if (s_capture != on) {  // the microphone only runs while Junior is listening
            on = s_capture;
            if (on) {
                i2s_channel_enable(s_rx);
                settle = 15;  // drop the first 300 ms while the mic wakes up
                hp_x = hp_y = 0, prev = 0;
            } else {
                i2s_channel_disable(s_rx);
                s_mic_level = 0;
            }
        }
        if (!on) {
            vTaskDelay(pdMS_TO_TICKS(30));
            continue;
        }
        size_t got = 0;
        if (i2s_channel_read(s_rx, raw, FRAME * sizeof(int32_t), &got, pdMS_TO_TICKS(100)) != ESP_OK || !got) continue;
        int n = got / sizeof(int32_t);
        for (int i = 0; i < n; i++) {
            int32_t s = raw[i] >> MIC_SHIFT;
            // click removal: a lone sample far from both neighbours is a wiring glitch, not sound
            int32_t next = i + 1 < n ? raw[i + 1] >> MIC_SHIFT : s;
            if (abs(s - prev) > 6000 && abs(s - next) > 6000 && abs(next - prev) < 3000) s = (prev + next) / 2;
            prev = s;
            // high-pass: y = a * (y + x - x_prev)
            hp_y = MIC_HPF * (hp_y + (float)s - hp_x);
            hp_x = (float)s;
            float v = hp_y;
            pcm[i] = (int16_t)(v > 32767 ? 32767 : v < -32768 ? -32768 : v);
        }
        if (settle > 0) {
            settle--;
            continue;
        }
        s_mic_level = s_mic_level * 0.6f + rms01(pcm, n) * 0.4f;
        if (s_capture && s_mic_cb) s_mic_cb(pcm, n);
    }
}

static void amp_power(bool on) {
    static bool cur;
    if (on == cur) return;
    cur = on;
    gpio_set_level(PIN_AMP_SD, on);
    if (on) vTaskDelay(pdMS_TO_TICKS(5));
}

static void play_task(void *arg) {
    int16_t *buf = heap_caps_malloc(FRAME * sizeof(int16_t), MALLOC_CAP_INTERNAL);
    int16_t *out = heap_caps_malloc(FRAME * 2 * sizeof(int16_t), MALLOC_CAP_INTERNAL);  // 48 kHz for the amplifier
    int quiet_ms = 0;
    // 24 -> 48 kHz: every input sample is kept, and a smooth value is computed half-way between neighbours
    // (8-tap windowed-sinc interpolation, so no harsh "staircase" images)
    float iw[8], hist[8] = {0};
    {
        float sum = 0;
        for (int k = 0; k < 8; k++) {
            float t = (k - 3) - 0.5f;  // distance from the half-way point between hist[3] and hist[4]
            float s = sinf((float)M_PI * t) / ((float)M_PI * t);
            float l = sinf((float)M_PI * t / 4) / ((float)M_PI * t / 4);
            iw[k] = s * l, sum += iw[k];
        }
        for (int k = 0; k < 8; k++) iw[k] /= sum;
    }
    // 2nd-order high-pass (Butterworth) for the speaker
    const float fc = SPEAKER_HPF_HZ, q = 0.7071f, w0 = 2 * (float)M_PI * fc / AUDIO_RATE;
    const float cw = cosf(w0), al = sinf(w0) / (2 * q), a0 = 1 + al;
    const float hb0 = (1 + cw) / 2 / a0, hb1 = -(1 + cw) / a0, hb2 = hb0, ha1 = -2 * cw / a0, ha2 = (1 - al) / a0;
    float hx1 = 0, hx2 = 0, hy1 = 0, hy2 = 0;
    while (1) {
        if (s_play_active && !s_primed) {
            // test mode "buffer all": wait for the whole reply, so Wi-Fi is quiet while she speaks
            if (!s_play_eof && (s_buffer_all || xStreamBufferBytesAvailable(s_play) < PRIME_BYTES)) {
                vTaskDelay(pdMS_TO_TICKS(10));
                continue;
            }
            s_primed = true;
        }
        size_t avail = xStreamBufferBytesAvailable(s_play);
        if (s_play_active && !s_play_eof && avail < FRAME * sizeof(int16_t)) {  // ran low: wait, don't crackle
            if (avail > 0) s_underruns++;
            s_primed = false;
            vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }
        size_t got = xStreamBufferReceive(s_play, buf, FRAME * sizeof(int16_t), pdMS_TO_TICKS(20));
        if (got >= 2) {
            quiet_ms = 0;
            amp_power(true);
            int n = got / 2;
            if (n < FRAME) memset(buf + n, 0, (FRAME - n) * 2);
            float g = s_gain;
            for (int i = 0; i < FRAME; i++) {
                // bass cut (~250 Hz): a small speaker can't play it, it only makes the cone flap and rattle
                float x0 = buf[i];
                float hp = hb0 * x0 + hb1 * hx1 + hb2 * hx2 - ha1 * hy1 - ha2 * hy2;
                hx2 = hx1, hx1 = x0, hy2 = hy1, hy1 = hp;
                // volume, then a soft limiter: loud peaks are rounded off, not clipped
                float y = hp * g, a = fabsf(y);
                if (a > 16000) y = copysignf(16000 + (a - 16000) * 0.25f, y);
                buf[i] = (int16_t)y;
                // up-sample to 48 kHz
                memmove(hist, hist + 1, 7 * sizeof(float));
                hist[7] = y;
                float mid = 0;
                for (int k = 0; k < 8; k++) mid += iw[k] * hist[k];
                out[2 * i] = (int16_t)hist[3];
                out[2 * i + 1] = (int16_t)fmaxf(-32768, fminf(32767, mid));
            }
            s_out_level = s_out_level * 0.5f + rms01(buf, n) / fmaxf(g, 0.05f) * 0.5f;
            size_t w;
            i2s_channel_write(s_tx, out, FRAME * 2 * sizeof(int16_t), &w, pdMS_TO_TICKS(200));
        } else {
            s_out_level *= 0.7f;
            quiet_ms += 20;
            if (quiet_ms > 1500 && !s_play_active) amp_power(false);  // amplifier off after 1.5 s of silence
            if (s_play_active && s_play_eof && xStreamBufferIsEmpty(s_play)) {
                s_play_active = false;
                s_out_level = 0;
            }
        }
    }
}

void audio_init(audio_mic_cb_t mic_cb) {
    s_mic_cb = mic_cb;
    i2s_setup();
    s_play = xStreamBufferCreateWithCaps(PLAY_BUF, 2, MALLOC_CAP_SPIRAM);
    xTaskCreatePinnedToCore(mic_task, "mic", 4096, NULL, 6, NULL, 0);
    xTaskCreatePinnedToCore(play_task, "play", 4096, NULL, 7, NULL, 0);
    ESP_LOGI(TAG, "microphone and speaker ready");
}

void audio_capture(bool on) { s_capture = on; }

void audio_play_begin(void) {
    xStreamBufferReset(s_play);
    s_play_eof = false;
    s_primed = false;
    s_play_active = true;
}

void audio_play_push(const uint8_t *pcm, size_t bytes) {
    if (!s_play_active) return;
    xStreamBufferSend(s_play, pcm, bytes, pdMS_TO_TICKS(2000));
}

void audio_play_end(void) { s_play_eof = true; }

void audio_play_stop(void) {
    s_play_active = false;
    s_play_eof = true;
    xStreamBufferReset(s_play);
}

bool audio_playing(void) { return s_play_active; }
uint32_t audio_underruns(void) { return s_underruns; }
float audio_mic_level(void) { return s_mic_level; }
float audio_out_level(void) { return s_out_level; }
int audio_get_volume(void) { return s_volume; }

void audio_set_volume(int vol) {
    s_volume = vol;
    s_gain = MAX_GAIN * powf(10.0f, (vol - 100) / 50.0f);  // 100 = the cap, 40 = about 24 dB quieter
}

void audio_beep(int freq, int ms) {
    int n = AUDIO_RATE * ms / 1000;
    int16_t *tone = heap_caps_malloc(n * 2, MALLOC_CAP_SPIRAM);
    if (!tone) return;
    for (int i = 0; i < n; i++) {
        float env = fminf(1.0f, fminf(i, n - i) / 400.0f);
        tone[i] = (int16_t)(sinf(2 * M_PI * freq * i / AUDIO_RATE) * 5000 * env);
    }
    audio_play_begin();
    audio_play_push((uint8_t *)tone, n * 2);
    audio_play_end();
    free(tone);
}

// ---------------------------------------------------------------- sound tests (run over USB, see usb_setup.c)
void audio_set_buffer_all(bool on) { s_buffer_all = on; }

void audio_test_tone(int f0, int f1, int ms) {
    int n = AUDIO_RATE * ms / 1000;
    int16_t *tone = heap_caps_malloc(n * 2, MALLOC_CAP_SPIRAM);
    if (!tone) return;
    float ph = 0;
    for (int i = 0; i < n; i++) {
        float f = f0 + (f1 - f0) * (float)i / n;  // f0 == f1: steady tone, otherwise a sweep
        ph += 2 * (float)M_PI * f / AUDIO_RATE;
        float env = fminf(1.0f, fminf(i, n - i) / 600.0f);
        tone[i] = (int16_t)(sinf(ph) * 20000 * env);  // about as loud as her voice
    }
    audio_play_begin();
    audio_play_push((uint8_t *)tone, n * 2);
    audio_play_end();
    free(tone);
}
