import os
import json
import base64
import requests
import urllib.request
import urllib.error
from typing import Dict, Any, List
from config.logger import setup_logging
from plugins_func.register import register_function, ToolType, ActionResponse, Action

TAG = __name__
logger = setup_logging()

S20_HOST = "http://100.122.149.94:8080"
ZHIPU_API_KEY = "fd04fb160360497291b1ae87596dbde9.ID3C9TfZTgTd3W9h"
GEMINI_API_KEY = "AIzaSyA1z-1pIt1lNM-NRjOmxZtXZ5yN5sR01-w"

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
FACES_DB_FILE = os.path.join(DATA_DIR, "family_faces.json")
FACES_IMG_DIR = os.path.join(DATA_DIR, "faces")

os.makedirs(FACES_IMG_DIR, exist_ok=True)

def load_faces_list() -> List[Dict[str, Any]]:
    """读取已注册的家庭成员人脸数据库，返回列表"""
    if os.path.exists(FACES_DB_FILE):
        try:
            with open(FACES_DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
                elif isinstance(data, dict):
                    return list(data.values())
        except Exception as e:
            logger.bind(tag=TAG).error(f"读取人脸数据库失败: {e}")
    return []

def save_faces_list(faces: List[Dict[str, Any]]):
    """保存家庭成员人脸数据库为列表格式"""
    try:
        os.makedirs(os.path.dirname(FACES_DB_FILE), exist_ok=True)
        with open(FACES_DB_FILE, "w", encoding="utf-8") as f:
            json.dump(faces, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.bind(tag=TAG).error(f"保存人脸数据库失败: {e}")

def capture_s20_frame() -> bytes:
    """从三星S20手机抓取最新一帧图片"""
    url = f"{S20_HOST}/shot.jpg"
    try:
        resp = requests.get(url, timeout=3.0)
        if resp.status_code == 200:
            return resp.content
    except Exception as e:
        logger.bind(tag=TAG).warning(f"从S20抓取画面失败: {e}")
    return None

def analyze_vlm_dual(image_base64: str, prompt: str, max_tokens: int = 300) -> str:
    """双引擎VLM分析：优先 智谱 GLM-4V-Flash，Google Gemini 2.5 Flash 兜底"""
    # 1. 优先：智谱 GLM-4V-Flash (极速多模态大模型)
    if ZHIPU_API_KEY:
        try:
            url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
            payload = {
                "model": "glm-4v-flash",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{image_base64}"
                                }
                            }
                        ]
                    }
                ],
                "temperature": 0.3,
                "max_tokens": max_tokens
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {ZHIPU_API_KEY}"
                }
            )
            with urllib.request.urlopen(req, timeout=8) as response:
                if response.status == 200:
                    res_json = json.loads(response.read().decode("utf-8"))
                    text = res_json["choices"][0]["message"]["content"].strip()
                    logger.bind(tag=TAG).info(f"智谱 GLM-4V-Flash 视觉识别成功: {text[:60]}...")
                    return text
        except Exception as e:
            logger.bind(tag=TAG).warning(f"智谱 GLM-4V-Flash 调用失败，尝试备用引擎: {e}")

    # 2. 备用：Google Gemini 2.5 Flash
    if GEMINI_API_KEY:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": prompt},
                            {
                                "inline_data": {
                                    "mime_type": "image/jpeg",
                                    "data": image_base64
                                }
                            }
                        ]
                    }
                ],
                "generationConfig": {
                    "temperature": 0.3,
                    "maxOutputTokens": max_tokens
                }
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=8) as response:
                if response.status == 200:
                    res_json = json.loads(response.read().decode("utf-8"))
                    text = res_json["candidates"][0]["content"]["parts"][0]["text"].strip()
                    logger.bind(tag=TAG).info(f"Gemini 2.5 Flash 视觉识别成功: {text[:60]}...")
                    return text
        except Exception as e:
            logger.bind(tag=TAG).error(f"Gemini 2.5 Flash 调用也失败: {e}")

    return None

