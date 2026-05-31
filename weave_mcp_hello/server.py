import weave
from mcp.server.fastmcp import FastMCP

weave_client = weave.init("afitzc-mit/weave-mcp-hello")

mcp = FastMCP("HelloWorld")

@mcp.tool()
def hello(name: str) -> str:
    """Say hello to someone."""
    return f"Hello, {name}! Welcome to Weave MCP."

@mcp.resource("message://greeting")
def get_greeting() -> str:
    """Get a friendly greeting message."""
    return "Greetings from your MCP server!"

@mcp.prompt()
def welcome_prompt(user: str) -> str:
    """Generate a welcome prompt."""
    return f"Welcome {user}! This is your first Weave MCP integration."

if __name__ == "__main__":
    mcp.run(transport="stdio")
