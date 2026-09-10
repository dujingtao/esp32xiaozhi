#include "websocket_protocol.h"
#include "application.h"
#include "board.h"
#include "settings.h"
#include "system_info.h"

#include <esp_log.h>
#include <arpa/inet.h>
#include <cJSON.h>
#include <cstring>
#include "assets/lang_config.h"

#define TAG "WS"

WebsocketProtocol::WebsocketProtocol() {
    event_group_handle_ = xEventGroupCreate();

    // Initialize reconnect timer
    esp_timer_create_args_t reconnect_timer_args = {
        .callback =
            [](void* arg) {
                WebsocketProtocol* protocol = (WebsocketProtocol*)arg;
                auto& app = Application::GetInstance();
                if (app.GetDeviceState() == kDeviceStateIdle) {
                    ESP_LOGI(TAG, "Reconnecting to WebSocket server");
                    auto alive = protocol->alive_;
                    app.Schedule([protocol, alive]() {
                        if (*alive) {
                            protocol->ConnectWebSocket(false);
                        }
                    });
                }
            },
        .arg = this,
    };
    esp_timer_create(&reconnect_timer_args, &reconnect_timer_);
}

WebsocketProtocol::~WebsocketProtocol() {
    *alive_ = false;

    if (reconnect_timer_ != nullptr) {
        esp_timer_stop(reconnect_timer_);
        esp_timer_delete(reconnect_timer_);
    }

    vEventGroupDelete(event_group_handle_);
}

bool WebsocketProtocol::Start() {
    // Schedule non-blocking background connection to WebSocket server for persistent standby
    ScheduleReconnect();
    return true;
}

bool WebsocketProtocol::SendAudio(std::unique_ptr<AudioStreamPacket> packet) {
    if (websocket_ == nullptr || !websocket_->IsConnected()) {
        return false;
    }

    if (version_ == 2) {
        std::string serialized;
        serialized.resize(sizeof(BinaryProtocol2) + packet->payload.size());
        auto bp2 = (BinaryProtocol2*)serialized.data();
        bp2->version = htons(version_);
        bp2->type = 0;
        bp2->reserved = 0;
        bp2->timestamp = htonl(packet->timestamp);
        bp2->payload_size = htonl(packet->payload.size());
        memcpy(bp2->payload, packet->payload.data(), packet->payload.size());

        return websocket_->Send(serialized.data(), serialized.size(), true);
    } else if (version_ == 3) {
        std::string serialized;
        serialized.resize(sizeof(BinaryProtocol3) + packet->payload.size());
        auto bp3 = (BinaryProtocol3*)serialized.data();
        bp3->type = 0;
        bp3->reserved = 0;
        bp3->payload_size = htons(packet->payload.size());
        memcpy(bp3->payload, packet->payload.data(), packet->payload.size());

        return websocket_->Send(serialized.data(), serialized.size(), true);
    } else {
        return websocket_->Send(packet->payload.data(), packet->payload.size(), true);
    }
}

bool WebsocketProtocol::SendText(const std::string& text) {
    if (websocket_ == nullptr || !websocket_->IsConnected()) {
        return false;
    }

    if (!websocket_->Send(text)) {
        ESP_LOGE(TAG, "Failed to send text: %s", text.c_str());
        SetError(Lang::Strings::SERVER_ERROR);
        return false;
    }

    return true;
}

bool WebsocketProtocol::IsAudioChannelOpened() const {
    return websocket_ != nullptr && websocket_->IsConnected() && !error_occurred_;
}

void WebsocketProtocol::CloseAudioChannel(bool send_goodbye) {
    (void)send_goodbye;
    // Keep websocket connection alive in background for persistent standby and proactive wake.
    // If the server explicitly disconnected, OnDisconnected will handle auto-reconnect.
}

bool WebsocketProtocol::OpenAudioChannel() {
    if (IsAudioChannelOpened()) {
        return true;
    }
    return ConnectWebSocket(true);
}

