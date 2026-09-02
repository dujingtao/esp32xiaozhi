import json
import os
from aiohttp import web
from config.logger import setup_logging
from core.services.fall_detector import FallDetector

TAG = __name__
logger = setup_logging()

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
EVENTS_PATH = os.path.join(DATA_DIR, "fall_events.json")

class FallHandler:
    def __init__(self):
        self.detector = FallDetector()

    async def handle_fall_status(self, request):
        return web.json_response({"code": 0, "success": True, "msg": "success", "data": self.detector.get_status()})

    async def handle_fall_toggle(self, request):
        new_status = not self.detector.config.get("enabled", True)
        res = self.detector.update_config({"enabled": new_status})
        return web.json_response({"code": 0, "success": True, "msg": "Toggled", "data": res})

    async def handle_fall_config(self, request):
        try:
            data = await request.json()
            res = self.detector.update_config(data)
            return web.json_response({"code": 0, "success": True, "msg": "Config updated", "data": res})
        except Exception as e:
            return web.json_response({"code": 1, "success": False, "msg": str(e)}, status=400)

    async def handle_fall_events(self, request):
        history = []
        if os.path.exists(EVENTS_PATH):
            try:
                with open(EVENTS_PATH, "r", encoding="utf-8") as f:
                    history = json.load(f)
            except Exception:
                pass
        return web.json_response({"code": 0, "success": True, "msg": "success", "data": history})

    async def handle_fall_test(self, request):
        try:
            self.detector.trigger_emergency_fall_alert("【手动模拟演练】测试老人跌倒全通道紧急响应与语音对讲", {"aspect_ratio": 0.45, "posture": "手动测试"})
            return web.json_response({"code": 0, "success": True, "msg": "Emergency test alert dispatched"})
        except Exception as e:
            return web.json_response({"code": 1, "success": False, "msg": str(e)}, status=500)
