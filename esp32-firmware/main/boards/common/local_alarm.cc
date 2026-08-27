#include "local_alarm.h"
#include <esp_log.h>
#include <sys/time.h>
#include <cstring>
#include <sstream>
#include <iomanip>
#include "settings.h"
#include "application.h"
#include "assets/lang_config.h"

static const char* TAG = "LocalAlarm";

LocalAlarmClock& LocalAlarmClock::GetInstance() {
    static LocalAlarmClock instance;
    return instance;
}

LocalAlarmClock::LocalAlarmClock() {
}

LocalAlarmClock::~LocalAlarmClock() {
    if (timer_handle_ != nullptr) {
        esp_timer_stop(timer_handle_);
        esp_timer_delete(timer_handle_);
        timer_handle_ = nullptr;
    }
}

void LocalAlarmClock::Initialize() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (initialized_) {
        return;
    }

    LoadFromNvs();

    esp_timer_create_args_t timer_args = {
        .callback = [](void* arg) {
            LocalAlarmClock* clock = static_cast<LocalAlarmClock*>(arg);
            clock->CheckAlarms();
        },
        .arg = this,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "local_alarm_timer",
        .skip_unhandled_events = true,
    };

    esp_timer_create(&timer_args, &timer_handle_);
    esp_timer_start_periodic(timer_handle_, 1000000); // Check every 1 second

    initialized_ = true;
    ESP_LOGI(TAG, "LocalAlarmClock initialized with %u saved alarms", (unsigned int)alarms_.size());
}

void LocalAlarmClock::RegisterMcpTools() {
    auto& mcp = McpServer::GetInstance();

    mcp.AddTool("self.alarm.set_alarm",
        "Set a local hardware alarm on the ESP32 device at a specific time (HH:MM format, e.g. '07:30', '12:00', '20:00'). Works even when offline or disconnected.",
        PropertyList({
            Property("time_str", kPropertyTypeString),
            Property("note", kPropertyTypeString, std::string("闹钟"))
        }),
        [this](const PropertyList& properties) -> ReturnValue {
            auto time_str = properties["time_str"].value<std::string>();
            auto note = properties["note"].value<std::string>();
            return SetAlarm(time_str, note);
        });

    mcp.AddTool("self.alarm.set_countdown",
        "Set a local hardware countdown timer on the ESP32 device for a given number of seconds. Works even when offline or disconnected.",
        PropertyList({
            Property("duration_seconds", kPropertyTypeInteger),
            Property("note", kPropertyTypeString, std::string("倒计时"))
        }),
        [this](const PropertyList& properties) -> ReturnValue {
            auto duration_seconds = properties["duration_seconds"].value<int>();
            auto note = properties["note"].value<std::string>();
            return SetCountdown(duration_seconds, note);
        });

    mcp.AddTool("self.alarm.list_alarms",
        "List all active local hardware alarms and countdown timers stored on the ESP32 device.",
        PropertyList(),
        [this](const PropertyList& properties) -> ReturnValue {
            return ListAlarms();
        });

    mcp.AddTool("self.alarm.cancel_alarm",
        "Cancel a local hardware alarm or countdown timer matching the keyword, or 'all'/'全部' to cancel all.",
        PropertyList({
            Property("keyword", kPropertyTypeString)
        }),
        [this](const PropertyList& properties) -> ReturnValue {
            auto keyword = properties["keyword"].value<std::string>();
            return CancelAlarm(keyword);
        });

    ESP_LOGI(TAG, "Local hardware alarm MCP tools registered (self.alarm.*)");
}

std::string LocalAlarmClock::SetAlarm(const std::string& time_str, const std::string& note) {
    int target_hour = 0;
    int target_min = 0;

    if (sscanf(time_str.c_str(), "%d:%d", &target_hour, &target_min) < 2) {
        return "时间格式错误，请使用 HH:MM 格式，例如 07:30";
    }

    time_t now = time(nullptr);
    struct tm tm_now;
    localtime_r(&now, &tm_now);

    struct tm tm_target = tm_now;
    tm_target.tm_hour = target_hour;
    tm_target.tm_min = target_min;
    tm_target.tm_sec = 0;

    time_t target_ts = mktime(&tm_target);
    bool is_tomorrow = false;
    if (target_ts <= now) {
        target_ts += 86400; // Tomorrow
        is_tomorrow = true;
    }

    std::string actual_note = note.empty() ? "闹钟" : note;
    std::string id = "alarm_" + std::to_string(esp_timer_get_time() / 1000);

    LocalAlarmItem item;
    item.id = id;
    item.type = "alarm";
    item.time_str = time_str;
    item.note = actual_note;
    item.target_time = target_ts;
    item.target_uptime_us = esp_timer_get_time() + static_cast<int64_t>(target_ts - now) * 1000000;
    item.status = "running";

    {
        std::lock_guard<std::mutex> lock(mutex_);
        alarms_.push_back(item);
        SaveToNvs();
    }

    int diff_sec = static_cast<int>(target_ts - now);
    int hours = diff_sec / 3600;
    int mins = (diff_sec % 3600) / 60;

    std::ostringstream oss;
    oss << "已在本地硬件设置【" << actual_note << "】闹钟！响铃时间："
        << (is_tomorrow ? "明天 " : "今天 ") << std::setw(2) << std::setfill('0') << target_hour
        << ":" << std::setw(2) << std::setfill('0') << target_min
        << "（距离响铃约 " << hours << " 小时 " << mins << " 分钟）。芯片本地定时，即使离线断网也会准时唤醒响铃。";

    ESP_LOGI(TAG, "Added local alarm: %s at %ld", actual_note.c_str(), (long)target_ts);
    return oss.str();
}

