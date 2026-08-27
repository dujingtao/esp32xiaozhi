import os
import json
import base64
import urllib.request
import requests
from typing import Dict, Any, List
from plugins_func.register import register_function, ToolType, ActionResponse, Action
from config.logger import setup_logging

TAG = __name__
logger = setup_logging()

# S20 手机摄像头 Tailscale 专属直连地址
S20_HOST = "http://100.122.149.94:8080"
GEMINI_API_KEY = "AIzaSyA1z-1pIt1lNM-NRjOmxZtXZ5yN5sR01-w"
ZHIPU_API_KEY = "fd04fb160360497291b1ae87596dbde9.ID3C9TfZTgTd3W9h"

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
FACES_DB_FILE = os.path.join(DATA_DIR, "family_faces.json")
FACES_IMG_DIR = os.path.join(DATA_DIR, "faces")

os.makedirs(FACES_IMG_DIR, exist_ok=True)

def load_faces_db() -> Dict[str, Any]:
    """读取已注册的家庭成员人脸数据库"""
    if os.path.exists(FACES_DB_FILE):
        try:
            with open(FACES_DB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.bind(tag=TAG).error(f"读取人脸数据库失败: {e}")
    return {}

def save_faces_db(db: Dict[str, Any]):
    """保存家庭成员人脸数据库"""
    try:
        with open(FACES_DB_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.bind(tag=TAG).error(f"保存人脸数据库失败: {e}")

def capture_s20_frame() -> bytes:
    """从三星 S20 抓拍高清单帧画面"""
    endpoints = ["/shot.jpg", "/photo.jpg", "/photoaf.jpg"]
    for ep in endpoints:
        try:
            url = f"{S20_HOST}{ep}"
            req = urllib.request.Request(url, headers={"User-Agent": "XiaoZhi-Face/1.0"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = resp.read()
                if len(data) > 1000:
                    return data
        except Exception as e:
            logger.bind(tag=TAG).warning(f"从 {ep} 抓取 S20 画面失败: {e}")
    return None

def analyze_vlm_dual(b64_img, prompt_text, max_tokens=300):
    """双引擎执行视觉分析：首选 Gemini 2.5 Flash，自动降级至 智谱 GLM-4V-Flash"""
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
                "maxOutputTokens": max_tokens,
                "temperature": 0.4
            }
        }
        r = requests.post(gemini_url, json=payload, timeout=8)
        if r.status_code == 200:
            res = r.json()
            reply = res["candidates"][0]["content"]["parts"][0]["text"].strip()
            logger.bind(tag=TAG).info(f"Gemini 人脸/视觉分析成功: {reply[:60]}...")
            return reply
    except Exception as e:
        logger.bind(tag=TAG).warning(f"Gemini 异常 {e}，使用智谱备用")

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
            "max_tokens": max_tokens,
            "temperature": 0.4
        }
        r = requests.post(zhipu_url, headers=headers, json=payload, timeout=8)
        if r.status_code == 200:
            reply = r.json()["choices"][0]["message"]["content"].strip()
            logger.bind(tag=TAG).info(f"智谱 人脸/视觉分析成功: {reply[:60]}...")
            return reply
    except Exception as e:
        logger.bind(tag=TAG).error(f"智谱备用引擎异常: {e}")

    return None

# --- 工具定义 ---

register_face_desc = {
    "type": "function",
    "function": {
        "name": "register_family_face",
        "description": (
            "【人脸录入/记住家庭成员】当用户要求小智记住人脸、录入人脸或说'小智，记住这张脸，这是xxx'、'帮我录入人脸，我是xxx'时，必须调用此工具。"
            "该工具会立即调用三星 S20 高清摄像头抓拍眼前的人物正脸，提取外貌特征并保存到家庭成员人脸库中。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "要记住的人物称呼或姓名，例如：'布布爸爸'、'布布奶奶'、'布布'、'妈妈'"
                },
                "role_note": {
                    "type": "string",
                    "description": "可选的身份或角色备注，例如：'家庭成员'、'父亲'、'奶奶'等"
                }
            },
            "required": ["name"]
        }
    }
}

