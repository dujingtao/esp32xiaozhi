import os
import re
import time
import random
import difflib
import hashlib
import urllib.request
import urllib.parse
import json
import traceback
from pathlib import Path
from typing import Optional, Tuple
from plugins_func.register import register_function, ToolType, ActionResponse, Action
from core.providers.tts.dto.dto import ContentType, TTSMessageDTO, SentenceType

TAG = "plugins.play_music"
MUSIC_CACHE = {}

# 最小完整歌曲文件阈值（1.2MB），低于此大小的一律判定为试听片段或平台引流音频，直接拦截丢弃
MIN_FULL_SONG_BYTES = 1200000

# 纯正高品质流行金曲精选池（100%排除儿歌、杂音、朗诵等曲目）
POPULAR_RANDOM_SONGS = [
    "晴天 周杰伦", "稻香 周杰伦", "青花瓷 周杰伦", "七里香 周杰伦", "夜曲 周杰伦",
    "十年 陈奕迅", "孤勇者 陈奕迅", "富士山下 陈奕迅", "红豆 王菲", "起风了 买辣椒也用券",
    "如愿 王菲", "大鱼 周深", "光年之外 邓紫棋", "泡沫 邓紫棋", "句号 邓紫棋",
    "江南 林俊杰", "可惜没如果 林俊杰", "修炼爱情 林俊杰", "素颜 许嵩", "清明雨上 许嵩",
    "年少有为 李荣浩", "李白 李荣浩", "消愁 毛不易", "平凡之路 朴树", "生如夏花 朴树",
    "夜空中最亮的星 逃跑计划", "海阔天空 Beyond", "光辉岁月 Beyond", "后来 刘若英",
    "千千阙歌 陈慧娴", "爱存在 王呈章", "突然好想你 五月天", "知足 五月天"
]

play_music_function_desc = {
    "type": "function",
    "function": {
        "name": "play_music",
        "description": "【播放音乐首选工具】当用户要求播放音乐、点歌、放歌、听歌、播放本地歌曲或音频时必须调用此工具。",
        "parameters": {
            "type": "object",
            "properties": {
                "song_name": {
                    "type": "string",
                    "description": "歌曲名称或歌手名称。如果用户没有指定具体歌名（如‘随便放首歌’、‘来点音乐’、‘放首歌’）则填入 'random'。明确指定时填入歌名或歌手名，如 '晴天'、'周杰伦'、'孤勇者'。",
                }
            },
            "required": ["song_name"],
        },
    },
}

def clean_html_tags(text: str) -> str:
    """清理歌名中的HTML标记与多余特殊符号"""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
    text = re.sub(r"-《[^》]+》.*$", "", text)
    text = re.sub(r"\|.*$", "", text)
    return text.strip()

def search_online_music(query: str, logger=None) -> Optional[Tuple[str, str, str]]:
    """在线搜索音乐并获取直链，返回 (song_title, artist, mp3_url)"""
    try:
        encoded_kw = urllib.parse.quote(query)
        search_url = (
            f"https://search.kuwo.cn/r.s?client=kt&all={encoded_kw}&pn=0&rn=8"
            f"&uid=794762529&ver=kwplayer_ar_99.99.99.99&vipver=1&show_copyright_off=1"
            f"&newsearch=1&ft=music&cluster=0&strategy=2012&encoding=utf8&rformat=json"
        )
        req = urllib.request.Request(search_url, headers={"User-Agent": "okhttp/3.10.0"})
        with urllib.request.urlopen(req, timeout=4) as res:
            raw = res.read().decode("utf-8", errors="ignore")
            data = json.loads(raw.replace("'", '"')) if "{" in raw else {}
            abslist = data.get("abslist", [])
            if not abslist:
                return None
            
            for top in abslist[:4]:
                rid = top.get("MUSICRID", "").replace("MUSIC_", "")
                raw_title = top.get("SONGNAME", query)
                raw_artist = top.get("ARTIST", "")
                title = clean_html_tags(raw_title)
                artist = clean_html_tags(raw_artist)
                
                if not rid:
                    continue
                    
                play_api = f"https://antiserver.kuwo.cn/anti.s?type=convert_url&rid={rid}&format=mp3&response=url"
                req_play = urllib.request.Request(play_api, headers={"User-Agent": "okhttp/3.10.0"})
                with urllib.request.urlopen(req_play, timeout=4) as res_play:
                    audio_url = res_play.read().decode("utf-8", errors="ignore").strip()
                    if audio_url.startswith("http"):
                        http_audio_url = audio_url.replace("https://", "http://")
                        if logger:
                            logger.info(f"在线音乐解析候选: 《{title}》 - {artist} => {http_audio_url[:60]}...")
                        return (title, artist, http_audio_url)
    except Exception as e:
        if logger:
            logger.warning(f"在线音乐搜索异常: {e}")
    return None

