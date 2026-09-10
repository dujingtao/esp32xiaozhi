#ifndef CUSTOM_WAKE_WORD_H
#define CUSTOM_WAKE_WORD_H

#include <esp_attr.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <esp_mn_iface.h>
#include <esp_mn_models.h>
#include <model_path.h>

#include <deque>
#include <string>
#include <vector>
#include <functional>
#include <mutex>
#include <condition_variable>
#include <atomic>

#include "audio_codec.h"
#include "wake_word.h"
#include "wake_word_audio_cache.h"

class CustomWakeWord : public WakeWord {
public:
    CustomWakeWord();
    ~CustomWakeWord();

    bool Initialize(AudioCodec* codec, srmodel_list_t* models_list);
    void Feed(const std::vector<int16_t>& data);
    void FeedMono(const int16_t* data, size_t samples);
    void OnWakeWordDetected(std::function<void(const std::string& wake_word)> callback);
    void Start();
    void Stop();
    size_t GetFeedSize();
    void EncodeWakeWordData();
    const std::string& GetLastDetectedWakeWord() const { return last_detected_wake_word_; }

    /**
     * @brief 动态更新本地语音唤醒词（免固件重编译，支持运行时热重载）
     * 
     * @param command 唤醒词拼音全拼，多音节以空格分隔，如 "jia wei si"、"ni hao xiao zhi"
     * @param text 唤醒词中文显示文本，如 "贾维斯"、"你好小智"
     * @param threshold 识别灵敏度阈值 (1-99，默认 20，数值越小越敏感)；若为 0 则保持原阈值不变
     * @return true 唤醒词成功写入 NVS 并热重载至 Multinet 引擎；false 参数无效或更新失败
     * 
     * @note 1. 该方法会自动将新唤醒词配置持久化保存至 NVS 命名空间 "wake_word" 中（掉电不丢失）；
     *       2. 运行时加锁清除旧指令并调用 esp_mn_commands_update() 重构声学 FST 图，实现秒级即时生效；
     *       3. 支持通过 Web 控制台远程推送、OTA 更新下发，或通过端侧语音 MCP 命令 (self.device.set_wake_word) 动态调用。
     */
    bool SetWakeWord(const std::string& command, const std::string& text, int threshold = 0);

private:
    struct Command {
        std::string command;
        std::string text;
        std::string action;
    };

    // multinet 相关成员变量
    esp_mn_iface_t* multinet_ = nullptr;
    model_iface_data_t* multinet_model_data_ = nullptr;
    srmodel_list_t *models_ = nullptr;
    bool owns_models_ = false;
    char* mn_name_ = nullptr;
    std::string language_ = "cn";
    int duration_ = 3000;
    float threshold_ = 0.2;
    std::deque<Command> commands_;
 
    std::function<void(const std::string& wake_word)> wake_word_detected_callback_;
    AudioCodec* codec_ = nullptr;
    std::string last_detected_wake_word_;
    std::atomic<bool> running_ = false;
    std::vector<int16_t> input_buffer_;
    std::mutex input_buffer_mutex_;

    TaskHandle_t wake_word_encode_task_ = nullptr;
    StaticTask_t* wake_word_encode_task_buffer_ = nullptr;
    StackType_t* wake_word_encode_task_stack_ = nullptr;
    WakeWordAudioCache wake_word_audio_cache_;
    std::deque<std::vector<uint8_t>> wake_word_opus_;
    std::mutex wake_word_mutex_;
    std::condition_variable wake_word_cv_;

    void FeedSamples(const int16_t* data, size_t samples, bool mono);
    void ParseWakenetModelConfig();
};

#endif
