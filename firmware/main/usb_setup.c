// Part of Chitti Junior by Chaitanya Eshwar Prasad (https://chaitanyaeshwarprasad.com). MIT licence.
// Wi-Fi setup over the USB cable: the PC script (server/set_wifi.py) sends one line
//   CHITTI-WIFI {"ssid":"...","pass":"...","server":""}
// and Chitti saves it and restarts. No phone needed.
#include <string.h>

#include "cJSON.h"
#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "net.h"
#include "audio.h"
#include "face.h"

static const char *TAG = "usb_setup";
#define PREFIX "CHITTI-WIFI "

static void reply(const char *s) { usb_serial_jtag_write_bytes(s, strlen(s), pdMS_TO_TICKS(100)); }

static void handle_line(char *line) {
    if (strcmp(line, "CHITTI-PING") == 0) {
        reply("\nCHITTI-PONG\n");
        return;
    }
    if (strncmp(line, "CHITTI-TEST ", 12) == 0) {  // sound tests, run from the PC
        const char *t = line + 12;
        if (!strcmp(t, "tone")) face_set_text("Sound test 1", "steady tone, Wi-Fi quiet"), audio_test_tone(440, 440, 3000);
        else if (!strcmp(t, "tone_wifi"))
            face_set_text("Sound test 2", "tone + busy Wi-Fi"), net_test_traffic(3500), audio_test_tone(440, 440, 3000);
        else if (!strcmp(t, "tone_screen"))
            face_set_text("Sound test 3", "tone + busy screen"), face_stress_screen(3500), audio_test_tone(440, 440, 3000);
        else if (!strcmp(t, "bass")) face_set_text("Sound test 4", "low bass tone"), audio_test_tone(150, 150, 3000);
        else if (!strcmp(t, "sweep")) face_set_text("Sound test 5", "low to high sweep"), audio_test_tone(150, 4000, 6000);
        else if (!strcmp(t, "buffer_on")) face_set_text("Sound test 6", "talk to me: full download first"), audio_set_buffer_all(true);
        else if (!strcmp(t, "buffer_off")) face_set_text(NULL, NULL), audio_set_buffer_all(false);
        else if (!strcmp(t, "clear")) face_set_text(NULL, NULL);
        else {
            reply("\nCHITTI-TEST-UNKNOWN\n");
            return;
        }
        reply("\nCHITTI-TEST-OK\n");
        return;
    }
    if (strncmp(line, PREFIX, strlen(PREFIX)) != 0) return;
    cJSON *j = cJSON_Parse(line + strlen(PREFIX));
    const char *ssid = j ? cJSON_GetStringValue(cJSON_GetObjectItem(j, "ssid")) : NULL;
    const char *pass = j ? cJSON_GetStringValue(cJSON_GetObjectItem(j, "pass")) : NULL;
    const char *server = j ? cJSON_GetStringValue(cJSON_GetObjectItem(j, "server")) : NULL;
    if (!ssid || !ssid[0] || strlen(ssid) > 32 || (pass && strlen(pass) > 64)) {
        reply("\nCHITTI-WIFI-ERROR bad settings\n");
    } else {
        ESP_LOGI(TAG, "Wi-Fi settings received over USB for '%s'", ssid);
        reply("\nCHITTI-WIFI-OK\n");
        vTaskDelay(pdMS_TO_TICKS(300));
        net_save_and_restart(ssid, pass ? pass : "", server ? server : "");  // does not return
    }
    cJSON_Delete(j);
}

static void usb_setup_task(void *arg) {
    char line[256];
    size_t n = 0;
    uint8_t buf[64];
    while (1) {
        int got = usb_serial_jtag_read_bytes(buf, sizeof(buf), pdMS_TO_TICKS(200));
        for (int i = 0; i < got; i++) {
            char c = buf[i];
            if (c == '\n' || c == '\r') {
                line[n] = 0;
                if (n) handle_line(line);
                n = 0;
            } else if (n < sizeof(line) - 1) {
                line[n++] = c;
            } else {
                n = 0;  // too long: drop
            }
        }
    }
}

void usb_setup_init(void) {
    usb_serial_jtag_driver_config_t cfg = USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
    if (usb_serial_jtag_driver_install(&cfg) != ESP_OK) return;
    usb_serial_jtag_vfs_use_driver();  // logs keep working through the driver
    xTaskCreatePinnedToCore(usb_setup_task, "usb_setup", 4096, NULL, 3, NULL, 0);
}