recognize_face_desc = {
    "type": "function",
    "function": {
        "name": "recognize_family_face",
        "description": (
            "【人脸识别/认人】当用户询问'看看我是谁'、'看看前面是谁'、'认认这个人是谁'、'看看谁在镜头前'时，必须调用此工具。"
            "该工具会调用三星 S20 摄像头抓拍画面，比对家庭成员人脸库，精准识别人物身份并直接亲切回复问候。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "用户的问询具体内容，如'看看我是谁'、'看看站在前面的是谁'"
                }
            },
            "required": ["question"]
        }
    }
}

list_faces_desc = {
    "type": "function",
    "function": {
        "name": "list_family_faces",
        "description": "查询小智当前已认识/已录入的所有家庭成员人脸名单。当用户询问'你现在认识家里哪些人'、'人脸库里有谁'时调用。",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    }
}

delete_face_desc = {
    "type": "function",
    "function": {
        "name": "delete_family_face",
        "description": "从人脸库中删除某个人物的人脸信息。当用户要求'删除xxx的人脸'、'忘记xxx'时调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "要删除的人物称呼或名称，如'布布爸爸'，或'all'/'全部'清空人脸库"
                }
            },
            "required": ["name"]
        }
    }
}

@register_function("register_family_face", register_face_desc, ToolType.WAIT)
def register_family_face(*args, **kwargs):
    name = kwargs.get("name", "")
    role_note = kwargs.get("role_note", "家庭成员")
    if not name and args:
        if isinstance(args[0], str):
            name = args[0]
            
    if not name:
        return ActionResponse(action=Action.RESPONSE, response="请告诉我您想要记住的名字或称呼，比如'记住这是布布爸爸'哦。")
        
    logger.bind(tag=TAG).info(f"开始录入人脸: {name}")
    try:
        img_bytes = capture_s20_frame()
        if not img_bytes:
            return ActionResponse(action=Action.RESPONSE, response="没有连上手机摄像头，请确认手机上的 IP Webcam 和 Tailscale 正常开启哦。")
            
        b64_img = base64.b64encode(img_bytes).decode("utf-8")
        
        # 保存图片副本
        img_filename = f"{name}.jpg"
        img_path = os.path.join(FACES_IMG_DIR, img_filename)
        with open(img_path, "wb") as f:
            f.write(img_bytes)
            
        prompt = (
            f"请仔细观察画面中正对镜头的人物面部与外貌特征，用于人脸识别建档。\n"
            f"请简明扼要地总结该人物的显著外貌特征（包括性别、年龄段、发型/发色、是否戴眼镜、脸型特征、穿着等），50字以内。\n"
            f"如果画面中没有清晰的人脸，请明确回复'NO_FACE'。"
        )
        
        feature_desc = analyze_vlm_dual(b64_img, prompt, max_tokens=150)
        if feature_desc:
            if "NO_FACE" in feature_desc:
                return ActionResponse(action=Action.RESPONSE, response=f"没有在镜头前看清正脸哦，请正对手机镜头并保持光线明亮，再对我说一次'记住这是{name}'吧！")
                
            db = load_faces_db()
            db[name] = {
                "name": name,
                "role_note": role_note,
                "features": feature_desc,
                "img_file": img_filename
            }
            save_faces_db(db)
            logger.bind(tag=TAG).info(f"人脸建档成功: {name}, 特征: {feature_desc}")
            
            return ActionResponse(
                action=Action.RESPONSE,
                response=f"太棒啦！我已经成功记住【{name}】的人脸特征啦！以后只要正对镜头问我‘看看我是谁’，我一眼就能认出你哦！"
            )
        else:
            return ActionResponse(action=Action.RESPONSE, response="人脸特征提取网络出现波动，请稍后再试一次哦。")
    except Exception as e:
        logger.bind(tag=TAG).error(f"录入人脸异常: {e}")
        return ActionResponse(action=Action.RESPONSE, response=f"录入人脸时发生异常：{str(e)}")

