# Grant Tools MCP setup

Grant Tools runs as an independent local MCP server. You need this repository,
Python 3.11 or newer, an MCP client, and either a country data download or your
own build of the source tables. The Research MCP is not required.

Follow the [README quickstart](../README.md#quickstart) to install Python
dependencies, get USA or Canada data, start `server.py`, and connect your MCP
client. The default endpoint is:

```text
http://127.0.0.1:7020/mcp
```

The default server listens only on your computer and needs no bearer token.
Set `GRANT_MCP_ACCESS_TOKENS` before exposing it beyond localhost. Place-name
lookups use DaedalMap's hosted geometry MCP by default; set `GEOMETRY_MCP_URL`
to use a local geometry endpoint, or provide `loc_id`s directly.

For the research workflow after connecting, see the
[tool quickstart](grant_mcp_quickstart.md).
