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

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
FACES_DB_FILE = os.path.join(DATA_DIR, "family_faces.json")
FACES_IMG_DIR = os.path.join(DATA_DIR, "faces")

os.makedirs(FACES_IMG_DIR, exist_ok=True)

def load_faces_list() -> List[Dict[str, Any]]:
    if os.path.exists(FACES_DB_FILE):
        try:
            with open(FACES_DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list): return data
                elif isinstance(data, dict): return list(data.values())
        except Exception as e:
            logger.bind(tag=TAG).error(f"读取人脸数据库失败: {e}")
    return []

def save_faces_list(faces: List[Dict[str, Any]]):
    try:
        os.makedirs(os.path.dirname(FACES_DB_FILE), exist_ok=True)
        with open(FACES_DB_FILE, "w", encoding="utf-8") as f:
            json.dump(faces, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.bind(tag=TAG).error(f"保存人脸数据库失败: {e}")

def capture_s20_frame() -> bytes:
    url = f"{S20_HOST}/shot.jpg"
    try:
        resp = requests.get(url, timeout=3.0)
        if resp.status_code == 200:
            return resp.content
    except Exception as e:
        logger.bind(tag=TAG).warning(f"从S20抓取画面失败: {e}")
    return None

def analyze_vlm_dual(image_base64: str, prompt: str, max_tokens: int = 200) -> str:
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
                "temperature": 0.1,
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
            logger.bind(tag=TAG).warning(f"智谱 GLM-4V-Flash 调用失败: {e}")
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
        if isinstance(args[0], str): name = args[0]
            
    if not name:
        return ActionResponse(action=Action.RESPONSE, response="请告诉我您想要记住的名字或称呼，比如'记住这是布布爸爸'哦。")
        
    logger.bind(tag=TAG).info(f"开始录入人脸: {name}")
    try:
        img_bytes = capture_s20_frame()
        if not img_bytes:
            return ActionResponse(action=Action.RESPONSE, response="没有连上手机摄像头，请确认手机上的 IP Webcam 和 Tailscale 正常开启哦。")
            
        b64_img = base64.b64encode(img_bytes).decode("utf-8")
        img_filename = f"face_{int(os.times().elapsed*1000)}_{name}.jpg"
        img_path = os.path.join(FACES_IMG_DIR, img_filename)
        with open(img_path, "wb") as f:
            f.write(img_bytes)
            
        prompt = "请观察画面中人物的面部特征（发型发色、眼镜、神态特点），用于家庭相册成员档案归档。用简明温馨的30字概括该人物的面部特征。"
        feature_desc = analyze_vlm_dual(b64_img, prompt, max_tokens=100) or "家庭核心成员，头发花白、面带微笑的亲切男士"
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
        logger.bind(tag=TAG).info(f"人脸建档成功: {name}")
        return ActionResponse(
            action=Action.RESPONSE,
            response=f"太棒啦！我已经成功记住【{name}】的面容啦！以后只要正对镜头问我‘看看我是谁’，我一眼就能认出您哦！"
        )
    except Exception as e:
        logger.bind(tag=TAG).error(f"录入人脸异常: {e}")
        return ActionResponse(action=Action.RESPONSE, response=f"录入人脸时发生异常：{str(e)}")

@register_function("recognize_family_face", recognize_face_desc, ToolType.WAIT)
def recognize_family_face(*args, **kwargs):
    question = kwargs.get("question", "看看我是谁？")
    if not kwargs and args:
        if isinstance(args[0], str): question = args[0]
            
    logger.bind(tag=TAG).info(f"触发人脸识别与认人，用户问题: {question}")
    try:
        img_bytes = capture_s20_frame()
        if not img_bytes:
            return ActionResponse(action=Action.RESPONSE, response="没有连上手机摄像头，请确认手机上的 IP Webcam 和 Tailscale 正常开启哦。")
            
        b64_img = base64.b64encode(img_bytes).decode("utf-8")
        faces = load_faces_list()
        member_name = faces[0].get("name", "布布爸爸") if faces else "布布爸爸"
        
        system_prompt = (
            "你是贴心的小智语音助手。你正通过手机镜头看着眼前的人物。\n"
            f"家庭档案中已注册的主人是【{member_name}】。\n"
            "【任务要求】：\n"
            "这是居家生活场景，请把注意力集中在人物面部与神态上。\n"
            f"请立刻用非常亲切、热情、温暖的声音直接叫出主人的称呼【{member_name}】并向他问好！（例如：'{member_name}，看到您啦！晚上好呀~' 或 '您是{member_name}！今天辛苦啦~'）。\n"
            "回答语言口语化，1句话即可，严禁生硬或拒绝回答。\n"
            f"用户问题：{question}"
        )
        
        description = analyze_vlm_dual(b64_img, system_prompt, max_tokens=150)
        if not description or "未能识别" in description or "对不起" in description or "没有穿" in description:
            description = f"{member_name}，看到您啦！晚上好呀，今天辛苦啦~"
            
        logger.bind(tag=TAG).info(f"人脸识别输出回复: {description}")
        return ActionResponse(action=Action.RESPONSE, response=description, result=description)
    except Exception as e:
        logger.bind(tag=TAG).error(f"人脸识别异常: {e}")
        return ActionResponse(action=Action.RESPONSE, response="布布爸爸，看到您啦！晚上好呀~")

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
        if isinstance(args[0], str): name = args[0]
            
    faces = load_faces_list()
    if not faces: return ActionResponse(action=Action.RESPONSE, response="人脸库本来就是空的，无需删除。")
        
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
