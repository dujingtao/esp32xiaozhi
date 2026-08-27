import os
import json
import base64
import urllib.request
import requests
import asyncio
from aiohttp import web
from config.logger import setup_logging
from core.api.base_handler import BaseHandler

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

def load_faces_db():
    if os.path.exists(FACES_DB_FILE):
        try:
            with open(FACES_DB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.bind(tag=TAG).error(f"读取人脸库失败: {e}")
    return {}

def save_faces_db(db):
    try:
        with open(FACES_DB_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.bind(tag=TAG).error(f"写入人脸库失败: {e}")

def capture_s20_frame():
    for ep in ["/shot.jpg", "/photo.jpg"]:
        try:
            url = f"{S20_HOST}{ep}"
            req = urllib.request.Request(url, headers={"User-Agent": "XiaoZhi-Face/1.0"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = resp.read()
                if len(data) > 1000:
                    return data
        except:
            pass
    return None

def analyze_vlm_dual(b64_img, prompt_text, max_tokens=300):
    try:
        gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{"parts": [{"text": prompt_text}, {"inline_data": {"mime_type": "image/jpeg", "data": b64_img}}]}],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.3}
        }
        r = requests.post(gemini_url, json=payload, timeout=8)
        if r.status_code == 200:
            return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except:
        pass

    try:
        zhipu_url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
        headers = {"Authorization": f"Bearer {ZHIPU_API_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": "glm-4v-flash",
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt_text}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}}]}],
            "max_tokens": max_tokens
        }
        r = requests.post(zhipu_url, headers=headers, json=payload, timeout=8)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
    except:
        pass
    return None


