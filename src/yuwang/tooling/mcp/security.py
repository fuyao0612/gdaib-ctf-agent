"""MCP 传输安全校验，禁止把管理配置退化为任意 Shell 或 SSRF 通道。"""

from __future__ import annotations

import ipaddress
import socket
from pathlib import Path
from urllib.parse import urlsplit

from .models import McpServerConfig

_SHELL_NAMES = {"sh", "bash", "zsh", "fish", "cmd", "cmd.exe", "powershell", "pwsh"}
_SHELL_METACHARACTERS = {"|", "&", ";", ">", "<", "`", "\n", "\r"}
_LOCAL_NAMES = {"localhost", "127.0.0.1", "::1"}


def _normalized_command(value: str) -> str:
    try:
        return str(Path(value).resolve()).casefold()
    except OSError:
        return value.casefold()


def validate_stdio_config(config: McpServerConfig, allowed_commands: set[str]) -> None:
    if not config.command:
        raise ValueError("stdio MCP 缺少 command")
    command_name = Path(config.command).name.casefold()
    if command_name in _SHELL_NAMES:
        raise ValueError("stdio MCP 禁止使用 Shell 解释器")
    if any(character in config.command for character in _SHELL_METACHARACTERS):
        raise ValueError("stdio MCP command 不能包含 Shell 元字符")
    if not allowed_commands or _normalized_command(config.command) not in allowed_commands:
        raise ValueError("stdio MCP 可执行程序不在管理员允许列表中")
    for argument in config.args:
        if not argument or any(character in argument for character in _SHELL_METACHARACTERS):
            raise ValueError("stdio MCP args 不能包含 Shell 元字符或空参数")


def validate_http_config(config: McpServerConfig, *, allow_insecure_local: bool) -> None:
    if not config.url:
        raise ValueError("Streamable HTTP MCP 缺少 url")
    parsed = urlsplit(config.url)
    host = parsed.hostname
    if parsed.username or parsed.password or parsed.query or parsed.fragment or not host:
        raise ValueError("MCP URL 禁止内嵌凭据、查询参数或片段")
    local = host.casefold() in _LOCAL_NAMES
    try:
        local = local or ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    if parsed.scheme != "https" and not (allow_insecure_local and local and parsed.scheme == "http"):
        raise ValueError("MCP HTTP 服务必须使用 HTTPS；仅开发测试允许 localhost HTTP")


def assert_resolved_endpoint_is_safe(url: str, *, allow_insecure_local: bool) -> None:
    """连接前解析主机，阻断回环绕过、私网与云元数据地址。"""

    parsed = urlsplit(url)
    host = parsed.hostname
    if not host:
        raise ValueError("MCP URL 缺少主机名")
    local_name = host.casefold() in _LOCAL_NAMES
    try:
        values = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("无法解析 MCP 服务地址") from exc
    addresses = {item[4][0] for item in values}
    if not addresses:
        raise ValueError("MCP 服务地址没有可用解析结果")
    for address in addresses:
        value = ipaddress.ip_address(address)
        if value.is_loopback:
            if allow_insecure_local and local_name:
                continue
            raise ValueError("生产 MCP 不允许访问回环地址")
        if value.is_private or value.is_link_local or value.is_reserved or value.is_unspecified:
            raise ValueError("MCP 服务地址指向受保护网络或云元数据范围")
