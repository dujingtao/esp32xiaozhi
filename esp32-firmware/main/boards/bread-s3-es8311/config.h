#ifndef _BOARD_CONFIG_H_
#define _BOARD_CONFIG_H_

#include <driver/gpio.h>

// 音频采样率 (标准 16000Hz 单声道，与服务端原生 Opus 对齐，免二次软件重采样开销)
#define AUDIO_INPUT_SAMPLE_RATE  16000
#define AUDIO_OUTPUT_SAMPLE_RATE 16000

// I2S 数字音频接口引脚 (ES8311 + NS4150B)
#define AUDIO_I2S_GPIO_MCLK GPIO_NUM_38
#define AUDIO_I2S_GPIO_BCLK GPIO_NUM_14
#define AUDIO_I2S_GPIO_WS   GPIO_NUM_13
#define AUDIO_I2S_GPIO_DOUT GPIO_NUM_12  // ESP32 数据输出 -> 模块 DIN (放音)
#define AUDIO_I2S_GPIO_DIN  GPIO_NUM_11  // ESP32 数据输入 <- 模块 DOUT (录音)

// I2C 寄存器控制引脚 (配置 ES8311 声卡芯片)
#define AUDIO_CODEC_I2C_SDA_PIN  GPIO_NUM_1
#define AUDIO_CODEC_I2C_SCL_PIN  GPIO_NUM_2
#define AUDIO_CODEC_ES8311_ADDR  ES8311_CODEC_DEFAULT_ADDR
#define AUDIO_CODEC_PA_PIN       GPIO_NUM_NC  // 鹿川班板载 NS4150B 功放默认常开

// 按键与板载外设
#define BUILTIN_LED_GPIO        GPIO_NUM_48  // 板载指示灯
#define BOOT_BUTTON_GPIO        GPIO_NUM_0   // BOOT 按钮 (短按打断/对话，启动长按配网)

#endif // _BOARD_CONFIG_H_
