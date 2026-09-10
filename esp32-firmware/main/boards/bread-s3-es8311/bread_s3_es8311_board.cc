#include "wifi_board.h"
#include "codecs/es8311_audio_codec.h"
#include "display/display.h"
#include "application.h"
#include "button.h"
#include "config.h"
#include "led/single_led.h"

#include <esp_log.h>
#include <driver/i2c_master.h>

#define TAG "BreadS3Es8311Board"

class BreadS3Es8311Board : public WifiBoard {
private:
    i2c_master_bus_handle_t codec_i2c_bus_ = nullptr;
    Display* display_ = nullptr;
    Button boot_button_;
    uint8_t codec_addr_ = AUDIO_CODEC_ES8311_ADDR;

    void InitializeI2c() {
        ESP_LOGI(TAG, "=== STARTING GPIO 1 & 2 DIAGNOSTIC ===");
        
        // 测试 1: 输入 + 上拉模式
        gpio_config_t in_conf = {
            .pin_bit_mask = (1ULL << AUDIO_CODEC_I2C_SDA_PIN) | (1ULL << AUDIO_CODEC_I2C_SCL_PIN),
            .mode = GPIO_MODE_INPUT,
            .pull_up_en = GPIO_PULLUP_ENABLE,
            .pull_down_en = GPIO_PULLDOWN_DISABLE,
            .intr_type = GPIO_INTR_DISABLE,
        };
        gpio_config(&in_conf);
        vTaskDelay(pdMS_TO_TICKS(10));
        int in_sda = gpio_get_level(AUDIO_CODEC_I2C_SDA_PIN);
        int in_scl = gpio_get_level(AUDIO_CODEC_I2C_SCL_PIN);
        ESP_LOGI(TAG, "Test 1 (Input + Pullup): SDA(GPIO%d)=%d, SCL(GPIO%d)=%d",
                 AUDIO_CODEC_I2C_SDA_PIN, in_sda, AUDIO_CODEC_I2C_SCL_PIN, in_scl);

        // 测试 2: 主动驱动 HIGH 输出测试
        gpio_set_direction(AUDIO_CODEC_I2C_SDA_PIN, GPIO_MODE_INPUT_OUTPUT);
        gpio_set_direction(AUDIO_CODEC_I2C_SCL_PIN, GPIO_MODE_INPUT_OUTPUT);
        gpio_set_level(AUDIO_CODEC_I2C_SDA_PIN, 1);
        gpio_set_level(AUDIO_CODEC_I2C_SCL_PIN, 1);
        vTaskDelay(pdMS_TO_TICKS(10));
        int drive_high_sda = gpio_get_level(AUDIO_CODEC_I2C_SDA_PIN);
        int drive_high_scl = gpio_get_level(AUDIO_CODEC_I2C_SCL_PIN);
        ESP_LOGI(TAG, "Test 2 (Drive HIGH): SDA=%d, SCL=%d", drive_high_sda, drive_high_scl);

        // 测试 3: 主动驱动 LOW 输出测试
        gpio_set_level(AUDIO_CODEC_I2C_SDA_PIN, 0);
        gpio_set_level(AUDIO_CODEC_I2C_SCL_PIN, 0);
        vTaskDelay(pdMS_TO_TICKS(10));
        int drive_low_sda = gpio_get_level(AUDIO_CODEC_I2C_SDA_PIN);
        int drive_low_scl = gpio_get_level(AUDIO_CODEC_I2C_SCL_PIN);
        ESP_LOGI(TAG, "Test 3 (Drive LOW): SDA=%d, SCL=%d", drive_low_sda, drive_low_scl);

        // 恢复为输入上拉
        gpio_set_direction(AUDIO_CODEC_I2C_SDA_PIN, GPIO_MODE_INPUT);
        gpio_set_direction(AUDIO_CODEC_I2C_SCL_PIN, GPIO_MODE_INPUT);
        gpio_pullup_en(AUDIO_CODEC_I2C_SDA_PIN);
        gpio_pullup_en(AUDIO_CODEC_I2C_SCL_PIN);
        vTaskDelay(pdMS_TO_TICKS(10));

        ESP_LOGI(TAG, "=== END DIAGNOSTIC ===");

        i2c_master_bus_config_t i2c_bus_cfg = {
            .i2c_port = (i2c_port_t)0,
            .sda_io_num = AUDIO_CODEC_I2C_SDA_PIN,
            .scl_io_num = AUDIO_CODEC_I2C_SCL_PIN,
            .clk_source = I2C_CLK_SRC_DEFAULT,
            .glitch_ignore_cnt = 7,
            .intr_priority = 0,
            .trans_queue_depth = 0,
            .flags = {
                .enable_internal_pullup = 1,
            },
        };
        ESP_ERROR_CHECK(i2c_new_master_bus(&i2c_bus_cfg, &codec_i2c_bus_));
        I2cDetect();
    }

