#ifndef LOCAL_ALARM_H
#define LOCAL_ALARM_H

#include <string>
#include <vector>
#include <mutex>
#include <ctime>
#include <esp_timer.h>
#include "mcp_server.h"

struct LocalAlarmItem {
    std::string id;
    std::string type;          // "alarm" or "timer"
    std::string time_str;      // "HH:MM"
    std::string note;          // e.g. "起床", "吃药", "关火"
    time_t target_time;        // Unix timestamp
    int64_t target_uptime_us;  // Uptime in microseconds (fallback when SNTP time not ready)
    std::string status;        // "running", "completed", "cancelled"
};

class LocalAlarmClock {
public:
    static LocalAlarmClock& GetInstance();

    void Initialize();
    void RegisterMcpTools();

    std::string SetAlarm(const std::string& time_str, const std::string& note = "闹钟");
    std::string SetCountdown(int duration_seconds, const std::string& note = "倒计时");
    std::string ListAlarms();
    std::string CancelAlarm(const std::string& keyword);

    void CheckAlarms();

private:
    LocalAlarmClock();
    ~LocalAlarmClock();

    void LoadFromNvs();
    void SaveToNvs();
    void TriggerAlarm(const LocalAlarmItem& item);

    std::mutex mutex_;
    std::vector<LocalAlarmItem> alarms_;
    esp_timer_handle_t timer_handle_ = nullptr;
    bool initialized_ = false;
};

#endif // LOCAL_ALARM_H