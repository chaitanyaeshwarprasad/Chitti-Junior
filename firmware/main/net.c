// Chitti Junior: same Wi-Fi, discovery and server link as big Chitti (copied, unchanged otherwise).
#include "net.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_event.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_websocket_client.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "lwip/sockets.h"
#include "nvs.h"

static const char *TAG = "net";

#define SETUP_SSID "ChittiJr-Setup"
#define DISCOVERY_PORT 8766
#define BIT_GOT_IP BIT0

static net_callbacks_t s_cb;
static EventGroupHandle_t s_events;
static esp_websocket_client_handle_t s_ws;
static volatile bool s_ws_up;
static char s_ssid[33], s_pass[65], s_server[64];
static char s_scan[1024];
static bool s_setup_running;

static void status(const char *a, const char *b) {
    if (s_cb.on_status) s_cb.on_status(a, b);
}

// ---------------------------------------------------------------- settings
static void load_settings(void) {
    nvs_handle_t h;
    if (nvs_open("chitti", NVS_READONLY, &h) != ESP_OK) return;
    size_t n = sizeof(s_ssid);
    nvs_get_str(h, "ssid", s_ssid, &n);
    n = sizeof(s_pass);
    nvs_get_str(h, "pass", s_pass, &n);
    n = sizeof(s_server);
    nvs_get_str(h, "server", s_server, &n);
    nvs_close(h);
}

static void save_settings(const char *ssid, const char *pass, const char *server) {
    nvs_handle_t h;
    ESP_ERROR_CHECK(nvs_open("chitti", NVS_READWRITE, &h));
    nvs_set_str(h, "ssid", ssid);
    nvs_set_str(h, "pass", pass);
    nvs_set_str(h, "server", server);
    nvs_commit(h);
    nvs_close(h);
}

void net_save_and_restart(const char *ssid, const char *pass, const char *server) {
    save_settings(ssid, pass, server);
    status("WiFi saved!", "Restarting...");
    vTaskDelay(pdMS_TO_TICKS(800));
    esp_restart();
}

void net_forget_wifi(void) {
    nvs_handle_t h;
    if (nvs_open("chitti", NVS_READWRITE, &h) == ESP_OK) {
        nvs_erase_all(h);
        nvs_commit(h);
        nvs_close(h);
    }
    esp_restart();
}

// ---------------------------------------------------------------- setup web page
static void url_decode(char *s) {
    char *o = s;
    for (; *s; s++) {
        if (*s == '+') *o++ = ' ';
        else if (*s == '%' && s[1] && s[2]) {
            char hex[3] = {s[1], s[2], 0};
            *o++ = (char)strtol(hex, NULL, 16);
            s += 2;
        } else *o++ = *s;
    }
    *o = 0;
}

static void form_value(const char *body, const char *key, char *out, size_t cap) {
    out[0] = 0;
    char pat[16];
    snprintf(pat, sizeof(pat), "%s=", key);
    for (const char *p = body; (p = strstr(p, pat)); p++) {
        if (p != body && p[-1] != '&') continue;
        p += strlen(pat);
        size_t n = strcspn(p, "&");
        if (n >= cap) n = cap - 1;
        memcpy(out, p, n);
        out[n] = 0;
        url_decode(out);
        return;
    }
}

static esp_err_t page_get(httpd_req_t *req) {
    httpd_resp_set_type(req, "text/html; charset=utf-8");
    httpd_resp_sendstr_chunk(req,
        "<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Chitti setup</title><style>body{font-family:sans-serif;background:#0b0b10;color:#e8fbff;"
        "max-width:420px;margin:auto;padding:24px}input,button{width:100%;padding:12px;margin:6px 0 14px;"
        "border-radius:10px;border:1px solid #2a3a44;background:#12161c;color:#fff;font-size:16px}"
        "button{background:#19c6de;color:#002;border:0;font-weight:600}small{color:#8aa}</style>"
        "<h2>Hi, I'm Chitti Junior</h2><p>Connect me to your Wi-Fi.</p><form method=post action=/save>"
        "<label>Wi-Fi name</label><input name=ssid list=nets required><datalist id=nets>");
    httpd_resp_sendstr_chunk(req, s_scan);
    httpd_resp_sendstr_chunk(req,
        "</datalist><label>Wi-Fi password</label><input name=pass type=password>"
        "<label>PC server (optional)</label><input name=server placeholder='found automatically'>"
        "<small>Leave empty and I'll find the Chitti server on your network.</small>"
        "<button>Save and restart</button></form>");
    return httpd_resp_sendstr_chunk(req, NULL);
}