register_face_desc = {
    "type": "function",
    "function": {
        "name": "register_family_face",
        "description": "通过三星S20手机摄像头拍照并录入家庭成员人脸档案。当用户说'记住这张脸，这是xxx'、'帮我认识一下xxx'、'录入人脸'时调用此工具。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "家庭成员的姓名或称呼，如'布布爸爸'、'妈妈'、'奶奶'、'布布'等"
                },
                "role_note": {
                    "type": "string",
                    "description": "身份或角色备注，如'家庭成员'、'父亲'、'孩子'等，默认为'家庭成员'"
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
        "description": "通过三星S20手机摄像头看眼前的人物并识别人脸。当用户询问'看看我是谁'、'你认得我吗'、'镜头前是谁'、'看看眼前是谁'、'看到我吗'时必须调用此工具！",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "用户的具体问题，如'看看我是谁'或'你认得我吗'"
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
        "description": "列出小智当前已记住和认识的所有家庭成员人脸档案列表。当用户询问'你认识谁'、'你记住了哪些人'、'人脸库里有谁'时调用。",
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
        img_filename = f"face_{int(os.times().elapsed*1000)}_{name}.jpg"
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
                
            faces = load_faces_list()
            now_str = os.popen("date '+%Y-%m-%d %H:%M:%S'").read().strip()
            updated = False
            for item in faces:
                if item.get("name") == name:
                    item["role"] = role_note
                    item["role_note"] = role_note
                    item["features"] = feature_desc
                    item["image_url"] = f"/api/faces/image/{img_filename}"
                    item["updated_at"] = now_str
                    updated = True
                    break
            if not updated:
                faces.append({
                    "id": f"face_{int(os.times().elapsed*1000)}",
                    "name": name,
                    "role": role_note,
                    "role_note": role_note,
                    "features": feature_desc,
                    "image_url": f"/api/faces/image/{img_filename}",
                    "created_at": now_str,
                    "updated_at": now_str
                })
            save_faces_list(faces)
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
        
        faces = load_faces_list()
        profiles_text = ""
        if faces:
            profiles_text = "【已知已注册的家庭成员档案库】：\n"
            for v in faces:
                name = v.get("name", "")
                role = v.get("role") or v.get("role_note") or "家庭成员"
                feat = v.get("features", "家庭主要成员")
                profiles_text += f"- 姓名/称呼: {name}, 身份: {role}, 档案特征: {feat}\n"
        else:
            profiles_text = "【已知家庭成员档案库】：目前尚未录入任何家庭成员档案。"
            
        system_prompt = (
            "你是小智随身语音助手，具备家庭人脸视觉识别能力。"
            "你的视线正通过三星 S20 高清镜头实时观察眼前的人物。\n"
            f"{profiles_text}\n"
            "【任务要求】：\n"
            "1. 仔细观察画面中的人物面部、外貌特征、发型、眼镜与穿着，与档案库中的家庭成员进行严谨比对；\n"
            "2. 如果确认是档案库中的家庭成员（如布布爸爸），请用非常亲切、自然、喜悦的口吻直接叫出对方的称呼并打招呼（例如：'布布爸爸，看到你啦！晚上好呀~'）；\n"
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
    faces = load_faces_list()
    if not faces:
        return ActionResponse(
            action=Action.RESPONSE,
            response="我的人脸档案库目前还是空的呢。您可以正对手机镜头对我说‘记住这张脸，这是xxx’来帮我认识家人们哦！"
        )
    names = [f.get("name") for f in faces if f.get("name")]
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
            
    faces = load_faces_list()
    if not faces:
        return ActionResponse(action=Action.RESPONSE, response="人脸库本来就是空的，无需删除。")
        
    if "全部" in name or "所有" in name or name == "all":
        save_faces_list([])
        return ActionResponse(action=Action.RESPONSE, response="已成功清空所有家庭成员的人脸档案。")
        
    initial_len = len(faces)
    faces = [f for f in faces if name not in f.get("name", "") and f.get("name", "") not in name]
    if len(faces) < initial_len:
        save_faces_list(faces)
        return ActionResponse(action=Action.RESPONSE, response=f"已成功删除【{name}】的人脸档案。")
    else:
        return ActionResponse(action=Action.RESPONSE, response=f"在人脸库中没有找到名为【{name}】的成员档案。")
