from plugins_func.register import register_function, ToolType, ActionResponse, Action
from config.logger import setup_logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

handle_exit_intent_function_desc = {
    "type": "function",
    "function": {
        "name": "handle_exit_intent",
        "description": (
            "【结束对话与休眠退出】当用户表达对话结束、道别、或回复收尾词如'好的'、'行了'、'没事了'、'知道了'、'好啦'、'再见'、'退下吧'、'不用了'、'就这样吧'时，【必须优先调用此工具】。"
            "该工具会生成简短亲切的道别语，并立即关闭会话让小智进入休眠待命状态（熄灭拾音灯，等待下次唤醒词）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "say_goodbye": {
                    "type": "string",
                    "description": "和用户友好道别的简短口语回复（例如：'好的奶奶，那您先忙，有事随时叫我~'、'好嘞，拜拜~'）",
                }
            },
            "required": ["say_goodbye"],
        },
    },
}

@register_function(
    "handle_exit_intent", handle_exit_intent_function_desc, ToolType.SYSTEM_CTL
)
def handle_exit_intent(conn: "ConnectionHandler", say_goodbye: str | None = None):
    try:
        if say_goodbye is None:
            say_goodbye = "好的，那您先忙，随时喊我哦~"
        conn.close_after_chat = True
        logger.bind(tag=TAG).info(f"退出意图已处理，会话即将休眠退出: {say_goodbye}")
        return ActionResponse(
            action=Action.RESPONSE, result="退出意图已处理", response=say_goodbye
        )
    except Exception as e:
        logger.bind(tag=TAG).error(f"处理退出意图错误: {e}")
        return ActionResponse(
            action=Action.NONE, result="退出意图处理失败", response=""
        )