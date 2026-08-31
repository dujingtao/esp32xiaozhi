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
        self.logger = setup_logging()

    async def handle_page(self, request):
        html_path = os.path.join(os.path.dirname(__file__), "music_admin.html")
        if os.path.exists(html_path):
            with open(html_path, "r", encoding="utf-8") as f:
                content = f.read()
            return web.Response(text=content, content_type="text/html")
        return web.Response(text="<h1>Music Admin Page Not Found</h1>", content_type="text/html", status=404)

    async def handle_list(self, request):
        return await self.handle_list_music(request)

    async def handle_list_music(self, request):
        try:
            songs = []
            total_size = 0
            if os.path.exists(MUSIC_DIR):
                for f in sorted(os.listdir(MUSIC_DIR)):
                    ext = os.path.splitext(f)[1].lower()
                    if ext in SUPPORTED_EXTS:
                        fpath = os.path.join(MUSIC_DIR, f)
                        if os.path.isfile(fpath):
                            info = get_audio_info(fpath)
                            if info:
                                songs.append(info)
                                total_size += info["size"]

            active_conns = list(ConnectionRegistry._connections.keys())
            
            return web.json_response({
                "code": 0,
                "success": True,
                "msg": "success",
                "data": {
                    "total_count": len(songs),
                    "total_size": total_size,
                    "total_size_formatted": f"{total_size / (1024 * 1024):.2f} MB",
                    "device_online": len(active_conns) > 0,
                    "supported_formats": ["MP3", "WAV", "M4A", "FLAC", "AAC", "OGG"],
                    "songs": songs
                }
            })
        except Exception as e:
            return web.json_response({"code": 500, "success": False, "msg": str(e)})

    async def handle_upload(self, request):
        try:
            reader = await request.multipart()
            uploaded_files = []
            
            while True:
                field = await reader.next()
                if field is None:
                    break
                if field.name == 'file':
                    filename = field.filename
                    if not filename:
                        continue
                    ext = os.path.splitext(filename)[1].lower()
                    if ext not in SUPPORTED_EXTS:
                        return web.json_response({
                            "code": 400,
                            "success": False,
                            "msg": f"不支持的文件格式: {ext}，仅支持 MP3, WAV, FLAC, M4A, AAC, OGG"
                        })
                    
                    target_path = os.path.join(MUSIC_DIR, filename)
                    base, ext = os.path.splitext(filename)
                    counter = 1
                    while os.path.exists(target_path):
                        filename = f"{base}_{counter}{ext}"
                        target_path = os.path.join(MUSIC_DIR, filename)
                        counter += 1
                        
                    size = 0
                    with open(target_path, 'wb') as f:
                        while True:
                            chunk = await field.read_chunk()
                            if not chunk:
                                break
                            size += len(chunk)
                            f.write(chunk)
                            
                    logger.bind(tag=TAG).info(f"成功上传音乐: {filename} ({size / (1024*1024):.2f} MB)")
                    uploaded_files.append(filename)

            if uploaded_files:
                return web.json_response({
                    "code": 0,
                    "success": True,
                    "msg": f"成功上传 {len(uploaded_files)} 首歌曲",
                    "data": uploaded_files
                })
            else:
                return web.json_response({"code": 400, "success": False, "msg": "未接收到任何文件"})
        except Exception as e:
            logger.bind(tag=TAG).error(f"上传音乐异常: {e}")
            return web.json_response({"code": 500, "success": False, "msg": str(e)})

    async def handle_delete(self, request):
        try:
            data = await request.json()
            filename = data.get("filename", "").strip()
            if not filename:
                return web.json_response({"code": 400, "success": False, "msg": "未指定删除文件名"})
                
            filepath = os.path.join(MUSIC_DIR, filename)
            if not os.path.exists(filepath):
                return web.json_response({"code": 404, "success": False, "msg": "文件不存在"})
                
            os.remove(filepath)
            
            p3_cache = os.path.join(CACHE_DIR, f"{os.path.splitext(filename)[0]}.p3")
            if os.path.exists(p3_cache):
                try:
                    os.remove(p3_cache)
                except Exception:
                    pass
                    
            logger.bind(tag=TAG).info(f"成功删除音乐: {filename}")
            return web.json_response({"code": 0, "success": True, "msg": f"已成功删除歌曲: {filename}"})
        except Exception as e:
            return web.json_response({"code": 500, "success": False, "msg": str(e)})

    async def handle_stream(self, request):
        filename = request.match_info.get("filename", "")
        filename = urllib.parse.unquote(filename)
        filepath = os.path.join(MUSIC_DIR, filename)
        if not os.path.exists(filepath) or not os.path.isfile(filepath):
            return web.Response(text="Music file not found", status=404)

        ext = os.path.splitext(filename)[1].lower()
        content_type = "audio/mpeg"
        if ext == ".wav":
            content_type = "audio/wav"
        elif ext == ".flac":
            content_type = "audio/flac"
        elif ext in [".m4a", ".aac"]:
            content_type = "audio/mp4"
        elif ext == ".ogg":
            content_type = "audio/ogg"

        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": content_type,
                "Content-Length": str(os.path.getsize(filepath)),
                "Accept-Ranges": "bytes",
                "Cache-Control": "public, max-age=86400"
            }
        )
        await response.prepare(request)
        with open(filepath, "rb") as f:
            while True:
                chunk = f.read(64 * 1024)
                if not chunk:
                    break
                await response.write(chunk)
        return response

    async def handle_rename(self, request):
        try:
            data = await request.json()
            old_name = data.get("old_filename", "").strip()
            new_name = data.get("new_filename", "").strip()
            if not old_name or not new_name:
                return web.json_response({"code": 400, "success": False, "msg": "文件名不能为空"})
                
            old_path = os.path.join(MUSIC_DIR, old_name)
            if not os.path.exists(old_path):
                return web.json_response({"code": 404, "success": False, "msg": "原文件不存在"})
                
            old_ext = os.path.splitext(old_name)[1].lower()
            new_ext = os.path.splitext(new_name)[1].lower()
            if not new_ext:
                new_name = f"{new_name}{old_ext}"
                
            new_path = os.path.join(MUSIC_DIR, new_name)
            if os.path.exists(new_path) and new_path != old_path:
                return web.json_response({"code": 400, "success": False, "msg": "目标文件名已存在"})
                
            os.rename(old_path, new_path)
            logger.bind(tag=TAG).info(f"重命名音乐: {old_name} -> {new_name}")
            return web.json_response({"code": 0, "success": True, "msg": f"重命名成功: {new_name}"})
        except Exception as e:
            return web.json_response({"code": 500, "success": False, "msg": str(e)})

    async def handle_play_on_device(self, request):
        """一键向在线 ESP32 音箱直接推送播放音频流（零延迟、100%可靠）"""
        try:
            data = await request.json()
            filename = data.get("filename", "").strip()
            title = data.get("title", "") or os.path.splitext(os.path.basename(filename))[0]

            if not filename:
                return web.json_response({"code": 400, "success": False, "msg": "未指定歌曲"})

            music_file = os.path.join(MUSIC_DIR, filename)
            if not os.path.exists(music_file):
                music_file = os.path.join(CACHE_DIR, filename)

            if not os.path.exists(music_file):
                return web.json_response({"code": 404, "success": False, "msg": f"音乐文件不存在: {filename}"})

            active_conns = list(ConnectionRegistry._connections.values())
            if not active_conns:
                return web.json_response({
                    "code": 400,
                    "success": False,
                    "msg": "当前没有在线连接的小智 ESP32 音箱，请确认设备已开机联网"
                })

            prompt_text = f"正在为您播放，《{title}》"
            from core.providers.tts.dto.dto import ContentType, TTSMessageDTO, SentenceType
            import uuid

            from core.handle.sendAudioHandle import send_tts_message
            for conn in active_conns:
                try:
                    conn.client_abort = False
                    conn.sentence_id = str(uuid.uuid4().hex)
                    import asyncio
                    asyncio.run_coroutine_threadsafe(
                        send_tts_message(conn, "start"),
                        conn.loop
                    )
                    conn.tts.store_tts_text(conn.sentence_id, prompt_text)
                    conn.tts.tts_text_queue.put(
                        TTSMessageDTO(
                            sentence_id=conn.sentence_id,
                            sentence_type=SentenceType.FIRST,
                            content_type=ContentType.ACTION,
                        )
                    )
                    conn.tts.tts_text_queue.put(
                        TTSMessageDTO(
                            sentence_id=conn.sentence_id,
                            sentence_type=SentenceType.MIDDLE,
                            content_type=ContentType.TEXT,
                            content_detail=prompt_text,
                        )
                    )
                    conn.tts.tts_text_queue.put(
                        TTSMessageDTO(
                            sentence_id=conn.sentence_id,
                            sentence_type=SentenceType.MIDDLE,
                            content_type=ContentType.FILE,
                            content_file=music_file,
                        )
                    )
                    conn.tts.tts_text_queue.put(
                        TTSMessageDTO(
                            sentence_id=conn.sentence_id,
                            sentence_type=SentenceType.LAST,
                            content_type=ContentType.ACTION,
                        )
                    )
                    logger.bind(tag=TAG).info(f"已直接将音乐 {filename} 推送到设备 TTS 队列")
                except Exception as ce:
                    logger.bind(tag=TAG).error(f"推送音乐到连接失败: {ce}")

            return web.json_response({
                "code": 0,
                "success": True,
                "msg": f"已成功向小智音箱推送播放：《{title}》"
            })
        except Exception as e:
            return web.json_response({"code": 500, "success": False, "msg": str(e)})

    async def handle_stop_device(self, request):
        """一键打断并停止所有在线 ESP32 音箱的音乐/语音播放"""
        try:
            from core.handle.abortHandle import handleAbortMessage
            active_conns = list(ConnectionRegistry._connections.values())
            if not active_conns:
                return web.json_response({
                    "code": 400,
                    "success": False,
                    "msg": "当前没有在线连接的 ESP32 设备"
                })
            
            stopped_count = 0
            for conn in active_conns:
                try:
                    await handleAbortMessage(conn)
                    stopped_count += 1
                except Exception as ce:
                    logger.bind(tag=TAG).error(f"打断设备播放失败: {ce}")
                    
            return web.json_response({
                "code": 0,
                "success": True,
                "msg": f"已成功向 {stopped_count} 台在线音箱发送即时停止指令"
            })
        except Exception as e:
            return web.json_response({"code": 500, "success": False, "msg": str(e)})
