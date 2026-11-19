// Part of Chitti Junior by Chaitanya Eshwar Prasad (https://chaitanyaeshwarprasad.com). MIT licence.
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct {
    void (*on_status)(const char *line1, const char *line2);  // human-readable connection status
    void (*on_connected)(void);                               // server link up
    void (*on_disconnected)(void);
    void (*on_text)(const char *json, size_t len);            // one complete JSON message
    void (*on_audio)(const uint8_t *pcm, size_t len);         // speech PCM chunk
} net_callbacks_t;

void net_init(const net_callbacks_t *cb);
bool net_ready(void);
void net_fast_mode(bool on);  // full-speed Wi-Fi during a conversation
void net_send_text(const char *json);
void net_send_audio(const int16_t *pcm, size_t samples);
void net_forget_wifi(void);  // erase saved Wi-Fi and reboot into setup
void net_save_and_restart(const char *ssid, const char *pass, const char *server);

void usb_setup_init(void);   // Wi-Fi setup over the USB cable (usb_setup.c)
void net_test_traffic(int ms);  // sound test: keep Wi-Fi busy for ms
