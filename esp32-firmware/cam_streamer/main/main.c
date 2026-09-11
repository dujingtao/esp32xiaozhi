#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "esp_camera.h"
#include "esp_http_server.h"
#include "esp_timer.h"
#include "driver/gpio.h"

static const char *TAG = "S3CAM";

#define WIFI_SSID      "CMCC-jTNP"
#define WIFI_PASS      "n2hsv7xu"
#define WIFI_MAX_RETRY 20

#define CAM_PIN_PWDN    -1
#define CAM_PIN_RESET   -1
#define CAM_PIN_XCLK    15
#define CAM_PIN_SIOD    4
#define CAM_PIN_SIOC    5

#define CAM_PIN_D7      16
#define CAM_PIN_D6      17
#define CAM_PIN_D5      18
#define CAM_PIN_D4      12
#define CAM_PIN_D3      10
#define CAM_PIN_D2      8
#define CAM_PIN_D1      9
#define CAM_PIN_D0      11
#define CAM_PIN_VSYNC   6
#define CAM_PIN_HREF    7
#define CAM_PIN_PCLK    13

#define FLASH_LED_PIN   GPIO_NUM_48

#define PART_BOUNDARY "123456789000000000000987654321"
static const char* _STREAM_CONTENT_TYPE = "multipart/x-mixed-replace;boundary=" PART_BOUNDARY;
static const char* _STREAM_BOUNDARY = "\r\n--" PART_BOUNDARY "\r\n";
static const char* _STREAM_PART = "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n";

static EventGroupHandle_t s_wifi_event_group;
#define WIFI_CONNECTED_BIT BIT0
#define WIFI_FAIL_BIT      BIT1
static int s_retry_num = 0;
static char s_ip_addr[20] = "0.0.0.0";
static bool s_led_state = false;

static esp_err_t init_camera(void) {
    camera_config_t config;
    config.ledc_channel = LEDC_CHANNEL_0;
    config.ledc_timer = LEDC_TIMER_0;
    config.pin_d0 = CAM_PIN_D0;
    config.pin_d1 = CAM_PIN_D1;
    config.pin_d2 = CAM_PIN_D2;
    config.pin_d3 = CAM_PIN_D3;
    config.pin_d4 = CAM_PIN_D4;
    config.pin_d5 = CAM_PIN_D5;
    config.pin_d6 = CAM_PIN_D6;
    config.pin_d7 = CAM_PIN_D7;
    config.pin_xclk = CAM_PIN_XCLK;
    config.pin_pclk = CAM_PIN_PCLK;
    config.pin_vsync = CAM_PIN_VSYNC;
    config.pin_href = CAM_PIN_HREF;
    config.pin_sccb_sda = CAM_PIN_SIOD;
    config.pin_sccb_scl = CAM_PIN_SIOC;
    config.pin_pwdn = CAM_PIN_PWDN;
    config.pin_reset = CAM_PIN_RESET;
    config.xclk_freq_hz = 20000000;
    config.pixel_format = PIXFORMAT_JPEG;
    config.frame_size = FRAMESIZE_VGA;
    config.jpeg_quality = 12;
    config.fb_count = 2;
    config.fb_location = CAMERA_FB_IN_PSRAM;
    config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;

    esp_err_t err = esp_camera_init(&config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Camera Init Failed: 0x%x", err);
        return err;
    }

    sensor_t *s = esp_camera_sensor_get();
    if (s) {
        s->set_vflip(s, 0);
        s->set_hmirror(s, 0);
        s->set_brightness(s, 1);
        s->set_contrast(s, 1);
        s->set_saturation(s, 0);
    }
    ESP_LOGI(TAG, "Camera Init OK");
    return ESP_OK;
}

static void wifi_event_handler(void* arg, esp_event_base_t event_base,
                                int32_t event_id, void* event_data) {
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        ESP_LOGW(TAG, "WiFi disconnected, retrying...");
        esp_wifi_connect();
        s_retry_num++;
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t* event = (ip_event_got_ip_t*) event_data;
        snprintf(s_ip_addr, sizeof(s_ip_addr), IPSTR, IP2STR(&event->ip_info.ip));
        ESP_LOGI(TAG, "WiFi Got IP: %s", s_ip_addr);
        s_retry_num = 0;
        xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
    }
}