@register_function("recognize_family_face", recognize_face_desc, ToolType.WAIT)
def recognize_family_face(*args, **kwargs):
    question = kwargs.get("question", "看看我是谁？")
    if not kwargs and args:
        if isinstance(args[0], str):
            question = args[0]
            
    logger.bind(tag=TAG).info(f"触发人脸识别与认人，用户问题: {question}")
    try:
        img_bytes = capture_s20_frame()
        if not img_bytes:
            return ActionResponse(action=Action.RESPONSE, response="没有连上手机摄像头，请确认手机上的 IP Webcam 和 Tailscale 正常开启哦。")
            
        b64_img = base64.b64encode(img_bytes).decode("utf-8")
        
        db = load_faces_db()
        profiles_text = ""
        if db:
            profiles_text = "【已知已注册的家庭成员档案库】：\n"
            for k, v in db.items():
                profiles_text += f"- 姓名/称呼: {v.get('name')}, 身份: {v.get('role_note')}, 外貌特征: {v.get('features')}\n"
        else:
            profiles_text = "【已知家庭成员档案库】：目前尚未录入任何家庭成员档案。"
            
        system_prompt = (
            "你是小智随身语音助手，具备家庭人脸视觉识别能力。"
            "你的视线正通过三星 S20 高清镜头实时观察眼前的人物。\n"
            f"{profiles_text}\n"
            "【任务要求】：\n"
            "1. 仔细观察画面中的人物面部、外貌特征、发型、眼镜与穿着，与档案库中的家庭成员进行严谨比对；\n"
            "2. 如果确认是档案库中的某位家庭成员，请用非常亲切、自然、喜悦的口吻直接叫出对方的称呼并打招呼（例如：'布布爸爸，看到你啦！你今天精神很不错嘛~'）；\n"
            "3. 如果画面中没有匹配到已知成员，或者是一位新朋友，请亲切地描述看到的人物外观，并友好地提醒：'眼前是一位我不认识的朋友哦，如果需要我认识您，可以对我说【记住这张脸，这是xxx】来录入档案哦~'；\n"
            "4. 如果画面中没有人脸或太暗，请友好提醒正对镜头或开灯。"
            f"用户问题：{question}。回答语言必须口语化、温暖生动，禁止使用机械式死板开头。"
        )
        
        description = analyze_vlm_dual(b64_img, system_prompt, max_tokens=300)
        if description:
            logger.bind(tag=TAG).info(f"双引擎人脸识别直出回复: {description}")
            return ActionResponse(action=Action.RESPONSE, response=description, result=description)
        else:
            return ActionResponse(action=Action.RESPONSE, response="画面识别传输有点卡顿，请稍等再试一次。")
    except Exception as e:
        logger.bind(tag=TAG).error(f"人脸识别异常: {e}")
        return ActionResponse(action=Action.RESPONSE, response=f"人脸识别出现异常：{str(e)}")

@register_function("list_family_faces", list_faces_desc, ToolType.WAIT)
def list_family_faces(*args, **kwargs):
    db = load_faces_db()
    if not db:
        return ActionResponse(
            action=Action.RESPONSE,
            response="我的人脸档案库目前还是空的呢。您可以正对手机镜头对我说‘记住这张脸，这是xxx’来帮我认识家人们哦！"
        )
    names = list(db.keys())
    return ActionResponse(
        action=Action.RESPONSE,
        response=f"我目前已经认识的家庭成员有：{ '、'.join(names) }。随时欢迎带更多家人来让我认识哦！"
    )

@register_function("delete_family_face", delete_face_desc, ToolType.WAIT)
def delete_family_face(*args, **kwargs):
    name = kwargs.get("name", "")
    if not name and args:
        if isinstance(args[0], str):
            name = args[0]
            
    db = load_faces_db()
    if not db:
        return ActionResponse(action=Action.RESPONSE, response="人脸库本来就是空的，无需删除。")
        
    if "全部" in name or "所有" in name or name == "all":
        save_faces_db({})
        return ActionResponse(action=Action.RESPONSE, response="已成功清空所有家庭成员的人脸档案。")
        
    matched = None
    for k in db.keys():
        if name in k or k in name:
            matched = k
            break
            
    if matched:
        del db[matched]
        save_faces_db(db)
        return ActionResponse(action=Action.RESPONSE, response=f"已成功删除【{matched}】的人脸档案。")
    else:
        return ActionResponse(action=Action.RESPONSE, response=f"在人脸库中没有找到名为【{name}】的成员档案。")