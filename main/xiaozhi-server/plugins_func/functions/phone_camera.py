import os
import urllib.request
import base64
import requests
import json
from plugins_func.register import register_function, ToolType, ActionResponse, Action
from config.logger import setup_logging

TAG = __name__
logger = setup_logging()

# S20 手机摄像头 Tailscale 专属直连地址
S20_HOST = "http://100.122.149.94:8080"

# 免费多模态双引擎密钥
GEMINI_API_KEY = "AIzaSyA1z-1pIt1lNM-NRjOmxZtXZ5yN5sR01-w"
ZHIPU_API_KEY = "fd04fb160360497291b1ae87596dbde9.ID3C9TfZTgTd3W9h"

def capture_s20_image(try_autofocus=False):
    """从三星 S20 获取实时高清画面（默认极速 shot.jpg 单帧，耗时仅 100ms）"""
    endpoints = ["/shot.jpg", "/photo.jpg", "/photoaf.jpg"] if not try_autofocus else ["/photoaf.jpg", "/shot.jpg"]
    for ep in endpoints:
        try:
            url = f"{S20_HOST}{ep}"
            req = urllib.request.Request(url, headers={"User-Agent": "XiaoZhi-Vision/1.0"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = resp.read()
                if len(data) > 1000:
                    return data
        except Exception as e:
            logger.bind(tag=TAG).warning(f"从 {ep} 抓取 S20 画面失败: {e}")
    return None

def analyze_vision_dual_engine(b64_img, prompt_text):
    """优先使用 Google Gemini 2.5 Flash（超聪明），故障自动降级至 智谱 GLM-4V-Flash（0.6秒极速）"""
    # 1. 尝试 Google Gemini 2.5 Flash
    try:
        gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt_text},
                        {
                            "inline_data": {
                                "mime_type": "image/jpeg",
                                "data": b64_img
                            }
                        }
                    ]
                }
            ],
            "generationConfig": {
                "maxOutputTokens": 350,
                "temperature": 0.4
            }
        }
        r = requests.post(gemini_url, json=payload, timeout=8)
        if r.status_code == 200:
            res = r.json()
            reply = res["candidates"][0]["content"]["parts"][0]["text"].strip()
            logger.bind(tag=TAG).info(f"Gemini 2.5 Flash 视觉解析成功: {reply[:60]}...")
            return reply
        else:
            logger.bind(tag=TAG).warning(f"Gemini 状态码异常 {r.status_code}: {r.text[:100]}，切入智谱备用")
    except Exception as e:
        logger.bind(tag=TAG).warning(f"Gemini 调用异常 {e}，切入智谱备用")

    # 2. 备用引擎：智谱 GLM-4V-Flash
    try:
        zhipu_url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
        headers = {"Authorization": f"Bearer {ZHIPU_API_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": "glm-4v-flash",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}}
                    ]
                }
            ],
            "max_tokens": 350,
            "temperature": 0.5
        }
        r = requests.post(zhipu_url, headers=headers, json=payload, timeout=8)
        if r.status_code == 200:
            reply = r.json()["choices"][0]["message"]["content"].strip()
            logger.bind(tag=TAG).info(f"智谱 GLM-4V-Flash 视觉解析成功: {reply[:60]}...")
            return reply
    except Exception as e:
        logger.bind(tag=TAG).error(f"智谱备用引擎异常: {e}")

    return None

see_camera_desc = {
    "type": "function",
    "function": {
        "name": "see_through_phone_camera",
        "description": (
            "【视觉能力/看东西】当用户要求小智看非人物体的物品、环境、书本作业题目，或询问'看看我手里拿的是什么'、'看看前面'、'帮我看下这道题'时，必须调用此工具。"
            "该工具会立即调用三星 S20 高清摄像头抓拍画面并智能分析解答。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "用户对于画面的具体问题或要求（如：'看看我手里拿的是什么'、'帮我解答这道题'、'看看面前的环境'）"
                }
            },
            "required": ["question"]
        }
    }
}

torch_control_desc = {
    "type": "function",
    "function": {
        "name": "control_phone_camera_torch",
        "description": (
            "控制三星 S20 手机摄像头的闪光灯/手电筒补光。当用户要求'开灯看看'、'打开闪光灯/手电筒'、'关灯'、'关闭手电筒'时调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "enable": {
                    "type": "boolean",
                    "description": "True 为打开闪光灯补光，False 为关闭"
                }
            },
            "required": ["enable"]
        }
    }
}

@register_function("see_through_phone_camera", see_camera_desc, ToolType.WAIT)
def see_through_phone_camera(*args, **kwargs):
    question = kwargs.get("question", "请描述你从画面中看到了什么？")
    if not kwargs and args:
        if isinstance(args[0], str):
            question = args[0]
        elif len(args) > 1 and isinstance(args[1], str):
            question = args[1]
            
    logger.bind(tag=TAG).info(f"触发 S20 视觉识别（Gemini 2.5 + GLM-4V 双引擎），用户问题: {question}")
    try:
        img_bytes = capture_s20_image(try_autofocus=False)
        if not img_bytes:
            return ActionResponse(
                action=Action.RESPONSE,
                response="没有连上手机摄像头，请确认手机上的 IP Webcam 应用和 Tailscale 是否正常开启哦。"
            )
        
        b64_img = base64.b64encode(img_bytes).decode("utf-8")
        
        prompt = (
            f"你是小智随身语音助手。你的视线正通过三星S20高清镜头实时观察。\n"
            f"用户提问：{question}\n"
            f"请根据用户的提问，仔细观察画面内容，用亲切、生动、口语化、温暖且简练的中文直接回答用户。"
            f"如果是认东西，直接告诉用户这是什么、有什么特征，像朋友一样自然交流；"
            f"如果是做题辅导，清晰地分步讲解核心思路和答案。禁止出现机器腔或死板的开头套话。"
        )
        
        reply = analyze_vision_dual_engine(b64_img, prompt)
        if reply:
            return ActionResponse(action=Action.RESPONSE, response=reply, result=reply)
        else:
            return ActionResponse(action=Action.RESPONSE, response="画面识别传输有点卡顿，请稍等一下再试一次。")
    except Exception as e:
        logger.bind(tag=TAG).error(f"执行视觉识别异常: {e}")
        return ActionResponse(action=Action.RESPONSE, response="视觉分析出了点小问题，请稍后再试。")

@register_function("control_phone_camera_torch", torch_control_desc, ToolType.WAIT)
def control_phone_camera_torch(*args, **kwargs):
    enable = kwargs.get("enable", True)
    if not kwargs and args:
        if isinstance(args[0], bool):
            enable = args[0]
        elif len(args) > 1 and isinstance(args[1], bool):
            enable = args[1]
            
    try:
        ep = "/enabletorch" if enable else "/disabletorch"
        url = f"{S20_HOST}{ep}"
        req = urllib.request.Request(url, headers={"User-Agent": "XiaoZhi-Vision/1.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            status_text = "打开" if enable else "关闭"
            logger.bind(tag=TAG).info(f"S20 闪光灯已{status_text}")
            return ActionResponse(
                action=Action.RESPONSE,
                response=f"好的，手机手电筒已经{status_text}啦。"
            )
    except Exception as e:
        logger.bind(tag=TAG).error(f"控制 S20 闪光灯失败: {e}")
        return ActionResponse(
            action=Action.RESPONSE,
            response=f"控制手机手电筒失败了，请确认手机连接正常。"
        )