void WebsocketProtocol::ScheduleReconnect() {
    if (reconnect_timer_ != nullptr && !esp_timer_is_active(reconnect_timer_)) {
        esp_timer_start_once(reconnect_timer_, 3000 * 1000);  // 3s later
    }
}

bool WebsocketProtocol::ConnectWebSocket(bool report_error) {
    if (websocket_ != nullptr) {
        if (websocket_->IsConnected()) {
            return true;
        }
        websocket_.reset();
    }

    Settings settings("websocket", false);
    std::string url = settings.GetString("url");
    std::string token = settings.GetString("token");
    int version = settings.GetInt("version");
    if (version != 0) {
        version_ = version;
    }

    error_occurred_ = false;

    auto network = Board::GetInstance().GetNetwork();
    websocket_ = network->CreateWebSocket(1);
    if (websocket_ == nullptr) {
        ESP_LOGE(TAG, "Failed to create websocket");
        if (report_error) {
            SetError(Lang::Strings::SERVER_NOT_CONNECTED);
        }
        ScheduleReconnect();
        return false;
    }

    if (!token.empty()) {
        // If token not has a space, add "Bearer " prefix
        if (token.find(" ") == std::string::npos) {
            token = "Bearer " + token;
        }
        websocket_->SetHeader("Authorization", token.c_str());
    }
    websocket_->SetHeader("Protocol-Version", std::to_string(version_).c_str());
    websocket_->SetHeader("Device-Id", SystemInfo::GetMacAddress().c_str());
    websocket_->SetHeader("Client-Id", Board::GetInstance().GetUuid().c_str());

    websocket_->OnData([this](const char* data, size_t len, bool binary) {
        if (binary) {
            if (on_incoming_audio_ != nullptr) {
                if (version_ == 2) {
                    BinaryProtocol2* bp2 = (BinaryProtocol2*)data;
                    bp2->version = ntohs(bp2->version);
                    bp2->type = ntohs(bp2->type);
                    bp2->timestamp = ntohl(bp2->timestamp);
                    bp2->payload_size = ntohl(bp2->payload_size);
                    auto payload = (uint8_t*)bp2->payload;
                    on_incoming_audio_(std::make_unique<AudioStreamPacket>(AudioStreamPacket{
                        .sample_rate = server_sample_rate_,
                        .frame_duration = server_frame_duration_,
                        .timestamp = bp2->timestamp,
                        .payload = std::vector<uint8_t>(payload, payload + bp2->payload_size)}));
                } else if (version_ == 3) {
                    BinaryProtocol3* bp3 = (BinaryProtocol3*)data;
                    bp3->type = 0;
                    bp3->reserved = 0;
                    bp3->payload_size = ntohs(bp3->payload_size);
                    auto payload = (uint8_t*)bp3->payload;
                    on_incoming_audio_(std::make_unique<AudioStreamPacket>(AudioStreamPacket{
                        .sample_rate = server_sample_rate_,
                        .frame_duration = server_frame_duration_,
                        .timestamp = 0,
                        .payload = std::vector<uint8_t>(payload, payload + bp3->payload_size)}));
                } else {
                    on_incoming_audio_(std::make_unique<AudioStreamPacket>(AudioStreamPacket{
                        .sample_rate = server_sample_rate_,
                        .frame_duration = server_frame_duration_,
                        .timestamp = 0,
                        .payload = std::vector<uint8_t>((uint8_t*)data, (uint8_t*)data + len)}));
                }
            }
        } else {
            // Parse JSON data
            auto root = cJSON_ParseWithLength(data, len);
            auto type = cJSON_GetObjectItem(root, "type");
            if (cJSON_IsString(type)) {
                if (strcmp(type->valuestring, "hello") == 0) {
                    ParseServerHello(root);
                } else {
                    if (on_incoming_json_ != nullptr) {
                        on_incoming_json_(root);
                    }
                }
            } else {
                ESP_LOGE(TAG, "Missing message type, data: %s", std::string(data, len).c_str());
            }
            cJSON_Delete(root);
        }
        last_incoming_time_ = std::chrono::steady_clock::now();
    });

    websocket_->OnDisconnected([this]() {
        ESP_LOGI(TAG, "Websocket disconnected");
        if (on_audio_channel_closed_ != nullptr) {
            on_audio_channel_closed_();
        }
        ScheduleReconnect();
    });

    ESP_LOGI(TAG, "Connecting to websocket server: %s with version: %d", url.c_str(), version_);
    if (!websocket_->Connect(url.c_str())) {
        ESP_LOGE(TAG, "Failed to connect to websocket server, code=%d", websocket_->GetLastError());
        if (report_error) {
            SetError(Lang::Strings::SERVER_NOT_CONNECTED);
        }
        ScheduleReconnect();
        return false;
    }

    // Send hello message to describe the client
    auto message = GetHelloMessage();
    if (!SendText(message)) {
        ScheduleReconnect();
        return false;
    }

    if (report_error) {
        // Wait for server hello
        EventBits_t bits =
            xEventGroupWaitBits(event_group_handle_, WEBSOCKET_PROTOCOL_SERVER_HELLO_EVENT, pdTRUE,
                                pdFALSE, pdMS_TO_TICKS(10000));
        if (!(bits & WEBSOCKET_PROTOCOL_SERVER_HELLO_EVENT)) {
            ESP_LOGE(TAG, "Failed to receive server hello");
            SetError(Lang::Strings::SERVER_TIMEOUT);
            ScheduleReconnect();
            return false;
        }

        if (on_audio_channel_opened_ != nullptr) {
            on_audio_channel_opened_();
        }
    }

    return true;
}

