import os
import json
import time
import uuid
import asyncio
from datetime import datetime, timedelta
from plugins_func.register import register_function, ToolType, ActionResponse, Action
from config.logger import setup_logging
from typing import TYPE_CHECKING, Dict, List, Any

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

ALARMS_DIR = "data/alarms"
ALARMS_FILE = os.path.join(ALARMS_DIR, "alarms.json")

active_timers: Dict[str, asyncio.Task] = {}
active_device_connections: Dict[str, Any] = {}

def ensure_alarms_file():
    if not os.path.exists(ALARMS_DIR):
        os.makedirs(ALARMS_DIR, exist_ok=True)
    if not os.path.exists(ALARMS_FILE):
        with open(ALARMS_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)

def load_alarms() -> List[Dict[str, Any]]:
    ensure_alarms_file()
    try:
        with open(ALARMS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_alarms(alarms: List[Dict[str, Any]]):
    ensure_alarms_file()
    with open(ALARMS_FILE, "w", encoding="utf-8") as f:
        json.dump(alarms, f, ensure_ascii=False, indent=2)

def has_active_timers(device_id: str = None) -> bool:
    now = datetime.now()
    alarms = load_alarms()
    for a in alarms:
        if a.get("status") == "running":
            if device_id and a.get("device_id") != device_id:
                continue
            try:
                target_time = datetime.strptime(a["target_time"], "%Y-%m-%d %H:%M:%S")
                if target_time > now:
                    return True
            except Exception:
                pass
    return False

def is_conn_alive(c: Any) -> bool:
    if not c:
        return False
    if hasattr(c, "stop_event") and c.stop_event and c.stop_event.is_set():
        return False
    if hasattr(c, "websocket") and c.websocket:
        if getattr(c.websocket, "closed", False):
            return False
        return True
    return True

async def timer_worker(conn: "ConnectionHandler", timer_id: str, duration_sec: int, note: str):
    try:
        start_t = time.time()
        while True:
            elapsed = time.time() - start_t
            remaining = duration_sec - elapsed
            if remaining <= 0:
                break
                
            sleep_chunk = min(15.0, remaining)
            await asyncio.sleep(sleep_chunk)
            
            # 定期向 ESP32 发送心跳包，双向刷新保活计时（防止客户端 120s 自动休眠断开）
            target_conn = conn
            device_id = getattr(conn, "device_id", "default")
            if device_id in active_device_connections:
                candidate = active_device_connections[device_id]
                if is_conn_alive(candidate):
                    target_conn = candidate
                    
            if is_conn_alive(target_conn) and hasattr(target_conn, "websocket") and target_conn.websocket:
                target_conn.last_activity_time = time.time() * 1000
                try:
                    await target_conn.websocket.send(json.dumps({
                        "session_id": getattr(target_conn, "session_id", ""),
                        "type": "heartbeat"
                    }))
                    logger.bind(tag=TAG).debug(f"已向客户端发送倒计时保活心跳: {timer_id} (剩余 {int(remaining)}s)")
                except Exception as send_err:
                    logger.bind(tag=TAG).warning(f"发送心跳失败: {send_err}")

        logger.bind(tag=TAG).info(f"【定时器响铃触发】: {timer_id} - {note}")
        
        target_conn = conn
        device_id = getattr(conn, "device_id", "default")
        if device_id in active_device_connections:
            candidate = active_device_connections[device_id]
            if is_conn_alive(candidate):
                target_conn = candidate
                
        if is_conn_alive(target_conn):
            target_conn.last_activity_time = time.time() * 1000
            target_conn.client_abort = False
            
            from core.handle.receiveAudioHandle import startToChat
            alarm_prompt = f"请你以“叮叮叮，时间到了”为开头，用亲切、响亮、热情的语气提醒用户：您设定的【{note}】时间到了，请记得及时处理哦！"
            await startToChat(target_conn, alarm_prompt)
            logger.bind(tag=TAG).info(f"已触发系统级 startToChat 语音播报: {alarm_prompt}")
        
        all_alarms = load_alarms()
        for a in all_alarms:
            if a.get("id") == timer_id:
                a["status"] = "completed"
                a["completed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_alarms(all_alarms)
        
        if timer_id in active_timers:
            del active_timers[timer_id]
            
    except asyncio.CancelledError:
        logger.bind(tag=TAG).info(f"定时任务已取消: {timer_id}")
    except Exception as e:
        logger.bind(tag=TAG).error(f"定时任务运行异常: {e}")

set_timer_desc = {
    "type": "function",
    "function": {
        "name": "set_countdown_timer",
        "description": "当用户想要设置倒计时或定时提醒时调用此工具。例如用户说：'倒计时5分钟提醒我关火'、'定个10分钟的计时器'、'半小时后叫我'",
        "parameters": {
            "type": "object",
            "properties": {
                "duration_seconds": {
                    "type": "integer",
                    "description": "倒计时的总秒数（例如5分钟=300，10分钟=600，半小时=1800，1小时=3600）"
                },
                "note": {
                    "type": "string",
                    "description": "倒计时的提醒事项或原因，例如'关火'、'泡面好了'、'喝水'、'休息'（默认为'倒计时'）"
                }
            },
            "required": ["duration_seconds"]
        }
    }
}

@register_function("set_countdown_timer", set_timer_desc, ToolType.SYSTEM_CTL)
def set_countdown_timer(conn: "ConnectionHandler", duration_seconds: int, note: str = "倒计时"):
    if not note:
        note = "倒计时"
    duration_seconds = int(duration_seconds)
    if duration_seconds <= 0:
        return ActionResponse(Action.REQLLM, "倒计时时长必须大于0秒", None)
        
    device_id = getattr(conn, "device_id", "default")
    active_device_connections[device_id] = conn
    conn.last_activity_time = time.time() * 1000
    
    now = datetime.now()
    trigger_time = now + timedelta(seconds=duration_seconds)
    timer_id = f"timer_{int(time.time()*1000)}"
    
    if duration_seconds < 60:
        time_display = f"{duration_seconds}秒"
    elif duration_seconds % 60 == 0:
        time_display = f"{duration_seconds // 60}分钟"
    else:
        time_display = f"{duration_seconds // 60}分{duration_seconds % 60}秒"
        
    alarm_item = {
        "id": timer_id,
        "type": "timer",
        "device_id": device_id,
        "note": note,
        "duration_seconds": duration_seconds,
        "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "target_time": trigger_time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "running"
    }
    
    alarms = load_alarms()
    alarms.append(alarm_item)
    save_alarms(alarms)
    
    if hasattr(conn, "loop") and conn.loop and conn.loop.is_running():
        task = asyncio.run_coroutine_threadsafe(timer_worker(conn, timer_id, duration_seconds, note), conn.loop)
    else:
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(timer_worker(conn, timer_id, duration_seconds, note))
        except Exception:
            task = None
            
    active_timers[timer_id] = task
    
    result_text = f"成功设置倒计时！事项：【{note}】，时长：{time_display}，预计到期时间：{trigger_time.strftime('%H:%M:%S')}。请用温暖亲切的语音告诉用户已经帮他开始倒计时了，时间一到会准时语音提醒他。"
    return ActionResponse(Action.REQLLM, result_text, None)

set_alarm_desc = {
    "type": "function",
    "function": {
        "name": "set_alarm_clock",
        "description": "当用户想要设定指定时刻的闹钟或提醒时调用此工具。例如用户说：'定个明天早上7点半的闹钟'、'下午3点提醒我开会'、'晚上8点定个闹钟'",
        "parameters": {
            "type": "object",
            "properties": {
                "time_str": {
                    "type": "string",
                    "description": "闹钟的触发时刻，格式为 HH:MM（如 '07:30'、'15:00'、'20:00'）"
                },
                "note": {
                    "type": "string",
                    "description": "闹钟的提醒内容，例如'起床'、'开会'、'吃药'（默认为'闹钟'）"
                }
            },
            "required": ["time_str"]
        }
    }
}

@register_function("set_alarm_clock", set_alarm_desc, ToolType.SYSTEM_CTL)
def set_alarm_clock(conn: "ConnectionHandler", time_str: str, note: str = "闹钟"):
    if not note:
        note = "闹钟"
    now = datetime.now()
    
    try:
        target_hour, target_minute = map(int, time_str.strip().split(":"))
        target_dt = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
        
        if target_dt <= now:
            target_dt += timedelta(days=1)
            day_desc = "明天"
        else:
            day_desc = "今天"
            
        duration_seconds = int((target_dt - now).total_seconds())
    except Exception as e:
        return ActionResponse(Action.REQLLM, f"时间格式解析错误: {e}，请使用 HH:MM 格式，例如 07:30", None)
        
    device_id = getattr(conn, "device_id", "default")
    active_device_connections[device_id] = conn
    timer_id = f"alarm_{int(time.time()*1000)}"
    
    alarm_item = {
        "id": timer_id,
        "type": "alarm",
        "device_id": device_id,
        "note": note,
        "duration_seconds": duration_seconds,
        "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "target_time": target_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "running"
    }
    
    alarms = load_alarms()
    alarms.append(alarm_item)
    save_alarms(alarms)
    
    if hasattr(conn, "loop") and conn.loop and conn.loop.is_running():
        task = asyncio.run_coroutine_threadsafe(timer_worker(conn, timer_id, duration_seconds, note), conn.loop)
    else:
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(timer_worker(conn, timer_id, duration_seconds, note))
        except Exception:
            task = None
            
    active_timers[timer_id] = task
    
    result_text = f"已成功为您设置闹钟！提醒事项：【{note}】，响铃时间：{day_desc} {target_dt.strftime('%H:%M')}，距离现在还有：约 {duration_seconds // 3600} 小时 {(duration_seconds % 3600) // 60} 分钟。请用亲切自然的语音告知用户闹钟已设置成功。"
    return ActionResponse(Action.REQLLM, result_text, None)

list_timers_desc = {
    "type": "function",
    "function": {
        "name": "list_active_timers",
        "description": "当用户想要查看、查询当前已设置了哪些闹钟、倒计时或定时提醒时调用此工具。",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
}

@register_function("list_active_timers", list_timers_desc, ToolType.WAIT)
def list_active_timers():
    alarms = load_alarms()
    now = datetime.now()
    
    active_list = []
    for a in alarms:
        if a.get("status") == "running":
            target_time = datetime.strptime(a["target_time"], "%Y-%m-%d %H:%M:%S")
            remaining = int((target_time - now).total_seconds())
            if remaining > 0:
                a["remaining_seconds"] = remaining
                active_list.append(a)
            else:
                a["status"] = "completed"
                
    save_alarms(alarms)
    
    if not active_list:
        return ActionResponse(Action.REQLLM, "您当前没有正在运行的闹钟或倒计时。", None)
        
    res = f"您当前共有 {len(active_list)} 个正在运行的提醒任务：\n"
    for idx, a in enumerate(active_list, 1):
        rem = a["remaining_seconds"]
        if rem >= 3600:
            rem_str = f"{rem // 3600}小时{(rem % 3600) // 60}分"
        elif rem >= 60:
            rem_str = f"{rem // 60}分{rem % 60}秒"
        else:
            rem_str = f"{rem}秒"
            
        t_type = "倒计时" if a.get("type") == "timer" else "闹钟"
        res += f"{idx}. 【{t_type}】{a.get('note')}：将于 {a.get('target_time')} 响铃（还剩 {rem_str}）\n"
        
    res += "\n请为用户语音汇报当前运行的闹钟和倒计时状态。"
    return ActionResponse(Action.REQLLM, res, None)

cancel_timer_desc = {
    "type": "function",
    "function": {
        "name": "cancel_timer_or_alarm",
        "description": "当用户想要取消、关闭、删除某个闹钟或倒计时时调用此工具。",
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "要取消的闹钟或倒计时关键词，例如'关火'、'起床'、'刚才的倒计时'、'全部取消'"
                }
            },
            "required": ["keyword"]
        }
    }
}

