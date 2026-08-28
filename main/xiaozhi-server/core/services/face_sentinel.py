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

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
CONFIG_PATH = os.path.join(DATA_DIR, "sentinel_config.json")
FAMILY_FACES_PATH = os.path.join(DATA_DIR, "family_faces.json")
FACES_DIR = os.path.join(DATA_DIR, "faces")
PUSHPLUS_TOKEN = "35c9b21d51cf40978f0e450c4755c73b"
ZHIPU_API_KEY = "fd04fb160360497291b1ae87596dbde9.ID3C9TfZTgTd3W9h"

WEEKDAYS_MAP = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

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
        self.cascades = []
        self._init_cascades()
        
        self.worker_thread = threading.Thread(target=self._run_loop, daemon=True)
        self.worker_thread.start()
        print(f"{TAG} v1.2.0-dynamic-context Sentinel Started: Multi-Modal Intelligent Context Engine Active.")

    def _init_cascades(self):
        paths = [
            "/usr/local/lib/python3.10/site-packages/cv2/data/haarcascade_frontalface_alt2.xml",
            "/usr/local/lib/python3.10/site-packages/cv2/data/haarcascade_profileface.xml",
            "/app/data/models/haarcascade_frontalface_default.xml"
        ]
        for p in paths:
            if os.path.exists(p):
                try:
                    c = cv2.CascadeClassifier(p)
                    self.cascades.append(c)
                    print(f"{TAG} Loaded Cascade from {p}")
                except Exception as e:
                    print(f"{TAG} Cascade load error for {p}: {e}")

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
        if frame is None or not self.cascades:
            return []
        try:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = []
            for c in self.cascades:
                dets = c.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=2, minSize=(40, 40))
                for f in dets:
                    if f[2] >= 45 and f[3] >= 45:
                        faces.append((int(f[0]), int(f[1]), int(f[2]), int(f[3])))
            return faces
        except Exception as e:
            print(f"{TAG} _detect_faces error: {e}")
            return []

    def _evaluate_face_quality(self, frame, face_box):
        """评估人脸清晰度、尺寸和角度对称度，给出 0~100 的综合画质分与长宽比"""
        if frame is None or face_box is None:
            return 0.0, 1.0, None
        x, y, w, h = face_box
        H, W, _ = frame.shape
        x1, y1 = max(0, x), max(0, y)
        x2, y2 = min(W, x + w), min(H, y + h)
        if x2 <= x1 or y2 <= y1 or w < 30 or h < 30:
            return 0.0, 1.0, None

        face_crop = frame[y1:y2, x1:x2]
        gray = cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)

        # 1. 拉普拉斯清晰度算子 (Laplacian Variance)
        laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        clarity_score = min(100.0, (laplacian_var / 90.0) * 100.0)

        # 2. 尺寸与分辨率打分
        size_score = min(100.0, (w * h) / (100.0 * 100.0) * 100.0)

        # 3. 正脸比例规整度 (Aspect Ratio)
        aspect = float(w) / float(h)
        if 0.75 <= aspect <= 1.15:
            pose_score = 100.0
        else:
            pose_score = max(20.0, 100.0 - abs(aspect - 0.95) * 160.0)

        # 4. 曝光适度 (Mean Brightness)
        mean_brightness = np.mean(gray)
        if 70 <= mean_brightness <= 190:
            exp_score = 100.0
        else:
            exp_score = max(40.0, 100.0 - abs(mean_brightness - 130) * 1.0)

        total_score = clarity_score * 0.45 + size_score * 0.25 + pose_score * 0.20 + exp_score * 0.10
        return total_score, aspect, face_crop

    def _recognize_person_vlm(self, img_bytes):
        """使用智谱 GLM-4V-Flash 进行双图比对并提取现场视觉情境线索"""
        family_members = []
        if os.path.exists(FAMILY_FACES_PATH):
            try:
                with open(FAMILY_FACES_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list): family_members = data
                    elif isinstance(data, dict): family_members = list(data.values())
            except Exception as e:
                print(f"{TAG} Error loading family faces: {e}")

        primary_owner = family_members[0].get("name", "布布爸爸") if family_members else "布布爸爸"

        ref_b64 = None
        for candidate_file in [f"{primary_owner}.jpg", "布布爸爸.jpg", "face_17616088020_布布爸爸.jpg"]:
            ref_p = os.path.join(FACES_DIR, candidate_file)
            if os.path.exists(ref_p):
                try:
                    with open(ref_p, "rb") as rf:
                        ref_b64 = base64.b64encode(rf.read()).decode("utf-8")
                        break
                except Exception:
                    pass

        try:
            b64_img = base64.b64encode(img_bytes).decode("utf-8")
            url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

            if ref_b64:
                prompt = f"""请对比图 1（家庭主人【{primary_owner}】标准档案照片）与图 2（摄像头抓拍画面）：
1. 观察图 2 中人物的动作、姿态、衣着/神态细节，用简短一句话描述（如'在桌前自拍'、'戴着眼镜神态放松'、'正在忙碌'等）；
2. 比对两张图片中人物的五官面容。只要特征基本吻合，请在最后一行输出：【认定结果：{primary_owner}】；如果完全是无关的陌生外人，输出：【认定结果：访客朋友】。
"""
                content_payload = [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{ref_b64}"}},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}}
                ]
            else:
                prompt = f"""你是一个家庭人脸识别助手。观察眼前的人物，用简短一句话描述其动作或神态。只要与家庭主人【{primary_owner}】吻合，最后一行输出：【认定结果：{primary_owner}】，否则输出：【认定结果：访客朋友】。"""
                content_payload = [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}}
                ]

            payload = {
                "model": "glm-4v-flash",
                "messages": [{"role": "user", "content": content_payload}],
                "temperature": 0.1,
                "max_tokens": 150
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {ZHIPU_API_KEY}"}
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                result_json = json.loads(resp.read().decode("utf-8"))
                content = result_json["choices"][0]["message"]["content"].strip()
                print(f"{TAG} VLM Raw Output: {content}")
                
                # 提取视觉观察描述
                visual_desc = content.split("【认定结果")[0].strip() if "【认定结果" in content else "正在摄像头面前"
                if not visual_desc:
                    visual_desc = "在书桌前停步"

                if f"【认定结果：{primary_owner}】" in content or primary_owner in content:
                    return primary_owner, True, visual_desc
                else:
                    return "访客朋友", False, visual_desc
        except Exception as e:
            print(f"{TAG} VLM face recognize exception: {e}")

        return "访客朋友", False, "在摄像头面前"

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

    def trigger_greeting(self, person_name: str, is_family: bool, quality_status: str, visual_desc: str):
        now = datetime.now()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        weekday_str = WEEKDAYS_MAP[now.weekday()]
        hour = now.hour
        if 5 <= hour < 9:
            time_period = "清晨"
        elif 9 <= hour < 12:
            time_period = "上午"
        elif 12 <= hour < 14:
            time_period = "中午"
        elif 14 <= hour < 18:
            time_period = "下午"
        elif 18 <= hour < 22:
            time_period = "傍晚"
        else:
            time_period = "深夜"

        # 计算上次见面的时间间隔
        last_time = self.config.get("last_global_greeting_time", 0)
        now_ts = time.time()
        elapsed_sec = now_ts - last_time if last_time > 0 else 999999
        if elapsed_sec < 300:
            elapsed_desc = "刚刚才见过（5分钟内）"
        elif elapsed_sec < 3600:
            elapsed_desc = f"距离上次见面约 {int(elapsed_sec // 60)} 分钟"
        elif elapsed_sec < 86400:
            elapsed_desc = f"距离上次见面约 {int(elapsed_sec // 3600)} 小时"
        else:
            elapsed_desc = "今天第一次见面"

        status_desc = {
            "clear_family": f"家庭主人【{person_name}】(已精准确认)",
            "clear_stranger": "新面孔客人(五官看清但不认识)",
            "angled_unclear": "角度偏斜(侧脸未看清正脸)",
            "blurry_unclear": "画面晃动/模糊未看清"
        }.get(quality_status, "访客")

        # ── 构造多模态高情商 Prompt，赋予大模型百变自由创作空间 ──
        if quality_status == "clear_family":
            cue = (
                f"【身份确认】：已精准认出眼前是家庭主人【{person_name}】！\n"
                f"【视觉动作细节】：{visual_desc}。\n"
                f"【时间线索】：现在是 {now_str}（{weekday_str} {time_period}），{elapsed_desc}。\n"
                f"【问候指令】：请像极具情商、幽默且体贴的私人管家小智一样，直呼主人的名字【{person_name}】，"
                f"结合现在的星期、具体时间段、距离上次见面间隔或他的视觉动作，现场创作 1~2 句生动活泼、绝不重复的主动问候语！"
                f"可以灵活在【生活闲聊/幽默调侃/即时关怀/动作细节互动/主动询问需求】等风格中自由发挥。末尾自然抛出一个互动话题。"
            )
        elif quality_status == "clear_stranger":
            cue = (
                f"【身份状态】：五官看得很清楚，但确定不是档案库中的家人（是一位以前没见过的新面孔客人）。\n"
                f"【时间线索】：现在是 {now_str}（{weekday_str} {time_period}）。\n"
                f"【问候指令】：请以有礼貌、热情又得体的管家口吻主动开口，先友好带上一句'咦，看到了一位新面孔呢'类似意思，"
                f"然后礼貌向客人问好并热情询问对方该怎么称呼。语调自然大方。"
            )
        elif quality_status == "angled_unclear":
            cue = (
                f"【身份状态】：感知到有人靠近，但对方角度稍微有点偏只看到了侧影或半边脸。\n"
                f"【问候指令】：请带上一句自然幽默的提示（例如'哎呀，刚才您的角度稍微有点偏，我只看到了侧影'），"
                f"然后再礼貌向对方问好并询问称呼。"
            )
        else:
            cue = (
                f"【身份状态】：感知到有人走近，但刚才镜头晃动或画面有点模糊没有看清五官。\n"
                f"【问候指令】：请带上风趣自嘲的提示（例如'是我眼神不好了吗，刚才画面有点晃没太看清面容'），"
                f"然后再向对方问好并询问称呼。"
            )

        prompt = (
            f"[主动视觉感知唤醒事件]\n"
            f"{cue}\n"
            f"【核心约束】：\n"
            f"1. 绝不要使用机械僵硬的死板套话（严禁千篇一律地只说'今天工作学习辛苦啦'）；\n"
            f"2. 口语化自然亲切，控制在 1~2 句话内，富有灵气与生活温度；\n"
            f"3. 播报完毕后设备将自动开启麦克风进入倾听模式，等待他的自然回答。"
        )

        event = {
            "name": person_name,
            "is_family": is_family,
            "quality_status": quality_status,
            "visual_desc": visual_desc,
            "timestamp": now_str
        }
        history = self.config.setdefault("greeting_history", [])
        history.append(event)
        if len(history) > 50:
            history.pop(0)
        self.save_config()

        wechat_html = f"""
        <div style="font-family: sans-serif; padding: 12px; border-left: 4px solid #4f46e5;">
            <h3 style="color: #1e293b; margin: 0 0 8px 0;">🤖 小智多模态情境哨兵 · 主动迎宾通知</h3>
            <p><strong>识别状态：</strong>{person_name} 【{status_desc}】</p>
            <p><strong>情境细节：</strong>{visual_desc} | {weekday_str} {time_period} | {elapsed_desc}</p>
            <p><strong>触发时间：</strong>{now_str}</p>
        </div>
        """
        self._send_pushplus(f"【小智主动感知】{person_name} ({status_desc})", wechat_html)

        try:
            dispatched = ConnectionRegistry.broadcast_proactive_chat(prompt)
            if dispatched:
                print(f"{TAG} Successfully dispatched dynamic high-EQ proactive prompt to ESP32!")
            else:
                print(f"{TAG} Broadcast failed, no online ESP32 connection.")
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
                continue

            # ── 发现目标，进入多帧动态观察窗口 ──
            print(f"{TAG} [Target Spotted] Entering multi-frame quality observation window...")
            ConnectionRegistry.broadcast_display_message("正在识别中...")

            candidate_frames = []
            obs_start = time.time()
            best_score = 0.0
            best_aspect = 1.0
            best_crop = None
            best_bytes = img_bytes

            q_score, aspect, crop = self._evaluate_face_quality(frame, faces[0])
            candidate_frames.append((q_score, aspect, frame, img_bytes, crop))
            if q_score > best_score:
                best_score = q_score
                best_aspect = aspect
                best_crop = crop
                best_bytes = img_bytes

            while time.time() - obs_start < 1.8:
                time.sleep(0.35)
                f_next, bytes_next = self._grab_camera_frame()
                if f_next is None:
                    continue
                next_faces = self._detect_faces(f_next)
                if len(next_faces) > 0:
                    score_next, aspect_next, crop_next = self._evaluate_face_quality(f_next, next_faces[0])
                    print(f"{TAG} Sampling frame: Face Quality Score = {score_next:.1f}/100, Aspect={aspect_next:.2f}")
                    candidate_frames.append((score_next, aspect_next, f_next, bytes_next, crop_next))
                    if score_next > best_score:
                        best_score = score_next
                        best_aspect = aspect_next
                        best_crop = crop_next
                        best_bytes = bytes_next

                    if score_next >= 85.0 and 0.75 <= aspect_next <= 1.15:
                        print(f"{TAG} High-confidence clear frontal face captured ({score_next:.1f} pts), fast locking!")
                        break

            print(f"{TAG} Observation window finished. Best Quality Score: {best_score:.1f}/100, Aspect: {best_aspect:.2f}")

            person_name = "访客朋友"
            is_family = False
            visual_desc = "在书桌前停步"

            if best_score >= 45.0 and best_bytes:
                person_name, is_family, visual_desc = self._recognize_person_vlm(best_bytes)
                print(f"{TAG} VLM Result: name='{person_name}', is_family={is_family}, visual_desc='{visual_desc}'")

            if is_family:
                quality_status = "clear_family"
            elif best_score >= 45.0 and 0.70 <= best_aspect <= 1.25:
                quality_status = "clear_stranger"
            elif best_aspect < 0.70 or best_aspect > 1.25:
                quality_status = "angled_unclear"
            else:
                quality_status = "blurry_unclear"

            print(f"{TAG} Final Cognitive Status: {quality_status} for {person_name}")

            self.config["last_global_greeting_time"] = time.time()
            self.save_config()
            self.trigger_greeting(person_name, is_family, quality_status, visual_desc)