static void init_wifi(void) {
    s_wifi_event_group = xEventGroupCreate();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    esp_event_handler_instance_t instance_any_id;
    esp_event_handler_instance_t instance_got_ip;
    ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT,
                                                        ESP_EVENT_ANY_ID,
                                                        &wifi_event_handler,
                                                        NULL,
                                                        &instance_any_id));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT,
                                                        IP_EVENT_STA_GOT_IP,
                                                        &wifi_event_handler,
                                                        NULL,
                                                        &instance_got_ip));

    wifi_config_t wifi_config = {
        .sta = {
            .ssid = WIFI_SSID,
            .password = WIFI_PASS,
            .threshold.authmode = WIFI_AUTH_WPA2_PSK,
        },
    };
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));

    ESP_LOGI(TAG, "Connecting to %s...", WIFI_SSID);
    xEventGroupWaitBits(s_wifi_event_group,
            WIFI_CONNECTED_BIT,
            pdFALSE,
            pdFALSE,
            portMAX_DELAY);
}

/* HTTP Handlers */
static esp_err_t capture_handler(httpd_req_t *req) {
    ESP_LOGI(TAG, "HTTP Snapshot Requested");
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) {
        ESP_LOGE(TAG, "Camera capture failed");
        httpd_resp_send_500(req);
        return ESP_FAIL;
    }

    httpd_resp_set_type(req, "image/jpeg");
    httpd_resp_set_hdr(req, "Content-Disposition", "inline; filename=capture.jpg");
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    httpd_resp_set_hdr(req, "Cache-Control", "no-cache, no-store, must-revalidate");

    esp_err_t res = httpd_resp_send(req, (const char *)fb->buf, fb->len);
    esp_camera_fb_return(fb);
    ESP_LOGI(TAG, "Snapshot sent (%u bytes, res=%d)", (unsigned int)fb->len, res);
    return res;
}

