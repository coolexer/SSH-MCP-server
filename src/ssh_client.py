import asyncio
import re
from abc import ABC, abstractmethod
from typing import Optional
import asyncssh

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b[=>]|\r")


class SSHSession(ABC):
    def __init__(self):
        self.host: str = ""
        self.username: str = ""
        self.device_type: str = "generic"
        self.is_connected: bool = False
        self._conn = None
        self._process = None
        self._buffer: str = ""
        self._buffer_lock = asyncio.Lock()
        self._reader_task = None

    async def connect(self, host, port=22, username="", password=None,
                      private_key=None, timeout=30):
        self.host = host
        self.username = username
        kw = dict(host=host, port=port, username=username,
                  known_hosts=None, connect_timeout=timeout)
        if private_key:
            kw["client_keys"] = [asyncssh.import_private_key(private_key)]
        elif password:
            kw["password"] = password
            kw["preferred_auth"] = ["password", "keyboard-interactive"]
        self._conn = await asyncssh.connect(**kw)
        self._process = await self._conn.create_process(
            term_type="vt100", term_size=(220, 50))
        self.is_connected = True
        self._reader_task = asyncio.create_task(self._background_reader())
        await self._post_connect()

    async def _background_reader(self):
        try:
            while self.is_connected:
                try:
                    chunk = await asyncio.wait_for(
                        self._process.stdout.read(4096), timeout=0.1)
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

    async def _read_until(self, pattern, timeout=30.0):
        compiled = re.compile(pattern, re.MULTILINE | re.DOTALL)
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            async with self._buffer_lock:
                clean = ANSI_RE.sub("", self._buffer)
                if compiled.search(clean):
                    output = self._buffer
                    self._buffer = ""
                    return output
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                async with self._buffer_lock:
                    buf = self._buffer
                raise TimeoutError(
                    f"Timeout waiting for '{pattern}'.\nBuffer:\n{buf}")
            await asyncio.sleep(0.05)

    async def _send(self, text):
        self._process.stdin.write(text)

    async def send_command(self, command, timeout=30.0):
        async with self._buffer_lock:
            self._buffer = ""
        await self._send(command + "\n")
        output = await self._read_until(self._prompt_pattern, timeout=timeout)
        return self._clean_output(output, command)

    def _clean_output(self, raw, command):
        cleaned = ANSI_RE.sub("", raw)
        lines = cleaned.split("\n")
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

    async def send_raw(self, text, wait_seconds=1.0):
        async with self._buffer_lock:
            self._buffer = ""
        await self._send(text)
        await asyncio.sleep(wait_seconds)
        async with self._buffer_lock:
            output = self._buffer
            self._buffer = ""
        return ANSI_RE.sub("", output)

    @property
    @abstractmethod
    def _prompt_pattern(self):
        pass

    @abstractmethod
    async def _post_connect(self):
        pass

    async def close(self):
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
