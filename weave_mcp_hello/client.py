import asyncio
import sys
import weave
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

weave_client = weave.init("afitzc-mit/weave-mcp-hello")

async def main():
    server_script_path = sys.argv[1] if len(sys.argv) > 1 else "server.py"
    
    server_params = StdioServerParameters(
        command="python",
        args=[server_script_path],
    )
    
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            
            print("=== Weave MCP Hello World ===\n")
            
            print("1. Calling tool: hello('World')")
            result = await session.call_tool("hello", arguments={"name": "World"})
            print(f"   Result: {result.content[0].text}\n")
            
            print("2. Reading resource: message://greeting")
            resource = await session.read_resource("message://greeting")
            print(f"   Content: {resource.contents[0].text}\n")
            
            print("3. Getting prompt: welcome_prompt('Alice')")
            prompt = await session.get_prompt("welcome_prompt", arguments={"user": "Alice"})
            print(f"   Prompt: {prompt.messages[0].content.text}\n")
            
            print("✓ All operations traced in Weave!")
            print("  View at: https://wandb.ai/afitzc-mit/weave-mcp-hello")

if __name__ == "__main__":
    asyncio.run(main())
