import os
import time
import json
import base64
import math
import re
import threading
import urllib.request
import urllib.error
import cv2
import numpy as np
from datetime import datetime
from core.utils.connection_registry import ConnectionRegistry

TAG = "[FallDetector]"

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
CONFIG_PATH = os.path.join(DATA_DIR, "fall_config.json")
EVENTS_PATH = os.path.join(DATA_DIR, "fall_events.json")
MODELS_DIR = os.path.join(DATA_DIR, "models")
PERSON_DET_PATH = os.path.join(MODELS_DIR, "person_detection_mediapipe_2023mar.onnx")
POSE_EST_PATH = os.path.join(MODELS_DIR, "pose_estimation_mediapipe_2023mar.onnx")

PUSHPLUS_TOKEN = "35c9b21d51cf40978f0e450c4755c73b"
ZHIPU_API_KEY = "fd04fb160360497291b1ae87596dbde9.ID3C9TfZTgTd3W9h"

class FallDetector:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(FallDetector, cls).__new__(cls)
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
            "aspect_ratio_threshold": 0.70,   # 身体 H/W 宽高比低于 0.7 判定为卧倒
            "torso_angle_threshold": 35.0,    # 躯干与水平面夹角小于 35 度
            "ground_dwell_seconds": 8.0,      # 地面卧倒持续 8 秒不起来才判定为疑似跌倒（防捡物误报）
            "vlm_verification": True,          # 启用智谱 GLM-4V 大模型二次语义复核
            "alert_cooldown_seconds": 120,     # 报警后冷却期 120 秒
            "wechat_notify": True,
            "last_alert_time": 0
        }
        self.load_config()
        self.status = "monitoring"
        self.fall_state = "NORMAL"            # NORMAL | SUSPECTED_FALL | CONFIRMED_FALL | ALERTED
        self.fall_start_time = 0
        self.last_check_time = 0
        self.current_posture = "无目标"
        self.last_metrics = {}
        
        self.person_detector = None
        self.pose_estimator = None
        self._init_models()

        self.worker_thread = threading.Thread(target=self._run_loop, daemon=True)
        self.worker_thread.start()
        print(f"{TAG} Elderly Fall & Danger Action Detection Service Started.")

    def _init_models(self):
        try:
            from core.services.mp_persondet import MPPersonDet
            if os.path.exists(PERSON_DET_PATH):
                self.person_detector = MPPersonDet(modelPath=PERSON_DET_PATH, scoreThreshold=0.45)
                print(f"{TAG} Initialized Person Detection ONNX from {PERSON_DET_PATH}")
        except Exception as e:
            print(f"{TAG} Failed to init MPPersonDet: {e}")

        try:
            from core.services.mp_pose import MPPose
            if os.path.exists(POSE_EST_PATH):
                self.pose_estimator = MPPose(modelPath=POSE_EST_PATH, confThreshold=0.45)
                print(f"{TAG} Initialized Pose Estimation ONNX from {POSE_EST_PATH}")
        except Exception as e:
            print(f"{TAG} Failed to init MPPose: {e}")

    def load_config(self):
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    self.config.update(saved)
            except Exception as e:
                print(f"{TAG} Load config error: {e}")

    def save_config(self):
        try:
            os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"{TAG} Save config error: {e}")

    def get_status(self):
        now_ts = time.time()
        cooldown_rem = max(0, int(self.config.get("last_alert_time", 0) + self.config.get("alert_cooldown_seconds", 120) - now_ts))
        fall_dwell = int(now_ts - self.fall_start_time) if self.fall_start_time > 0 else 0
        return {
            "enabled": self.config.get("enabled", True),
            "status": self.status,
            "fall_state": self.fall_state,
            "current_posture": self.current_posture,
            "fall_dwell_seconds": fall_dwell,
            "cooldown_remaining_seconds": cooldown_rem,
            "last_metrics": self.last_metrics,
            "last_check_time": self.last_check_time,
            "config": self.config
        }

    def update_config(self, new_cfg: dict):
        self.config.update(new_cfg)
        self.save_config()
        return self.get_status()

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

    def _verify_with_vlm(self, img_bytes: bytes) -> tuple:
        """调用智谱 GLM-4V-Flash 对疑似跌倒画面进行紧急多模态语义复核"""
        try:
            b64_img = base64.b64encode(img_bytes).decode("utf-8")
            url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
            prompt = """【紧急健康看护识别】
请仔细观察画面中人物的身体姿态、所处空间与动作：
1. 观察人物是发生了摔倒倒地不起、滑倒在地面、跌落昏迷、或趴在地上无力起身？
2. 还是正常在床上/沙发上睡觉休息、坐在地毯上、弯腰捡东西或做拉伸运动？
3. 判定输出规则（最后一行必须严格输出）：
   - 若确系摔倒在地不起等意外险情，最后一行输出：【判定结论：跌倒险情】；
   - 若属于正常躺床睡觉、坐卧休息、或正常日常活动，最后一行输出：【判定结论：正常安全】。
"""
            payload = {
                "model": "glm-4v-flash",
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}}
                ]}],
                "temperature": 0.1,
                "max_tokens": 150
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {ZHIPU_API_KEY}"}
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                content = result["choices"][0]["message"]["content"].strip()
                print(f"{TAG} VLM Fall Verification Raw: {content}")
                
                conclusion_match = re.search(r"【判定结论[：:]\s*(.*?)】", content)
                conclusion = conclusion_match.group(1).strip() if conclusion_match else ""
                
                raw_desc = content.split("【判定结论")[0].strip() if "【判定结论" in content else content
                desc = raw_desc.split("\n")[0].strip()
                
                if "跌倒险情" in conclusion or conclusion == "跌倒险情":
                    return True, desc
                else:
                    return False, desc
        except Exception as e:
            print(f"{TAG} VLM verification exception: {e}")
            # 若 VLM 异常超时，保守放行（宁可误报，不可漏报）
            return True, "疑似倒地，视觉复核超时，保守告警"

    def _send_pushplus_alert(self, title: str, html_body: str):
        if not self.config.get("wechat_notify", True) or not PUSHPLUS_TOKEN:
            return
        try:
            payload = {
                "token": PUSHPLUS_TOKEN,
                "title": title,
                "content": html_body,
                "template": "html"
            }
            req = urllib.request.Request(
                "http://www.pushplus.plus/send",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            urllib.request.urlopen(req, timeout=3.0)
            print(f"{TAG} PushPlus emergency notification sent: {title}")
        except Exception as e:
            print(f"{TAG} PushPlus error: {e}")

    def trigger_emergency_fall_alert(self, visual_desc: str, metrics: dict):
        """执行全通道紧急响应：音箱现场关怀大声呼问 + 微信紧急穿透推送"""
        now = datetime.now()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        self.config["last_alert_time"] = time.time()
        self.save_config()

        # 1. 记录事件历史
        event = {
            "type": "FALL_DETECTED",
            "timestamp": now_str,
            "description": visual_desc,
            "metrics": metrics
        }
        try:
            history = []
            if os.path.exists(EVENTS_PATH):
                with open(EVENTS_PATH, "r", encoding="utf-8") as f:
                    history = json.load(f)
            history.append(event)
            if len(history) > 30:
                history.pop(0)
            with open(EVENTS_PATH, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"{TAG} Save fall event history error: {e}")

        # 2. 微信强提醒卡片
        html_card = f"""
        <div style="font-family: sans-serif; padding: 16px; border-left: 6px solid #dc2626; background: #fef2f2; border-radius: 6px;">
            <h2 style="color: #991b1b; margin-top: 0; font-size: 18px;">🚨【紧急看护告警】检测到老人疑似跌倒！</h2>
            <p style="color: #374151; font-size: 14px;"><strong>发生时间：</strong>{now_str}</p>
            <p style="color: #374151; font-size: 14px;"><strong>现场分析：</strong>{visual_desc}</p>
            <p style="color: #374151; font-size: 14px;"><strong>身体姿态指标：</strong>纵横比={metrics.get('aspect_ratio', 0):.2f} (身体呈水平卧倒，持续超过8秒未起身)</p>
            <div style="background: #fee2e2; padding: 10px; border-radius: 4px; margin-top: 12px;">
                <p style="color: #b91c1c; font-weight: bold; margin: 0;">⚠️ ESP32 音箱已自动在现场提高音量询问老人并开启呼救监听，请家属立刻查看监控或致电确认安全！</p>
            </div>
        </div>
        """
        self._send_pushplus_alert("🚨【紧急求助】老人疑似跌倒卧地不起！", html_card)

        # 3. 调度 ESP32 音箱现场大声问询并倾听反馈
        emergency_prompt = (
            "[紧急跌倒问候事件]\n"
            "【现场险情】：视觉看护中枢检测到房间老人发生身体卧倒且在地表超过8秒未能起身！\n"
            "【交互指令】：请以极为关切、响亮清晰、沉稳温暖的声音立刻大声呼唤询问："
            "'爷爷，您摔倒了吗？能听到我说话吗？如果需要帮助请大声回答我！'\n"
            "【核心约束】：播报完毕后立刻长开麦克风倾听现场是否有老人的呼救声或呻吟声！"
        )
        try:
            dispatched = ConnectionRegistry.broadcast_proactive_chat(emergency_prompt)
            if dispatched:
                print(f"{TAG} Successfully dispatched emergency inquiry to ESP32 speaker!")
            else:
                print(f"{TAG} No online ESP32 connection for speaker broadcast.")
        except Exception as e:
            print(f"{TAG} Dispatch emergency speech error: {e}")

    def _run_loop(self):
        """跌倒看护持续抽帧与多维动力学研判主循环"""
        while True:
            time.sleep(self.config.get("check_interval", 0.5))
            if not self.config.get("enabled", True):
                self.status = "paused"
                self.fall_state = "NORMAL"
                self.fall_start_time = 0
                continue

            now_ts = time.time()
            self.last_check_time = now_ts

            # 检查报警后冷却期
            cooldown_sec = self.config.get("alert_cooldown_seconds", 120)
            last_alert = self.config.get("last_alert_time", 0)
            if now_ts - last_alert < cooldown_sec:
                self.status = "alert_cooldown"
                continue

            frame, img_bytes = self._grab_camera_frame()
            if frame is None:
                self.status = "camera_offline"
                continue

            H_img, W_img, _ = frame.shape
            is_horizontal_lying = False
            current_aspect = 1.0
            current_angle = 90.0
            centroid_y_norm = 0.5

            # ── 1. 人体检测与姿态分析 ──
            if self.person_detector is not None:
                try:
                    persons = self.person_detector.infer(frame)
                    if persons is not None and len(persons) > 0:
                        # 找到画面中最大的人体目标
                        largest_p = max(persons, key=lambda p: p[2] * p[3])
                        x, y, w, h = largest_p[0:4]
                        score = largest_p[-1]
                        
                        if w > 30 and h > 30:
                            current_aspect = float(h) / max(1.0, float(w))
                            centroid_y_norm = (y + h / 2.0) / float(H_img)
                            
                            # 尝试获取人体姿态骨骼关键点
                            torso_angle = 90.0
                            if self.pose_estimator is not None:
                                try:
                                    res = self.pose_estimator.infer(frame, largest_p)
                                    if res is not None:
                                        landmarks = res[1] # 33 keypoints
                                        # 计算肩部中心与臀部中心
                                        l_sh, r_sh = landmarks[11][:2], landmarks[12][:2]
                                        l_hip, r_hip = landmarks[23][:2], landmarks[24][:2]
                                        sh_c = (l_sh + r_sh) / 2.0
                                        hip_c = (l_hip + r_hip) / 2.0
                                        dx = abs(hip_c[0] - sh_c[0])
                                        dy = abs(hip_c[1] - sh_c[1])
                                        torso_angle = math.degrees(math.atan2(dy, max(1.0, dx)))
                                        current_angle = torso_angle
                                except Exception:
                                    pass

                            # 判定是否处于水平卧倒姿态：
                            # 1) H/W 宽高比低于阈值（如 0.70）
                            # 2) 或躯干角度小于 35 度，且身体处于画面中下层
                            if (current_aspect <= self.config.get("aspect_ratio_threshold", 0.70) or torso_angle <= self.config.get("torso_angle_threshold", 35.0)) and centroid_y_norm > 0.40:
                                is_horizontal_lying = True
                                self.current_posture = "水平卧倒/地表倒地"
                            elif current_aspect >= 1.2:
                                self.current_posture = "直立/走动"
                            else:
                                self.current_posture = "坐姿/活动"
                    else:
                        self.current_posture = "画面无人"
                except Exception as e:
                    print(f"{TAG} Detection exception: {e}")

            self.last_metrics = {
                "aspect_ratio": current_aspect,
                "torso_angle": current_angle,
                "centroid_y": centroid_y_norm,
                "posture": self.current_posture
            }

            # ── 2. 空间动力学与地表滞留状态机 ──
            dwell_threshold = self.config.get("ground_dwell_seconds", 8.0)

            if is_horizontal_lying:
                if self.fall_start_time == 0:
                    self.fall_start_time = now_ts
                    self.fall_state = "SUSPECTED_FALL"
                    self.status = "confirming_fall"
                    print(f"{TAG} Horizontal posture spotted! Starting {dwell_threshold}s confirmation timer...")
                else:
                    elapsed = now_ts - self.fall_start_time
                    if elapsed >= dwell_threshold:
                        print(f"{TAG} Floor dwell exceeded {dwell_threshold}s ({elapsed:.1f}s)! Transitioning to CONFIRMED_FALL.")
                        self.fall_state = "CONFIRMED_FALL"
                        self.status = "verifying_vlm"
                        
                        # 触发大模型二次复核
                        is_fall = True
                        desc = "身体水平卧倒超过8秒未见起身"
                        if self.config.get("vlm_verification", True) and img_bytes:
                            is_fall, desc = self._verify_with_vlm(img_bytes)
                            print(f"{TAG} VLM Verification Result: is_fall={is_fall}, desc='{desc}'")

                        if is_fall:
                            self.fall_state = "ALERTED"
                            self.status = "alert_dispatched"
                            self.trigger_emergency_fall_alert(desc, self.last_metrics)
                            self.fall_start_time = 0
                        else:
                            print(f"{TAG} False alarm suppressed by VLM (normal bed/couch rest or activity).")
                            self.fall_state = "NORMAL"
                            self.status = "monitoring"
                            self.fall_start_time = 0
                    else:
                        self.status = f"confirming_fall ({int(dwell_threshold - elapsed)}s left)"
            else:
                # 恢复站立或坐姿，平滑重置
                if self.fall_start_time > 0:
                    print(f"{TAG} Person recovered to standing/sitting. Resetting fall timer.")
                self.fall_start_time = 0
                self.fall_state = "NORMAL"
                self.status = "monitoring"