std::string LocalAlarmClock::SetCountdown(int duration_seconds, const std::string& note) {
    if (duration_seconds <= 0) {
        return "倒计时时长必须大于0秒";
    }

    time_t now = time(nullptr);
    time_t target_ts = now + duration_seconds;
    int64_t now_us = esp_timer_get_time();
    int64_t target_us = now_us + static_cast<int64_t>(duration_seconds) * 1000000;

    std::string actual_note = note.empty() ? "倒计时" : note;
    std::string id = "timer_" + std::to_string(now_us / 1000);

    LocalAlarmItem item;
    item.id = id;
    item.type = "timer";
    item.time_str = std::to_string(duration_seconds) + "s";
    item.note = actual_note;
    item.target_time = target_ts;
    item.target_uptime_us = target_us;
    item.status = "running";

    {
        std::lock_guard<std::mutex> lock(mutex_);
        alarms_.push_back(item);
        SaveToNvs();
    }

    std::string dur_str;
    if (duration_seconds >= 3600) {
        dur_str = std::to_string(duration_seconds / 3600) + "小时" + std::to_string((duration_seconds % 3600) / 60) + "分钟";
    } else if (duration_seconds >= 60) {
        dur_str = std::to_string(duration_seconds / 60) + "分钟" + (duration_seconds % 60 ? std::to_string(duration_seconds % 60) + "秒" : "");
    } else {
        dur_str = std::to_string(duration_seconds) + "秒";
    }

    std::string result = "已在本地硬件启动【" + actual_note + "】倒计时（时长：" + dur_str + "）！硬件本地倒计时已开始，即使设备待机休眠也会准时响铃。";
    ESP_LOGI(TAG, "Added local countdown: %s for %d sec", actual_note.c_str(), duration_seconds);
    return result;
}

std::string LocalAlarmClock::ListAlarms() {
    std::lock_guard<std::mutex> lock(mutex_);
    time_t now = time(nullptr);
    int64_t now_us = esp_timer_get_time();

    std::vector<LocalAlarmItem> active;
    for (const auto& a : alarms_) {
        if (a.status == "running") {
            if ((now > 1700000000 && a.target_time > now) || (a.target_uptime_us > now_us)) {
                active.push_back(a);
            }
        }
    }

    if (active.empty()) {
        return "本地硬件当前没有正在运行的闹钟或倒计时。";
    }

    std::ostringstream oss;
    oss << "本地硬件当前共有 " << active.size() << " 个正在运行的提醒：\n";
    for (size_t i = 0; i < active.size(); ++i) {
        const auto& a = active[i];
        int remaining_sec = 0;
        if (now > 1700000000 && a.target_time > now) {
            remaining_sec = static_cast<int>(a.target_time - now);
        } else if (a.target_uptime_us > now_us) {
            remaining_sec = static_cast<int>((a.target_uptime_us - now_us) / 1000000);
        }

        std::string rem_str;
        if (remaining_sec >= 3600) {
            rem_str = std::to_string(remaining_sec / 3600) + "小时" + std::to_string((remaining_sec % 3600) / 60) + "分";
        } else if (remaining_sec >= 60) {
            rem_str = std::to_string(remaining_sec / 60) + "分" + std::to_string(remaining_sec % 60) + "秒";
        } else {
            rem_str = std::to_string(remaining_sec) + "秒";
        }

        oss << (i + 1) << ". 【" << (a.type == "timer" ? "倒计时" : "闹钟") << "】"
            << a.note << "（剩余 " << rem_str << "）\n";
    }

    return oss.str();
}

std::string LocalAlarmClock::CancelAlarm(const std::string& keyword) {
    std::lock_guard<std::mutex> lock(mutex_);
    int count = 0;
    bool cancel_all = (keyword == "all" || keyword == "全部" || keyword == "所有" || keyword == "刚才");

    for (auto& a : alarms_) {
        if (a.status == "running") {
            if (cancel_all || a.note.find(keyword) != std::string::npos || keyword.find(a.note) != std::string::npos) {
                a.status = "cancelled";
                count++;
            }
        }
    }

    if (count > 0) {
        SaveToNvs();
        return "已成功为您取消 " + std::to_string(count) + " 个本地硬件闹钟/倒计时任务。";
    }

    return "未找到与【" + keyword + "】匹配的本地硬件闹钟或倒计时。";
}

