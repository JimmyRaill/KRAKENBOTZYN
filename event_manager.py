import asyncio
from typing import Dict, Set
from datetime import datetime

class EventManager:
    def __init__(self):
        self._queues: Dict[str, Set[asyncio.Queue]] = {}
        self._active_requests: Set[str] = set()
    
    def subscribe(self, request_id: str) -> asyncio.Queue:
        queue = asyncio.Queue()
        if request_id not in self._queues:
            self._queues[request_id] = set()
        self._queues[request_id].add(queue)
        return queue
    
    def unsubscribe(self, request_id: str, queue: asyncio.Queue):
        if request_id in self._queues:
            self._queues[request_id].discard(queue)
            if not self._queues[request_id]:
                del self._queues[request_id]
    
    async def emit(self, request_id: str, event_type: str, data: dict = None):
        if request_id not in self._queues:
            return
        
        event_data = {
            "type": event_type,
            "timestamp": datetime.utcnow().isoformat(),
            "data": data or {}
        }
        
        for queue in self._queues[request_id]:
            try:
                await queue.put(event_data)
            except Exception:
                pass
    
    def typing_start(self, request_id: str):
        self._active_requests.add(request_id)
        asyncio.create_task(self.emit(request_id, "typing_start", {"status": "thinking"}))
    
    def typing_stop(self, request_id: str):
        self._active_requests.discard(request_id)
        asyncio.create_task(self.emit(request_id, "typing_stop", {"status": "complete"}))

event_manager = EventManager()
