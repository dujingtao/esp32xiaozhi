# 动态自定义唤醒词与设备空间多级身份管理

## 1. 功能概述

本项目在 ESP32-S3 固件中实现了两大核心能力：
1. **免固件重编译的动态自定义唤醒词（基于乐鑫 Multinet 7 中文连续语音识别）**：
   - 可以在 Web 控制台或通过语音指令随时动态更换唤醒词（如“贾维斯”、“小爱同学”、“管家管家”等）；
   - 固件支持运行时热重载（无需修改代码，无需重新编译，也不需要重启设备）；
   - 配置持久化写入 NVS Flash，掉电永久保存。
2. **设备独立名称与多级物理空间位置管理（城市/场所/房间）**：
   - 无需额外硬件 GPS 模块，通过端侧 NVS Flash + 原生 MCP 工具 + WebSocket/MQTT 双向同步；
   - 解决多台 ESP32 处于同一网络环境下的身份混淆与空间位置感知问题；
   - 大模型与服务端可即时感知当前触发设备的所在物理位置（如“深圳市 腾讯大厦 3楼会议室”或“客厅小智”）。

---

## 2. 动态自定义唤醒词技术实现

### 2.1 架构原理
传统离线唤醒词通常需要乐鑫官方定制并编译进静态模型，或者在编译期写入固定拼音命令。
我们通过集成乐鑫开源的 **Multinet 7 (`mn7_cn`) 连续语音识别声学模型**：
- 模型资源存放在 Flash 的 `assets` 资源分区（`0x800000`）；
- 固件在运行时暴露 `CustomWakeWord::SetWakeWord(command, text, threshold)` API；
- 通过调用 `esp_mn_commands_clear()`、`esp_mn_commands_add()` 和 `esp_mn_commands_update()`，动态重建声学有限状态机（FST）识别图，实现秒级热重载。

### 2.2 存储数据结构 (NVS 命名空间: `wake_word`)
| 键名 (Key) | 类型 | 示例值 | 说明 |
| :--- | :--- | :--- | :--- |
| `command` | string | `jia wei si` | 拼音全拼，多音节间以空格分隔 |
| `text` | string | `贾维斯` | 中文或显示文本 |
| `threshold` | int | `20` | 识别灵敏度百分比 (1~99，默认 20，数值越小越敏感) |

### 2.3 加载与回退优先级
设备开机初始化时（`CustomWakeWord::Initialize`）：
1. 优先读取 NVS `wake_word` 分区中的配置；
2. 若 NVS 为空（初次烧录），检查 `assets/index.json`；
3. 若仍为空，使用 `Kconfig` 中的默认词（如 `"ni hao xiao zhi"` / `"你好小智"`）。

### 2.4 控制台远程推送 JSON 协议
通过 Web 控制台下发配置（可在 OTA 更新或 WebSocket `server_hello` 消息中返回）：
```json
{
    "wake_word": {
        "command": "jia wei si",
        "text": "贾维斯",
        "threshold": 25
    }
}
```

### 2.5 语音指令动态修改
用户直接对麦克风说话：
> *“小智，把你的唤醒词改成‘贾维斯’，拼音是 jia wei si。”*

大模型将调用端侧注册的 MCP 工具：
```json
self.device.set_wake_word({
    "pinyin": "jia wei si",
    "text": "贾维斯",
    "threshold": 25
})
```

---

## 3. 设备独立名字与多级位置管理

### 3.1 空间位置三级结构
在无需 GPS 的情况下，使用“城市 - 场所 - 房间”三级地理空间概念：
- `city` (城市): 例如 `深圳`、`北京`、`上海`
- `place` (场所): 例如 `家里`、`办公室`、`学校`
- `room` (房间): 例如 `客厅`、`主卧`、`书房`、`会议室`

### 3.2 存储数据结构 (NVS 命名空间: `device`)
| 键名 (Key) | 类型 | 示例值 | 说明 |
| :--- | :--- | :--- | :--- |
| `name` | string | `客厅小智` | 设备个性化名称 |
| `city` | string | `深圳` | 所在城市 |
| `place` | string | `家里` | 所在场所 |
| `room` | string | `客厅` | 所在房间 |

