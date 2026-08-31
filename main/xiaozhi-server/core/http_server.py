import os
import asyncio
from aiohttp import web
from config.logger import setup_logging
from core.api.ota_handler import OTAHandler
from core.api.vision_handler import VisionHandler
from core.api.face_handler import FaceWebHandler
from core.api.music_handler import MusicWebHandler
from core.api.email_handler import EmailWebHandler
from core.services.face_sentinel import FaceSentinel

TAG = __name__


class SimpleHttpServer:
    def __init__(self, config: dict):
        self.config = config
        self.logger = setup_logging()
        self.ota_handler = OTAHandler(config)
        self.vision_handler = VisionHandler(config)
        self.face_handler = FaceWebHandler(config)
        self.music_handler = MusicWebHandler(config)
        self.email_handler = EmailWebHandler(config)
        self.sentinel = FaceSentinel()

    async def handle_console_page(self, request):
        html_path = os.path.join(os.path.dirname(__file__), "api", "console_admin.html")
        if os.path.exists(html_path):
            with open(html_path, "r", encoding="utf-8") as f:
                return web.Response(text=f.read(), content_type="text/html")
        return web.Response(text="<h1>Console Admin Page Not Found</h1>", content_type="text/html", status=404)

    def _get_websocket_url(self, local_ip: str, port: int) -> str:
        server_config = self.config["server"]
        websocket_config = server_config.get("websocket")
        if websocket_config and "你" not in websocket_config:
            return websocket_config
        else:
            return f"ws://{local_ip}:{port}/xiaozhi/v1/"

    async def start(self):
        try:
            server_config = self.config["server"]
            read_config_from_api = self.config.get("read_config_from_api", False)
            host = server_config.get("ip", "0.0.0.0")
            port = int(server_config.get("http_port", 8003))

            if port:
                app = web.Application()

                if not read_config_from_api:
                    app.add_routes(
                        [
                            web.get("/xiaozhi/ota/", self.ota_handler.handle_get),
                            web.post("/xiaozhi/ota/", self.ota_handler.handle_post),
                            web.options("/xiaozhi/ota/", self.ota_handler.handle_options),
                            web.get("/xiaozhi/ota/download/{filename}", self.ota_handler.handle_download),
                            web.options("/xiaozhi/ota/download/{filename}", self.ota_handler.handle_options),
                        ]
                    )
                # 添加路由
                app.add_routes(
                    [
                        web.get("/mcp/vision/explain", self.vision_handler.handle_get),
                        web.post("/mcp/vision/explain", self.vision_handler.handle_post),
                        web.options("/mcp/vision/explain", self.vision_handler.handle_options),
                        # 🎛️ 小智全能一体化智控中枢 (Unified Console)
                        web.get("/console", self.handle_console_page),
                        web.get("/console/", self.handle_console_page),
                        # 邮件与通讯录中枢管理后台
                        web.get("/email", self.email_handler.handle_page),
                        web.get("/email/", self.email_handler.handle_page),
                        web.get("/api/email/contacts", self.email_handler.handle_get_contacts),
                        web.post("/api/email/contacts/save", self.email_handler.handle_save_contact),
                        web.post("/api/email/contacts/delete", self.email_handler.handle_delete_contact),
                        web.get("/api/email/history", self.email_handler.handle_get_history),
                        web.post("/api/email/send", self.email_handler.handle_send_email),
                        # 人脸记忆与视觉中枢管理后台
                        web.get("/faces", self.face_handler.handle_page),
                        web.get("/faces/", self.face_handler.handle_page),
                        web.get("/faces/index.html", self.face_handler.handle_page),
                        web.get("/api/faces", self.face_handler.handle_get_faces),
                        web.get("/api/faces/image/{filename}", self.face_handler.handle_get_image),
                        web.post("/api/faces/register", self.face_handler.handle_register_face),
                        web.post("/api/faces/update", self.face_handler.handle_update_face),
                        web.post("/api/faces/delete", self.face_handler.handle_delete_face),
                        web.post("/api/faces/recognize", self.face_handler.handle_test_recognize),
                        web.get("/api/faces/s20/status", self.face_handler.handle_s20_status),
                        web.get("/api/faces/preview", self.face_handler.handle_camera_preview),
                        web.get("/api/faces/stream", self.face_handler.handle_camera_stream),
                        # 视觉哨兵路由
                        web.get("/api/faces/sentinel", self.face_handler.handle_sentinel_status),
                        web.post("/api/faces/sentinel/toggle", self.face_handler.handle_sentinel_toggle),
                        web.post("/api/faces/sentinel/config", self.face_handler.handle_sentinel_config),
                        web.post("/api/faces/sentinel/trigger_test", self.face_handler.handle_sentinel_trigger_test),
                        # 🎵 本地音乐中枢与曲库管理后台
                        web.get("/music", self.music_handler.handle_page),
                        web.get("/music/", self.music_handler.handle_page),
                        web.get("/music/index.html", self.music_handler.handle_page),
                        web.get("/api/music/list", self.music_handler.handle_list),
                        web.post("/api/music/upload", self.music_handler.handle_upload),
                        web.get("/api/music/stream/{filename}", self.music_handler.handle_stream),
                        web.post("/api/music/delete", self.music_handler.handle_delete),
                        web.post("/api/music/rename", self.music_handler.handle_rename),
                        web.post("/api/music/play_on_device", self.music_handler.handle_play_on_device),
                        web.post("/api/music/stop_device", self.music_handler.handle_stop_device),
                    ]
                )

                runner = web.AppRunner(app)
                await runner.setup()
                site = web.TCPSite(runner, host, port)
                await site.start()
                self.logger.bind(tag=TAG).info(f"HTTP服务器已启动在 {host}:{port}")

                while True:
                    await asyncio.sleep(3600)
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"HTTP服务器启动失败: {e}")
            import traceback
            self.logger.bind(tag=TAG).error(f"错误堆栈: {traceback.format_exc()}")
            raise
