import os
import time
import json
import base64
import re
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
MODELS_DIR = os.path.join(DATA_DIR, "models")
YUNET_PATH = os.path.join(MODELS_DIR, "face_detection_yunet_2023mar.onnx")

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
            "detector_type": "deep_learning_onnx",
            "absence_threshold_sec": 45,      # 连续 45 秒未检测到正面即进入离席准备
            "min_reentry_cooldown_sec": 30,   # 触发后 30 秒内静默
            "reengage_timeout_sec": 7200,     # 同一人 2 小时内重看镜头仅轻咳应答，不长篇大论
            "wechat_notify": True,
            "greet_stranger": True,
            "last_global_greeting_time": 0,
            "greeting_history": []
        }
        self.load_config()
        self.status = "monitoring"
        
        # 状态机: "ABSENT" (离席无人) | "PRESENT" (在场伴随静默) | "LEAVING" (离开过渡) | "ON_CALL" (通话免打扰)
        self.presence_state = "ABSENT"
        self.last_seen_time = 0
        self.last_unseen_time = time.time()
        self.last_check_time = 0
        
        # 告别与通话免打扰保护锁 (时间戳)
        self.post_exit_mute_until = 0
        
        # 身份与同人重入记忆
        self.last_greeted_person = None
        self.last_full_greeting_time = 0
        
        # 初始化检测器 (ONNX 深度学习 + Haar 级联双引擎)
        self.dnn_detector = None
        self.dnn_target_size = (640, 360)
        self.cascades = []
        self._init_detectors()
        
        self.worker_thread = threading.Thread(target=self._run_loop, daemon=True)
        self.worker_thread.start()
        print(f"{TAG} v2.4.0 Sentinel Started: ONNX Deep Learning Detector & Call Perception Active.")

    def set_post_exit_cooldown(self, seconds=300):
        """用户主动告别/退出/打电话后，进入 5 分钟静默保护期，绝不主动打扰"""
        self.post_exit_mute_until = time.time() + seconds
        self.presence_state = "PRESENT"
        self.status = "post_exit_silent"
        print(f"{TAG} Post-exit DND activated for {seconds}s (until {datetime.fromtimestamp(self.post_exit_mute_until).strftime('%H:%M:%S')})")

    def _init_detectors(self):
        # 1. 深度学习 ONNX 人脸检测引擎 (YuNet)
        if os.path.exists(YUNET_PATH) and hasattr(cv2, "FaceDetectorYN"):
            try:
                self.dnn_detector = cv2.FaceDetectorYN.create(
                    YUNET_PATH,
                    "",
                    self.dnn_target_size,
                    score_threshold=0.65,
                    nms_threshold=0.3,
                    top_k=5000
                )
                print(f"{TAG} [DeepLearning] Initialized ONNX YuNet Face Detector from {YUNET_PATH}")
            except Exception as e:
                print(f"{TAG} [DeepLearning] YuNet load error: {e}")

        # 2. Haar 级联后备引擎
        paths = [
            "/usr/local/lib/python3.10/site-packages/cv2/data/haarcascade_frontalface_alt2.xml",
            "/usr/local/lib/python3.10/site-packages/cv2/data/haarcascade_profileface.xml",
        ]
        for p in paths:
            if os.path.exists(p):
                try:
                    c = cv2.CascadeClassifier(p)
                    self.cascades.append(c)
                except Exception:
                    pass

    def load_config(self):
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    self.config.update(saved)
            except Exception as e:
                print(f"{TAG} Failed to load config: {e}")

    def get_status(self):
        now_ts = time.time()
        absence_sec = int(now_ts - self.last_seen_time) if self.last_seen_time > 0 else 9999
        dnd_remaining = max(0, int(self.post_exit_mute_until - now_ts))
        return {
            "enabled": self.config.get("enabled", True),
            "status": getattr(self, "status", "monitoring"),
            "presence_state": getattr(self, "presence_state", "ABSENT"),
            "detector_engine": "ONNX_DeepLearning_YuNet" if self.dnn_detector else "Haar_Cascade",
            "dnd_remaining_seconds": dnd_remaining,
            "last_greeted_person": getattr(self, "last_greeted_person", None),
            "absence_seconds": absence_sec,
            "last_seen_time": getattr(self, "last_seen_time", 0),
            "last_check_time": getattr(self, "last_check_time", 0),
            "config": self.config
        }

    def update_config(self, new_cfg: dict):
        self.config.update(new_cfg)
        self.save_config()
        return self.get_status()

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
        """深度学习 ONNX 人脸检测（YuNet 高速推理与高召回率）"""
        if frame is None:
            return []
        H, W, _ = frame.shape

        # ── 1. 首选 ONNX 深度学习引擎 ──
        if self.dnn_detector is not None:
            try:
                target_w = 640
                target_h = int(640 * H / W)
                resized = cv2.resize(frame, (target_w, target_h))
                scale_x, scale_y = W / target_w, H / target_h
                
                self.dnn_detector.setInputSize((target_w, target_h))
                _, faces = self.dnn_detector.detect(resized)
                
                detected = []
                if faces is not None:
                    for f in faces:
                        score = float(f[-1])
                        if score >= 0.60:
                            x = int(f[0] * scale_x)
                            y = int(f[1] * scale_y)
                            w = int(f[2] * scale_x)
                            h = int(f[3] * scale_y)
                            if w >= 40 and h >= 40:
                                detected.append((x, y, w, h))
                return detected
            except Exception as e:
                print(f"{TAG} ONNX detect error: {e}")

        # ── 2. 备用 Haar 级联检测 ──
        try:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = []
            if self.cascades:
                c_front = self.cascades[0]
                dets = c_front.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=3, minSize=(60, 60))
                for f in dets:
                    faces.append((int(f[0]), int(f[1]), int(f[2]), int(f[3])))
            return faces
        except Exception as e:
            print(f"{TAG} Cascade fallback error: {e}")
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
        """使用智谱 GLM-4V-Flash 进行多模态动作姿态与通话判定"""
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
1. 观察图 2 中人物动作与姿态（特别注意：是否手持手机在耳边接打电话、是否戴着耳机通话中、是否正在看手机），用简短一句话描述；
2. 判定结果输出规则：
   - 若画面中人物手持手机在耳边打电话或明显正在语音通话，最后一行必须输出：【认定结果：正在打电话】；
   - 若根本没有人脸或只是静物被褥，输出：【认定结果：无人】；
   - 若特征与图1吻合且未在打电话，输出：【认定结果：{primary_owner}】；
   - 若是陌生面孔且未在打电话，输出：【认定结果：访客朋友】。
