"""
Linux SSH клиент — обычный bash shell.
"""

import re
from typing import Optional

from .ssh_client import SSHSession


# Стандартные bash промпты:  user@host:~$   root@host:#   [user@host dir]$
LINUX_PROMPT_RE = r"[\$#]\s*$"


class LinuxSession(SSHSession):
    """SSH-сессия для Linux-хостов."""

    def __init__(self):
        super().__init__()
        self.device_type = "linux"

    @property
    def _prompt_pattern(self) -> str:
        return LINUX_PROMPT_RE

    async def _post_connect(self) -> None:
        """Дождаться промпта и настроить терминал."""
        await self._read_until(LINUX_PROMPT_RE, timeout=20)

        # Отключить цвета и специальные символы для чистого парсинга
        await self._send("export PS1='$ '\n")
        await self._read_until(LINUX_PROMPT_RE, timeout=10)

        await self._send("export TERM=dumb\n")
        await self._read_until(LINUX_PROMPT_RE, timeout=10)

        # Отключить history
        await self._send("unset HISTFILE\n")
        await self._read_until(LINUX_PROMPT_RE, timeout=10)

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
        """Записать текст в файл через echo/heredoc."""
        # Используем base64 для безопасной передачи
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
