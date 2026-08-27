import os
import json
from datetime import datetime
from plugins_func.register import register_function, ToolType, ActionResponse, Action
from config.logger import setup_logging

TAG = __name__
logger = setup_logging()

NOTES_DIR = "data/notes"
NOTES_JSON_PATH = os.path.join(NOTES_DIR, "user_notes.json")
NOTES_MD_PATH = os.path.join(NOTES_DIR, "我的随身笔记本.md")

def ensure_notes_dir():
    if not os.path.exists(NOTES_DIR):
        os.makedirs(NOTES_DIR, exist_ok=True)
    if not os.path.exists(NOTES_JSON_PATH):
        with open(NOTES_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)

def load_notes():
    ensure_notes_dir()
    try:
        with open(NOTES_JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_notes(notes):
    ensure_notes_dir()
    with open(NOTES_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(notes, f, ensure_ascii=False, indent=2)
    
    try:
        with open(NOTES_MD_PATH, "w", encoding="utf-8") as f:
            f.write("# 📝 小智随身笔记本 & 想法灵感库\n\n")
            f.write(f"> 最后更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write("| 序号 | 记录时间 | 分类 | 标题 | 详细内容 |\n")
            f.write("| :---: | :---: | :---: | :--- | :--- |\n")
            for idx, n in enumerate(notes, 1):
                f.write(f"| {idx} | {n.get('time', '')} | **{n.get('category', '随笔')}** | {n.get('title', '无标题')} | {n.get('content', '')} |\n")
            f.write("\n---\n")
    except Exception as e:
        logger.bind(tag=TAG).error(f"写入 Markdown 笔记失败: {e}")

record_note_desc = {
    "type": "function",
    "function": {
        "name": "record_note",
        "description": (
            "当用户想要记录想法、备忘录、待办事项、灵感、日记、日用清单或随笔时调用此工具。"
            "例如用户说：'帮我记一下...'、'记录一个想法...'、'把这个存进笔记本...'、'记个待办...'"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "用户要记录的具体想法、笔记或备忘内容（完整记录）"
                },
                "category": {
                    "type": "string",
                    "description": "分类标签，例如：灵感、待办、学习、工作、生活、备忘、随笔（默认为灵感或待办）"
                },
                "title": {
                    "type": "string",
                    "description": "简短摘要标题（4~10个字，概括该想法）"
                }
            },
            "required": ["content"]
        }
    }
}

@register_function("record_note", record_note_desc, ToolType.WAIT)
def record_note(content: str, category: str = "灵感", title: str = None):
    """记录笔记/想法到持久化笔记本中"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not title:
        title = content[:15] + "..." if len(content) > 15 else content
    if not category:
        category = "灵感"
    
    notes = load_notes()
    note_id = len(notes) + 1
    new_note = {
        "id": note_id,
        "time": now_str,
        "category": category,
        "title": title,
        "content": content
    }
    notes.append(new_note)
    save_notes(notes)
    
    logger.bind(tag=TAG).info(f"成功保存笔记: {new_note}")
    
    result_text = (
        f"已成功将用户的想法记录到笔记本中！\n"
        f"- 编号：第 {note_id} 条\n"
        f"- 分类：【{category}】\n"
        f"- 标题：{title}\n"
        f"- 内容：{content}\n"
        f"- 时间：{now_str}\n"
        f"请以自然、亲切、贴心的语音语气回复用户，告诉他已经妥善记录好了，并简要复述重点。"
    )
    return ActionResponse(Action.REQLLM, result_text, None)


read_notes_desc = {
    "type": "function",
    "function": {
        "name": "read_notes",
        "description": (
            "当用户想要查看、查询、翻阅、朗读自己的笔记本、待办事项、以往的想法或备忘录时调用此工具。"
            "例如用户说：'我之前记了什么？'、'看看我的笔记本'、'读一下我的待办事项'、'找找关于物理的笔记'"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词（如：物理、买东西、开会，不传则列出最近的笔记）"
                },
                "category": {
                    "type": "string",
                    "description": "按分类筛选（如：待办、灵感、学习）"
                },
                "limit": {
                    "type": "integer",
                    "description": "查询最近几条，默认 5 条"
                }
            },
            "required": []
        }
    }
}

@register_function("read_notes", read_notes_desc, ToolType.WAIT)
def read_notes(query: str = None, category: str = None, limit: int = 5):
    """查询并朗读用户的笔记"""
    notes = load_notes()
    if not notes:
        return ActionResponse(Action.REQLLM, "用户的笔记本目前是空的，尚未记录任何内容。请友好地提醒用户可以随时对您说出想法来进行记录。", None)
    
    filtered = notes
    if category:
        filtered = [n for n in filtered if category.lower() in n.get("category", "").lower()]
    if query:
        filtered = [n for n in filtered if query.lower() in n.get("content", "").lower() or query.lower() in n.get("title", "").lower()]
    
    if not filtered:
        return ActionResponse(Action.REQLLM, f"未找到与'{query or category}'相关的笔记。目前笔记本共有 {len(notes)} 条其他记录。", None)
    
    recent = filtered[-limit:]
    recent.reverse()
    
    res = f"查询到以下 {len(recent)} 条笔记（按最新时间排序）：\n"
    for n in recent:
        res += f"- [{n.get('time', '')}] 【{n.get('category', '随笔')}】 {n.get('title', '')}：{n.get('content', '')}\n"
    
    res += "\n请根据以上笔记内容，用条理清晰、口语化的语言为用户汇总朗读汇报。"
    return ActionResponse(Action.REQLLM, res, None)


delete_note_desc = {
    "type": "function",
    "function": {
        "name": "delete_note",
        "description": "当用户想要删除某条笔记、清除待办或清空笔记本时调用此工具。",
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "要删除的笔记关键词或内容描述，例如'刚才记的'、'关于买菜的'、'全部清空'"
                }
            },
            "required": ["keyword"]
        }
    }
}

@register_function("delete_note", delete_note_desc, ToolType.WAIT)
def delete_note(keyword: str):
    """删除或清空笔记"""
    notes = load_notes()
    if not notes:
        return ActionResponse(Action.REQLLM, "笔记本已经是空的，无需删除。", None)
    
    if "全部" in keyword or "所有" in keyword or "清空" in keyword:
        save_notes([])
        return ActionResponse(Action.REQLLM, "已成功清空所有笔记内容。请语音告知用户笔记本已清空。", None)
    
    target_idx = None
    for i in range(len(notes) - 1, -1, -1):
        if keyword in notes[i].get("content", "") or keyword in notes[i].get("title", "") or "刚才" in keyword:
            target_idx = i
            break
            
    if target_idx is not None:
        deleted = notes.pop(target_idx)
        save_notes(notes)
        return ActionResponse(Action.REQLLM, f"已成功删除笔记：【{deleted.get('title')}】（{deleted.get('content')}）。请语音确认已删除。", None)
    else:
        return ActionResponse(Action.REQLLM, f"未能找到包含'{keyword}'的相关笔记，无法删除。", None)