def download_and_cache_music(audio_url: str, title: str, artist: str, cache_dir: str, logger=None) -> Optional[str]:
    """下载并验证完整歌曲，若小于 1.2MB 则视为试听片段直接拒绝"""
    try:
        os.makedirs(cache_dir, exist_ok=True)
        safe_name = f"{artist} - {title}".replace("/", "_").replace("\\", "_").replace(":", "_").strip()
        url_hash = hashlib.md5(audio_url.encode()).hexdigest()[:8]
        file_name = f"{safe_name}_{url_hash}.mp3" if safe_name != " - " else f"online_{url_hash}.mp3"
        target_path = os.path.join(cache_dir, file_name)
        
        # 命中缓存且满足完整曲目大小
        if os.path.exists(target_path) and os.path.getsize(target_path) >= MIN_FULL_SONG_BYTES:
            if logger:
                logger.info(f"命中完整本地音乐缓存: {target_path} ({os.path.getsize(target_path)/1024/1024:.2f} MB)")
            return target_path
            
        if logger:
            logger.info(f"开始高速下载歌曲: 《{title}》 到 {target_path}")
            
        req = urllib.request.Request(audio_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=10) as response:
            with open(target_path, "wb") as f:
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    
        file_size = os.path.getsize(target_path) if os.path.exists(target_path) else 0
        if file_size >= MIN_FULL_SONG_BYTES:
            if logger:
                logger.info(f"完整歌曲下载验证成功: {file_size / 1024 / 1024:.2f} MB")
            return target_path
        else:
            if logger:
                logger.warning(f"下载文件过小 ({file_size/1024:.1f} KB < 1.2MB)，判定为试听片段或版权保护限制，已自动过滤丢弃！")
            if os.path.exists(target_path):
                os.remove(target_path)
            return None
    except Exception as e:
        if logger:
            logger.error(f"下载在线音乐失败: {e}")
    return None

def get_music_files(music_dir, music_ext):
    music_dir = Path(music_dir)
    music_files = []
    music_file_names = []
    if not music_dir.exists():
        return music_files, music_file_names
    for file in music_dir.rglob("*"):
        if "cache" in file.parts:
            continue
        if file.is_file() and file.suffix.lower() in music_ext:
            rel = str(file.relative_to(music_dir))
            music_files.append(rel)
            music_file_names.append(os.path.splitext(rel)[0])
    return music_files, music_file_names

def _find_best_match(potential_song, music_files):
    best_match = None
    highest_ratio = 0
    for music_file in music_files:
        song_name = os.path.splitext(music_file)[0]
        ratio = difflib.SequenceMatcher(None, potential_song, song_name).ratio()
        if ratio > highest_ratio and ratio > 0.4:
            highest_ratio = ratio
            best_match = music_file
    return best_match

def initialize_music_handler(conn: "ConnectionHandler"):
    global MUSIC_CACHE
    if MUSIC_CACHE == {}:
        MUSIC_CACHE["music_dir"] = os.path.abspath("./music")
        MUSIC_CACHE["cache_dir"] = os.path.join(MUSIC_CACHE["music_dir"], "cache")
        MUSIC_CACHE["music_ext"] = (".mp3", ".wav", ".p3")
        MUSIC_CACHE["refresh_time"] = 60
        MUSIC_CACHE["music_files"], MUSIC_CACHE["music_file_names"] = get_music_files(
            MUSIC_CACHE["music_dir"], MUSIC_CACHE["music_ext"]
        )
        MUSIC_CACHE["scan_time"] = time.time()
    return MUSIC_CACHE

