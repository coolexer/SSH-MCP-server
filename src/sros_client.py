"""
Nokia SR OS MD-CLI клиент (model-driven режим, SR OS 23+).

Особенности:
- Вход в configure: edit-config exclusive / edit-config global
- Команды конфигурации с полным путём: /configure router Base interface system ...
- Выход: quit-config
- commit / discard
- Промпт operational: [/]\nA:admin@pe1#
- Промпт configure: (ex)[/]\nA:admin@pe1#  или  *(ex)[/configure/...]\nA:admin@pe1#
"""

import re
from typing import Optional

from .ssh_client import SSHSession


# Промпт MD-CLI — последняя строка всегда: A:<user>@<host>#
SROS_PROMPT_RE = r"[\*\!]?\(?[\w\-]*\)?(\[.*?\])?\r?\n[AB]:[^\s#@]+@[^\s#]+#\s*$"


class SROSSession(SSHSession):
    """SSH-сессия для Nokia SR OS в MD-CLI model-driven режиме."""

    def __init__(self):
        super().__init__()
        self.device_type = "sros"
        self._hostname: str = ""

    @property
    def _prompt_pattern(self) -> str:
        return SROS_PROMPT_RE

    async def _post_connect(self) -> None:
        """Дождаться приветствия и отключить пейджинг."""
        banner = await self._read_until(SROS_PROMPT_RE, timeout=30)
        # Извлечь hostname
        m = re.search(r"[AB]:[^\s#@]+@([^\s#]+)#", banner)
        if m:
            self._hostname = m.group(1)
        # Отключить пейджинг
        await self.send_command("environment more false", timeout=10)

    async def cli(self, command: str, timeout: float = 60.0) -> str:
        """
        Выполнить MD-CLI операционную команду.
        Для show-команд автоматически добавляет | no-more.
        """
        cmd = command.strip()
        if cmd.lower().startswith("show") and "| no-more" not in cmd:
            cmd = cmd + " | no-more"
        return await self.send_command(cmd, timeout=timeout)

    async def configure(self, commands: list[str], commit: bool = True) -> dict:
        """
        Выполнить блок конфигурации в model-driven MD-CLI.

        commands: список команд с полным путём, например:
            ["/configure router Base interface system ipv4 primary address 1.1.1.1 prefix-length 32"]
        commit: True = commit, False = discard
        """
        results = []

        # Войти в edit-config exclusive
        enter_out = await self.send_command("edit-config exclusive", timeout=15)
        results.append({"command": "edit-config exclusive", "output": enter_out})

        for cmd in commands:
            out = await self.send_command(cmd, timeout=30)
            results.append({"command": cmd, "output": out})

        # Commit или discard
        if commit:
            commit_out = await self.send_command("commit", timeout=30)
            results.append({"command": "commit", "output": commit_out})
        else:
            discard_out = await self.send_command("discard", timeout=15)
            results.append({"command": "discard", "output": discard_out})

        # Выйти из configure режима
        quit_out = await self.send_command("quit-config", timeout=10)
        results.append({"command": "quit-config", "output": quit_out})

        return {
            "committed": commit,
            "steps": results,
        }

    async def get_context(self) -> str:
        """Получить текущий CLI-контекст (pwc)."""
        output = await self.send_command("pwc", timeout=10)
        m = re.search(r"Current context:\s*(.+)", output)
        return m.group(1).strip() if m else output.strip()

    async def rollback(self, index: int = 1) -> str:
        """Откатить конфигурацию на N шагов назад."""
        return await self.send_command(f"rollback {index}", timeout=30)