static esp_err_t stream_handler(httpd_req_t *req) {
    ESP_LOGI(TAG, "HTTP Stream Started");
    camera_fb_t *fb = NULL;
    esp_err_t res = ESP_OK;
    char part_buf[128];

    httpd_resp_set_type(req, _STREAM_CONTENT_TYPE);
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    httpd_resp_set_hdr(req, "X-Framerate", "25");

    while (true) {
        fb = esp_camera_fb_get();
        if (!fb) {
            ESP_LOGE(TAG, "Camera capture failed during stream");
            res = ESP_FAIL;
            break;
        }

        size_t hlen = snprintf(part_buf, sizeof(part_buf), _STREAM_PART, fb->len);
        res = httpd_resp_send_chunk(req, part_buf, hlen);
        if (res == ESP_OK) {
            res = httpd_resp_send_chunk(req, (const char *)fb->buf, fb->len);
        }
        if (res == ESP_OK) {
            res = httpd_resp_send_chunk(req, _STREAM_BOUNDARY, strlen(_STREAM_BOUNDARY));
        }

        esp_camera_fb_return(fb);
        fb = NULL;

        if (res != ESP_OK) {
            ESP_LOGI(TAG, "Stream client disconnected");
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    return res;
}

static esp_err_t led_handler(httpd_req_t *req) {
    char query[32];
    if (httpd_req_get_url_query_str(req, query, sizeof(query)) == ESP_OK) {
        if (strstr(query, "state=1") != NULL) {
            s_led_state = true;
            gpio_set_level(FLASH_LED_PIN, 1);
            ESP_LOGI(TAG, "LED Flash ON");
        } else if (strstr(query, "state=0") != NULL) {
            s_led_state = false;
            gpio_set_level(FLASH_LED_PIN, 0);
            ESP_LOGI(TAG, "LED Flash OFF");
        }
    }
    httpd_resp_set_type(req, "application/json");
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    char resp[64];
    snprintf(resp, sizeof(resp), "{\"led\":%d}", s_led_state ? 1 : 0);
    return httpd_resp_send(req, resp, strlen(resp));
}

static esp_err_t index_handler(httpd_req_t *req) {
    const char *html = 
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>小智 ESP32-S3-CAM 视觉哨兵</title>"
        "<style>"
        "body{font-family:system-ui,-apple-system,sans-serif;background:#0f172a;color:#f8fafc;margin:0;padding:20px;display:flex;flex-direction:column;align-items:center;}"
        ".card{background:#1e293b;border-radius:16px;padding:20px;max-width:680px;width:100%;box-shadow:0 10px 25px rgba(0,0,0,0.5);border:1px solid #334155;}"
        "h1{font-size:20px;margin-top:0;display:flex;align-items:center;gap:8px;color:#38bdf8;}"
        ".stream-box{width:100%;background:#000;border-radius:12px;overflow:hidden;min-height:360px;display:flex;align-items:center;justify-content:center;}"
        "img{width:100%;height:auto;display:block;}"
        ".ctrls{display:flex;gap:12px;margin-top:16px;flex-wrap:wrap;}"
        "button{background:#4f46e5;color:#fff;border:none;padding:10px 18px;border-radius:8px;font-weight:bold;cursor:pointer;transition:0.2s;}"
        "button:hover{background:#4338ca;}"
        ".info{margin-top:16px;font-size:13px;color:#94a3b8;line-height:1.8;background:#0f172a;padding:12px;border-radius:8px;}"
        "code{background:#334155;padding:2px 6px;border-radius:4px;color:#38bdf8;}"
        "</style></head><body>"
        "<div class='card'>"
        "<h1>📷 小智 ESP32-S3-CAM 独立视觉哨兵节点</h1>"
        "<div class='stream-box'>"
        "<img src='/stream' alt='Live Stream'>"
        "</div>"
        "<div class='ctrls'>"
        "<button onclick=\"fetch('/led?state=1')\">💡 开启补光灯</button>"
        "<button onclick=\"fetch('/led?state=0')\" style='background:#64748b;'>🌑 关闭补光灯</button>"
        "<button onclick=\"window.open('/shot.jpg')\" style='background:#059669;'>📸 抓拍高清图片</button>"
        "</div>"
        "<div class='info'>"
        "<div><strong>设备 IP:</strong> %s &nbsp;|&nbsp; <strong>传感器:</strong> OV5640 (VGA 640x480)</div>"
        "<div><strong>抓拍接口 (Shot):</strong> <code>http://%s/shot.jpg</code></div>"
        "<div><strong>视频流接口 (Stream):</strong> <code>http://%s/stream</code></div>"
        "</div>"
        "</div>"
        "</body></html>";

    char resp_buf[2048];
    snprintf(resp_buf, sizeof(resp_buf), html, s_ip_addr, s_ip_addr, s_ip_addr);
    httpd_resp_set_type(req, "text/html");
    return httpd_resp_send(req, resp_buf, strlen(resp_buf));
}

static httpd_handle_t start_webserver(void) {
    httpd_handle_t server = NULL;
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.server_port = 80;
    config.ctrl_port = 32768;
    config.max_open_sockets = 7;
    config.lru_purge_enable = true;
    config.stack_size = 12288; // 12KB stack for camera frame handling

    ESP_LOGI(TAG, "Starting HTTP server on port 80 (stack=%d)", config.stack_size);
    if (httpd_start(&server, &config) == ESP_OK) {
        httpd_uri_t uri_index = {
            .uri       = "/",
            .method    = HTTP_GET,
            .handler   = index_handler,
            .user_ctx  = NULL
        };
        httpd_register_uri_handler(server, &uri_index);

        httpd_uri_t uri_shot = {
            .uri       = "/shot.jpg",
            .method    = HTTP_GET,
            .handler   = capture_handler,
            .user_ctx  = NULL
        };
        httpd_register_uri_handler(server, &uri_shot);

        httpd_uri_t uri_capture = {
            .uri       = "/capture",
            .method    = HTTP_GET,
            .handler   = capture_handler,
            .user_ctx  = NULL
        };
        httpd_register_uri_handler(server, &uri_capture);

        httpd_uri_t uri_stream = {
            .uri       = "/stream",
            .method    = HTTP_GET,
            .handler   = stream_handler,
            .user_ctx  = NULL
        };
        httpd_register_uri_handler(server, &uri_stream);

        httpd_uri_t uri_led = {
            .uri       = "/led",
            .method    = HTTP_GET,
            .handler   = led_handler,
            .user_ctx  = NULL
        };
        httpd_register_uri_handler(server, &uri_led);

        return server;
    }

    ESP_LOGE(TAG, "Error starting server!");
    return NULL;
}

void app_main(void) {
    // Configure Flash LED GPIO
    gpio_config_t io_conf = {
        .pin_bit_mask = (1ULL << FLASH_LED_PIN),
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_ENABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&io_conf);
    gpio_set_level(FLASH_LED_PIN, 0);

    // Initialize NVS
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    // Initialize Camera
    if (init_camera() != ESP_OK) {
        ESP_LOGE(TAG, "Camera initialization failed!");
        return;
    }

    // Connect WiFi
    init_wifi();

    // Start Web Server
    start_webserver();

    ESP_LOGI(TAG, "ESP32-S3 Camera Streamer Ready! Access at: http://%s/", s_ip_addr);
}