    void I2cDetect() {
        ESP_LOGI(TAG, "Scanning I2C bus (SDA=GPIO%d, SCL=GPIO%d)...", AUDIO_CODEC_I2C_SDA_PIN, AUDIO_CODEC_I2C_SCL_PIN);
        int found_count = 0;
        for (uint8_t addr = 0x08; addr < 0x78; addr++) {
            esp_err_t ret = i2c_master_probe(codec_i2c_bus_, addr, 50);
            if (ret == ESP_OK) {
                ESP_LOGI(TAG, "  -> Found I2C device at 0x%02X", addr);
                found_count++;
                if (addr == 0x18 || addr == 0x19) {
                    codec_addr_ = (addr << 1);
                }
            }
        }
        if (found_count == 0) {
            ESP_LOGE(TAG, "No I2C device responded! Please check connections:");
            ESP_LOGE(TAG, "  - Audio Module SDA -> ESP32-S3 GPIO%d", AUDIO_CODEC_I2C_SDA_PIN);
            ESP_LOGE(TAG, "  - Audio Module SCL -> ESP32-S3 GPIO%d", AUDIO_CODEC_I2C_SCL_PIN);
            ESP_LOGE(TAG, "  - Audio Module 5V  -> ESP32-S3 3V3");
            ESP_LOGE(TAG, "  - Audio Module GND -> ESP32-S3 GND");
        } else {
            ESP_LOGI(TAG, "ES8311 codec configured at I2C address: 0x%02X (8-bit: 0x%02X)",
                     codec_addr_ >> 1, codec_addr_);
        }
    }

    void InitializeButtons() {
        boot_button_.OnClick([this]() {
            auto& app = Application::GetInstance();
            if (app.GetDeviceState() == kDeviceStateStarting) {
                EnterWifiConfigMode();
                return;
            }
            app.ToggleChatState();
        });
    }

public:
    BreadS3Es8311Board() : boot_button_(BOOT_BUTTON_GPIO) {
        InitializeI2c();
        display_ = new NoDisplay();
        InitializeButtons();
        ESP_LOGI(TAG, "BreadS3Es8311Board initialized (ES8311 + NS4150B, NoDisplay)");
    }

    virtual Led* GetLed() override {
        static SingleLed led(BUILTIN_LED_GPIO);
        return &led;
    }

    virtual AudioCodec* GetAudioCodec() override {
        static Es8311AudioCodec audio_codec(
            codec_i2c_bus_,
            I2C_NUM_0,
            AUDIO_INPUT_SAMPLE_RATE,
            AUDIO_OUTPUT_SAMPLE_RATE,
            AUDIO_I2S_GPIO_MCLK,
            AUDIO_I2S_GPIO_BCLK,
            AUDIO_I2S_GPIO_WS,
            AUDIO_I2S_GPIO_DOUT,
            AUDIO_I2S_GPIO_DIN,
            AUDIO_CODEC_PA_PIN,
            codec_addr_,
            true);
        return &audio_codec;
    }

    virtual Display* GetDisplay() override {
        return display_;
    }
};

DECLARE_BOARD(BreadS3Es8311Board);
