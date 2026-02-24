"""
Session Manager — хранит активные SSH-сессии в памяти.
"""

import asyncio
import time
import uuid
from typing import Dict, Optional

from .ssh_client import SSHSession
from .sros_client import SROSSession
from .linux_client import LinuxSession


class SessionManager:
    def __init__(self, default_ttl: int = 3600):
        self._sessions: Dict[str, SSHSession] = {}
        self._labels: Dict[str, str] = {}  # label -> session_id
        self._created_at: Dict[str, float] = {}
        self._default_ttl = default_ttl
        self._lock = asyncio.Lock()

    def _new_id(self) -> str:
        return str(uuid.uuid4())[:8]

    async def create_session(
        self,
        host: str,
        username: str,
        password: Optional[str] = None,
        private_key: Optional[str] = None,
        port: int = 22,
        device_type: str = "linux",
        label: Optional[str] = None,
        timeout: int = 30,
    ) -> str:
        """Открыть новую SSH-сессию. Вернуть session_id."""
        async with self._lock:
            session_id = label or self._new_id()

            # Закрыть существующую сессию с тем же label/id
            if session_id in self._sessions:
                await self._sessions[session_id].close()

            if device_type == "sros":
                session = SROSSession()
            else:
                session = LinuxSession()

            await session.connect(
                host=host,
                port=port,
                username=username,
                password=password,
                private_key=private_key,
                timeout=timeout,
            )

            self._sessions[session_id] = session
            self._created_at[session_id] = time.time()
            if label:
                self._labels[label] = session_id

            return session_id

    async def get_session(self, session_id: str) -> SSHSession:
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session '{session_id}' not found. Use ssh_connect first.")
        return session

    async def close_session(self, session_id: str) -> None:
        async with self._lock:
            session = self._sessions.pop(session_id, None)
            if session:
                await session.close()
            self._created_at.pop(session_id, None)

    def list_sessions(self) -> list:
        result = []
        for sid, session in self._sessions.items():
            result.append({
                "session_id": sid,
                "host": session.host,
                "username": session.username,
                "device_type": session.device_type,
                "connected": session.is_connected,
                "age_seconds": int(time.time() - self._created_at.get(sid, 0)),
            })
        return result

    async def cleanup_expired(self) -> None:
        """Закрыть сессии старше TTL."""
        now = time.time()
        expired = [
            sid for sid, t in self._created_at.items()
            if now - t > self._default_ttl
        ]
        for sid in expired:
            await self.close_session(sid)

    async def close_all(self) -> None:
        async with self._lock:
            for session in self._sessions.values():
                try:
                    await session.close()
                except Exception:
                    pass
            self._sessions.clear()
            self._created_at.clear()
