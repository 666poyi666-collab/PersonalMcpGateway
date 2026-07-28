import { createRemoteJWKSet, jwtVerify } from "jose";

const PRODUCTS = ["identity-focus", "journal", "watch", "suixin"] as const;
const REQUIRED_SCOPE = "gateway:read";
const MAX_AUTHORITY_BYTES = 64_000;
const MCP_PROTOCOL_VERSION = "2025-06-18";

type ProductId = (typeof PRODUCTS)[number];
type AuthorityIssue =
  | "authority_not_configured"
  | "authority_fetch_failed"
  | "authority_signature_invalid"
  | "authority_status_expired"
  | "authority_product_mismatch"
  | "authority_revision_rollback"
  | "authority_checkpoint_unavailable";

interface Env {
  ENVIRONMENT: string;
  DEPLOYMENT_REVISION: string;
  OAUTH_ISSUER: string;
  OAUTH_AUDIENCE: string;
  OAUTH_JWKS_URL: string;
  OAUTH_INTROSPECTION_URL: string;
  OAUTH_RS_CLIENT_ID: string;
  OAUTH_RS_CLIENT_SECRET?: string;
  AUTHORITY_CONFIG_JSON: string;
  AUTHORITY_CHECKPOINTS: KVNamespace;
}

interface AuthorityConfig {
  productId: ProductId;
  url: string;
  publicKey: string;
  maxAgeSeconds: number;
}

interface AuthorityTruth {
  revision: number;
  freshness: "fresh" | "stale" | "offline" | "blocked" | "unknown";
  lastVerifiedAt: string;
  pendingCount: number;
  blockerReason: string | null;
  pcOff: {
    readAvailable: boolean;
    writeAvailable: boolean;
    continuedSync: boolean;
  };
}

interface AuthorityDocument {
  schemaVersion: 1;
  productId: string;
  issuedAt: string;
  expiresAt: string;
  truth: AuthorityTruth;
  signature: string;
}

interface VerifiedAuthority {
  truth: AuthorityTruth;
  truthHash: string;
  publicKeyHash: string;
}

interface ProductSummary {
  productId: ProductId;
  revision: number | null;
  freshness: AuthorityTruth["freshness"];
  lastVerifiedAt: string | null;
  pendingCount: number | null;
  blockerReason: string | null;
  pcOff: AuthorityTruth["pcOff"];
  authorityVerification: {
    state: "verified" | "rejected" | "missing";
    issue: AuthorityIssue | null;
  };
}

const jsonHeaders = {
  "content-type": "application/json; charset=utf-8",
  "cache-control": "no-store",
  "x-content-type-options": "nosniff",
  "referrer-policy": "no-referrer",
};

function json(body: unknown, status = 200, headers: HeadersInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...jsonHeaders, ...headers },
  });
}

function exactHttpsUrl(value: string, pathRequired?: string): URL | null {
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== "https:" || parsed.username || parsed.password || parsed.hash) return null;
    if (pathRequired !== undefined && parsed.pathname !== pathRequired) return null;
    return parsed;
  } catch {
    return null;
  }
}

function base64UrlBytes(value: string): Uint8Array | null {
  if (!value || !/^[A-Za-z0-9_-]+$/.test(value)) return null;
  try {
    const padded = value.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - value.length % 4) % 4);
    const raw = atob(padded);
    return Uint8Array.from(raw, (character) => character.charCodeAt(0));
  } catch {
    return null;
  }
}

