"""
Linux SSH клиент — поддержка bash/zsh/fish.
Запускаем явный bash с контролируемым промптом.
"""

import asyncio
import re

from .ssh_client import SSHSession

MCP_PROMPT = "MCPSSH>"
MCP_PROMPT_RE = r"MCPSSH>"

# Широкий паттерн для первого промпта (любой shell включая zsh fancy)
INITIAL_PROMPT_RE = r"[\$#>%]\s*$|└──>\s*$|»\s*$|❯\s*$"


class LinuxSession(SSHSession):
    """SSH-сессия для Linux-хостов."""

    def __init__(self):
        super().__init__()
        self.device_type = "linux"
        self._prompt_re = INITIAL_PROMPT_RE

    @property
    def _prompt_pattern(self) -> str:
        return self._prompt_re

    async def _post_connect(self) -> None:
        """Дождаться любого промпта, запустить bash с нашим промптом."""
        await self._read_until(INITIAL_PROMPT_RE, timeout=20)

        # Запускаем bash явно (работает из любого shell включая zsh/fish)
        await self._send("env -i HOME=$HOME USER=$USER TERM=dumb bash --norc --noprofile\n")
        await asyncio.sleep(0.8)

        # Устанавливаем уникальный промпт + чистим окружение
        await self._send(
            f"PS1='{MCP_PROMPT} '; export PS1; export TERM=dumb; unset HISTFILE\n"
        )
        await asyncio.sleep(0.5)

        # Переключаемся на наш промпт
        self._prompt_re = MCP_PROMPT_RE
        await self._read_until(self._prompt_re, timeout=10)

    async def exec(self, command: str, timeout: float = 60.0) -> str:
        """Выполнить shell-команду и вернуть вывод."""
        return await self.send_command(command, timeout=timeout)

    async def exec_multi(self, commands: list[str], timeout: float = 60.0) -> list[dict]:
        """Выполнить список команд последовательно."""
        results = []
        for cmd in commands:
            try:
                output = await self.exec(cmd, timeout=timeout)
                results.append({"command": cmd, "output": output, "error": None})
            except Exception as e:
                results.append({"command": cmd, "output": "", "error": str(e)})
        return results

    async def upload_text(self, remote_path: str, content: str) -> str:
        """Записать текст в файл через base64."""
        import base64
        encoded = base64.b64encode(content.encode()).decode()
        cmd = f"echo '{encoded}' | base64 -d > {remote_path}"
        return await self.exec(cmd)

    async def get_os_info(self) -> dict:
        """Получить базовую информацию об ОС."""
        hostname = await self.exec("hostname", timeout=10)
        uname = await self.exec("uname -a", timeout=10)
        try:
            os_release = await self.exec("cat /etc/os-release 2>/dev/null | head -5", timeout=10)
        except Exception:
            os_release = "N/A"
        return {
            "hostname": hostname.strip(),
            "uname": uname.strip(),
            "os_release": os_release.strip(),
        }
