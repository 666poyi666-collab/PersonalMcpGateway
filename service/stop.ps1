$ErrorActionPreference = 'Stop'
Stop-Service OpenAISecureMcpTunnel -ErrorAction SilentlyContinue
Stop-Service PoyiPersonalMcpGateway -ErrorAction SilentlyContinue
