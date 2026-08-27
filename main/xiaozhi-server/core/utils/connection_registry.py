import threading
from typing import Dict, Any

TAG = "[ConnectionRegistry]"

class ConnectionRegistry:
    _connections: Dict[str, Any] = {}
    _lock = threading.Lock()

    @classmethod
    def register(cls, device_id: str, handler: Any):
        with cls._lock:
            cls._connections[device_id] = handler
            print(f"{TAG} Registered device connection: {device_id} (Total: {len(cls._connections)})")

    @classmethod
    def unregister(cls, device_id: str):
        with cls._lock:
            if device_id in cls._connections:
                del cls._connections[device_id]
                print(f"{TAG} Unregistered device connection: {device_id} (Remaining: {len(cls._connections)})")

    @classmethod
    def get_active_connections(cls):
        with cls._lock:
            return list(cls._connections.values())

    @classmethod
    def broadcast_proactive_chat(cls, prompt: str):
        conns = cls.get_active_connections()
        if not conns:
            print(f"{TAG} No active ESP32 device connected to speak: '{prompt[:40]}...'")
            return False
        success = False
        for handler in conns:
            try:
                if hasattr(handler, 'proactive_wake_and_chat'):
                    print(f"{TAG} Waking up device {getattr(handler, 'device_id', 'unknown')} and initiating dialogue...")
                    handler.proactive_wake_and_chat(prompt)
                    success = True
                elif hasattr(handler, 'chat'):
                    print(f"{TAG} Falling back to chat on device {getattr(handler, 'device_id', 'unknown')}...")
                    if hasattr(handler, 'executor') and handler.executor:
                        handler.executor.submit(handler.chat, prompt)
                    else:
                        handler.chat(prompt)
                    success = True
            except Exception as e:
                print(f"{TAG} Proactive wake dispatch error: {e}")
        return success

    @classmethod
    def broadcast_chat(cls, query: str):
        return cls.broadcast_proactive_chat(query)