std::string WebsocketProtocol::GetHelloMessage() {
    // keys: message type, version, audio_params (format, sample_rate, channels)
    cJSON* root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "type", "hello");
    cJSON_AddNumberToObject(root, "version", version_);
    cJSON* features = cJSON_CreateObject();
#if CONFIG_USE_SERVER_AEC
    cJSON_AddBoolToObject(features, "aec", true);
#endif
    cJSON_AddBoolToObject(features, "mcp", true);
    cJSON_AddItemToObject(root, "features", features);
    AddTextFontCapabilities(root);
    cJSON_AddStringToObject(root, "transport", "websocket");
    cJSON* audio_params = cJSON_CreateObject();
    cJSON_AddStringToObject(audio_params, "format", "opus");
    cJSON_AddNumberToObject(audio_params, "sample_rate", 16000);
    cJSON_AddNumberToObject(audio_params, "channels", 1);
    cJSON_AddNumberToObject(audio_params, "frame_duration", OPUS_FRAME_DURATION_MS);
    cJSON_AddItemToObject(root, "audio_params", audio_params);

    // [设备身份与唤醒词上报]: 客户端握手时自动上报当前设备的自定义名字、空间多级位置以及生效中的唤醒词
    // 使得云端服务在建立 WebSocket 语音会话的第一时间即可获知此 ESP32 节点的身份与唤醒配置
    Settings device_settings("device");
    std::string dev_name = device_settings.GetString("name");
    std::string city = device_settings.GetString("city");
    std::string place = device_settings.GetString("place");
    std::string room = device_settings.GetString("room");
    cJSON* device_info = nullptr;
    if (!dev_name.empty() || !city.empty() || !place.empty() || !room.empty()) {
        device_info = cJSON_CreateObject();
        if (!dev_name.empty()) cJSON_AddStringToObject(device_info, "name", dev_name.c_str());
        if (!city.empty()) cJSON_AddStringToObject(device_info, "city", city.c_str());
        if (!place.empty()) cJSON_AddStringToObject(device_info, "place", place.c_str());
        if (!room.empty()) cJSON_AddStringToObject(device_info, "room", room.c_str());
        cJSON_AddItemToObject(root, "device_info", device_info);
    }

    Settings wake_settings("wake_word");
    std::string wake_cmd = wake_settings.GetString("command");
    std::string wake_text = wake_settings.GetString("text");
    if (!wake_cmd.empty()) {
        if (device_info == nullptr) {
            device_info = cJSON_CreateObject();
            cJSON_AddItemToObject(root, "device_info", device_info);
        }
        cJSON_AddStringToObject(device_info, "wake_pinyin", wake_cmd.c_str());
        cJSON_AddStringToObject(device_info, "wake_text", wake_text.c_str());
    }

    auto json_str = cJSON_PrintUnformatted(root);
    std::string message(json_str);
    cJSON_free(json_str);
    cJSON_Delete(root);
    return message;
}

