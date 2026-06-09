# Science Agent MCP — Deploy Runbook

A read-only MCP server that lets a Claude.ai Project (e.g. "SpotitEarly —
Knowledge Center Project") query the Science Agent corpus over HTTPS.

- Endpoint: `https://science-mcp.spotitearly.com/mcp`
- Health: `https://science-mcp.spotitearly.com/healthz`
- Auth: `Authorization: Bearer <SCIENCE_MCP_TOKEN>`
- Tools: `search_publications`, `get_publication`, `get_must_reads`

The server lives alongside `upload_app` on the same EC2 box, behind
Cloudflare Tunnel. Bearer token auth at the app layer (no Cloudflare Access
on this hostname — claude.ai can't pass through the OAuth handshake).

## First-time install on the EC2 box

```bash
ssh ubuntu@ai.spotitearly.com
cd /home/ubuntu/sie-ai/acitracker_v1

# 1. Make sure the venv has the new deps.
.venv/bin/pip install --upgrade -r requirements.txt

# 2. Generate a token and write the env file (root:ubuntu, chmod 640).
TOKEN=$(openssl rand -hex 32)
sudo install -o root -g ubuntu -m 640 /dev/stdin /etc/sie-ai/science-mcp.env <<EOF
SCIENCE_MCP_TOKEN=$TOKEN
DATABASE_URL=postgresql://sie-ai:postgres@localhost:5432/sie-ai
OPENAI_API_KEY=sk-...
EOF

# 3. Install the systemd unit.
sudo cp mcp_server/deploy/science-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now science-mcp
sudo systemctl status science-mcp --no-pager

# 4. Add the Cloudflare Tunnel route.
#    Append the snippet to /etc/cloudflared/config.yml ABOVE the catch-all 404,
#    then:
sudo cloudflared tunnel route dns <tunnel-name> science-mcp.spotitearly.com
sudo systemctl restart cloudflared

# 5. Smoke test from the box (loopback) and from your laptop (via tunnel).
curl -s http://127.0.0.1:5006/healthz
curl -s https://science-mcp.spotitearly.com/healthz
curl -s -H "Authorization: Bearer $TOKEN" \
     -H "Accept: application/json, text/event-stream" \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
     https://science-mcp.spotitearly.com/mcp | head -c 1000

echo "Token (give to Camille, do not commit): $TOKEN"
```

## Updating after a code change

```bash
ssh ubuntu@ai.spotitearly.com
cd /home/ubuntu/sie-ai/acitracker_v1
git pull
.venv/bin/pip install --upgrade -r requirements.txt   # only if deps changed
sudo systemctl restart science-mcp
journalctl -u science-mcp -f                          # tail logs
```

## How Camille adds the connector in claude.ai

In the Project ("SpotitEarly — Knowledge Center Project"):

1. Settings → Connectors → **Add custom connector**.
2. Name: `Science Agent`.
3. URL: `https://science-mcp.spotitearly.com/mcp`.
4. Auth: paste the token under `Authorization: Bearer …`.
5. Save. The three tools should appear in the connector's tool list.
6. Test in chat: *"Search Science Agent for recent papers on circulating tumor DNA in breast cancer screening and summarize the top 3 with links."*

## Pre-deploy sanity checks

```bash
# Embedding coverage — semantic search silently misses publications without
# embeddings. If this returns a meaningful number, run a backfill before
# pointing Camille at the connector.
psql -d sie-ai -c "
  SELECT count(*) AS missing_embeddings
  FROM publications p
  LEFT JOIN publication_embeddings e ON e.publication_id = p.id
  WHERE e.publication_id IS NULL;
"
```

## Token rotation

```bash
NEW=$(openssl rand -hex 32)
sudo sed -i "s|^SCIENCE_MCP_TOKEN=.*|SCIENCE_MCP_TOKEN=$NEW|" /etc/sie-ai/science-mcp.env
sudo systemctl restart science-mcp
# Update the connector in claude.ai with the new token.
```

## Troubleshooting

- **401 from `/mcp`** — wrong/missing bearer token, or `SCIENCE_MCP_TOKEN`
  env var didn't load (check `journalctl -u science-mcp` for the warning).
- **502/504 from Cloudflare** — `science-mcp` service down (`systemctl status`)
  or cloudflared not reloaded after editing `config.yml`.
- **`tools/list` works but `search_publications` returns empty** — likely the
  embedding coverage gap above; check `OPENAI_API_KEY` is set so the *query*
  embedding can be generated.
- **Stdio regression** — `python3 -m mcp_server.server` should still start the
  legacy stdio server unchanged. Both transports share `mcp_server.registry`.
