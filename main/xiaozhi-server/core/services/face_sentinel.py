import os
import time
import json
import base64
import threading
import urllib.request
import urllib.error
import cv2
import numpy as np
from datetime import datetime
from core.utils.connection_registry import ConnectionRegistry

TAG = "[FaceSentinel]"

CONFIG_PATH = "/app/data/sentinel_config.json"
FAMILY_FACES_PATH = "/app/data/family_faces.json"
CASCADE_PATH = "/app/data/models/haarcascade_frontalface_default.xml"
PUSHPLUS_TOKEN = "35c9b21d51cf40978f0e450c4755c73b"
ZHIPU_API_KEY = "fd04fb160360497291b1ae87596dbde9.ID3C9TfZTgTd3W9h"

class FaceSentinel:
    _instance = None
    _lock = threading.Lock()
    _pending_greeting = None

    @classmethod
    def get_pending_greeting(cls):
        with cls._lock:
            g = cls._pending_greeting
            cls._pending_greeting = None
            return g

    @classmethod
    def set_pending_greeting(cls, text):
        with cls._lock:
            cls._pending_greeting = text

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
            "check_interval": 0.5,
            "cooldown_minutes": 1,
            "wechat_notify": True,
            "greet_stranger": True,
            "last_global_greeting_time": 0,
            "greeting_history": []
        }
        self.load_config()
        self.status = "idle"
        self.last_check_time = 0
        self.face_cascade = None
        self._init_cascade()
        
        self.worker_thread = threading.Thread(target=self._run_loop, daemon=True)
        self.worker_thread.start()
        print(f"{TAG} v1.1.0-smart-vision Initialized: Continuous Quality Assessment & 3-Tier Sentinel Started.")

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

    def _grab_camera_frame(self):
        url = self.config.get("camera_url", "http://100.122.149.94:8080/shot.jpg")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=1.8) as resp:
                if resp.status == 200:
                    img_bytes = resp.read()
                    nparr = np.frombuffer(img_bytes, np.uint8)
                    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                    return frame, img_bytes
        except Exception:
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
                minNeighbors=3,
                minSize=(30, 30)
            )
            return [(x*2, y*2, w*2, h*2) for (x, y, w, h) in faces]
        except Exception:
            return []

    def _evaluate_face_quality(self, frame, face_box):
        """评估人脸清晰度、尺寸和角度对称度，给出 0~100 的综合画质分"""
        if frame is None or face_box is None:
            return 0.0, None
        x, y, w, h = face_box
        H, W, _ = frame.shape
        # 边界安全保护
        x1, y1 = max(0, x), max(0, y)
        x2, y2 = min(W, x + w), min(H, y + h)
        if x2 <= x1 or y2 <= y1 or w < 30 or h < 30:
            return 0.0, None

        face_crop = frame[y1:y2, x1:x2]
        gray = cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)

        # 1. 拉普拉斯清晰度算子 (Laplacian Variance)
        laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        clarity_score = min(100.0, (laplacian_var / 120.0) * 100.0)

        # 2. 尺寸与分辨率打分
        size_score = min(100.0, (w * h) / (120.0 * 120.0) * 100.0)

        # 3. 正脸比例规整度 (Aspect Ratio)
        aspect = float(w) / float(h)
        if 0.75 <= aspect <= 1.15:
            pose_score = 100.0
        else:
            pose_score = max(30.0, 100.0 - abs(aspect - 0.95) * 150.0)

        # 4. 曝光适度 (Mean Brightness)
        mean_brightness = np.mean(gray)
        if 70 <= mean_brightness <= 190:
            exp_score = 100.0
        else:
            exp_score = max(40.0, 100.0 - abs(mean_brightness - 130) * 1.0)

        # 综合加权质量评分
        total_score = clarity_score * 0.45 + size_score * 0.25 + pose_score * 0.20 + exp_score * 0.10
        return total_score, face_crop

    def _recognize_person_vlm(self, img_bytes):
        """使用智谱 GLM-4V-Flash 大模型进行高精度家庭人脸比对"""
        family_members = []
        if os.path.exists(FAMILY_FACES_PATH):
            try:
                with open(FAMILY_FACES_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list): family_members = data
                    elif isinstance(data, dict): family_members = list(data.values())
            except Exception:
                pass

        if not family_members:
            return "访客朋友", False

        members_desc = []
        for m in family_members:
            name = m.get("name", "")
            role = m.get("role", "家人")
            feat = m.get("features", "")
            members_desc.append(f"- 姓名：【{name}】，身份：{role}，特征描述：{feat}")

        prompt = f"""你是一个家庭人脸识别助手。请仔细观察这张手机摄像头拍摄的画面，判断画面正中央最主要的人物是谁。
家庭人脸库信息如下：
{chr(10).join(members_desc)}

请严格按以下 JSON 格式返回结果（不要包含 Markdown 代码块或额外说明）：
{{"recognized": true或false, "name": "若确认为库中家人则写其姓名，否则写'访客朋友'", "confidence": 0.0到1.0}}
"""
        try:
            b64_img = base64.b64encode(img_bytes).decode("utf-8")
            url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
            payload = {
                "model": "glm-4v-flash",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}}
                        ]
                    }
                ],
                "temperature": 0.1,
                "max_tokens": 100
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {ZHIPU_API_KEY}"}
            )
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                result_json = json.loads(resp.read().decode("utf-8"))
                content = result_json["choices"][0]["message"]["content"].strip()
                if "{" in content and "}" in content:
                    content = content[content.find("{"):content.rfind("}")+1]
                data = json.loads(content)
                if data.get("recognized", False) and data.get("name") and data.get("name") != "访客朋友":
                    return data["name"], True
        except Exception as e:
            print(f"{TAG} VLM face recognize exception: {e}")

        return "访客朋友", False

    def _generate_greeting(self, name: str, is_family: bool, quality_clear: bool):
        now = datetime.now()
        hour = now.hour
        if 5 <= hour < 11:
            time_str = "早上好"
            family_sub = "开启元气满满的一天，今天有什么需要小智协助您的吗？"
        elif 11 <= hour < 14:
            time_str = "中午好"
            family_sub = "记得吃顿美味的午饭，适当休息一下哦！"
        elif 14 <= hour < 19:
            time_str = "下午好"
            family_sub = "今天工作学习辛苦啦，需要为您播放点轻松的音乐吗？"
        else:
            time_str = "晚上好"
            family_sub = "夜深了，注意保护眼睛早点休息哦！"

        if is_family and quality_clear:
            # 🟢 Tier 1: 看清五官且属于家庭成员
            return f"{name}，{time_str}！{family_sub}"
        elif quality_clear:
            # 🟡 Tier 2: 看清五官但不在库中（明确是新客人）
            return f"您好，{time_str}！欢迎来家里做客，请问怎么称呼您呢？"
        else:
            # 🔴 Tier 3: 一直未看清面部特征（移动模糊/角度不佳/侧脸）
            return f"是我眼神不好了吗？刚刚没有太看清楚您的面容。您好呀，欢迎来家里做客，请问怎么称呼您呢？"

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

    def trigger_greeting(self, person_name: str, is_family: bool, quality_clear: bool):
        greeting_text = self._generate_greeting(person_name, is_family, quality_clear)
        FaceSentinel.set_pending_greeting(greeting_text)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        status_tag = "家庭成员" if is_family else ("新客人(已看清)" if quality_clear else "访客(未完全看清)")
        event = {
            "name": person_name,
            "is_family": is_family,
            "quality_clear": quality_clear,
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
            <h3 style="color: #1e293b; margin: 0 0 8px 0;">🤖 小智视觉哨兵 · 主动迎宾通知 (v1.1.0)</h3>
            <p><strong>识别结果：</strong>{person_name} ({status_tag})</p>
            <p><strong>画质状态：</strong>{'清晰正脸已锁定' if quality_clear else '模糊/侧脸容错'}</p>
            <p><strong>主动播报：</strong>{greeting_text}</p>
            <p><strong>触发时间：</strong>{now_str}</p>
        </div>
        """
        self._send_pushplus(f"【小智主动迎宾】检测到 {person_name} ({status_tag}) 走近", wechat_html)

        try:
            if is_family and quality_clear:
                cue = f"你通过 S20 手机摄像头清晰看到【{person_name}】走到了音箱面前。请用亲切熟悉的家人语调主动向他打招呼，内容大致为：'{greeting_text}'。"
            elif quality_clear:
                cue = f"你通过 S20 手机摄像头观察到一位新的客人走到了音箱面前。请以礼貌热情的管家语调向客人打招呼迎宾，内容大致为：'{greeting_text}'。"
            else:
                cue = f"你通过 S20 手机摄像头感知到音箱面前有人，但画面有些模糊或者角度偏了没太看清面部。请以幽默谦逊的语调（带上'是我眼神不好了吗'）向他打招呼，内容大致为：'{greeting_text}'。"

            prompt = (
                f"[主动视觉感知唤醒事件] {cue} 当前时间为 {now_str}。"
                f"播报完毕后设备将自动开启麦克风进入倾听模式，等待他的自然回应。语调温暖自然。"
            )
            dispatched = ConnectionRegistry.broadcast_proactive_chat(prompt)
            if dispatched:
                print(f"{TAG} Successfully dispatched autonomous proactive chat to ESP32 hardware!")
            else:
                print(f"{TAG} Saved as pending greeting for next immediate wake-up.")
        except Exception as e:
            print(f"{TAG} Broadcast chat error: {e}")

        return event

    def _run_loop(self):
        """持续多帧分析与面部画质优选主循环"""
        while True:
            time.sleep(self.config.get("check_interval", 0.5))
            if not self.config.get("enabled", True):
                self.status = "paused"
                continue

            frame, img_bytes = self._grab_camera_frame()
            self.last_check_time = time.time()
            if frame is None:
                self.status = "camera_offline"
                continue

            faces = self._detect_faces(frame)
            if len(faces) == 0:
                self.status = "monitoring"
                continue

            # 检查全局冷却
            cooldown_sec = self.config.get("cooldown_minutes", 1) * 60
            now_ts = time.time()
            last_global = self.config.get("last_global_greeting_time", 0)
            remaining = max(0, int(cooldown_sec - (now_ts - last_global)))
            if remaining > 0:
                print(f"{TAG} Face detected but in cooldown ({remaining}s remaining)")
                continue

            # ── 发现目标，进入多帧动态观察窗口 (Analyzing Window: 1.8~2.2s) ──
            print(f"{TAG} [Target Spotted] Entering multi-frame quality observation window...")
            # 立即向 ESP32 屏幕广播“正在识别中...”
            ConnectionRegistry.broadcast_display_message("正在识别中...")

            candidate_frames = []
            obs_start = time.time()
            best_score = 0.0
            best_crop = None
            best_bytes = img_bytes

            # 初始帧评估
            q_score, crop = self._evaluate_face_quality(frame, faces[0])
            candidate_frames.append((q_score, frame, img_bytes, crop))
            if q_score > best_score:
                best_score = q_score
                best_crop = crop
                best_bytes = img_bytes

            # 在接下来 1.8 秒内连续采样 3~5 帧
            while time.time() - obs_start < 1.8:
                time.sleep(0.35)
                f_next, bytes_next = self._grab_camera_frame()
                if f_next is None:
                    continue
                next_faces = self._detect_faces(f_next)
                if len(next_faces) > 0:
                    score_next, crop_next = self._evaluate_face_quality(f_next, next_faces[0])
                    print(f"{TAG} Sampling frame: Face Quality Score = {score_next:.1f}/100")
                    candidate_frames.append((score_next, f_next, bytes_next, crop_next))
                    if score_next > best_score:
                        best_score = score_next
                        best_crop = crop_next
                        best_bytes = bytes_next

                    # 遇到超高画质正脸（>=85 分），快速锁定无需多等
                    if score_next >= 85.0:
                        print(f"{TAG} High-confidence clear face captured ({score_next:.1f} pts), fast locking!")
                        break

            print(f"{TAG} Observation window finished. Best Face Quality Score: {best_score:.1f}/100")

            # ── 判断是否看清五官 ──
            quality_clear = (best_score >= 50.0)  # 50分以上视为看清五官
            person_name = "访客朋友"
            is_family = False

            if quality_clear and best_bytes:
                # 送入 VLM 模型高精度认人
                person_name, is_family = self._recognize_person_vlm(best_bytes)
                print(f"{TAG} VLM Recognition Result: name='{person_name}', is_family={is_family}, quality_clear=True")
            else:
                print(f"{TAG} Face quality insufficient or blurry (score {best_score:.1f}), triggering Tier-3 unclear greeting.")
                person_name = "访客朋友"
                is_family = False
                quality_clear = False

            self.config["last_global_greeting_time"] = time.time()
            self.save_config()
            self.trigger_greeting(person_name, is_family, quality_clear)
