# Weave MCP Hello World

A minimal example demonstrating Weave integration with Model Context Protocol (MCP).

## What This Does

This example shows how to:
- Create an MCP server with a tool, resource, and prompt
- Connect a client to the server
- Trace all MCP operations in Weave for observability

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

## Run It

```bash
python client.py server.py
```

This will:
1. Start the MCP server
2. Connect the client to it
3. Call a tool (`hello`)
4. Read a resource (`message://greeting`)
5. Get a prompt (`welcome_prompt`)
6. Trace everything to your Weave project

## View Traces

After running, view your traces at:
https://wandb.ai/afitzc-mit/weave-mcp-hello

## What's Being Traced

- **Tools**: Function calls like `hello(name="World")`
- **Resources**: Data reads like `message://greeting`
- **Prompts**: Template generations like `welcome_prompt(user="Alice")`

All interactions between client and server are automatically logged to Weave!