static esp_err_t page_save(httpd_req_t *req) {
    char body[512] = {0};
    int len = req->content_len < (int)sizeof(body) - 1 ? req->content_len : (int)sizeof(body) - 1;
    int got = 0;
    while (got < len) {
        int r = httpd_req_recv(req, body + got, len - got);
        if (r <= 0) return ESP_FAIL;
        got += r;
    }
    char ssid[33], pass[65], server[64];
    form_value(body, "ssid", ssid, sizeof(ssid));
    form_value(body, "pass", pass, sizeof(pass));
    form_value(body, "server", server, sizeof(server));
    if (!ssid[0]) return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Wi-Fi name is empty");
    save_settings(ssid, pass, server);
    httpd_resp_sendstr(req, "<meta name=viewport content='width=device-width'><h2>Saved! Chitti is restarting...</h2>");
    status("Saved!", "Restarting...");
    vTaskDelay(pdMS_TO_TICKS(1500));
    esp_restart();
    return ESP_OK;
}

static void scan_networks(void) {
    wifi_scan_config_t sc = {0};
    if (esp_wifi_scan_start(&sc, true) != ESP_OK) return;
    uint16_t n = 20;
    wifi_ap_record_t *aps = calloc(n, sizeof(wifi_ap_record_t));
    if (!aps) return;
    esp_wifi_scan_get_ap_records(&n, aps);
    size_t pos = 0;
    for (int i = 0; i < n && pos < sizeof(s_scan) - 48; i++) {
        if (!aps[i].ssid[0]) continue;
        pos += snprintf(s_scan + pos, sizeof(s_scan) - pos, "<option value='%s'>", (char *)aps[i].ssid);
    }
    free(aps);
}

static void start_setup(void) {
    if (s_setup_running) return;
    s_setup_running = true;
    esp_netif_create_default_wifi_ap();
    esp_wifi_set_mode(WIFI_MODE_APSTA);
    wifi_config_t ap = {.ap = {.ssid = SETUP_SSID, .ssid_len = strlen(SETUP_SSID), .channel = 6,
                               .authmode = WIFI_AUTH_OPEN, .max_connection = 2}};
    esp_wifi_set_config(WIFI_IF_AP, &ap);
    scan_networks();

    httpd_handle_t srv = NULL;
    httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
    if (httpd_start(&srv, &cfg) == ESP_OK) {
        httpd_uri_t get = {.uri = "/", .method = HTTP_GET, .handler = page_get};
        httpd_uri_t post = {.uri = "/save", .method = HTTP_POST, .handler = page_save};
        httpd_register_uri_handler(srv, &get);
        httpd_register_uri_handler(srv, &post);
    }
    ESP_LOGI(TAG, "setup hotspot '%s' at http://192.168.4.1", SETUP_SSID);
    status("WiFi: ChittiJr-Setup", "open 192.168.4.1");
}

// ---------------------------------------------------------------- Wi-Fi
static void wifi_event(void *arg, esp_event_base_t base, int32_t id, void *data) {
    static int retries;
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        if (s_ssid[0]) esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(s_events, BIT_GOT_IP);
        if (!s_ssid[0]) return;
        wifi_event_sta_disconnected_t *d = data;
        retries++;
        const char *why;
        char other[24];
        switch (d->reason) {
            case WIFI_REASON_NO_AP_FOUND:
            case WIFI_REASON_NO_AP_FOUND_W_COMPATIBLE_SECURITY:
            case WIFI_REASON_NO_AP_FOUND_IN_RSSI_THRESHOLD:
                why = "WiFi not found";  // wrong name, out of range, or 5 GHz only
                break;
            case WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT:
            case WIFI_REASON_HANDSHAKE_TIMEOUT:
            case WIFI_REASON_AUTH_FAIL:
            case WIFI_REASON_AUTH_EXPIRE:
            case WIFI_REASON_MIC_FAILURE:
                why = "Wrong password?";
                break;
            default:
                snprintf(other, sizeof(other), "WiFi error %d", d->reason);
                why = other;
        }
        ESP_LOGW(TAG, "join '%s' failed (try %d): reason %d, %s", s_ssid, retries, d->reason, why);
        if (retries >= 3) status(why, s_ssid);
        if (retries == 6) start_setup();  // can't join: offer the setup page while still retrying
        else if (retries < 3 && !s_setup_running) status("Joining WiFi...", s_ssid);
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        retries = 0;
        ip_event_got_ip_t *e = data;
        ESP_LOGI(TAG, "got IP " IPSTR, IP2STR(&e->ip_info.ip));
        xEventGroupSetBits(s_events, BIT_GOT_IP);
    }
}

