"""仅供测试使用的官方 MCP stdio 服务，绝不进入生产镜像。"""

from mcp.server.fastmcp import FastMCP

server = FastMCP("御网智元 MCP 测试服务")


@server.tool()
def echo(text: str) -> dict[str, str]:
    """回显受控文本，验证 tools/list 与 tools/call 链路。"""

    return {"echoed": text}


if __name__ == "__main__":
    server.run(transport="stdio")