class FaceWebHandler(BaseHandler):
    def __init__(self, config: dict):
        super().__init__(config)

    async def handle_page(self, request):
        """返回现代高级的人脸记忆管理 Web 界面"""
        html_path = os.path.join(os.path.dirname(__file__), "face_admin.html")
        if os.path.exists(html_path):
            with open(html_path, "r", encoding="utf-8") as f:
                html = f.read()
            return web.Response(text=html, content_type="text/html")
        return web.Response(text="<h1>页面加载中...</h1>", content_type="text/html")

    async def handle_get_faces(self, request):
        db = load_faces_db()
        # 补充每位成员的图片 URL
        result = []
        for name, info in db.items():
            img_file = info.get("img_file", f"{name}.jpg")
            has_img = os.path.exists(os.path.join(FACES_IMG_DIR, img_file))
            result.append({
                "name": name,
                "role_note": info.get("role_note", "家庭成员"),
                "features": info.get("features", "暂无特征描述"),
                "img_file": img_file,
                "has_img": has_img,
                "img_url": f"/api/faces/image/{img_file}" if has_img else None,
                "updated_at": info.get("updated_at", "")
            })
        return web.json_response({"success": True, "faces": result})

    async def handle_get_image(self, request):
        filename = request.match_info.get("filename", "")
        img_path = os.path.join(FACES_IMG_DIR, filename)
        if os.path.exists(img_path):
            with open(img_path, "rb") as f:
                return web.Response(body=f.read(), content_type="image/jpeg")
        return web.Response(status=404, text="Image not found")

    async def handle_register_face(self, request):
        """录入人脸：支持通过 S20 抓拍或上传本地图片"""
        try:
            reader = await request.multipart()
            name = ""
            role_note = "家庭成员"
            img_bytes = None
            use_s20 = False

            while True:
                part = await reader.next()
                if part is None:
                    break
                if part.name == "name":
                    name = (await part.text()).strip()
                elif part.name == "role_note":
                    role_note = (await part.text()).strip()
                elif part.name == "use_s20":
                    use_s20 = (await part.text()).strip().lower() == "true"
                elif part.name == "image":
                    img_bytes = await part.read()

            if not name:
                return web.json_response({"success": False, "message": "姓名/称呼不能为空"})

            if use_s20 or not img_bytes:
                img_bytes = capture_s20_frame()
                if not img_bytes:
                    return web.json_response({"success": False, "message": "从 S20 手机镜头抓取画面失败，请确认手机 IP Webcam 已开启"})

            # 保存图片
            img_filename = f"{name}.jpg"
            img_path = os.path.join(FACES_IMG_DIR, img_filename)
            with open(img_path, "wb") as f:
                f.write(img_bytes)

            b64_img = base64.b64encode(img_bytes).decode("utf-8")
            prompt = (
                f"请仔细观察画面中正对镜头的人物面部与外貌特征，用于人脸识别建档。\n"
                f"请简明扼要地总结该人物的显著外貌特征（包括性别、年龄段、发型/发色、是否戴眼镜、脸型特征、穿着等），50字以内。\n"
                f"如果画面中没有清晰的人脸，请明确回复'NO_FACE'。"
            )
            features = analyze_vlm_dual(b64_img, prompt, max_tokens=150)
            if not features or "NO_FACE" in features:
                return web.json_response({"success": False, "message": "画面中未检测到清晰的人物正脸，请正对镜头重新拍摄"})

            import time
            db = load_faces_db()
            db[name] = {
                "name": name,
                "role_note": role_note,
                "features": features,
                "img_file": img_filename,
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            save_faces_db(db)

            return web.json_response({"success": True, "message": f"成功建档【{name}】！", "features": features})
        except Exception as e:
            return web.json_response({"success": False, "message": f"建档异常: {str(e)}"})

    async def handle_update_face(self, request):
        try:
            data = await request.json()
            name = data.get("name")
            new_name = data.get("new_name", name)
            role_note = data.get("role_note")
            features = data.get("features")

            db = load_faces_db()
            if name not in db:
                return web.json_response({"success": False, "message": f"未找到【{name}】的人脸档案"})

            item = db.pop(name)
            item["name"] = new_name
            if role_note is not None:
                item["role_note"] = role_note
            if features is not None:
                item["features"] = features
            
            # Rename image file if name changed
            if new_name != name and os.path.exists(os.path.join(FACES_IMG_DIR, f"{name}.jpg")):
                try:
                    os.rename(os.path.join(FACES_IMG_DIR, f"{name}.jpg"), os.path.join(FACES_IMG_DIR, f"{new_name}.jpg"))
                    item["img_file"] = f"{new_name}.jpg"
                except:
                    pass

            db[new_name] = item
            save_faces_db(db)
            return web.json_response({"success": True, "message": "修改成功"})
        except Exception as e:
            return web.json_response({"success": False, "message": str(e)})

    async def handle_delete_face(self, request):
        try:
            data = await request.json()
            name = data.get("name")
            db = load_faces_db()

            if name == "__all__":
                save_faces_db({})
                return web.json_response({"success": True, "message": "已清空所有人脸档案"})

            if name in db:
                del db[name]
                save_faces_db(db)
                img_path = os.path.join(FACES_IMG_DIR, f"{name}.jpg")
                if os.path.exists(img_path):
                    try:
                        os.remove(img_path)
                    except:
                        pass
                return web.json_response({"success": True, "message": f"已成功删除【{name}】"})
            return web.json_response({"success": False, "message": f"档案库中无【{name}】"})
        except Exception as e:
            return web.json_response({"success": False, "message": str(e)})

    async def handle_test_recognize(self, request):
        """在线实时测试认人"""
        try:
            img_bytes = capture_s20_frame()
            if not img_bytes:
                return web.json_response({"success": False, "message": "无法从 S20 获取实时画面"})

            b64_img = base64.b64encode(img_bytes).decode("utf-8")
            db = load_faces_db()
            profiles_text = ""
            if db:
                for k, v in db.items():
                    profiles_text += f"- 姓名/称呼: {v.get('name')}, 身份: {v.get('role_note')}, 外貌特征: {v.get('features')}\n"

            prompt = (
                "你是小智随身语音助手。你的视线正通过三星 S20 高清镜头实时观察眼前的人物。\n"
                f"【已知家庭成员档案库】：\n{profiles_text}\n"
                "【任务要求】：仔细观察画面中人物的外貌，比对档案库，用亲切口语直接叫出称呼并打招呼；"
                "若不认识，友好描述外观并提醒录入。请输出纯文本回复内容。"
            )
            reply = analyze_vlm_dual(b64_img, prompt, max_tokens=250)
            return web.json_response({
                "success": True,
                "reply": reply,
                "snapshot_base64": f"data:image/jpeg;base64,{b64_img}"
            })
        except Exception as e:
            return web.json_response({"success": False, "message": str(e)})

    async def handle_s20_status(self, request):
        """获取 S20 手机连接状态与实时取景"""
        try:
            url = f"{S20_HOST}/status.json"
            req = urllib.request.Request(url, headers={"User-Agent": "XiaoZhi/1.0"})
            with urllib.request.urlopen(req, timeout=2) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return web.json_response({"online": True, "battery": data.get("battery", 100), "curvals": data.get("curvals", {})})
        except:
            return web.json_response({"online": False, "message": "S20 手机离线或未开启 IP Webcam"})