void WebsocketProtocol::ParseServerHello(const cJSON* root) {
    auto transport = cJSON_GetObjectItem(root, "transport");
    if (transport == nullptr || strcmp(transport->valuestring, "websocket") != 0) {
        ESP_LOGE(TAG, "Unsupported transport: %s", transport->valuestring);
        return;
    }

    auto session_id = cJSON_GetObjectItem(root, "session_id");
    if (cJSON_IsString(session_id)) {
        session_id_ = session_id->valuestring;
        ESP_LOGI(TAG, "Session ID: %s", session_id_.c_str());
    }

    auto audio_params = cJSON_GetObjectItem(root, "audio_params");
    if (cJSON_IsObject(audio_params)) {
        auto sample_rate = cJSON_GetObjectItem(audio_params, "sample_rate");
        if (cJSON_IsNumber(sample_rate)) {
            server_sample_rate_ = sample_rate->valueint;
        }
        auto frame_duration = cJSON_GetObjectItem(audio_params, "frame_duration");
        if (cJSON_IsNumber(frame_duration)) {
            server_frame_duration_ = frame_duration->valueint;
        }
    }

    // [云端控制台同步 - 设备信息]: 若服务端返回包含 device_info，将新设定的名字/位置自动持久化到 NVS
    auto device_info = cJSON_GetObjectItem(root, "device_info");
    if (cJSON_IsObject(device_info)) {
        Settings settings("device", true);
        auto name = cJSON_GetObjectItem(device_info, "name");
        if (cJSON_IsString(name)) settings.SetString("name", name->valuestring);
        auto city = cJSON_GetObjectItem(device_info, "city");
        if (cJSON_IsString(city)) settings.SetString("city", city->valuestring);
        auto place = cJSON_GetObjectItem(device_info, "place");
        if (cJSON_IsString(place)) settings.SetString("place", place->valuestring);
        auto room = cJSON_GetObjectItem(device_info, "room");
        if (cJSON_IsString(room)) settings.SetString("room", room->valuestring);
    }

    // [云端控制台同步 - 唤醒词]: 若服务端返回包含 wake_word，自动持久化至 NVS "wake_word" 命名空间
    auto wake_word = cJSON_GetObjectItem(root, "wake_word");
    if (cJSON_IsObject(wake_word)) {
        Settings settings("wake_word", true);
        auto command = cJSON_GetObjectItem(wake_word, "command");
        if (cJSON_IsString(command)) {
            settings.SetString("command", command->valuestring);
        } else {
            auto pinyin = cJSON_GetObjectItem(wake_word, "pinyin");
            if (cJSON_IsString(pinyin)) {
                settings.SetString("command", pinyin->valuestring);
            }
        }
        auto text = cJSON_GetObjectItem(wake_word, "text");
        if (cJSON_IsString(text)) {
            settings.SetString("text", text->valuestring);
        }
        auto threshold = cJSON_GetObjectItem(wake_word, "threshold");
        if (cJSON_IsNumber(threshold)) {
            settings.SetInt("threshold", threshold->valueint);
        }
    }

    error_occurred_ = false;
    if (on_connected_ != nullptr) {
        on_connected_();
    }

    xEventGroupSetBits(event_group_handle_, WEBSOCKET_PROTOCOL_SERVER_HELLO_EVENT);
}