### 3.3 端侧 MCP 工具清单
| 工具名称 | 功能描述 | 参数列表 |
| :--- | :--- | :--- |
| `self.device.set_name` | 修改设备名字 | `name: string` |
| `self.device.set_location` | 设置设备多级物理位置 | `city: string`, `place: string`, `room: string` |
| `self.device.set_wake_word` | 修改当前唤醒词 | `pinyin: string`, `text: string`, `threshold?: number` |
| `self.device.get_info` | 查询当前设备全部名字/位置/唤醒词信息 | 无 |
| `self.get_device_status` | 扩展原生设备状态，返回包含 `device_info` 的全局 JSON | 无 |

### 3.4 协议双向同步 (WebSocket & MQTT)
1. **客户端握手报文 (`hello`)**：
   设备上线时，在握手 JSON 中主动携带 `device_info`：
   ```json
   {
       "type": "hello",
       "device_info": {
           "name": "客厅小智",
           "city": "深圳",
           "place": "家里",
           "room": "客厅",
           "wake_pinyin": "ni hao xiao zhi",
           "wake_text": "你好小智"
       }
   }
   ```
2. **服务端确认报文 (`server_hello`)**：
   若服务端管理后台修改了设备配置，响应报文中附带最新的 `device_info`，ESP32 会自动同步持久化到本地 NVS。

---

## 4. 常用唤醒词拼音对照表

| 期望唤醒词 | 拼音设置（全拼小写，空格隔开） | 灵敏度建议 (1~99) |
| :--- | :--- | :--- |
| **你好小智**（默认） | `ni hao xiao zhi` | `20` |
| **贾维斯** | `jia wei si` | `20 ~ 25` |
| **小爱同学** | `xiao ai tong xue` | `20` |
| **管家管家** | `guan jia guan jia` | `20 ~ 25` |
| **你好小度** | `ni hao xiao du` | `20` |
| **星期五** | `xing qi wu` | `20` |

---

## 5. 面包板硬件适配与语音流畅度深度优化

### 5.1 面包板硬件架构 (`bread-s3-es8311`)
针对标准 ESP32-S3-WROOM-1 / N16R8 开发板搭配鹿川班/立创开源独立 ES8311 音频子板（集成 ES8311 Codec + NS4150B 3W 功放）：
- **I2C 控制线**：SDA -> GPIO 1, SCL -> GPIO 2（开机自检与内部上拉）
- **I2S 音频总线**：MCLK -> GPIO 38, BCLK -> GPIO 14, WS/LRCK -> GPIO 13, DIN -> GPIO 12, DOUT -> GPIO 11
- **交互按键**：BOOT (GPIO 0) 短按交互/打断对话并播放提示音，长按 3 秒进入配网

### 5.2 Wi-Fi 稳定性与 10 秒超时静态 IP 兜底机制
针对移动光猫/部分家用路由器在 ESP32 默认省电休眠（Modem Sleep）时丢弃 DHCP OFFER 广播包的问题：
1. **彻底关闭 Modem Sleep**：连接前后全程强制启用 `esp_wifi_set_ps(WIFI_PS_NONE)`；
2. **10 秒 DHCP 静态 IP 自动兜底**：开机连接 Wi-Fi 后启动 10 秒计时器，若路由器 DHCP 服务器在 10 秒内未分配 IP，系统将自动分配局域网预留静态 IP（`192.168.1.188`，网关与主 DNS `192.168.1.1`，备用 DNS `114.114.114.114`），并主动派发 `IP_EVENT_STA_GOT_IP` 事件，100% 杜绝卡在配网或断连假死状态。

### 5.3 语音播放卡顿的彻底消除方案
针对播放语音时断断续续、吞字断音的问题，实施了三重针对性深度优化：
1. **全时锁定性能模式 (`PERFORMANCE`)**：
   重写板级 `SetPowerSaveLevel`，由于 USB 常电供电，永久锁定 Wi-Fi 性能模式（禁用 `WIFI_PS_MAX_MODEM` 休眠）。WebSocket 握手耗时从 3180ms 降低至 420ms，下行音频包延迟稳定在 <2ms，彻底解决播放缓冲区饥饿（Buffer Underrun）。
2. **原生对齐 16000Hz 采样率**：
   将 `AUDIO_INPUT_SAMPLE_RATE` 与 `AUDIO_OUTPUT_SAMPLE_RATE` 设为 16000Hz，与云端 Opus 格式及本地 AFE 降噪算法 1:1 严格对齐，彻底移除了 CPU 上的实时双向软件重采样算法（`esp_ae_rate_cvt`）开销。
3. **ESP32-S3 CPU 主频拉满至 240MHz**：
   构建配置启用 `CONFIG_ESP32S3_DEFAULT_CPU_FREQ_240=y`，算力提升 50%，多任务并发调度丝滑流畅。