@register_function("cancel_timer_or_alarm", cancel_timer_desc, ToolType.WAIT)
def cancel_timer_or_alarm(keyword: str):
    alarms = load_alarms()
    cancelled_count = 0
    cancelled_notes = []
    
    for a in alarms:
        if a.get("status") == "running":
            t_id = a.get("id")
            if "全部" in keyword or "所有" in keyword or keyword in a.get("note", "") or "刚才" in keyword:
                a["status"] = "cancelled"
                if t_id in active_timers:
                    t = active_timers[t_id]
                    if hasattr(t, "cancel"):
                        t.cancel()
                    del active_timers[t_id]
                cancelled_count += 1
                cancelled_notes.append(a.get("note", "提醒"))
                
    save_alarms(alarms)
    
    if cancelled_count > 0:
        return ActionResponse(Action.REQLLM, f"已成功为您取消了 {cancelled_count} 个提醒任务（{', '.join(cancelled_notes)}）。请语音确认取消成功。", None)
    else:
        return ActionResponse(Action.REQLLM, f"未能找到与'{keyword}'匹配的正在运行的闹钟或倒计时。", None)

# ================= 5. 测试闹钟声音 =================
test_alarm_desc = {
    "type": "function",
    "function": {
        "name": "test_alarm_sound",
        "description": "当用户想要测试闹钟声音、试听提醒效果或检查扬声器能否听到倒计时播报时调用此工具。",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
}

@register_function("test_alarm_sound", test_alarm_desc, ToolType.WAIT)
def test_alarm_sound():
    return ActionResponse(
        Action.REQLLM,
        "请立即以极其响亮、欢快、清晰的语气向用户语音播报：'🔔 叮叮叮！时间到了！时间到了！这是一条闹钟与倒计时测试播报，您设定的【吃药】提醒时间到了，请记得及时吃药哦！请问您现在能清晰听到我的声音吗？'",
        None
    )
