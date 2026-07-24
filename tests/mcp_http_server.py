"""仅供 Streamable HTTP 协议测试的官方 MCP ASGI 应用。"""

from tests.mcp_test_server import server

app = server.streamable_http_app()
