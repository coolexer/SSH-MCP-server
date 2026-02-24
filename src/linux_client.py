"""
Linux SSH клиент — bash shell с поддержкой нестандартных промптов.
"""

import asyncio
import re
from typing import Optional

from .ssh_client import SSHSession

# После нашего PS1='$ ' промпт будет просто "$ "
# Но при первом подключении может быть любой fancy промпт
# Используем широкий паттерн для первого чтения
LINUX_PROMPT_INITIAL_RE = r"[\$#>]\s*$|└──>\s*$|»\s*$|❯\s*$"
LINUX_PROMPT_SIMPLE_RE = r"^\$\s"  # после установки PS1='$ '


class LinuxSession(SSHSession):
    """SSH-сессия для Linux-хостов."""

    def __init__(self):
        super().__init__()
        self.device_type = "linux"
        self._prompt_re = LINUX_PROMPT_INITIAL_RE

    @property
    def _prompt_pattern(self) -> str:
        return self._prompt_re

    async def _post_connect(self) -> None:
        """Дождаться промпта и упростить его для надёжного парсинга."""
        # Ждём любой промпт при первом подключении
        await self._read_until(LINUX_PROMPT_INITIAL_RE, timeout=20)

        # Устанавливаем простой промпт
        await self._send("export PS1='MCPPROMPT$ '\n")
        await asyncio.sleep(0.5)
        # Переключаемся на новый паттерн
        self._prompt_re = r"MCPPROMPT\$\s"
        await self._read_until(self._prompt_re, timeout=10)

        # Чистим окружение
        await self._send("export TERM=dumb; unset HISTFILE\n")
        await self._read_until(self._prompt_re, timeout=5)

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
