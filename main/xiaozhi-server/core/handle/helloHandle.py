import time
import json
import uuid
import random
import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler
from core.utils.dialogue import Message
from core.utils.util import audio_to_data
from core.providers.tts.dto.dto import SentenceType
from core.utils.wakeup_word import WakeupWordsConfig
from core.handle.sendAudioHandle import sendAudioMessage, send_tts_message
from core.utils.util import remove_punctuation_and_length, opus_datas_to_wav_bytes
from core.providers.tools.device_mcp import MCPClient, send_mcp_initialize_message
from core.services.face_sentinel import FaceSentinel

TAG = __name__

WAKEUP_CONFIG = {
    "refresh_time": 10,
    "responses": [
        "我一直都在呢，您请说。",
        "在的呢，请随时吩咐我。",
        "来啦来啦，请告诉我吧。",
        "您请说，我正听着。",
        "请您讲话，我准备好了。",
        "请您说出指令吧。",
        "我认真听着呢，请讲。",
        "请问您需要什么帮助？",
        "我在这里，等候您的指令。",
    ],
}

# 创建全局的唤醒词配置管理器
wakeup_words_config = WakeupWordsConfig()

# 用于防止并发调用wakeupWordsResponse的锁
_wakeup_response_lock = asyncio.Lock()


async def handleHelloMessage(conn: "ConnectionHandler", msg_json):
    """处理hello消息"""
    audio_params = msg_json.get("audio_params")
    if audio_params:
        format = audio_params.get("format")
        conn.logger.bind(tag=TAG).debug(f"客户端音频格式: {format}")
        conn.audio_format = format
        conn.welcome_msg["audio_params"] = audio_params
    features = msg_json.get("features")
    if features:
        conn.logger.bind(tag=TAG).debug(f"客户端特性: {features}")
        conn.features = features
        if features.get("mcp"):
            conn.logger.bind(tag=TAG).debug("客户端支持MCP")
            conn.mcp_client = MCPClient()
            # 发送初始化
            asyncio.create_task(send_mcp_initialize_message(conn))
        if features.get("aec"):
            conn.logger.bind(tag=TAG).debug("客户端启用了服务端AEC")
            conn.client_aec = True

    await conn.websocket.send(json.dumps(conn.welcome_msg))

    # 检查是否有待播报的哨兵迎宾词
    pending_greet = FaceSentinel.get_pending_greeting()
    if pending_greet:
        conn.logger.bind(tag=TAG).info(f"检测到待播报迎宾词，触发播报: {pending_greet}")
        if hasattr(conn, 'chat'):
            conn.chat(f"[系统迎宾] 请用温暖亲切的声音播报：'{pending_greet}'")


async def checkWakeupWords(conn: "ConnectionHandler", text):
    enable_wakeup_words_response_cache = conn.config[
        "enable_wakeup_words_response_cache"
    ]

    # 等待tts初始化，最多等待3秒
    start_time = time.time()
    while time.time() - start_time < 3:
        if conn.tts is not None:
            break
        await asyncio.sleep(0.1)

    if conn.tts is None:
        conn.logger.bind(tag=TAG).error("TTS未初始化成功，跳过唤醒词响应")
        return False

    # 检查是否有待播报的哨兵迎宾词
    pending_greet = FaceSentinel.get_pending_greeting()
    if pending_greet:
        conn.logger.bind(tag=TAG).info(f"检测到待播报迎宾词，优先播报: {pending_greet}")
        if hasattr(conn, 'chat'):
            conn.chat(f"[系统迎宾] 请用温暖亲切的声音播报：'{pending_greet}'")
            return True

    # 检查唤醒词匹配
    for word in conn.config.get("wakeup_words", []):
        if word in text:
            # 获取配置的响应列表
            responses = WAKEUP_CONFIG.get("responses", [])
            if not responses:
                return False

            # 随机选择一个响应
            response = random.choice(responses)

            if enable_wakeup_words_response_cache:
                async with _wakeup_response_lock:
                    audios = wakeup_words_config.get_response(response)
                    if audios is None:
                        audios = await conn.tts.text_to_speak(response)
                        if audios:
                            wakeup_words_config.save_response(response, audios)

                if audios:
                    conn.sentence_id = str(uuid.uuid4().hex)
                    await sendAudioMessage(
                        conn, SentenceType.FIRST, audios, response, conn.sentence_id
                    )
                    return True
            else:
                audios = await conn.tts.text_to_speak(response)
                if audios:
                    conn.sentence_id = str(uuid.uuid4().hex)
                    await sendAudioMessage(
                        conn, SentenceType.FIRST, audios, response, conn.sentence_id
                    )
                    return True
    return False
