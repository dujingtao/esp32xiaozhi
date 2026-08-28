import os
import json
from aiohttp import web
from config.logger import setup_logging

TAG = "core.api.email_handler"
logger = setup_logging()

EMAIL_DATA_DIR = "data/email"
CONTACTS_FILE = os.path.join(EMAIL_DATA_DIR, "contacts.json")
SENT_HISTORY_FILE = os.path.join(EMAIL_DATA_DIR, "sent_history.json")
CONFIG_FILE = os.path.join(EMAIL_DATA_DIR, "smtp_config.json")

def ensure_dirs():
    os.makedirs(EMAIL_DATA_DIR, exist_ok=True)
    if not os.path.exists(CONTACTS_FILE):
        default_contacts = {
            "我": "dujingt@gmail.com",
            "自己": "dujingt@gmail.com",
            "我的邮箱": "dujingt@gmail.com",
            "布布爸爸": "dujingt@gmail.com",
            "杜靖涛": "dujingt@gmail.com",
            "self": "dujingt@gmail.com",
            "maomao": "maomao@2ygwql.onmicrosoft.com"
        }
        with open(CONTACTS_FILE, "w", encoding="utf-8") as f:
            json.dump(default_contacts, f, ensure_ascii=False, indent=2)

class EmailWebHandler:
    def __init__(self, config: dict):
        self.config = config
        self.logger = setup_logging()
        ensure_dirs()

    def _load_contacts(self):
        ensure_dirs()
        try:
            with open(CONTACTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_contacts(self, contacts):
        ensure_dirs()
        with open(CONTACTS_FILE, "w", encoding="utf-8") as f:
            json.dump(contacts, f, ensure_ascii=False, indent=2)

    def _load_history(self):
        ensure_dirs()
        try:
            with open(SENT_HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    async def handle_get_contacts(self, request):
        contacts = self._load_contacts()
        contact_list = [{"name": k, "email": v} for k, v in contacts.items()]
        return web.json_response({"code": 0, "success": True, "data": contact_list})

    async def handle_save_contact(self, request):
        try:
            data = await request.json()
            name = data.get("name", "").strip()
            email = data.get("email", "").strip()
            if not name or not email:
                return web.json_response({"code": 400, "success": False, "msg": "姓名和邮箱均不能为空"})
            
            contacts = self._load_contacts()
            contacts[name] = email
            self._save_contacts(contacts)
            self.logger.bind(tag=TAG).info(f"添加/更新联系人: {name} -> {email}")
            return web.json_response({"code": 0, "success": True, "msg": f"成功保存联系人: {name}"})
        except Exception as e:
            return web.json_response({"code": 500, "success": False, "msg": str(e)})

    async def handle_delete_contact(self, request):
        try:
            data = await request.json()
            name = data.get("name", "").strip()
            if not name:
                return web.json_response({"code": 400, "success": False, "msg": "联系人名称不能为空"})
            
            contacts = self._load_contacts()
            if name in contacts:
                del contacts[name]
                self._save_contacts(contacts)
                self.logger.bind(tag=TAG).info(f"删除联系人: {name}")
                return web.json_response({"code": 0, "success": True, "msg": f"已删除联系人: {name}"})
            else:
                return web.json_response({"code": 404, "success": False, "msg": "联系人不存在"})
        except Exception as e:
            return web.json_response({"code": 500, "success": False, "msg": str(e)})

    async def handle_get_history(self, request):
        history = self._load_history()
        return web.json_response({"code": 0, "success": True, "data": list(reversed(history[-100:]))})

    async def handle_page(self, request):
        html_path = os.path.join(os.path.dirname(__file__), "email_admin.html")
        if os.path.exists(html_path):
            with open(html_path, "r", encoding="utf-8") as f:
                content = f.read()
            return web.Response(text=content, content_type="text/html")
        return web.Response(text="<h1>Email Admin Page Not Found</h1>", content_type="text/html", status=404)
