import httpx
from config.logger import setup_logging
from plugins_func.functions.hass_init import initialize_hass_handler
from plugins_func.register import register_function, ToolType, ActionResponse, Action
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

hass_play_music_function_desc = {
    "type": "function",
    "function": {
        "name": "hass_play_music",
        "description": "【仅限HomeAssistant集成】仅当用户明确要求在外部HomeAssistant智能家居音箱(media_player)中播放时调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "media_content_id": {
                    "type": "string",
                    "description": "可以是音乐或有声书的专辑名称、歌曲名、演唱者,如果未指定就填random",
                },
                "entity_id": {
                    "type": "string",
                    "description": "需要操作的音箱的设备id,homeassistant里的entity_id,media_player开头",
                },
            },
            "required": ["media_content_id"],
        },
    },
}

@register_function(
    "hass_play_music", hass_play_music_function_desc, ToolType.SYSTEM_CTL
)
async def hass_play_music(conn: "ConnectionHandler", entity_id="", media_content_id="random"):
    """执行HA音乐播放，若未接入HA则自动降级使用原生本地音乐引擎"""
    try:
        ha_config = initialize_hass_handler(conn)
        base_url = ha_config.get("base_url", "")
        if not base_url or not base_url.startswith("http"):
            logger.bind(tag=TAG).info("未检测到有效 HomeAssistant 配置，自动降级至小智原生音乐播放器")
            from plugins_func.functions.play_music import play_music
            return await play_music(conn, media_content_id)
            
        result = await handle_hass_play_music(conn, entity_id, media_content_id)
        return ActionResponse(
            action=Action.RECORD, result="指令已接收", response=result
        )
    except Exception as e:
        logger.bind(tag=TAG).warning(f"HomeAssistant 播放失败: {e}，自动降级至小智原生音乐播放")
        from plugins_func.functions.play_music import play_music
        return await play_music(conn, media_content_id)

async def handle_hass_play_music(
    conn: "ConnectionHandler", entity_id, media_content_id
):
    ha_config = initialize_hass_handler(conn)
    api_key = ha_config.get("api_key")
    base_url = ha_config.get("base_url")
    url = f"{base_url}/api/services/music_assistant/play_media"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    data = {"entity_id": entity_id, "media_id": media_content_id}

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0)) as client:
        response = await client.post(url, headers=headers, json=data)

    if response.status_code == 200:
        return f"正在播放{media_content_id}的音乐"
    else:
        return f"音乐播放失败，错误码: {response.status_code}"
