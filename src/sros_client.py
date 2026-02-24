"""
Nokia SR OS MD-CLI клиент.

Особенности:
- Промпт: [/<context>]\nA:hostname#   или   *(<state>)[/<context>]\nA:hostname#
- Отключаем пейджинг сразу после входа: environment more false
- configure режим: /configure  (потом commit / discard)
- Команды с | no-more для show
"""

import re
from typing import Optional

from .ssh_client import SSHSession


# Промпт MD-CLI: может быть многострочным.
# Последняя строка всегда: A:<hostname># или (A|B):<hostname>#
SROS_PROMPT_RE = r"[\*\!]?[\(\[]?[\w\-/\.\:]*[\)\]]?\n?[AB]:[^\s#]+[#>]\s*$"


class SROSSession(SSHSession):
    """SSH-сессия для Nokia SR OS в MD-CLI режиме."""

    def __init__(self):
        super().__init__()
        self.device_type = "sros"
        self._hostname: str = ""
        self._in_configure: bool = False
        self._current_context: str = "/"

    @property
    def _prompt_pattern(self) -> str:
        return SROS_PROMPT_RE

    async def _post_connect(self) -> None:
        """Дождаться приветствия и отключить пейджинг."""
        # Ждём первый промпт
        banner = await self._read_until(SROS_PROMPT_RE, timeout=30)
        # Извлечь hostname из промпта
        m = re.search(r"[AB]:([^\s#>]+)[#>]", banner)
        if m:
            self._hostname = m.group(1)

        # Отключить пейджинг
        await self.send_command("environment more false", timeout=10)
        # Убедиться что в operational mode (не в configure)
        await self.send_command("/", timeout=10)

    async def cli(self, command: str, timeout: float = 60.0) -> str:
        """
        Выполнить MD-CLI команду.
        Для show-команд автоматически добавляет | no-more если не указано.
        """
        cmd = command.strip()
        if cmd.lower().startswith("show") and "| no-more" not in cmd:
            cmd = cmd + " | no-more"
        return await self.send_command(cmd, timeout=timeout)

    async def configure(self, commands: list[str], commit: bool = True) -> dict:
        """
        Войти в configure, выполнить список команд, commit или discard.
        
        commands: список конфигурационных команд
        commit: True = commit, False = discard
        
        Возвращает dict с результатами каждой команды и итогом.
        """
        results = []

        # Войти в configure exclusive или просто configure
        enter_output = await self.send_command("/configure", timeout=15)
        self._in_configure = True
        results.append({"command": "/configure", "output": enter_output})

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

        # Вернуться в operational
        await self.send_command("/", timeout=10)
        self._in_configure = False

        return {
            "committed": commit,
            "steps": results,
        }

    async def get_context(self) -> str:
        """Получить текущий CLI-контекст (pwd аналог)."""
        output = await self.send_command("pwc", timeout=10)
        # Парсим вывод: Current context: /configure/router[router-name=Base]
        m = re.search(r"Current context:\s*(.+)", output)
        if m:
            self._current_context = m.group(1).strip()
        return self._current_context

    async def rollback(self, index: int = 1) -> str:
        """Откатить конфигурацию на N шагов назад."""
        return await self.send_command(
            f"/rollback {index}", timeout=30
        )
