import os
import json
import ssl
import smtplib
import traceback
import urllib.request
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from email.header import Header
from plugins_func.register import register_function, ToolType, ActionResponse, Action
from config.logger import setup_logging

TAG = "plugins.send_email"
logger = setup_logging()

EMAIL_DATA_DIR = "data/email"
ATTACHMENTS_DIR = os.path.join(EMAIL_DATA_DIR, "attachments")
CONFIG_FILE = os.path.join(EMAIL_DATA_DIR, "smtp_config.json")
CONTACTS_FILE = os.path.join(EMAIL_DATA_DIR, "contacts.json")
SENT_HISTORY_FILE = os.path.join(EMAIL_DATA_DIR, "sent_history.json")

S20_CAMERA_URL = "http://100.122.149.94:8080/shot.jpg"

DEFAULT_CONFIG = {
    "smtp_server": "smtp.office365.com",
    "smtp_port": 587,
    "use_tls": True,
    "email_user": "maomao@2ygwql.onmicrosoft.com",
    "email_pass": "Miaomiao11miaomiao",
    "sender_name": "小智 AI 语音助手"
}

DEFAULT_CONTACTS = {
    "self": "dujingt@gmail.com",
    "我": "dujingt@gmail.com",
    "自己": "dujingt@gmail.com",
    "我的邮箱": "dujingt@gmail.com",
    "布布爸爸": "dujingt@gmail.com",
    "杜靖涛": "dujingt@gmail.com",
    "dujingt": "dujingt@gmail.com",
    "maomao": "maomao@2ygwql.onmicrosoft.com"
}

def ensure_dirs():
    os.makedirs(EMAIL_DATA_DIR, exist_ok=True)
    os.makedirs(ATTACHMENTS_DIR, exist_ok=True)
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
    if not os.path.exists(CONTACTS_FILE):
        with open(CONTACTS_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONTACTS, f, ensure_ascii=False, indent=2)
    if not os.path.exists(SENT_HISTORY_FILE):
        with open(SENT_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)

def load_smtp_config():
    ensure_dirs()
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return DEFAULT_CONFIG

