import os
import json
import time
import shutil
import urllib.parse
from aiohttp import web
from pathlib import Path
from config.logger import setup_logging
from core.utils.connection_registry import ConnectionRegistry

TAG = "[MusicWebHandler]"
logger = setup_logging()

MUSIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "music")
CACHE_DIR = os.path.join(MUSIC_DIR, "cache")
SUPPORTED_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg", ".p3"}

os.makedirs(MUSIC_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

def get_audio_info(filepath: str):
    """获取音频文件基本信息（大小、修改时间、推算时长等）"""
    try:
        size = os.path.getsize(filepath)
        mtime = os.path.getmtime(filepath)
        mtime_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))
        ext = os.path.splitext(filepath)[1].lower()
        filename = os.path.basename(filepath)
        
        # 解析歌名与歌手（例如 "周杰伦 - 晴天.mp3" 或 "晴天.mp3"）
        title = os.path.splitext(filename)[0]
        artist = "未知歌手"
        if " - " in title:
            parts = title.split(" - ", 1)
            artist = parts[0].strip()
            title = parts[1].strip()
        elif "_" in title and not title.startswith("face_"):
            parts = title.split("_", 1)
            artist = parts[0].strip()
            title = parts[1].strip()
            
        return {
            "filename": filename,
            "title": title,
            "artist": artist,
            "ext": ext.replace(".", "").upper(),
            "size": size,
            "size_formatted": f"{size / (1024 * 1024):.2f} MB" if size >= 1024 * 1024 else f"{size / 1024:.1f} KB",
            "modified_at": mtime_str,
            "play_url": f"/api/music/stream/{urllib.parse.quote(filename)}"
        }
    except Exception as e:
        logger.bind(tag=TAG).error(f"解析音频信息失败 {filepath}: {e}")
        return None

