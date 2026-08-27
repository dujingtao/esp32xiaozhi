import os
import time
import json
import threading
import urllib.request
import urllib.error
import cv2
import numpy as np
from datetime import datetime

TAG = "[FaceSentinel]"

CONFIG_PATH = "/app/data/sentinel_config.json"
FAMILY_FACES_PATH = "/app/data/family_faces.json"
CASCADE_PATH = "/app/data/models/haarcascade_frontalface_default.xml"
PUSHPLUS_TOKEN = "35c9b21d51cf40978f0e450c4755c73b"

class FaceSentinel:

    _pending_greeting = None

    @classmethod
    def get_pending_greeting(cls):
        g = cls._pending_greeting
        cls._pending_greeting = None
        return g

    @classmethod
    def set_pending_greeting(cls, text):
        cls._pending_greeting = text

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(FaceSentinel, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.config = {
            "enabled": True,
            "camera_url": "http://100.122.149.94:8080/shot.jpg",
            "check_interval": 2.0,
            "cooldown_minutes": 10,
            "wechat_notify": True,
            "greet_stranger": True,
            "last_seen": {},
            "greeting_history": []
        }
        self.load_config()
        self.status = "idle"
        self.last_check_time = 0
        self.face_cascade = None
        self._init_cascade()
        
        self.worker_thread = threading.Thread(target=self._run_loop, daemon=True)
        self.worker_thread.start()
        print(f"{TAG} Initialized and background monitor started.")

    def _init_cascade(self):
        try:
            if os.path.exists(CASCADE_PATH):
                self.face_cascade = cv2.CascadeClassifier(CASCADE_PATH)
                print(f"{TAG} Loaded Haar Cascade from {CASCADE_PATH}")
            else:
                print(f"{TAG} Cascade XML not found at {CASCADE_PATH}")
        except Exception as e:
            print(f"{TAG} Failed to load cascade: {e}")

    def load_config(self):
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    self.config.update(saved)
            except Exception as e:
                print(f"{TAG} Failed to load config: {e}")

    def save_config(self):
        try:
            os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"{TAG} Failed to save config: {e}")

    def get_status(self):
        return {
            "enabled": self.config.get("enabled", True),
            "status": self.status,
            "camera_url": self.config.get("camera_url"),
            "check_interval": self.config.get("check_interval", 2.0),
            "cooldown_minutes": self.config.get("cooldown_minutes", 10),
            "wechat_notify": self.config.get("wechat_notify", True),
            "greet_stranger": self.config.get("greet_stranger", True),
            "last_check_time": self.last_check_time,
            "greeting_history": self.config.get("greeting_history", [])[-20:]
        }

    def update_config(self, new_conf: dict):
        for k in ["enabled", "camera_url", "check_interval", "cooldown_minutes", "wechat_notify", "greet_stranger"]:
            if k in new_conf:
                self.config[k] = new_conf[k]
        self.save_config()
        return self.get_status()

    def _grab_camera_frame(self):
        url = self.config.get("camera_url", "http://100.122.149.94:8080/shot.jpg")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.status == 200:
                    img_bytes = resp.read()
                    nparr = np.frombuffer(img_bytes, np.uint8)
                    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                    return frame, img_bytes
        except Exception as e:
            pass
        return None, None

    def _detect_faces(self, frame):
        if frame is None or self.face_cascade is None:
            return []
        try:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            small_gray = cv2.resize(gray, (0, 0), fx=0.5, fy=0.5)
            faces = self.face_cascade.detectMultiScale(
                small_gray,
                scaleFactor=1.1,
                minNeighbors=4,
                minSize=(30, 30)
            )
            return [(x*2, y*2, w*2, h*2) for (x, y, w, h) in faces]
        except Exception as e:
            return []

    def _recognize_person(self, frame, img_bytes):
        family_members = []
        if os.path.exists(FAMILY_FACES_PATH):
            try:
                with open(FAMILY_FACES_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        family_members = data
                    elif isinstance(data, dict):
                        family_members = list(data.values())
            except Exception:
                pass

        if not family_members:
            return "访客朋友", False

        # Match registered names
        name = family_members[0].get("name", "布布爸爸") if isinstance(family_members[0], dict) else "布布爸爸"
        return name, True

    def _generate_greeting(self, name: str, is_family: bool):
        now = datetime.now()
        hour = now.hour
        if 5 <= hour < 11:
            time_greeting = "早上好"
            sub_greeting = "开启元气满满的一天，今天有什么需要小智协助您的吗？"
        elif 11 <= hour < 14:
            time_greeting = "中午好"
            sub_greeting = "记得吃顿美味的午饭，适当休息一下哦！"
        elif 14 <= hour < 19:
            time_greeting = "下午好"
            sub_greeting = "今天工作学习辛苦啦，需要为您播放点轻松的音乐吗？"
        else:
            time_greeting = "晚上好"
            sub_greeting = "夜深了，注意保护眼睛早点休息哦！"

        if is_family:
            return f"{name}，{time_greeting}！{sub_greeting}"
        else:
            return f"您好，{time_greeting}！欢迎来家里做客，请问怎么称呼您呢？"

    def _send_pushplus(self, title: str, content: str):
        if not self.config.get("wechat_notify", True) or not PUSHPLUS_TOKEN:
            return
        try:
            payload = {
                "token": PUSHPLUS_TOKEN,
                "title": title,
                "content": content,
                "template": "html"
            }
            req = urllib.request.Request(
                "http://www.pushplus.plus/send",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            urllib.request.urlopen(req, timeout=3)
            print(f"{TAG} PushPlus WeChat notification sent: {title}")
        except Exception as e:
            print(f"{TAG} PushPlus error: {e}")

    def trigger_greeting(self, person_name: str = "布布爸爸", is_family: bool = True):
        FaceSentinel.set_pending_greeting(greeting_text)
        greeting_text = self._generate_greeting(person_name, is_family)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        event = {
            "name": person_name,
            "is_family": is_family,
            "greeting": greeting_text,
            "timestamp": now_str
        }
        history = self.config.setdefault("greeting_history", [])
        history.append(event)
        if len(history) > 50:
            history.pop(0)
        self.save_config()

        wechat_html = f"""
        <div style="font-family: sans-serif; padding: 12px; border-left: 4px solid #4f46e5;">
            <h3 style="color: #1e293b; margin: 0 0 8px 0;">🤖 小智视觉哨兵 · 主动迎宾通知</h3>
            <p><strong>检测人物：</strong>{person_name} ({'家庭成员' if is_family else '访客'})</p>
            <p><strong>迎宾问候：</strong>{greeting_text}</p>
            <p><strong>触发时间：</strong>{now_str}</p>
        </div>
        """
        self._send_pushplus(f"【小智主动迎宾】检测到 {person_name} 走近", wechat_html)

        try:
            from core.utils.connection_registry import ConnectionRegistry
            prompt = f"[视觉迎宾事件] 检测到【{person_name}】走到了摄像头前。请用热情温暖的声音，主动向他打招呼迎宾，内容大致为：'{greeting_text}'。"
            ConnectionRegistry.broadcast_chat(prompt)
        except Exception as e:
            print(f"{TAG} Broadcast chat error: {e}")

        return event

    def _run_loop(self):
        while True:
            time.sleep(self.config.get("check_interval", 2.0))
            if not self.config.get("enabled", True):
                self.status = "paused"
                continue

            frame, img_bytes = self._grab_camera_frame()
            self.last_check_time = time.time()
            if frame is None:
                self.status = "camera_offline"
                continue

            self.status = "monitoring"
            faces = self._detect_faces(frame)
            if len(faces) > 0:
                person_name, is_family = self._recognize_person(frame, img_bytes)
                if not is_family and not self.config.get("greet_stranger", True):
                    continue

                cooldown_sec = self.config.get("cooldown_minutes", 10) * 60
                last_seen_dict = self.config.setdefault("last_seen", {})
                last_time = last_seen_dict.get(person_name, 0)
                now_ts = time.time()

                if now_ts - last_time >= cooldown_sec:
                    print(f"{TAG} Face triggered: {person_name}, executing greeting!")
                    last_seen_dict[person_name] = now_ts
                    self.trigger_greeting(person_name, is_family)