def load_contacts():
    ensure_dirs()
    try:
        with open(CONTACTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return DEFAULT_CONTACTS

def save_sent_history(record):
    ensure_dirs()
    history = []
    try:
        with open(SENT_HISTORY_FILE, "r", encoding="utf-8") as f:
            history = json.load(f)
    except Exception:
        history = []
    
    history.append(record)
    with open(SENT_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

def resolve_recipient_email(recipient_str: str) -> str:
    contacts = load_contacts()
    r = str(recipient_str).strip().lower()
    if r in contacts:
        return contacts[r]
    for k, v in contacts.items():
        if k.lower() in r or r in k.lower():
            return v
    if "@" in recipient_str:
        return recipient_str.strip()
    return contacts.get("self", "dujingt@gmail.com")

def capture_s20_photo() -> bytes:
    """抓取 S20 实时高清照片"""
    endpoints = ["/photoaf.jpg", "/shot.jpg", "/photo.jpg"]
    for ep in endpoints:
        try:
            url = f"http://100.122.149.94:8080{ep}"
            req = urllib.request.Request(url, headers={"User-Agent": "XiaoZhi-Email-Camera/1.0"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = resp.read()
                if len(data) > 1000:
                    return data
        except Exception as e:
            logger.bind(tag=TAG).warning(f"S20 抓图 {ep} 失败: {e}")
    return None

def generate_html_email(subject: str, content: str, sender_name: str, has_photo: bool = False) -> str:
    now_str = datetime.now().strftime("%Y年%m月%d日 %H:%M")
    paragraphs = content.strip().split("\n")
    body_html = ""
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        if p.startswith("- ") or p.startswith("* ") or p.startswith("• "):
            body_html += f"<li style='margin-bottom: 6px; color: #334155;'>{p.lstrip('-*• ')}</li>"
        elif p.startswith("#"):
            body_html += f"<h3 style='color: #1e293b; margin: 16px 0 8px 0;'>{p.lstrip('# ')}</h3>"
        else:
            body_html += f"<p style='margin: 0 0 12px 0; color: #334155; line-height: 1.7; font-size: 15px;'>{p}</p>"
            
    if "<li" in body_html:
        body_html = f"<ul style='padding-left: 20px; margin: 12px 0;'>{body_html}</ul>"

    photo_block = ""
    if has_photo:
        photo_block = """
        <div style="margin: 20px 0; text-align: center; background: #0f172a; border-radius: 12px; padding: 12px; border: 1px solid #334155;">
            <p style="color: #94a3b8; font-size: 12px; margin: 0 0 8px 0;">📸 摄像头实时抓拍画面</p>
            <img src="cid:camera_photo" style="max-width: 100%; height: auto; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.3);" alt="摄像头抓拍照片">
        </div>
        """

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin: 0; padding: 0; background-color: #f1f5f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
    <div style="max-width: 600px; margin: 30px auto; background: #ffffff; border-radius: 16px; overflow: hidden; border: 1px solid #e2e8f0; box-shadow: 0 10px 25px -5px rgba(0,0,0,0.06);">
        <div style="background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%); padding: 30px 24px; text-align: center;">
            <div style="font-size: 32px; margin-bottom: 6px;">🤖 📸 ✉️</div>
            <h1 style="color: #ffffff; margin: 0; font-size: 20px; font-weight: 700; letter-spacing: 0.5px;">{subject}</h1>
            <p style="color: #c7d2fe; margin: 6px 0 0 0; font-size: 12px;">由 {sender_name} 自动生成并派发 · {now_str}</p>
        </div>
        <div style="padding: 28px 24px;">
            <div style="background: #f8fafc; border-left: 4px solid #4f46e5; border-radius: 8px; padding: 18px 20px; margin-bottom: 20px;">
                {body_html}
            </div>
            {photo_block}
            <div style="border-top: 1px solid #e2e8f0; padding-top: 16px; display: flex; justify-content: space-between; align-items: center;">
                <p style="margin: 0; font-size: 12px; color: #64748b;">
                    发信助手：<b>{sender_name}</b><br>
                    通道：Microsoft 365 Exchange Online
                </p>
                <span style="font-size: 11px; color: #94a3b8; background: #f1f5f9; padding: 4px 10px; border-radius: 20px;">
                    XiaoZhi Agent
                </span>
            </div>
        </div>
    </div>
</body>
</html>"""

send_email_desc = {
    "type": "function",
    "function": {
        "name": "send_email",
        "description": (
            "当用户想要发送普通电子邮件、发邮件给某人、将备忘/纪要/总结/通知发送到指定邮箱时调用此工具。"
            "例如用户说：'帮我发封邮件给张总，说下周一开会'、'发一封邮件到我的邮箱，主题是学习计划'、'把刚才的要点发邮件给我'。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "recipient": {
                    "type": "string",
                    "description": "收件人。可以是具体邮箱地址（如 dujingt@gmail.com）或联系人昵称/别名（如'我'、'自己'、'我的邮箱'、'布布爸爸'、'张总'），默认发给用户自己。"
                },
                "subject": {
                    "type": "string",
                    "description": "邮件主题/标题（简明得体、概括内容，4~20字）"
                },
                "content": {
                    "type": "string",
                    "description": "邮件正文内容。需按照正式、规范、排版清晰的书信或备忘录格式生成完整内容，语言通顺得体。"
                }
            },
            "required": ["subject", "content"]
        }
    }
}

capture_email_photo_desc = {
    "type": "function",
    "function": {
        "name": "capture_and_email_photo",
        "description": (
            "【拍照发邮件】当用户要求小智使用摄像头拍一张照片发给自己或发到邮箱、抓拍画面发邮件时调用此工具。"
            "例如用户说：'用摄像头拍一张照片发给我'、'拍张照片发到我的邮箱'、'帮我拍照发邮件'、'拍下现在的画面发给布布爸爸'。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "recipient": {
                    "type": "string",
                    "description": "收件人，如'我'、'自己'、'dujingt@gmail.com'，默认发给用户自己。"
                },
                "note": {
                    "type": "string",
                    "description": "对照片或邮件的补充说明（如'当前客厅画面抓拍'、'书桌近景'），可选。"
                }
            },
            "required": []
        }
    }
}

@register_function("send_email", send_email_desc, ToolType.WAIT)
def send_email(subject: str, content: str, recipient: str = "self"):
    """发送普通电子邮件工具"""
    try:
        cfg = load_smtp_config()
        to_email = resolve_recipient_email(recipient)
        sender_email = cfg.get("email_user", "maomao@2ygwql.onmicrosoft.com")
        sender_pass = cfg.get("email_pass", "Miaomiao11miaomiao")
        smtp_host = cfg.get("smtp_server", "smtp.office365.com")
        smtp_port = cfg.get("smtp_port", 587)
        sender_name = cfg.get("sender_name", "小智 AI 语音助手")
        
        logger.bind(tag=TAG).info(f"开始发送文本邮件: 从 {sender_email} -> {to_email}, 主题: {subject}")
        
        msg = MIMEMultipart()
        msg["From"] = f"{sender_name} <{sender_email}>"
        msg["To"] = to_email
        msg["Subject"] = Header(subject, "utf-8")
        
        html_body = generate_html_email(subject, content, sender_name, has_photo=False)
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        
        context = ssl.create_default_context()
        server = smtplib.SMTP(smtp_host, smtp_port, timeout=20)
        server.ehlo()
        if cfg.get("use_tls", True):
            server.starttls(context=context)
            server.ehlo()
            
        server.login(sender_email, sender_pass)
        server.sendmail(sender_email, [to_email], msg.as_string())
        server.quit()
        
        now_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        snippet = content[:30] + "..." if len(content) > 30 else content
        save_sent_history({
            "time": now_time,
            "recipient": to_email,
            "subject": subject,
            "snippet": snippet
        })
        
        logger.bind(tag=TAG).info(f"文本邮件成功发送至 {to_email}!")
        reply_prompt = (
            f"已成功发送电子邮件！\n"
            f"- 收件人：{to_email}\n"
            f"- 邮件主题：《{subject}》\n"
            f"- 发送时间：{now_time}\n"
            f"请用亲切自然的语音告知用户：邮件已经成功发送到了他的邮箱（{to_email}），并简要复述一下主题《{subject}》。"
        )
        return ActionResponse(Action.REQLLM, reply_prompt, None)
    except Exception as e:
        logger.bind(tag=TAG).error(f"发送邮件异常: {e}\n{traceback.format_exc()}")
        return ActionResponse(
            Action.REQLLM,
            f"发送邮件时遇到错误：{str(e)}。请友好地向用户解释发送失败的原因并请他稍后再试。",
            None
        )

@register_function("capture_and_email_photo", capture_email_photo_desc, ToolType.WAIT)
def capture_and_email_photo(recipient: str = "self", note: str = "实时摄像头抓拍"):
    """使用 S20 摄像头拍照并发送到邮箱"""
    try:
        ensure_dirs()
        cfg = load_smtp_config()
        to_email = resolve_recipient_email(recipient)
        sender_email = cfg.get("email_user", "maomao@2ygwql.onmicrosoft.com")
        sender_pass = cfg.get("email_pass", "Miaomiao11miaomiao")
        smtp_host = cfg.get("smtp_server", "smtp.office365.com")
        smtp_port = cfg.get("smtp_port", 587)
        sender_name = cfg.get("sender_name", "小智 AI 语音助手")
        
        logger.bind(tag=TAG).info(f"触发摄像头抓拍发信流程: 目标 {to_email}, 附言: {note}")
        
        img_bytes = capture_s20_photo()
        if not img_bytes:
            return ActionResponse(
                Action.REQLLM,
                "摄像头画面抓取失败，无法拍摄照片。请确认三星 S20 手机上的 IP Webcam 和网络正常连接。",
                None
            )
            
        now_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        now_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        photo_filename = f"snapshot_{now_ts}.jpg"
        photo_path = os.path.join(ATTACHMENTS_DIR, photo_filename)
        with open(photo_path, "wb") as f:
            f.write(img_bytes)
            
        subject = f"📸 小智摄像头实时抓拍照片 - {datetime.now().strftime('%m月%d日 %H:%M')}"
        content = (
            f"主人您好！这是小智通过三星 S20 高清摄像头为您拍摄的实时画面。\n\n"
            f"- 拍摄时间：{now_time}\n"
            f"- 照片说明：{note}\n"
            f"- 照片分辨率/大小：{len(img_bytes)/1024:.1f} KB\n\n"
            f"照片已作为附件并内嵌在下方，请您查收！"
        )
        
        msg = MIMEMultipart("related")
        msg["From"] = f"{sender_name} <{sender_email}>"
        msg["To"] = to_email
        msg["Subject"] = Header(subject, "utf-8")
        
        # HTML Part
        html_body = generate_html_email(subject, content, sender_name, has_photo=True)
        msg_alternative = MIMEMultipart("alternative")
        msg.attach(msg_alternative)
        msg_alternative.attach(MIMEText(html_body, "html", "utf-8"))
        
        # Inline Image Part
        img_part = MIMEImage(img_bytes)
        img_part.add_header("Content-ID", "<camera_photo>")
        img_part.add_header("Content-Disposition", "inline", filename=photo_filename)
        msg.attach(img_part)
        
        # SMTP Send
        context = ssl.create_default_context()
        server = smtplib.SMTP(smtp_host, smtp_port, timeout=20)
        server.ehlo()
        if cfg.get("use_tls", True):
            server.starttls(context=context)
            server.ehlo()
            
        server.login(sender_email, sender_pass)
        server.sendmail(sender_email, [to_email], msg.as_string())
        server.quit()
        
        save_sent_history({
            "time": now_time,
            "recipient": to_email,
            "subject": subject,
            "snippet": f"[含照片附件] {note} ({len(img_bytes)/1024:.1f} KB)"
        })
        
        logger.bind(tag=TAG).info(f"抓拍照片邮件成功送达 {to_email}!")
        
        reply_prompt = (
            f"已成功通过摄像头拍摄了一张高清照片并发送到了您的邮箱！\n"
            f"- 目标邮箱：{to_email}\n"
            f"- 拍摄时间：{now_time}\n"
            f"- 照片大小：{len(img_bytes)/1024:.1f} KB\n"
            f"请用亲切开朗的语音回复主人：照片已经拍好并发送到您的邮箱啦，请去查收吧！"
        )
        return ActionResponse(Action.REQLLM, reply_prompt, None)
    except Exception as e:
        logger.bind(tag=TAG).error(f"拍照发信异常: {e}\n{traceback.format_exc()}")
        return ActionResponse(
            Action.REQLLM,
            f"拍照发信失败：{str(e)}。请友好地向主人说明情况。",
            None
        )
