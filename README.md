# MCP SSH Server

MCP (Model Context Protocol) сервер для работы с **Nokia SR OS** и **Linux** хостами по SSH в интерактивном режиме.

## Особенности

- **Сессионная модель** — соединение живёт между вызовами инструментов
- **Креды в runtime** — никаких конфигов, пароли передаются в параметрах инструмента
- **SROS MD-CLI** — автоматическое отключение пейджинга, configure/commit/discard, распознавание промпта
- **Linux shell** — выполнение команд, загрузка файлов через base64
- **Множество сессий** — подключайтесь к нескольким устройствам одновременно

---

## Установка

### Через uv (рекомендуется)

```bash
# Клонировать репозиторий
git clone https://github.com/coolexer/SSH-MCP-server
cd SSH-MCP-server

# Установить зависимости
uv sync
```

### Через pip

```bash
pip install -e .
```

---

## Запуск

```bash
# Через uv
uv run python -m src.server

# Или после установки
mcp-ssh-server
```

---

## Конфигурация MCP клиента

Добавьте в `claude_desktop_config.json` (или аналог):

```json
{
  "mcpServers": {
    "ssh": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/SSH-MCP-server",
        "run",
        "python",
        "-m",
        "src.server"
      ]
    }
  }
}
```

---

## Инструменты

### Управление сессиями

| Tool | Описание |
|------|----------|
| `ssh_connect` | Открыть SSH-сессию. Параметры: `host`, `username`, `password`, `private_key`, `port`, `device_type` (`linux`/`sros`), `label`, `timeout` |
| `ssh_disconnect` | Закрыть сессию по `session_id` |
| `ssh_list_sessions` | Список всех активных сессий |

### Linux

| Tool | Описание |
|------|----------|
| `ssh_exec` | Выполнить команду в shell |
| `ssh_exec_multi` | Выполнить список команд последовательно |
| `ssh_send_raw` | Отправить raw-текст (для интерактивных программ) |
| `linux_os_info` | Получить hostname, uname, os-release |

### Nokia SR OS (MD-CLI)

| Tool | Описание |
|------|----------|
| `sros_cli` | Выполнить операционную команду (show, ping, etc.) |
| `sros_configure` | Выполнить блок конфигурации + commit/discard |
| `sros_get_context` | Получить текущий контекст CLI (`pwc`) |
| `sros_rollback` | Откатить конфигурацию на N шагов |

---

## Примеры использования

### Подключение к SR OS

```json
// ssh_connect
{
  "host": "192.168.0.1",
  "username": "admin",
  "password": "secret",
  "device_type": "sros",
  "label": "pe1"
}
// → {"session_id": "pe1", "status": "connected"}
```

### Show команда

```json
// sros_cli
{
  "session_id": "pe1",
  "command": "show router interface"
}
```

### Конфигурация

```json
// sros_configure
{
  "session_id": "pe1",
  "commands": [
    "router Base interface lo0",
    "ipv4 primary address 10.0.0.1 prefix-length 32",
    "no shutdown"
  ],
  "commit": true
}
```

### Linux хост

```json
// ssh_connect
{
  "host": "10.0.0.10",
  "username": "root",
  "password": "pass",
  "device_type": "linux",
  "label": "clab-vm"
}

// ssh_exec
{
  "session_id": "clab-vm",
  "command": "ip route show"
}
```

---

## Зависимости

- `mcp >= 1.0.0` — официальный MCP Python SDK
- `asyncssh >= 2.14.0` — асинхронный SSH клиент
- `pydantic >= 2.0.0`

---

## Безопасность

- Пароли **не логируются** и **не сохраняются** на диск
- `known_hosts` не проверяется (предназначено для lab/containerlab окружений)
- Для production рекомендуется SSH key авторизация и проверка host keys
- TTL сессий: по умолчанию 2 часа (настраивается в `SessionManager`)
