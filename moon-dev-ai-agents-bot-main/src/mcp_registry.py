"""
🔌 Moon Dev's MCP Integration — External Service Connectivity
DSH Pattern: mcp-client — register external tools via Model Context Protocol.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Any


@dataclass
class MCPTool:
    name: str
    description: str
    endpoint: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    provider: str = ""

    def to_dict(self):
        return {'name': self.name, 'description': self.description,
                'endpoint': self.endpoint, 'provider': self.provider, 'enabled': self.enabled}


@dataclass
class MCPServer:
    name: str
    url: str
    tools: List[MCPTool] = field(default_factory=list)
    connected: bool = False

    def to_dict(self):
        return {'name': self.name, 'url': self.url, 'connected': self.connected,
                'tools': [t.to_dict() for t in self.tools]}


class MCPRegistry:
    """Registry for external MCP-compatible trading services."""

    def __init__(self):
        self._servers: Dict[str, MCPServer] = {}
        self._tools: Dict[str, MCPTool] = {}

    def register_server(self, name: str, url: str) -> MCPServer:
        server = MCPServer(name=name, url=url)
        self._servers[name] = server
        return server

    def register_tool(self, server_name: str, tool: MCPTool):
        if server_name in self._servers:
            self._servers[server_name].tools.append(tool)
            self._tools[tool.name] = tool

    def get_tool(self, name: str) -> Optional[MCPTool]:
        return self._tools.get(name)

    def list_tools(self) -> List[dict]:
        return [t.to_dict() for t in self._tools.values()]

    def list_servers(self) -> List[dict]:
        return [s.to_dict() for s in self._servers.values()]

    async def call_tool(self, name: str, params: dict = None) -> dict:
        tool = self._tools.get(name)
        if not tool or not tool.enabled:
            return {'error': f'Tool {name} not found or disabled'}
        # In production, this would make HTTP call to the MCP server
        return {'tool': name, 'params': params, 'status': 'simulated'}


def create_default_mcp_registry() -> MCPRegistry:
    """Create MCP registry with common trading service stubs."""
    registry = MCPRegistry()

    for name, url, tools in [
        ('coinglass', 'https://api.coinglass.com', ['funding_rates', 'open_interest', 'liquidations']),
        ('birdeye', 'https://public-api.birdeye.so', ['token_info', 'price_history', 'holders']),
        ('helius', 'https://api.helius.xyz', ['on_chain_data', 'transaction_history']),
        ('defillama', 'https://api.llama.fi', ['tvl', 'protocols', 'yields']),
    ]:
        server = registry.register_server(name, url)
        for tool_name in tools:
            registry.register_tool(name, MCPTool(
                name=f"{name}/{tool_name}", description=f"{tool_name} from {name}",
                endpoint=f"{url}/{tool_name}", provider=name,
            ))

    return registry