function base64Url(value: Uint8Array): string {
  let binary = "";
  for (const byte of value) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

async function sha256(value: Uint8Array | string): Promise<string> {
  const bytes = typeof value === "string" ? new TextEncoder().encode(value) : value;
  return Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", Uint8Array.from(bytes).buffer)))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const object = value as Record<string, unknown>;
  return `{${Object.keys(object).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(object[key])}`).join(",")}}`;
}

function hasExactKeys(value: Record<string, unknown>, expected: string[]): boolean {
  const actual = Object.keys(value).sort();
  return actual.length === expected.length && actual.every((key, index) => key === [...expected].sort()[index]);
}

function parseTimestamp(value: unknown): number | null {
  if (typeof value !== "string" || !/(?:Z|[+-]\d{2}:\d{2})$/.test(value)) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function isProductId(value: unknown): value is ProductId {
  return typeof value === "string" && PRODUCTS.includes(value as ProductId);
}

export function validateAuthorityShape(value: unknown): value is AuthorityDocument {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const document = value as Record<string, unknown>;
  if (!hasExactKeys(document, ["schemaVersion", "productId", "issuedAt", "expiresAt", "truth", "signature"])) return false;
  if (document.schemaVersion !== 1 || typeof document.productId !== "string" || typeof document.signature !== "string") return false;
  if (parseTimestamp(document.issuedAt) === null || parseTimestamp(document.expiresAt) === null) return false;
  if (!document.truth || typeof document.truth !== "object" || Array.isArray(document.truth)) return false;
  const truth = document.truth as Record<string, unknown>;
  if (!hasExactKeys(truth, ["revision", "freshness", "lastVerifiedAt", "pendingCount", "blockerReason", "pcOff"])) return false;
  if (!Number.isSafeInteger(truth.revision) || (truth.revision as number) < 0) return false;
  if (!["fresh", "stale", "offline", "blocked", "unknown"].includes(String(truth.freshness))) return false;
  if (parseTimestamp(truth.lastVerifiedAt) === null) return false;
  if (!Number.isSafeInteger(truth.pendingCount) || (truth.pendingCount as number) < 0) return false;
  if (!(truth.blockerReason === null || typeof truth.blockerReason === "string")) return false;
  if (!truth.pcOff || typeof truth.pcOff !== "object" || Array.isArray(truth.pcOff)) return false;
  const pcOff = truth.pcOff as Record<string, unknown>;
  if (!hasExactKeys(pcOff, ["readAvailable", "writeAvailable", "continuedSync"])) return false;
  if (![pcOff.readAvailable, pcOff.writeAvailable, pcOff.continuedSync].every((item) => typeof item === "boolean")) return false;
  if (pcOff.continuedSync && !(pcOff.readAvailable && pcOff.writeAvailable)) return false;
  const freshness = truth.freshness as string;
  const pendingCount = truth.pendingCount as number;
  if (freshness === "fresh" && (pendingCount !== 0 || truth.blockerReason !== null)) return false;
  if (pendingCount > 0 && freshness !== "blocked") return false;
  if ((truth.blockerReason === null) !== (freshness !== "blocked")) return false;
  return true;
}

export function parseAuthorityConfig(raw: string): Map<ProductId, AuthorityConfig> {
  const result = new Map<ProductId, AuthorityConfig>();
  let parsed: unknown;
  try { parsed = JSON.parse(raw); } catch { return result; }
  if (!Array.isArray(parsed) || parsed.length > PRODUCTS.length) return result;
  for (const item of parsed) {
    if (!item || typeof item !== "object" || Array.isArray(item)) return new Map();
    const record = item as Record<string, unknown>;
    if (!hasExactKeys(record, ["productId", "url", "publicKey", "maxAgeSeconds"])) return new Map();
    if (!isProductId(record.productId) || result.has(record.productId)) return new Map();
    if (typeof record.url !== "string" || !exactHttpsUrl(record.url)) return new Map();
    const publicKey = typeof record.publicKey === "string" ? base64UrlBytes(record.publicKey) : null;
    if (!publicKey || publicKey.length !== 32) return new Map();
    if (!Number.isSafeInteger(record.maxAgeSeconds) || (record.maxAgeSeconds as number) < 60 || (record.maxAgeSeconds as number) > 86_400) return new Map();
    result.set(record.productId, record as unknown as AuthorityConfig);
  }
  return result;
}

async function fetchBounded(url: string): Promise<Uint8Array | null> {
  try {
    const response = await fetch(url, { method: "GET", redirect: "error", headers: { accept: "application/json" } });
    if (!response.ok) return null;
    const declared = Number(response.headers.get("content-length") ?? "0");
    if (declared > MAX_AUTHORITY_BYTES) return null;
    const bytes = new Uint8Array(await response.arrayBuffer());
    return bytes.length <= MAX_AUTHORITY_BYTES ? bytes : null;
  } catch {
    return null;
  }
}

async function verifyAuthority(
  env: Env,
  config: AuthorityConfig,
  now = Date.now(),
): Promise<{ verified: VerifiedAuthority | null; issue: AuthorityIssue | null }> {
  const bytes = await fetchBounded(config.url);
  if (!bytes) return { verified: null, issue: "authority_fetch_failed" };
  let document: unknown;
  try { document = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)); } catch { return { verified: null, issue: "authority_signature_invalid" }; }
  if (!validateAuthorityShape(document)) return { verified: null, issue: "authority_signature_invalid" };
  if (document.productId !== config.productId) return { verified: null, issue: "authority_product_mismatch" };
  const issuedAt = Date.parse(document.issuedAt);
  const expiresAt = Date.parse(document.expiresAt);
  const verifiedAt = Date.parse(document.truth.lastVerifiedAt);
  if (expiresAt <= issuedAt || verifiedAt > issuedAt || issuedAt > now || expiresAt <= now || now - issuedAt >= config.maxAgeSeconds * 1_000) {
    return { verified: null, issue: "authority_status_expired" };
  }
  const signature = base64UrlBytes(document.signature);
  const publicKey = base64UrlBytes(config.publicKey);
  if (!signature || signature.length !== 64 || !publicKey) return { verified: null, issue: "authority_signature_invalid" };
  const unsigned = { schemaVersion: document.schemaVersion, productId: document.productId, issuedAt: document.issuedAt, expiresAt: document.expiresAt, truth: document.truth };
  try {
    const key = await crypto.subtle.importKey("raw", Uint8Array.from(publicKey).buffer, { name: "Ed25519" }, false, ["verify"]);
    const valid = await crypto.subtle.verify(
      "Ed25519",
      key,
      Uint8Array.from(signature).buffer,
      new TextEncoder().encode(canonicalJson(unsigned)).buffer,
    );
    if (!valid) return { verified: null, issue: "authority_signature_invalid" };
  } catch {
    return { verified: null, issue: "authority_signature_invalid" };
  }
  const truthHash = await sha256(canonicalJson(document.truth));
  try {
    const checkpointKey = `authority:${config.productId}`;
    const prior = await env.AUTHORITY_CHECKPOINTS.get<{ revision: number; truthHash: string }>(checkpointKey, "json");
    if (prior && (document.truth.revision < prior.revision || (document.truth.revision === prior.revision && truthHash !== prior.truthHash))) {
      return { verified: null, issue: "authority_revision_rollback" };
    }
    if (!prior || document.truth.revision > prior.revision) {
      await env.AUTHORITY_CHECKPOINTS.put(checkpointKey, JSON.stringify({ revision: document.truth.revision, truthHash }));
    }
  } catch {
    return { verified: null, issue: "authority_checkpoint_unavailable" };
  }
  return { verified: { truth: document.truth, truthHash, publicKeyHash: await sha256(publicKey) }, issue: null };
}

function unknownProduct(productId: ProductId, issue: AuthorityIssue): ProductSummary {
  return {
    productId,
    revision: null,
    freshness: "unknown",
    lastVerifiedAt: null,
    pendingCount: null,
    blockerReason: null,
    pcOff: { readAvailable: false, writeAvailable: false, continuedSync: false },
    authorityVerification: { state: issue === "authority_not_configured" ? "missing" : "rejected", issue },
  };
}

async function cloudSummary(env: Env): Promise<{ summary: Record<string, unknown>; verifiedCount: number }> {
  const config = parseAuthorityConfig(env.AUTHORITY_CONFIG_JSON);
  const products = await Promise.all(PRODUCTS.map(async (productId): Promise<ProductSummary> => {
    const product = config.get(productId);
    if (!product) return unknownProduct(productId, "authority_not_configured");
    const result = await verifyAuthority(env, product);
    if (!result.verified) return unknownProduct(productId, result.issue ?? "authority_signature_invalid");
    const truth = result.verified.truth;
    return {
      productId,
      revision: truth.revision,
      freshness: truth.freshness,
      lastVerifiedAt: truth.lastVerifiedAt,
      pendingCount: truth.pendingCount,
      blockerReason: truth.blockerReason,
      pcOff: truth.pcOff,
      authorityVerification: { state: "verified", issue: null },
    };
  }));
  return {
    summary: {
      schemaVersion: 1,
      generatedAt: new Date().toISOString(),
      gatewayProfile: { compliance: "partial", supportsPcOff: false, supportsBidirectionalDelta: false },
      products,
    },
    verifiedCount: products.filter((product) => product.authorityVerification.state === "verified").length,
  };
}

function oauthConfigValid(env: Env): boolean {
  const issuer = exactHttpsUrl(env.OAUTH_ISSUER, "/");
  const audience = exactHttpsUrl(env.OAUTH_AUDIENCE, "/mcp");
  const jwks = exactHttpsUrl(env.OAUTH_JWKS_URL, "/jwks.json");
  const introspection = exactHttpsUrl(env.OAUTH_INTROSPECTION_URL, "/introspect");
  return Boolean(issuer && audience && jwks && introspection && issuer!.origin === jwks!.origin && issuer!.origin === introspection!.origin && env.OAUTH_RS_CLIENT_ID && env.OAUTH_RS_CLIENT_SECRET && env.OAUTH_RS_CLIENT_SECRET.length >= 32);
}

async function introspect(env: Env, token: string): Promise<boolean> {
  if (!oauthConfigValid(env)) return false;
  try {
    const basic = btoa(`${env.OAUTH_RS_CLIENT_ID}:${env.OAUTH_RS_CLIENT_SECRET}`);
    const response = await fetch(env.OAUTH_INTROSPECTION_URL, {
      method: "POST",
      redirect: "error",
      headers: { authorization: `Basic ${basic}`, "content-type": "application/x-www-form-urlencoded", accept: "application/json" },
      body: new URLSearchParams({ token }).toString(),
    });
    if (!response.ok || Number(response.headers.get("content-length") ?? "0") > 16_384) return false;
    const body = await response.json<Record<string, unknown>>();
    return body.active === true && body.aud === env.OAUTH_AUDIENCE && typeof body.scope === "string" && body.scope.split(/\s+/).includes(REQUIRED_SCOPE);
  } catch {
    return false;
  }
}

async function authenticate(request: Request, env: Env): Promise<boolean> {
  const authorization = request.headers.get("authorization") ?? "";
  const match = /^Bearer ([A-Za-z0-9._~-]+)$/.exec(authorization);
  if (!match || !oauthConfigValid(env)) return false;
  try {
    const jwks = createRemoteJWKSet(new URL(env.OAUTH_JWKS_URL), { timeoutDuration: 5_000, cooldownDuration: 30_000, cacheMaxAge: 300_000 });
    const { payload } = await jwtVerify(match[1]!, jwks, {
      issuer: env.OAUTH_ISSUER,
      audience: env.OAUTH_AUDIENCE,
      algorithms: ["RS256"],
      clockTolerance: 5,
      maxTokenAge: "15m",
    });
    const scope = typeof payload.scope === "string" ? payload.scope.split(/\s+/) : [];
    if (!scope.includes(REQUIRED_SCOPE) || payload.resource !== env.OAUTH_AUDIENCE || typeof payload.sub !== "string" || typeof payload.jti !== "string") return false;
    return await introspect(env, match[1]!);
  } catch {
    return false;
  }
}

function challenge(request: Request): Response {
  const origin = new URL(request.url).origin;
  return json({ error: "invalid_token" }, 401, {
    "www-authenticate": `Bearer resource_metadata="${origin}/.well-known/oauth-protected-resource/mcp", error="invalid_token"`,
  });
}

async function oauthDependencyProbe(env: Env): Promise<Record<string, unknown>> {
  if (!oauthConfigValid(env)) return { configured: false, metadata: false, jwks: false, introspection: false };
  const probe = async (url: string, init?: RequestInit): Promise<Response | null> => {
    try { return await fetch(url, { ...init, redirect: "error", signal: AbortSignal.timeout(5_000) }); } catch { return null; }
  };
  const [metadata, jwks, tokenStatus] = await Promise.all([
    probe(`${env.OAUTH_ISSUER}/.well-known/oauth-authorization-server`, { headers: { accept: "application/json" } }),
    probe(env.OAUTH_JWKS_URL, { headers: { accept: "application/json" } }),
    probe(env.OAUTH_INTROSPECTION_URL, {
      method: "POST",
      headers: {
        authorization: `Basic ${btoa(`${env.OAUTH_RS_CLIENT_ID}:${env.OAUTH_RS_CLIENT_SECRET}`)}`,
        "content-type": "application/x-www-form-urlencoded",
      },
      body: "token=readiness-probe-invalid-token",
    }),
  ]);
  let introspection = false;
  if (tokenStatus?.ok) {
    try { introspection = (await tokenStatus.json<Record<string, unknown>>()).active === false; } catch { introspection = false; }
  }
  return { configured: true, metadata: metadata?.ok === true, jwks: jwks?.ok === true, introspection };
}

async function ready(env: Env): Promise<Response> {
  let oauth: Record<string, unknown>;
  let oauthProbeError = false;
  try {
    oauth = await oauthDependencyProbe(env);
  } catch {
    oauthProbeError = true;
    oauth = { configured: oauthConfigValid(env), metadata: false, jwks: false, introspection: false };
  }
  let authority: { summary: Record<string, unknown>; verifiedCount: number };
  let authorityProbeError = false;
  try {
    authority = await cloudSummary(env);
  } catch {
    authorityProbeError = true;
    authority = { summary: {}, verifiedCount: 0 };
  }
  const oauthReady = Object.values(oauth).every((value) => value === true);
  const authorityReady = authority.verifiedCount === PRODUCTS.length;
  return json({
    ready: oauthReady && authorityReady,
    environment: env.ENVIRONMENT,
    deploymentRevision: env.DEPLOYMENT_REVISION,
    dependencies: {
      oauth: { ...oauth, probeError: oauthProbeError },
      authorities: {
        configured: parseAuthorityConfig(env.AUTHORITY_CONFIG_JSON).size,
        verified: authority.verifiedCount,
        required: PRODUCTS.length,
        probeError: authorityProbeError,
      },
    },
  }, oauthReady && authorityReady ? 200 : 503);
}

async function mcp(request: Request, env: Env): Promise<Response> {
  if (!(await authenticate(request, env))) return challenge(request);
  let body: Record<string, unknown>;
  try {
    if (Number(request.headers.get("content-length") ?? "0") > 64_000) return json({ error: "request_too_large" }, 413);
    body = await request.json<Record<string, unknown>>();
  } catch {
    return json({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" } }, 400);
  }
  const id = body.id ?? null;
  if (body.jsonrpc !== "2.0" || typeof body.method !== "string") return json({ jsonrpc: "2.0", id, error: { code: -32600, message: "Invalid Request" } }, 400);
  if (body.method === "initialize") {
    return json({ jsonrpc: "2.0", id, result: { protocolVersion: MCP_PROTOCOL_VERSION, capabilities: { tools: { listChanged: false } }, serverInfo: { name: "personal-mcp-gateway-staging", version: env.DEPLOYMENT_REVISION } } });
  }
  if (body.method === "notifications/initialized") return new Response(null, { status: 202 });
  if (body.method === "tools/list") {
    return json({ jsonrpc: "2.0", id, result: { tools: [{ name: "personal_cloud_sync_overview", description: "Read verified cloud-authority sync truth. Missing, tampered, expired, mismatched, or rolled-back authority documents fail closed to unknown/false.", inputSchema: { type: "object", properties: {}, additionalProperties: false }, annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true } }] } });
  }
  if (body.method === "tools/call") {
    const params = body.params as Record<string, unknown> | undefined;
    if (!params || params.name !== "personal_cloud_sync_overview") return json({ jsonrpc: "2.0", id, error: { code: -32602, message: "Unknown tool" } }, 400);
    const value = (await cloudSummary(env)).summary;
    return json({ jsonrpc: "2.0", id, result: { content: [{ type: "text", text: JSON.stringify(value) }], structuredContent: { ok: true, project: "personal", operation: "cloud_mcp_summary", data: value } } });
  }
  return json({ jsonrpc: "2.0", id, error: { code: -32601, message: "Method not found" } }, 404);
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/healthz") return json({ alive: true, environment: env.ENVIRONMENT, deploymentRevision: env.DEPLOYMENT_REVISION });
    if (request.method === "GET" && url.pathname === "/readyz") return ready(env);
    if (request.method === "GET" && (url.pathname === "/.well-known/oauth-protected-resource/mcp" || url.pathname === "/.well-known/oauth-protected-resource")) {
      return json({ resource: `${url.origin}/mcp`, authorization_servers: [env.OAUTH_ISSUER], jwks_uri: env.OAUTH_JWKS_URL, scopes_supported: [REQUIRED_SCOPE], bearer_methods_supported: ["header"] });
    }
    if (url.pathname === "/mcp" && request.method === "POST") return mcp(request, env);
    if (/^\/[^/]+\/mcp(?:\/.*)?$/.test(url.pathname)) return json({ error: "legacy_secret_path_removed", replacement: "/mcp" }, 410);
    return json({ error: "not_found" }, 404);
  },
};

export { base64Url, canonicalJson, unknownProduct };
