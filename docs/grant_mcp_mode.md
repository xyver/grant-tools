# Grant Tools MCP mode

Grant Tools is a community project adapted from DaedalMap's Research MCP. It
runs as an independent local MCP server. The tools guide a user through grant
discovery, eligibility, past awards, shortlisting, and proposal preparation.

The server reads local country tables from `data/` by default. Download a
[USA or Canada bundle](../README.md#quickstart), or build the tables with the
included source pipeline. Announcement collectors write recent leads to
`state/`; they can be run or scheduled separately.

The grant workflow uses DaedalMap's geometry MCP for place-name lookups by
default. You can provide a `loc_id` directly or configure another geometry
endpoint with `GEOMETRY_MCP_URL`.

Start with the [README](../README.md) for setup and the
[tool quickstart](grant_mcp_quickstart.md) for the research workflow.