// ---------------------------------------------------------------- server discovery (UDP broadcast)
static bool discover(char *uri, size_t cap) {
    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (sock < 0) return false;
    int yes = 1;
    setsockopt(sock, SOL_SOCKET, SO_BROADCAST, &yes, sizeof(yes));
    struct timeval tv = {.tv_sec = 2};
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    struct sockaddr_in to = {.sin_family = AF_INET, .sin_port = htons(DISCOVERY_PORT), .sin_addr.s_addr = htonl(INADDR_BROADCAST)};
    bool ok = false;
    for (int attempt = 0; attempt < 3 && !ok; attempt++) {
        sendto(sock, "CHITTI?", 7, 0, (struct sockaddr *)&to, sizeof(to));
        char buf[64];
        struct sockaddr_in from;
        socklen_t fl = sizeof(from);
        int n = recvfrom(sock, buf, sizeof(buf) - 1, 0, (struct sockaddr *)&from, &fl);
        if (n > 7 && memcmp(buf, "CHITTI ", 7) == 0) {
            buf[n] = 0;
            int port = atoi(buf + 7);
            snprintf(uri, cap, "ws://%s:%d/chitti", inet_ntoa(from.sin_addr), port);
            ok = true;
        }
    }
    close(sock);
    return ok;
}

// ---------------------------------------------------------------- WebSocket
static char *s_txt;
static size_t s_txt_cap;

static void ws_event(void *arg, esp_event_base_t base, int32_t id, void *data) {
    esp_websocket_event_data_t *e = data;
    switch (id) {
        case WEBSOCKET_EVENT_CONNECTED:
            s_ws_up = true;
            ESP_LOGI(TAG, "server connected");
            if (s_cb.on_connected) s_cb.on_connected();
            break;
        case WEBSOCKET_EVENT_DISCONNECTED:
        case WEBSOCKET_EVENT_CLOSED:
            if (s_ws_up && s_cb.on_disconnected) s_cb.on_disconnected();
            s_ws_up = false;
            break;
        case WEBSOCKET_EVENT_DATA: {
            static int last_op;
            int op = e->op_code == 0x0 ? last_op : e->op_code;  // 0 = continuation of the previous frame
            if (e->op_code == 0x1 || e->op_code == 0x2) last_op = e->op_code;
            if (op == 0x2) {
                if (s_cb.on_audio && e->data_len > 0) s_cb.on_audio((const uint8_t *)e->data_ptr, e->data_len);
            } else if (op == 0x1) {
                size_t total = e->payload_len;
                if (total + 1 > s_txt_cap) {
                    free(s_txt);
                    s_txt_cap = total + 1;
                    s_txt = malloc(s_txt_cap);
                }
                if (!s_txt) {
                    s_txt_cap = 0;
                    break;
                }
                memcpy(s_txt + e->payload_offset, e->data_ptr, e->data_len);
                if (e->payload_offset + e->data_len >= total) {
                    s_txt[total] = 0;
                    if (s_cb.on_text) s_cb.on_text(s_txt, total);
                }
            }
            break;
        }
        default:
            break;
    }
}

static void start_ws(const char *uri) {
    ESP_LOGI(TAG, "connecting to %s", uri);
    esp_websocket_client_config_t cfg = {
        .uri = uri,
        .buffer_size = 8192,
        .reconnect_timeout_ms = 3000,
        .network_timeout_ms = 10000,
        .task_stack = 6144,
        .task_prio = 8,
        .ping_interval_sec = 15,
    };
    s_ws = esp_websocket_client_init(&cfg);
    esp_websocket_register_events(s_ws, WEBSOCKET_EVENT_ANY, ws_event, NULL);
    esp_websocket_client_start(s_ws);
}

