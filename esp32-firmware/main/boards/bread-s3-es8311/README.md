# Bread S3 ES8311 (面包板 ESP32-S3 + 鹿川班 ES8311 音频板)

针对标准 ESP32-S3 开发板搭配鹿川班/立创开源 ES8311 独立音频子板（集成 ES8311 Codec + NS4150B 功放）的板级定义。

## 硬件规格
- **主控芯片**: ESP32-S3-WROOM-1 / ESP32-S3-N16R8 (16MB Flash, 8MB Octal PSRAM)
- **音频架构**: ES8311 Codec (I2C + I2S 双工通讯) + NS4150B 3W D类单声道功放
- **麦克风**: 板载模拟/数字驻极体/硅麦输入
- **扬声器**: 4Ω 3W 喇叭 (XH2.54 / MX1.25 接插件)
- **显示屏**: 无屏幕 (`NoDisplay`，低功耗轻量版)
- **物理按键**: GPIO 0 (BOOT 按键，短按交互/打断，启动长按进入配网)
- **LED 指示**: GPIO 48 (板载 RGB/单色状态指示灯)

## 引脚接线对照表 (Pinout)

| ES8311 音频板引脚 | ESP32-S3 GPIO | 功能说明 |
| :--- | :--- | :--- |
| **5V / VCC** | **3V3 或 5V** | 模块电源供电（建议连接稳定的 3.3V 或 5V） |
| **GND** | **GND** | 共地引脚 |
| **SDA** | **GPIO 1** | I2C 数据线（软件已启用内部弱上拉，芯片地址 `0x18`） |
| **SCL** | **GPIO 2** | I2C 时钟线 |
| **MCLK** | **GPIO 38** | I2S 主时钟 (Master Clock) |
| **BCLK** | **GPIO 14** | I2S 位时钟 (Bit Clock) |
| **LRCK / WS** | **GPIO 13** | I2S 声道帧同步时钟 (Word Select) |
| **DIN** | **GPIO 12** | 音频放音数据输入（连接 ESP32-S3 DOUT: GPIO 12） |
| **DOUT** | **GPIO 11** | 麦克风录音数据输出（连接 ESP32-S3 DIN: GPIO 11） |

## 编译与烧录命令

```bash
# 激活 ESP-IDF v6.0.2 环境后执行
python scripts/build.py bread-s3-es8311 --name bread-s3-es8311
```