"""
                content_payload = [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{ref_b64}"}},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}}
                ]
            else:
                prompt = f"""观察眼前的画面：若人物正在手持手机耳边打电话，输出【认定结果：正在打电话】；若为主人【{primary_owner}】，输出【认定结果：{primary_owner}】；否则输出【认定结果：访客朋友】。"""
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
                
                raw_desc = content.split("【认定结果")[0].strip() if "【认定结果" in content else "正在摄像头面前"
                visual_desc = re.sub(r"^\d+[\.\、\s]*", "", raw_desc).strip()
                visual_desc = re.sub(r"\n+\d+[\.\、\s]*$", "", visual_desc).strip()
                if not visual_desc:
                    visual_desc = "在书桌前停步"

                if "【认定结果：正在打电话】" in content or "正在打电话" in visual_desc or "耳边打电话" in visual_desc:
                    return "正在打电话", True, "正在手持电话通话中"
                elif "【认定结果：无人】" in content or ("无人" in content and primary_owner not in content):
                    return "无人", False, "静物/无人"
                elif f"【认定结果：{primary_owner}】" in content or primary_owner in content:
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

    def trigger_greeting(self, person_name: str, is_family: bool, quality_status: str, visual_desc: str, is_reengage: bool = False):
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

        if is_reengage and is_family:
            prompt = (
                f"[视觉感知就绪事件]\n"
                f"【情境】：家庭主人【{person_name}】刚才已打过招呼，现在依然在座或告别后再次看向镜头。\n"
                f"【社交指令】：无需重复任何长句问候！请只发出极其简短自然的一声轻咳或提示（严格回复：'嗯，在呢~' 或 '咳，在呢~'，严格限制在 4 个字以内）。\n"
                f"【核心约束】：严禁多说任何多余废话！短语播报完毕后设备将立刻自动开麦倾听主人的语音指令。"
            )
        else:
            if quality_status == "clear_family":
                cue = (
                    f"[主动视觉感知轻量问候]\n"
                    f"【身份确认】：已看到家庭主人【{person_name}】！\n"
                    f"【视觉动作】：{visual_desc}。\n"
                    f"【时间线索】：现在是 {now_str}（{weekday_str} {time_period}）。\n"
                    f"【社交规则指令】：请用极为轻量、自然、温暖的一句话向【{person_name}】问好（严格限制在 1 句话，15字以内，例如：'{person_name}好呀~'、'{person_name}晚上好呀~' 或 '{person_name}还在忙呢~'）。\n"
                    f"【核心约束】：绝对禁止长篇大论或连珠炮式提问！简短问候后你将安静等待主人的反应。知分寸、不喧宾夺主。"
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
            "is_reengage": is_reengage,
            "quality_status": quality_status,
            "visual_desc": visual_desc,
            "timestamp": now_str
        }
        history = self.config.setdefault("greeting_history", [])
        history.append(event)
        if len(history) > 50:
            history.pop(0)
        self.save_config()

        if not is_reengage:
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
                print(f"{TAG} Successfully dispatched {'[Re-engage Cough/Listen]' if is_reengage else '[Full Greeting]'} prompt to ESP32!")
            else:
                print(f"{TAG} Broadcast failed, no online ESP32 connection.")
        except Exception as e:
            print(f"{TAG} Broadcast chat error: {e}")

        return event

    def _run_loop(self):
        """持续在场状态机驱动与多模态通话静默守护主循环"""
        while True:
            time.sleep(self.config.get("check_interval", 0.5))
            if not self.config.get("enabled", True):
                self.status = "paused"
                continue

            now_ts = time.time()

            # ── 1. 检查告别/通话 5 分钟免打扰静默保护锁 ──
            if getattr(self, "post_exit_mute_until", 0) > now_ts:
                self.status = "post_exit_silent"
                continue

            frame, img_bytes = self._grab_camera_frame()
            self.last_check_time = now_ts
            if frame is None:
                self.status = "camera_offline"
                continue

            faces = self._detect_faces(frame)
            has_face = len(faces) > 0

            absence_threshold = self.config.get("absence_threshold_sec", 45)
            reentry_cooldown = self.config.get("min_reentry_cooldown_sec", 30)
            reengage_timeout = self.config.get("reengage_timeout_sec", 7200)

            # ── 2. 状态变迁处理 ──
            if has_face:
                self.last_seen_time = now_ts
                
                # 如果此前一直处于常驻伴随状态（PRESENT），继续保持静默陪伴
                if self.presence_state == "PRESENT":
                    self.status = "present_silent"
                    continue
                elif self.presence_state == "ON_CALL":
                    self.status = "on_call_silent"
                    continue
                elif self.presence_state == "LEAVING":
                    # 短暂低头/打字（<45秒）即回到视野，平滑恢复为常驻伴随，不打扰
                    self.presence_state = "PRESENT"
                    self.status = "present_silent"
                    continue

                # 只有此前处于 ABSENT（完全无人/离席超时）状态时，才进行视觉激活判定
                elif self.presence_state == "ABSENT":
                    last_global = self.config.get("last_global_greeting_time", 0)
                    if now_ts - last_global < reentry_cooldown:
                        self.status = "cooldown_waiting"
                        continue

                    # ── 发现目标，进入多帧动态观察窗口 ──
                    print(f"{TAG} [Target Spotted] Starting multi-frame observation window with ONNX detector...")
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

                    while time.time() - obs_start < 1.6:
                        time.sleep(0.3)
                        f_next, bytes_next = self._grab_camera_frame()
                        if f_next is None:
                            continue
                        next_faces = self._detect_faces(f_next)
                        if len(next_faces) > 0:
                            score_next, aspect_next, crop_next = self._evaluate_face_quality(f_next, next_faces[0])
                            candidate_frames.append((score_next, aspect_next, f_next, bytes_next, crop_next))
                            if score_next > best_score:
                                best_score = score_next
                                best_aspect = aspect_next
                                best_crop = crop_next
                                best_bytes = bytes_next

                            if score_next >= 80.0 and 0.75 <= aspect_next <= 1.15:
                                break

                    print(f"{TAG} Observation window finished. Best Quality Score: {best_score:.1f}/100, Aspect: {best_aspect:.2f}")

                    person_name = "访客朋友"
                    is_family = False
                    visual_desc = "在书桌前停步"

                    if best_score >= 40.0 and best_bytes:
                        person_name, is_family, visual_desc = self._recognize_person_vlm(best_bytes)
                        print(f"{TAG} VLM Result: name='{person_name}', is_family={is_family}, visual_desc='{visual_desc}'")
                        
                        # ── 若视觉检测到正在打电话，启动通话免打扰保护，绝对不发声！──
                        if person_name == "正在打电话":
                            print(f"{TAG} User is on a phone call. Activating ON_CALL silent protection.")
                            self.presence_state = "ON_CALL"
                            self.status = "on_call_silent"
                            continue
                        
                        if person_name == "无人":
                            print(f"{TAG} False positive (empty room / inanimate object). Muting.")
                            self.presence_state = "ABSENT"
                            self.status = "monitoring"
                            continue

                    if is_family:
                        quality_status = "clear_family"
                    elif best_score >= 40.0 and 0.70 <= best_aspect <= 1.25:
                        quality_status = "clear_stranger"
                    elif best_aspect < 0.70 or best_aspect > 1.25:
                        quality_status = "angled_unclear"
                    else:
                        quality_status = "blurry_unclear"

                    # ── 判定是否为同一人近期已打过招呼（重入/告别后再次看镜头）──
                    is_reengage = False
                    if is_family and self.last_greeted_person == person_name and (now_ts - self.last_full_greeting_time < reengage_timeout):
                        is_reengage = True
                        print(f"{TAG} Same person ({person_name}) re-engaged within {int(now_ts - self.last_full_greeting_time)}s. Using subtle cough/listen cue.")
                    else:
                        self.last_greeted_person = person_name
                        self.last_full_greeting_time = now_ts

                    self.config["last_global_greeting_time"] = time.time()
                    self.save_config()
                    self.presence_state = "PRESENT"
                    self.status = "present_silent"
                    self.trigger_greeting(person_name, is_family, quality_status, visual_desc, is_reengage=is_reengage)

            else:
                # ── 画面中无人脸 ──
                if self.presence_state in ["PRESENT", "ON_CALL"]:
                    self.presence_state = "LEAVING"
                    self.last_unseen_time = now_ts
                    self.status = "leaving_monitoring"
                    print(f"{TAG} Person not seen. Entering LEAVING state (will confirm ABSENT after {absence_threshold}s)...")
                elif self.presence_state == "LEAVING":
                    if now_ts - self.last_unseen_time >= absence_threshold:
                        self.presence_state = "ABSENT"
                        self.status = "monitoring"
                        print(f"{TAG} Absence threshold reached ({absence_threshold}s). State transitioned to ABSENT.")
                else:
                    self.status = "monitoring"