class MusicWebHandler:
    def __init__(self, config: dict):
        self.config = config

    async def handle_page(self, request):
        """渲染音乐管理 Web 页面"""
        html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "music_admin.html")
        if os.path.exists(html_path):
            with open(html_path, "r", encoding="utf-8") as f:
                return web.Response(text=f.read(), content_type="text/html")
        return web.Response(text="<h1>Music Admin HTML not found</h1>", content_type="text/html", status=404)

    async def handle_list(self, request):
        """获取本地所有音乐列表与统计数据"""
        try:
            songs = []
            total_size = 0
            
            if os.path.exists(MUSIC_DIR):
                for fname in sorted(os.listdir(MUSIC_DIR)):
                    if fname == "cache" or fname.startswith("."):
                        continue
                    full_p = os.path.join(MUSIC_DIR, fname)
                    if os.path.isfile(full_p):
                        ext = os.path.splitext(fname)[1].lower()
                        if ext in SUPPORTED_EXTS:
                            info = get_audio_info(full_p)
                            if info:
                                songs.append(info)
                                total_size += info["size"]

            total_size_mb = f"{total_size / (1024 * 1024):.2f} MB"
            device_online = len(ConnectionRegistry.get_active_connections()) > 0
            
            return web.json_response({
                "code": 0,
                "success": True,
                "msg": "success",
                "data": {
                    "total_count": len(songs),
                    "total_size": total_size,
                    "total_size_formatted": total_size_mb,
                    "device_online": device_online,
                    "supported_formats": ["MP3", "WAV", "M4A", "FLAC", "AAC", "OGG"],
                    "songs": songs
                }
            })
        except Exception as e:
            logger.bind(tag=TAG).error(f"获取音乐列表失败: {e}")
            return web.json_response({"code": 500, "success": False, "msg": str(e)})

    async def handle_upload(self, request):
        """支持单文件或多文件流式上传到 music/ 目录"""
        try:
            reader = await request.multipart()
            uploaded_files = []

            while True:
                part = await reader.next()
                if part is None:
                    break

                if part.filename:
                    raw_filename = part.filename
                    # 过滤非法字符
                    safe_filename = os.path.basename(raw_filename).strip()
                    ext = os.path.splitext(safe_filename)[1].lower()

                    if ext not in SUPPORTED_EXTS:
                        return web.json_response({
                            "code": 400,
                            "success": False,
                            "msg": f"不支持的音频格式: {ext}，仅支持 {', '.join(SUPPORTED_EXTS)}"
                        })

                    target_path = os.path.join(MUSIC_DIR, safe_filename)
                    size = 0
                    with open(target_path, "wb") as f:
                        while True:
                            chunk = await part.read_chunk()
                            if not chunk:
                                break
                            size += len(chunk)
                            f.write(chunk)

                    info = get_audio_info(target_path)
                    uploaded_files.append(info)
                    logger.bind(tag=TAG).info(f"成功上传音乐: {safe_filename} ({size / 1024 / 1024:.2f} MB)")

            if not uploaded_files:
                return web.json_response({"code": 400, "success": False, "msg": "未接收到有效音频文件"})

            return web.json_response({
                "code": 0,
                "success": True,
                "msg": f"成功上传 {len(uploaded_files)} 首歌曲",
                "data": uploaded_files
            })
        except Exception as e:
            logger.bind(tag=TAG).error(f"音乐上传异常: {e}")
            return web.json_response({"code": 500, "success": False, "msg": f"上传失败: {e}"})

    async def handle_stream(self, request):
        """支持 Range 请求的网页音频流预览播放"""
        raw_filename = request.match_info.get("filename", "")
        filename = urllib.parse.unquote(raw_filename)
        safe_filename = os.path.basename(filename)
        filepath = os.path.join(MUSIC_DIR, safe_filename)

        if not os.path.exists(filepath) or not os.path.isfile(filepath):
            return web.Response(status=404, text="Music file not found")

        ext = os.path.splitext(safe_filename)[1].lower()
        content_type_map = {
            ".mp3": "audio/mpeg",
            ".wav": "audio/wav",
            ".m4a": "audio/mp4",
            ".flac": "audio/flac",
            ".aac": "audio/aac",
            ".ogg": "audio/ogg",
            ".p3": "audio/mpeg"
        }
        content_type = content_type_map.get(ext, "application/octet-stream")

        file_size = os.path.getsize(filepath)
        range_header = request.headers.get("Range")

        if range_header:
            try:
                byte_range = range_header.replace("bytes=", "").split("-")
                start = int(byte_range[0])
                end = int(byte_range[1]) if byte_range[1] else file_size - 1
                length = end - start + 1

                with open(filepath, "rb") as f:
                    f.seek(start)
                    data = f.read(length)

                headers = {
                    "Content-Range": f"bytes {start}-{end}/{file_size}",
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(length),
                    "Content-Type": content_type
                }
                return web.Response(body=data, status=206, headers=headers)
            except Exception:
                pass

        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
            "Content-Type": content_type
        }
        with open(filepath, "rb") as f:
            return web.Response(body=f.read(), headers=headers)

    async def handle_delete(self, request):
        """删除指定本地音乐"""
        try:
            data = await request.json()
            filename = data.get("filename", "").strip()
            if not filename:
                return web.json_response({"code": 400, "success": False, "msg": "文件名不能为空"})

            safe_filename = os.path.basename(filename)
            filepath = os.path.join(MUSIC_DIR, safe_filename)

            if os.path.exists(filepath) and os.path.isfile(filepath):
                os.remove(filepath)
                logger.bind(tag=TAG).info(f"已删除音乐文件: {safe_filename}")
                return web.json_response({"code": 0, "success": True, "msg": f"成功删除歌曲《{safe_filename}》"})
            else:
                return web.json_response({"code": 404, "success": False, "msg": "文件不存在"})
        except Exception as e:
            return web.json_response({"code": 500, "success": False, "msg": str(e)})

    async def handle_rename(self, request):
        """重命名音乐文件（便于语音指令点歌匹配）"""
        try:
            data = await request.json()
            old_name = data.get("old_filename", "").strip()
            new_title = data.get("new_title", "").strip()

            if not old_name or not new_title:
                return web.json_response({"code": 400, "success": False, "msg": "参数不完整"})

            safe_old = os.path.basename(old_name)
            ext = os.path.splitext(safe_old)[1].lower()
            safe_new = os.path.basename(new_title)
            if not safe_new.lower().endswith(ext):
                safe_new += ext

            old_path = os.path.join(MUSIC_DIR, safe_old)
            new_path = os.path.join(MUSIC_DIR, safe_new)

            if not os.path.exists(old_path):
                return web.json_response({"code": 404, "success": False, "msg": "原文件不存在"})

            if os.path.exists(new_path) and old_path != new_path:
                return web.json_response({"code": 400, "success": False, "msg": "目标文件名已存在"})

            os.rename(old_path, new_path)
            info = get_audio_info(new_path)
            logger.bind(tag=TAG).info(f"音乐重命名: {safe_old} -> {safe_new}")
            return web.json_response({"code": 0, "success": True, "msg": "重命名成功", "data": info})
        except Exception as e:
            return web.json_response({"code": 500, "success": False, "msg": str(e)})

    async def handle_play_on_device(self, request):
        """一键向在线 ESP32 音箱推送播放指令"""
        try:
            data = await request.json()
            filename = data.get("filename", "").strip()
            title = data.get("title", "") or os.path.splitext(os.path.basename(filename))[0]

            if not filename:
                return web.json_response({"code": 400, "success": False, "msg": "未指定歌曲"})

            prompt = f"请为我播放本地音乐《{title}》"
            dispatched = ConnectionRegistry.broadcast_proactive_chat(prompt)

            if dispatched:
                return web.json_response({
                    "code": 0,
                    "success": True,
                    "msg": f"已成功向小智音箱推送播放指令：《{title}》"
                })
            else:
                return web.json_response({
                    "code": 400,
                    "success": False,
                    "msg": "当前没有在线连接的小智 ESP32 音箱，请确认设备已开机联网"
                })
        except Exception as e:
            return web.json_response({"code": 500, "success": False, "msg": str(e)})
