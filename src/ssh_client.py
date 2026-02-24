"""
Базовый SSH-клиент на asyncssh с фоновым reader'ом для правильной буферизации.
"""

import asyncio
import re
from abc import ABC, abstractmethod
from typing import Optional

import asyncssh


class SSHSession(ABC):
    """Абстрактная SSH-сессия с интерактивным shell."""

    def __init__(self):
        self.host: str = ""
        self.username: str = ""
        self.device_type: str = "generic"
        self.is_connected: bool = False
        self._conn: Optional[asyncssh.SSHClientConnection] = None
        self._process: Optional[asyncssh.SSHClientProcess] = None
        self._buffer: str = ""
        self._buffer_lock = asyncio.Lock()
        self._reader_task: Optional[asyncio.Task] = None

    async def connect(
        self,
        host: str,
        port: int = 22,
        username: str = "",
        password: Optional[str] = None,
        private_key: Optional[str] = None,
        timeout: int = 30,
    ) -> None:
        self.host = host
        self.username = username

        connect_kwargs = dict(
            host=host,
            port=port,
            username=username,
            known_hosts=None,
            connect_timeout=timeout,
        )

        if private_key:
            connect_kwargs["client_keys"] = [asyncssh.import_private_key(private_key)]
        elif password:
            connect_kwargs["password"] = password
            connect_kwargs["preferred_auth"] = ["password", "keyboard-interactive"]

        self._conn = await asyncssh.connect(**connect_kwargs)
        self._process = await self._conn.create_process(
            term_type="vt100",
            term_size=(220, 50),
        )
        self.is_connected = True

        # Запускаем фоновый reader
        self._reader_task = asyncio.create_task(self._background_reader())

        await self._post_connect()

    async def _background_reader(self) -> None:
        """Постоянно читает stdout и накапливает в буфер."""
        try:
            while self.is_connected:
                try:
                    chunk = await asyncio.wait_for(
                        self._process.stdout.read(4096),
                        timeout=0.1,
                    )
                    if chunk:
                        async with self._buffer_lock:
                            self._buffer += chunk
                    elif chunk == "":
                        break
                except asyncio.TimeoutError:
                    continue
                except Exception:
                    break
        except Exception:
            pass

    async def _read_until(self, pattern: str, timeout: float = 30.0) -> str:
        """Читать буфер до появления паттерна (regex)."""
        compiled = re.compile(pattern, re.MULTILINE | re.DOTALL)
        deadline = asyncio.get_event_loop().time() + timeout

        while True:
            async with self._buffer_lock:
                if compiled.search(self._buffer):
                    output = self._buffer
                    self._buffer = ""
                    return output

            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                async with self._buffer_lock:
                    buf = self._buffer
                raise TimeoutError(
                    f"Timeout waiting for pattern '{pattern}'.\nBuffer:\n{buf}"
                )
            await asyncio.sleep(0.05)

    async def _send(self, text: str) -> None:
        self._process.stdin.write(text)

    async def send_command(self, command: str, timeout: float = 30.0) -> str:
        """Отправить команду и вернуть вывод до следующего промпта."""
        async with self._buffer_lock:
            self._buffer = ""
        await self._send(command + "\n")
        output = await self._read_until(self._prompt_pattern, timeout=timeout)
        return self._clean_output(output, command)

    def _clean_output(self, raw: str, command: str) -> str:
        """Убрать ANSI escape codes, эхо команды и промпт из вывода."""
        ansi_escape = re.compile(
            r'\x1b\[[0-9;]*[mABCDEFGHJKSTfnsu]'
            r'|\x1b\[[\?][0-9;]*[hl]'
            r'|\x1b[=>]|\r'
        )
        cleaned = ansi_escape.sub('', raw)

        lines = cleaned.split('\n')
        result = []
        skip_echo = True
        prompt_re = re.compile(self._prompt_pattern)

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if skip_echo and command.strip() in stripped:
                skip_echo = False
                continue
            if prompt_re.search(stripped):
                continue
            result.append(line.rstrip())

        return "\n".join(result).strip()

    async def send_raw(self, text: str, wait_seconds: float = 1.0) -> str:
        """Отправить raw текст и вернуть всё накопленное за wait_seconds."""
        async with self._buffer_lock:
            self._buffer = ""
        await self._send(text)
        await asyncio.sleep(wait_seconds)
        async with self._buffer_lock:
            output = self._buffer
            self._buffer = ""
        return output

    @property
    @abstractmethod
    def _prompt_pattern(self) -> str:
        """Regex паттерн для распознавания промпта."""

    @abstractmethod
    async def _post_connect(self) -> None:
        """Действия после подключения."""

    async def close(self) -> None:
        self.is_connected = False
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        if self._process:
            try:
                self._process.stdin.write_eof()
            except Exception:
                pass
        if self._conn:
            self._conn.close()
            try:
                await self._conn.wait_closed()
            except Exception:
                pass