void LocalAlarmClock::CheckAlarms() {
    std::vector<LocalAlarmItem> triggered_items;
    time_t now = time(nullptr);
    int64_t now_us = esp_timer_get_time();

    {
        std::lock_guard<std::mutex> lock(mutex_);
        bool changed = false;

        for (auto& a : alarms_) {
            if (a.status == "running") {
                bool time_reached = false;
                if (now > 1700000000 && a.target_time > 0 && now >= a.target_time) {
                    time_reached = true;
                } else if (a.target_uptime_us > 0 && now_us >= a.target_uptime_us) {
                    time_reached = true;
                }

                if (time_reached) {
                    a.status = "completed";
                    triggered_items.push_back(a);
                    changed = true;
                }
            }
        }

        if (changed) {
            SaveToNvs();
        }
    }

    for (const auto& item : triggered_items) {
        TriggerAlarm(item);
    }
}

void LocalAlarmClock::TriggerAlarm(const LocalAlarmItem& item) {
    ESP_LOGI(TAG, "Triggering local hardware alarm! Note: %s, Time: %s", item.note.c_str(), item.time_str.c_str());

    auto& app = Application::GetInstance();
    app.Schedule([item]() {
        std::string alert_title = "⏰ 闹钟响铃";
        std::string alert_text = item.note.empty() ? "时间到了！" : ("【" + item.note + "】时间到了！");

        // 离线硬件弹窗并播放提示音
        Application::GetInstance().Alert(alert_title.c_str(), alert_text.c_str(), "happy", Lang::Sounds::OGG_VIBRATION);
    });
}

void LocalAlarmClock::LoadFromNvs() {
    Settings settings("local_alarm");
    std::string json_str = settings.GetString("alarms", "[]");

    cJSON* root = cJSON_Parse(json_str.c_str());
    if (root == nullptr || !cJSON_IsArray(root)) {
        if (root) cJSON_Delete(root);
        return;
    }

    alarms_.clear();
    int size = cJSON_GetArraySize(root);
    time_t now = time(nullptr);

    for (int i = 0; i < size; ++i) {
        cJSON* obj = cJSON_GetArrayItem(root, i);
        if (!cJSON_IsObject(obj)) continue;

        LocalAlarmItem item;
        auto c_id = cJSON_GetObjectItem(obj, "id");
        auto c_type = cJSON_GetObjectItem(obj, "type");
        auto c_time_str = cJSON_GetObjectItem(obj, "time_str");
        auto c_note = cJSON_GetObjectItem(obj, "note");
        auto c_target_time = cJSON_GetObjectItem(obj, "target_time");
        auto c_status = cJSON_GetObjectItem(obj, "status");

        if (c_id && cJSON_IsString(c_id)) item.id = c_id->valuestring;
        if (c_type && cJSON_IsString(c_type)) item.type = c_type->valuestring;
        if (c_time_str && cJSON_IsString(c_time_str)) item.time_str = c_time_str->valuestring;
        if (c_note && cJSON_IsString(c_note)) item.note = c_note->valuestring;
        if (c_target_time && cJSON_IsNumber(c_target_time)) item.target_time = static_cast<time_t>(c_target_time->valuedouble);
        if (c_status && cJSON_IsString(c_status)) item.status = c_status->valuestring;

        item.target_uptime_us = 0;

        // 保留活跃的或最近的历史记录
        if (item.status == "running" && now > 1700000000 && item.target_time > 0 && item.target_time < now) {
            item.status = "completed";
        }

        alarms_.push_back(item);
    }

    cJSON_Delete(root);
}

void LocalAlarmClock::SaveToNvs() {
    cJSON* root = cJSON_CreateArray();

    for (const auto& a : alarms_) {
        cJSON* obj = cJSON_CreateObject();
        cJSON_AddStringToObject(obj, "id", a.id.c_str());
        cJSON_AddStringToObject(obj, "type", a.type.c_str());
        cJSON_AddStringToObject(obj, "time_str", a.time_str.c_str());
        cJSON_AddStringToObject(obj, "note", a.note.c_str());
        cJSON_AddNumberToObject(obj, "target_time", static_cast<double>(a.target_time));
        cJSON_AddStringToObject(obj, "status", a.status.c_str());
        cJSON_AddItemToArray(root, obj);
    }

    char* json_str = cJSON_PrintUnformatted(root);
    if (json_str != nullptr) {
        Settings settings("local_alarm", true);
        settings.SetString("alarms", json_str);
        cJSON_free(json_str);
    }

    cJSON_Delete(root);
}