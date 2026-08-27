import threading
from typing import Dict, Any

TAG = "[ConnectionRegistry]"

class ConnectionRegistry:
    _connections = {}
    _lock = threading.Lock()

    @classmethod
    def register(cls, device_id: str, handler: Any):
        with cls._lock:
            cls._connections[device_id] = handler
            print(f"{TAG} Registered device connection: {device_id}")

    @classmethod
    def unregister(cls, device_id: str):
        with cls._lock:
            if device_id in cls._connections:
                del cls._connections[device_id]
                print(f"{TAG} Unregistered device connection: {device_id}")

    @classmethod
    def get_active_connections(cls):
        with cls._lock:
            return list(cls._connections.values())

    @classmethod
    def broadcast_chat(cls, query: str):
        conns = cls.get_active_connections()
        if not conns:
            print(f"{TAG} No active device connected to speak: '{query}'")
            return False
        for handler in conns:
            try:
                if hasattr(handler, 'chat'):
                    print(f"{TAG} Triggering proactive chat on device...")
                    if hasattr(handler, 'loop') and handler.loop and handler.loop.is_running():
                        handler.loop.call_soon_threadsafe(handler.chat, query)
                    else:
                        handler.chat(query)
            except Exception as e:
                print(f"{TAG} Proactive chat dispatch error: {e}")
        return True