static void net_task(void *arg) {
    char uri[96];
    while (1) {
        xEventGroupWaitBits(s_events, BIT_GOT_IP, false, true, portMAX_DELAY);
        if (s_server[0]) {
            if (strncmp(s_server, "ws://", 5) == 0) snprintf(uri, sizeof(uri), "%s", s_server);
            else snprintf(uri, sizeof(uri), "ws://%s%s/chitti", s_server, strchr(s_server, ':') ? "" : ":8765");
        } else {
            status("Looking for", "Chitti server...");
            if (!discover(uri, sizeof(uri))) {
                status("No server found", "Start it on your PC");
                vTaskDelay(pdMS_TO_TICKS(3000));
                continue;
            }
        }
        status("Connecting...", NULL);
        start_ws(uri);
        // Watch the link; if it stays down, look for the server again (its IP may have changed).
        int down_s = 0;
        while (1) {
            vTaskDelay(pdMS_TO_TICKS(1000));
            down_s = s_ws_up ? 0 : down_s + 1;
            if (down_s == 4) status("Server offline", "Retrying...");
            if (down_s > 25 && !s_server[0]) break;
        }
        esp_websocket_client_stop(s_ws);
        esp_websocket_client_destroy(s_ws);
        s_ws = NULL;
    }
}

// ---------------------------------------------------------------- public
void net_init(const net_callbacks_t *cb) {
    s_cb = *cb;
    s_events = xEventGroupCreate();
    load_settings();

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t wcfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&wcfg));
    esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event, NULL);
    esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event, NULL);
    esp_wifi_set_storage(WIFI_STORAGE_RAM);
    esp_wifi_set_mode(WIFI_MODE_STA);
    if (s_ssid[0]) {
        wifi_config_t sta = {0};
        strlcpy((char *)sta.sta.ssid, s_ssid, sizeof(sta.sta.ssid));
        strlcpy((char *)sta.sta.password, s_pass, sizeof(sta.sta.password));
        sta.sta.threshold.authmode = s_pass[0] ? WIFI_AUTH_WPA_PSK : WIFI_AUTH_OPEN;
        esp_wifi_set_config(WIFI_IF_STA, &sta);
        status("Joining WiFi...", s_ssid);
    }
    ESP_ERROR_CHECK(esp_wifi_start());
    esp_wifi_set_ps(WIFI_PS_MIN_MODEM);  // radio naps between beacons: much less heat, small latency cost
    // Junior: transmit at ~13 dBm instead of 20. Full-power bursts pull current spikes from the same 5 V line
    // as the speaker amplifier and crackle in her voice; a home router a few metres away doesn't need more.
    esp_wifi_set_max_tx_power(52);  // units of 0.25 dBm
    if (!s_ssid[0]) start_setup();
    xTaskCreatePinnedToCore(net_task, "net", 4096, NULL, 4, NULL, 0);
}

bool net_ready(void) { return s_ws_up; }

void net_fast_mode(bool on) {
    // Wi-Fi power saving adds ~100 ms per message on some access points (e.g. the PC hotspot).
    // Stay fully awake while talking; nap again when idle to keep Chitti cool.
    static bool cur;
    if (on == cur) return;
    cur = on;
    esp_wifi_set_ps(on ? WIFI_PS_NONE : WIFI_PS_MIN_MODEM);
}

void net_send_text(const char *json) {
    if (s_ws_up) esp_websocket_client_send_text(s_ws, json, strlen(json), pdMS_TO_TICKS(1000));
}

void net_send_audio(const int16_t *pcm, size_t samples) {
    if (s_ws_up) esp_websocket_client_send_bin(s_ws, (const char *)pcm, samples * 2, pdMS_TO_TICKS(2000));
}

// ---------------------------------------------------------------- sound test: busy Wi-Fi
static void traffic_task(void *arg) {
    int ms = (int)(intptr_t)arg;
    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (sock >= 0) {
        int yes = 1;
        setsockopt(sock, SOL_SOCKET, SO_BROADCAST, &yes, sizeof(yes));
        struct sockaddr_in to = {.sin_family = AF_INET, .sin_port = htons(9), .sin_addr.s_addr = htonl(INADDR_BROADCAST)};
        static char junk[1400];
        int64_t end = esp_timer_get_time() + ms * 1000LL;
        while (esp_timer_get_time() < end) {  // ~200 full-size packets a second, like streaming her voice and more
            sendto(sock, junk, sizeof(junk), 0, (struct sockaddr *)&to, sizeof(to));
            vTaskDelay(pdMS_TO_TICKS(5));
        }
        close(sock);
    }
    vTaskDelete(NULL);
}

void net_test_traffic(int ms) {
    esp_wifi_set_ps(WIFI_PS_NONE);
    xTaskCreate(traffic_task, "traffic", 3072, (void *)(intptr_t)ms, 4, NULL);
}
