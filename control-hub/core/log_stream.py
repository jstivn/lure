import json
import logging
from datetime import datetime
from typing import List, Set, Optional, Dict, Any
from fastapi import WebSocket

logger = logging.getLogger("LogStream")


class LogStreamManager:
    """Manages real-time log event streaming and state signals over WebSockets to the UI."""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self.history: List[dict] = []
        self.max_history = 400

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)
        # Send recent history on connect
        for entry in self.history[-60:]:
            try:
                await websocket.send_text(json.dumps(entry))
            except Exception:
                pass

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)

    async def broadcast(
        self,
        channel: str,
        message: str,
        level: str = "info",
        event_type: str = "log",
        data: Optional[Dict[str, Any]] = None
    ):
        """
        Broadcasts a log entry or structured event to all active WebSocket clients:
        channel: 'FB' | 'IG' | 'WA1' | 'WA2' | 'META' | 'WA' | 'SYSTEM'
        level: 'info' | 'success' | 'warning' | 'error'
        event_type: 'log' | 'status_update' | 'message_sent' | 'cooldown_tick' | 'cooldown_done' | 'session_finished'
        """
        timestamp = datetime.now().strftime("%H:%M:%S")
        entry = {
            "type": event_type,
            "timestamp": timestamp,
            "channel": channel.upper(),
            "message": message,
            "level": level,
            "data": data or {}
        }

        self.history.append(entry)
        if len(self.history) > self.max_history:
            self.history.pop(0)

        # Print to terminal safely
        try:
            print(f"[{timestamp}] [{entry['channel']}] ({event_type}) {message}")
        except Exception:
            pass

        # Send to all connected WebSocket clients
        dead_connections = set()
        payload = json.dumps(entry)
        for conn in list(self.active_connections):
            try:
                await conn.send_text(payload)
            except Exception:
                dead_connections.add(conn)

        for conn in dead_connections:
            self.active_connections.discard(conn)


log_stream = LogStreamManager()