@register_function("play_music", play_music_function_desc, ToolType.SYSTEM_CTL)
async def play_music(conn: "ConnectionHandler", song_name: str):
    try:
        initialize_music_handler(conn)
        logger = conn.logger.bind(tag=TAG)
        
        query = song_name.strip() if song_name else ""
        is_random = not query or query.lower() in ["random", "none", "null", "随机", "随便", "音乐", "放首歌", "来首曲子", "播放歌曲"]
        if is_random:
            query = random.choice(POPULAR_RANDOM_SONGS)
            logger.info(f"用户请求随机播放，精选华语流行金曲: {query}")
        else:
            logger.info(f"收到点歌请求: {query}")

        music_file_to_play = None
        play_title = query
        play_artist = ""

        # 1. 优先在线拉取完整版歌曲（自动过滤 < 1.2MB 的试听片段）
        online_result = search_online_music(query, logger=logger)
        if online_result:
            title, artist, audio_url = online_result
            downloaded = download_and_cache_music(audio_url, title, artist, MUSIC_CACHE["cache_dir"], logger=logger)
            if downloaded:
                music_file_to_play = downloaded
                play_title = title
                play_artist = artist

        # 2. 如果在线没有完整版，查找本地 music/ 目录用户放置的高品质歌曲
        if not music_file_to_play and os.path.exists(MUSIC_CACHE["music_dir"]):
            if time.time() - MUSIC_CACHE.get("scan_time", 0) > MUSIC_CACHE["refresh_time"]:
                MUSIC_CACHE["music_files"], MUSIC_CACHE["music_file_names"] = get_music_files(
                    MUSIC_CACHE["music_dir"], MUSIC_CACHE["music_ext"]
                )
                MUSIC_CACHE["scan_time"] = time.time()

            match = _find_best_match(query, MUSIC_CACHE["music_files"]) if not is_random else None
            if match:
                music_file_to_play = os.path.join(MUSIC_CACHE["music_dir"], match)
                play_title = os.path.splitext(os.path.basename(match))[0]
            elif is_random and MUSIC_CACHE["music_files"]:
                local_choice = random.choice(MUSIC_CACHE["music_files"])
                music_file_to_play = os.path.join(MUSIC_CACHE["music_dir"], local_choice)
                play_title = os.path.splitext(os.path.basename(local_choice))[0]

        # 3. 严格保护：若确实没有完整歌曲，明确告知用户，绝不播放残缺片段或提示音
        if not music_file_to_play or not os.path.exists(music_file_to_play):
            logger.warning(f"歌曲 {query} 未能获取到完整版音频，已拦截。")
            return ActionResponse(
                action=Action.RESPONSE, 
                result="未找到完整版音乐", 
                response=f"抱歉，歌曲《{query}》在线版本受版权保护仅有试听片段，暂无法完整播放，换一首试试吧。"
            )

        # 4. 播报自然引导语
        if play_artist and play_artist.strip():
            prompts = [
                f"正在为您播放，{play_artist}的《{play_title}》",
                f"请欣赏，{play_artist}演唱的《{play_title}》",
                f"现在为您带来，{play_artist}的《{play_title}》",
                f"接下来让我们一起聆听，{play_artist}的《{play_title}》",
            ]
        else:
            prompts = [
                f"正在为您播放，《{play_title}》",
                f"请欣赏歌曲，《{play_title}》",
                f"现在为您带来，《{play_title}》",
                f"接下来请欣赏，《{play_title}》",
            ]
        prompt_text = random.choice(prompts)
        
        # 5. 推送 TTS 引导语 + 完整音乐流至 ESP32
        conn.tts.store_tts_text(conn.sentence_id, prompt_text)
        if conn.intent_type == "intent_llm":
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
                content_file=music_file_to_play,
            )
        )
        if conn.intent_type == "intent_llm":
            conn.tts.tts_text_queue.put(
                TTSMessageDTO(
                    sentence_id=conn.sentence_id,
                    sentence_type=SentenceType.LAST,
                    content_type=ContentType.ACTION,
                )
            )

        return ActionResponse(
            action=Action.RECORD, result="指令已接收", response=prompt_text
        )
    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"处理音乐播放异常: {e}\n{traceback.format_exc()}")
        return ActionResponse(
            action=Action.RESPONSE, result=str(e), response="播放音乐时出错了，请稍后再试。"
        )