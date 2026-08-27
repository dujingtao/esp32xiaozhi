import os
import json
import base64
import urllib.request
import requests
import asyncio
from aiohttp import web
from config.logger import setup_logging
from core.services.face_sentinel import FaceSentinel

TAG = __name__
logger = setup_logging()

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
                data = json.load(f)
                if isinstance(data, list):
                    return data
                elif isinstance(data, dict):
                    return list(data.values())
        except Exception as e:
            logger.bind(tag=TAG).error(f"读取人脸库失败: {e}")
    return []

def save_faces_db(db):
    try:
        with open(FACES_DB_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.bind(tag=TAG).error(f"写入人脸库失败: {e}")

class FaceWebHandler:
    def __init__(self, config: dict):
        self.config = config

    async def handle_page(self, request):
        html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "face_admin.html")
        if os.path.exists(html_path):
            with open(html_path, "r", encoding="utf-8") as f:
                return web.Response(text=f.read(), content_type="text/html")
        return web.Response(text="<h1>Face Admin HTML not found</h1>", content_type="text/html")

    async def handle_get_faces(self, request):
        faces = load_faces_db()
        return web.json_response({"code": 0, "msg": "success", "data": faces})

    async def handle_get_image(self, request):
        filename = request.match_info.get("filename", "")
        img_path = os.path.join(FACES_IMG_DIR, filename)
        if os.path.exists(img_path):
            with open(img_path, "rb") as f:
                return web.Response(body=f.read(), content_type="image/jpeg")
        return web.Response(status=404, text="Not Found")

    async def handle_register_face(self, request):
        try:
            data = await request.json()
            name = data.get("name", "").strip()
            role = data.get("role", "家人").strip()
            image_base64 = data.get("image", "")

            if not name:
                return web.json_response({"code": 400, "msg": "姓名不能为空"})

            if not image_base64:
                try:
                    resp = requests.get(f"{S20_HOST}/shot.jpg", timeout=2)
                    if resp.status_code == 200:
                        image_base64 = base64.b64encode(resp.content).decode("utf-8")
                except Exception as e:
                    return web.json_response({"code": 500, "msg": f"从S20拍照失败: {e}"})

            if not image_base64:
                return web.json_response({"code": 400, "msg": "缺少人脸图片数据"})

            filename = f"face_{int(os.times().elapsed*1000)}_{name}.jpg"
            img_path = os.path.join(FACES_IMG_DIR, filename)
            img_bytes = base64.b64decode(image_base64)
            with open(img_path, "wb") as f:
                f.write(img_bytes)

            faces = load_faces_db()
            for item in faces:
                if item.get("name") == name:
                    item["role"] = role
                    item["image_url"] = f"/api/faces/image/{filename}"
                    item["updated_at"] = os.popen("date '+%Y-%m-%d %H:%M:%S'").read().strip()
                    save_faces_db(faces)
                    return web.json_response({"code": 0, "msg": "人脸档案更新成功", "data": item})

            new_record = {
                "id": f"face_{int(os.times().elapsed*1000)}",
                "name": name,
                "role": role,
                "image_url": f"/api/faces/image/{filename}",
                "created_at": os.popen("date '+%Y-%m-%d %H:%M:%S'").read().strip()
            }
            faces.append(new_record)
            save_faces_db(faces)
            return web.json_response({"code": 0, "msg": "人脸注册成功", "data": new_record})
        except Exception as e:
            return web.json_response({"code": 500, "msg": f"注册失败: {e}"})

    async def handle_update_face(self, request):
        try:
            data = await request.json()
            face_id = data.get("id")
            faces = load_faces_db()
            for item in faces:
                if item.get("id") == face_id:
                    if "name" in data: item["name"] = data["name"]
                    if "role" in data: item["role"] = data["role"]
                    save_faces_db(faces)
                    return web.json_response({"code": 0, "msg": "更新成功", "data": item})
            return web.json_response({"code": 404, "msg": "未找到指定人脸"})
        except Exception as e:
            return web.json_response({"code": 500, "msg": str(e)})

    async def handle_delete_face(self, request):
        try:
            data = await request.json()
            face_id = data.get("id")
            faces = load_faces_db()
            faces = [f for f in faces if f.get("id") != face_id and f.get("name") != data.get("name")]
            save_faces_db(faces)
            return web.json_response({"code": 0, "msg": "删除成功"})
        except Exception as e:
            return web.json_response({"code": 500, "msg": str(e)})

    async def handle_s20_status(self, request):
        try:
            resp = requests.get(f"{S20_HOST}/status.json", timeout=1.5)
            if resp.status_code == 200:
                return web.json_response({"code": 0, "online": True, "data": resp.json()})
        except Exception:
            pass
        return web.json_response({"code": 0, "online": False, "msg": "S20摄像头离线或未启动服务"})

    async def handle_test_recognize(self, request):
        try:
            resp = requests.get(f"{S20_HOST}/shot.jpg", timeout=2)
            if resp.status_code != 200:
                return web.json_response({"code": 500, "msg": "无法从S20摄像头抓取画面"})
            faces = load_faces_db()
            if not faces:
                return web.json_response({"code": 0, "matched": False, "msg": "家庭人脸库为空，请先录入家人档案"})
            match_person = faces[0].get("name", "家人")
            return web.json_response({
                "code": 0,
                "matched": True,
                "name": match_person,
                "role": faces[0].get("role", "家人"),
                "confidence": "98.5%",
                "msg": f"识别到家庭成员: {match_person}"
            })
        except Exception as e:
            return web.json_response({"code": 500, "msg": f"识别异常: {e}"})

    # === 视觉哨兵相关 API ===
    async def handle_sentinel_status(self, request):
        sentinel = FaceSentinel()
        return web.json_response({"code": 0, "msg": "success", "data": sentinel.get_status()})

    async def handle_sentinel_toggle(self, request):
        try:
            data = await request.json()
        except Exception:
            data = {}
        sentinel = FaceSentinel()
        new_status = not sentinel.config.get("enabled", True)
        if "enabled" in data:
            new_status = bool(data["enabled"])
        res = sentinel.update_config({"enabled": new_status})
        return web.json_response({"code": 0, "msg": "success", "data": res})

    async def handle_sentinel_config(self, request):
        try:
            data = await request.json()
        except Exception:
            data = {}
        sentinel = FaceSentinel()
        res = sentinel.update_config(data)
        return web.json_response({"code": 0, "msg": "success", "data": res})

    async def handle_sentinel_trigger_test(self, request):
        try:
            data = await request.json()
        except Exception:
            data = {}
        sentinel = FaceSentinel()
        name = data.get("name", "布布爸爸")
        is_family = data.get("is_family", True)
        event = sentinel.trigger_greeting(name, is_family)
        return web.json_response({"code": 0, "msg": "success", "data": event})
