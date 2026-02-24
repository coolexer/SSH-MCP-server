"""
Базовый SSH-клиент на asyncssh с поддержкой интерактивного shell.
"""

import asyncio
import re
from abc import ABC, abstractmethod
from typing import Optional

import asyncssh


class SSHSession(ABC):
    """Абстрактная SSH-сессия."""

    def __init__(self):
        self.host: str = ""
        self.username: str = ""
        self.device_type: str = "generic"
        self.is_connected: bool = False
        self._conn: Optional[asyncssh.SSHClientConnection] = None
        self._process: Optional[asyncssh.SSHClientProcess] = None
        self._buffer: str = ""

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
            known_hosts=None,  # Не проверяем host keys (lab environment)
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
        await self._post_connect()

    @abstractmethod
    async def _post_connect(self) -> None:
        """Действия после подключения (приветствие, отключение пейджинга и т.д.)"""

    async def _read_until(
        self,
        pattern: str,
        timeout: float = 30.0,
        strip_input_echo: bool = True,
    ) -> str:
        """Читать вывод до появления паттерна (regex)."""
        compiled = re.compile(pattern, re.MULTILINE)
        deadline = asyncio.get_event_loop().time() + timeout
        output = self._buffer

        while True:
            if compiled.search(output):
                self._buffer = ""
                return output

            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError(
                    f"Timeout waiting for pattern '{pattern}'. "
                    f"Buffer so far:\n{output}"
                )

            try:
                chunk = await asyncio.wait_for(
                    self._process.stdout.read(4096),
                    timeout=min(remaining, 1.0),
                )
                if chunk:
                    output += chunk
                else:
                    await asyncio.sleep(0.05)
            except asyncio.TimeoutError:
                continue

    async def _send(self, text: str) -> None:
        self._process.stdin.write(text)

    async def send_command(self, command: str, timeout: float = 30.0) -> str:
        """Отправить команду и вернуть вывод до следующего промпта."""
        await self._send(command + "\n")
        output = await self._read_until(self._prompt_pattern, timeout=timeout)
        return self._clean_output(output, command)

    def _clean_output(self, raw: str, command: str) -> str:
        """Убрать эхо команды и промпт из вывода."""
        lines = raw.splitlines()
        result = []
        skip_echo = True
        for line in lines:
            stripped = line.strip()
            # Пропустить эхо команды
            if skip_echo and command.strip() in stripped:
                skip_echo = False
                continue
            # Пропустить строку промпта
            if re.search(self._prompt_pattern, stripped):
                continue
            result.append(line)
        return "\n".join(result).strip()

    @property
    @abstractmethod
    def _prompt_pattern(self) -> str:
        """Regex паттерн для распознавания промпта."""

    async def close(self) -> None:
        self.is_connected = False
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
