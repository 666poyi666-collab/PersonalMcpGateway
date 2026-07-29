import { createLocalJWKSet, jwtVerify, type JWTPayload, type JSONWebKeySet } from "jose";

const PRODUCTS = ["identity-focus", "journal", "watch", "suixin"] as const;
const REQUIRED_SCOPE = "gateway:read";
const MAX_AUTHORITY_BYTES = 64_000;
const AUTHORITY_DOCUMENT_MEDIA_TYPE = "application/vnd.poyi.authority-document.v2+json";
const AUTHORITY_CLOCK_SKEW_MS = 60_000;
const MCP_PROTOCOL_VERSION = "2025-06-18";

type ProductId = (typeof PRODUCTS)[number];
type AuthorityIssue =
  | "authority_not_configured"
  | "authority_fetch_failed"
  | "authority_source_binding_missing"
  | "authority_source_capability_missing"
  | "authority_source_capability_not_string"
  | "authority_source_capability_length_invalid"
  | "authority_source_capability_control_invalid"
  | "authority_source_capability_unicode_invalid"
  | "authority_source_capability_characters_invalid"
  | "authority_source_unauthorized"
  | "authority_source_forbidden"
  | "authority_source_route_missing"
  | "authority_source_unavailable"
  | "authority_source_rejected"
  | "authority_source_contract_invalid"
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
  AUTHORITY_CHECKPOINTS: DurableObjectNamespace;
  AUTHORITY_SERVICE?: Fetcher;
  OAUTH_AS_SERVICE?: Fetcher;
}

export interface AuthorityConfig {
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
  schemaVersion: 2;
  productId: string;
  audience: string;
  issuedAt: string;
  expiresAt: string;
  sourceObservedAt: string;
  sourceExpiresAt: string;
  observationHash: string;
  truth: AuthorityTruth;
  signature: string;
}

interface VerifiedAuthority {
  truth: AuthorityTruth;
  truthHash: string;
  publicKeyHash: string;
}

interface AuthorityCheckpointRecord {
  schemaVersion: 3;
  environment: string;
  productId: ProductId;
  audience: string;
  publicKeyHash: string;
  revision: number;
  observationHash: string;
  truthHash: string;
  lastVerifiedAt: string;
  sourceObservedAt: string;
  sourceExpiresAt: string;
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
    if (parsed.protocol !== "https:" || parsed.username || parsed.password || parsed.hash || parsed.search) return null;
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

function asciiJsonString(value: string): string {
  return JSON.stringify(value).replace(/[\u007f-\uffff]/g, (character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`);
}

function canonicalJson(value: unknown): string {
  if (typeof value === "string") return asciiJsonString(value);
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const object = value as Record<string, unknown>;
  return `{${Object.keys(object).sort().map((key) => `${asciiJsonString(key)}:${canonicalJson(object[key])}`).join(",")}}`;
}

function hasExactKeys(value: Record<string, unknown>, expected: string[]): boolean {
  const actual = Object.keys(value).sort();
  return actual.length === expected.length && actual.every((key, index) => key === [...expected].sort()[index]);
}

function isCheckpointRecord(value: unknown): value is AuthorityCheckpointRecord {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  return hasExactKeys(record, [
    "schemaVersion", "environment", "productId", "audience", "publicKeyHash", "revision",
    "observationHash", "truthHash", "lastVerifiedAt", "sourceObservedAt", "sourceExpiresAt",
  ])
    && record.schemaVersion === 3
    && typeof record.environment === "string"
    && isProductId(record.productId)
    && typeof record.audience === "string"
    && exactHttpsUrl(record.audience, `/authority/${record.productId}`) !== null
    && typeof record.publicKeyHash === "string"
    && /^[0-9a-f]{64}$/.test(record.publicKeyHash)
    && Number.isSafeInteger(record.revision)
    && (record.revision as number) >= 0
    && typeof record.observationHash === "string"
    && /^[0-9a-f]{64}$/.test(record.observationHash)
    && typeof record.truthHash === "string"
    && /^[0-9a-f]{64}$/.test(record.truthHash)
    && parseTimestamp(record.lastVerifiedAt) !== null
    && parseTimestamp(record.sourceObservedAt) !== null
    && parseTimestamp(record.sourceExpiresAt) !== null;
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
  if (!hasExactKeys(document, [
    "schemaVersion", "productId", "audience", "issuedAt", "expiresAt", "sourceObservedAt",
    "sourceExpiresAt", "observationHash", "truth", "signature",
  ])) return false;
  if (document.schemaVersion !== 2
    || typeof document.productId !== "string"
    || typeof document.audience !== "string"
    || typeof document.signature !== "string"
    || typeof document.observationHash !== "string"
    || !/^[0-9a-f]{64}$/.test(document.observationHash)) return false;
  if (parseTimestamp(document.issuedAt) === null
    || parseTimestamp(document.expiresAt) === null
    || parseTimestamp(document.sourceObservedAt) === null
    || parseTimestamp(document.sourceExpiresAt) === null) return false;
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
    if (
      typeof record.url !== "string" ||
      !exactHttpsUrl(record.url, `/authority/${record.productId}`)
    ) return new Map();
    const publicKey = typeof record.publicKey === "string" ? base64UrlBytes(record.publicKey) : null;
    if (!publicKey || publicKey.length !== 32) return new Map();
    if (!Number.isSafeInteger(record.maxAgeSeconds) || (record.maxAgeSeconds as number) < 60 || (record.maxAgeSeconds as number) > 86_400) return new Map();
    result.set(record.productId, record as unknown as AuthorityConfig);
  }
  return result;
}

interface BoundedHttpResponse {
  ok: boolean;
  status: number;
  contentType: string;
  bytes: Uint8Array;
}

function authoritySourceIssue(response: BoundedHttpResponse): AuthorityIssue {
  let value: unknown;
  try {
    value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(response.bytes));
  } catch {
    return "authority_fetch_failed";
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) return "authority_fetch_failed";
  const record = value as Record<string, unknown>;
  if (!hasExactKeys(record, ["error", "sourceIssue", "sourceStatus"]) || record.error !== "authority_source_unavailable") {
    return "authority_fetch_failed";
  }
  if (record.sourceIssue === "binding_missing" && record.sourceStatus === null) return "authority_source_binding_missing";
  if (record.sourceIssue === "capability_missing" && record.sourceStatus === null) return "authority_source_capability_missing";
  if (record.sourceIssue === "capability_not_string" && record.sourceStatus === null) return "authority_source_capability_not_string";
  if (record.sourceIssue === "capability_length_invalid" && record.sourceStatus === null) return "authority_source_capability_length_invalid";
  if (record.sourceIssue === "capability_control_invalid" && record.sourceStatus === null) return "authority_source_capability_control_invalid";
  if (record.sourceIssue === "capability_unicode_invalid" && record.sourceStatus === null) return "authority_source_capability_unicode_invalid";
  if (record.sourceIssue === "capability_characters_invalid" && record.sourceStatus === null) return "authority_source_capability_characters_invalid";
  if (record.sourceIssue === "invalid_response" && record.sourceStatus === 200) return "authority_source_contract_invalid";
  if (record.sourceIssue === "transport_failed" && record.sourceStatus === null) return "authority_fetch_failed";
  if ((record.sourceIssue === "http_rejected" || record.sourceIssue === "redirect_rejected") && Number.isInteger(record.sourceStatus)) {
    if (record.sourceStatus === 401) return "authority_source_unauthorized";
    if (record.sourceStatus === 403) return "authority_source_forbidden";
    if (record.sourceStatus === 404) return "authority_source_route_missing";
    if (record.sourceStatus === 503) return "authority_source_unavailable";
    return "authority_source_rejected";
  }
  return "authority_fetch_failed";
}

type BoundedJsonResult =
  | { ok: true; value: unknown }
  | { ok: false; tooLarge: boolean };

async function readBoundedJson(request: Request, maxBytes: number): Promise<BoundedJsonResult> {
  const contentLength = request.headers.get("content-length");
  if (contentLength !== null) {
    const declared = Number(contentLength);
    if (!Number.isSafeInteger(declared) || declared < 0 || declared > maxBytes) {
      await request.body?.cancel();
      return { ok: false, tooLarge: true };
    }
  }
  if (!request.body) return { ok: false, tooLarge: false };
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > maxBytes) {
        await reader.cancel();
        return { ok: false, tooLarge: true };
      }
      chunks.push(value);
    }
    const bytes = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    return {
      ok: true,
      value: JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) as unknown,
    };
  } catch {
    return { ok: false, tooLarge: false };
  } finally {
    reader.releaseLock();
  }
}

// Cloudflare Workers blocks worker-to-worker subrequests over the public
// *.workers.dev hostnames (error 1042). Sibling workers are therefore reached
// through service bindings. Product authority reads never fall back to the
// public network; resolveBindingFetch remains for OAuth's public protocol URLs.
function resolveBindingFetch(env: Env, url: string): Fetcher | undefined {
  let hostname: string;
  try {
    hostname = new URL(url).hostname;
  } catch {
    return undefined;
  }
  const bindings: Array<[string, Fetcher | undefined]> = [
    ["poyi-oauth-as-staging.focuslink-poyi-6465e9.workers.dev", env.OAUTH_AS_SERVICE],
  ];
  for (const [boundHostname, binding] of bindings) {
    if (hostname === boundHostname && binding) return binding;
  }
  return undefined;
}

async function fetchBoundedHttp(
  url: string,
  init: RequestInit,
  maxBytes: number,
  timeoutMs = 5_000,
  fetcher?: Fetcher,
): Promise<BoundedHttpResponse | null> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const dispatch = fetcher === undefined
      ? (input: RequestInfo | URL, requestInit?: RequestInit) => fetch(input, requestInit)
      : typeof fetcher === "function"
        ? (input: RequestInfo | URL, requestInit?: RequestInit) => (fetcher as (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>)(input, requestInit)
        : (input: RequestInfo | URL, requestInit?: RequestInit) => fetcher.fetch(input as RequestInfo, requestInit);
    const response = await dispatch(url, { ...init, redirect: "manual", signal: controller.signal });
    // redirect:"manual" surfaces 3xx instead of following; treat any redirect as a failure.
    if (response.status >= 300 && response.status < 400) {
      await response.body?.cancel();
      return null;
    }
    const declared = Number(response.headers.get("content-length") ?? "0");
    if (declared > maxBytes) {
      await response.body?.cancel();
      return null;
    }
    if (!response.body) return { ok: response.ok, status: response.status, contentType: response.headers.get("content-type") ?? "", bytes: new Uint8Array() };
    const reader = response.body.getReader();
    const chunks: Uint8Array[] = [];
    let length = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > maxBytes) {
        await reader.cancel();
        return null;
      }
      chunks.push(value);
    }
    const bytes = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    return { ok: response.ok, status: response.status, contentType: response.headers.get("content-type") ?? "", bytes };
  } catch {
    return null;
  } finally {
    clearTimeout(timeout);
  }
}

export async function verifyAuthority(
  env: Env,
  config: AuthorityConfig,
  now = Date.now(),
): Promise<{ verified: VerifiedAuthority | null; issue: AuthorityIssue | null }> {
  if (!env.AUTHORITY_SERVICE) return { verified: null, issue: "authority_fetch_failed" };
  const response = await fetchBoundedHttp(
    config.url,
    { method: "GET", headers: { accept: AUTHORITY_DOCUMENT_MEDIA_TYPE } },
    MAX_AUTHORITY_BYTES,
    5_000,
    env.AUTHORITY_SERVICE,
  );
  if (!response) return { verified: null, issue: "authority_fetch_failed" };
  if (!response.ok) return { verified: null, issue: authoritySourceIssue(response) };
  if (response.contentType.split(";", 1)[0]?.trim().toLowerCase() !== AUTHORITY_DOCUMENT_MEDIA_TYPE) {
    return { verified: null, issue: "authority_signature_invalid" };
  }
  const bytes = response.bytes;
  let document: unknown;
  try { document = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)); } catch { return { verified: null, issue: "authority_signature_invalid" }; }
  if (!validateAuthorityShape(document)) return { verified: null, issue: "authority_signature_invalid" };
  if (document.productId !== config.productId || document.audience !== config.url) return { verified: null, issue: "authority_product_mismatch" };
  const issuedAt = Date.parse(document.issuedAt);
  const expiresAt = Date.parse(document.expiresAt);
  const sourceObservedAt = Date.parse(document.sourceObservedAt);
  const sourceExpiresAt = Date.parse(document.sourceExpiresAt);
  const verifiedAt = Date.parse(document.truth.lastVerifiedAt);
  if (
    expiresAt <= issuedAt ||
    sourceExpiresAt <= sourceObservedAt ||
    expiresAt > sourceExpiresAt ||
    verifiedAt > sourceObservedAt ||
    sourceObservedAt > issuedAt + AUTHORITY_CLOCK_SKEW_MS ||
    issuedAt > now + AUTHORITY_CLOCK_SKEW_MS ||
    expiresAt <= now ||
    sourceExpiresAt <= now ||
    now - issuedAt >= config.maxAgeSeconds * 1_000 ||
    now - sourceObservedAt >= config.maxAgeSeconds * 1_000 ||
    (document.truth.freshness === "fresh" && now - verifiedAt >= config.maxAgeSeconds * 1_000)
  ) {
    return { verified: null, issue: "authority_status_expired" };
  }
  const reconstructedObservation = {
    schemaVersion: 1,
    productId: document.productId,
    audience: document.audience,
    observedAt: document.sourceObservedAt,
    expiresAt: document.sourceExpiresAt,
    truth: document.truth,
  };
  if (await sha256(canonicalJson(reconstructedObservation)) !== document.observationHash) {
    return { verified: null, issue: "authority_signature_invalid" };
  }
  const signature = base64UrlBytes(document.signature);
  const publicKey = base64UrlBytes(config.publicKey);
  if (!signature || signature.length !== 64 || !publicKey) return { verified: null, issue: "authority_signature_invalid" };
  const unsigned = {
    schemaVersion: document.schemaVersion,
    productId: document.productId,
    audience: document.audience,
    issuedAt: document.issuedAt,
    expiresAt: document.expiresAt,
    sourceObservedAt: document.sourceObservedAt,
    sourceExpiresAt: document.sourceExpiresAt,
    observationHash: document.observationHash,
    truth: document.truth,
  };
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
  const publicKeyHash = await sha256(publicKey);
  try {
    const id = env.AUTHORITY_CHECKPOINTS.idFromName(`v3:${env.ENVIRONMENT}:${config.productId}`);
    const checkpoint = env.AUTHORITY_CHECKPOINTS.get(id);
    const response = await checkpoint.fetch("https://authority-checkpoint.internal/accept", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        schemaVersion: 3,
        environment: env.ENVIRONMENT,
        productId: config.productId,
        audience: document.audience,
        publicKeyHash,
        revision: document.truth.revision,
        observationHash: document.observationHash,
        truthHash,
        lastVerifiedAt: document.truth.lastVerifiedAt,
        sourceObservedAt: document.sourceObservedAt,
        sourceExpiresAt: document.sourceExpiresAt,
      } satisfies AuthorityCheckpointRecord),
    });
    if (response.status === 409) {
      return { verified: null, issue: "authority_revision_rollback" };
    }
    if (!response.ok) return { verified: null, issue: "authority_checkpoint_unavailable" };
  } catch {
    return { verified: null, issue: "authority_checkpoint_unavailable" };
  }
  return { verified: { truth: document.truth, truthHash, publicKeyHash }, issue: null };
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

async function cloudSummary(env: Env): Promise<{ summary: Record<string, unknown>; verifiedCount: number; operationalCount: number }> {
  const config = parseAuthorityConfig(env.AUTHORITY_CONFIG_JSON);
  const operational = new Set<ProductId>();
  const products = await Promise.all(PRODUCTS.map(async (productId): Promise<ProductSummary> => {
    const product = config.get(productId);
    if (!product) return unknownProduct(productId, "authority_not_configured");
    const result = await verifyAuthority(env, product);
    if (!result.verified) return unknownProduct(productId, result.issue ?? "authority_signature_invalid");
    const truth = result.verified.truth;
    if (truth.freshness === "fresh"
      && truth.pendingCount === 0
      && truth.blockerReason === null
      && Date.now() - Date.parse(truth.lastVerifiedAt) < product.maxAgeSeconds * 1_000) {
      operational.add(productId);
    }
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
    operationalCount: operational.size,
  };
}

function oauthConfigValid(env: Env): boolean {
  const issuer = exactHttpsUrl(env.OAUTH_ISSUER, "/");
  const audience = exactHttpsUrl(env.OAUTH_AUDIENCE, "/mcp");
  const jwks = exactHttpsUrl(env.OAUTH_JWKS_URL, "/jwks.json");
  const introspection = exactHttpsUrl(env.OAUTH_INTROSPECTION_URL, "/introspect");
  return Boolean(issuer && audience && jwks && introspection && issuer!.origin === jwks!.origin && issuer!.origin === introspection!.origin && env.OAUTH_RS_CLIENT_ID && env.OAUTH_RS_CLIENT_SECRET && env.OAUTH_RS_CLIENT_SECRET.length >= 32);
}

function exactAudience(value: unknown, audience: string): boolean {
  return value === audience || (Array.isArray(value) && value.length === 1 && value[0] === audience);
}

async function introspect(env: Env, token: string, payload: JWTPayload): Promise<boolean> {
  if (!oauthConfigValid(env)) return false;
  try {
    const basic = btoa(`${env.OAUTH_RS_CLIENT_ID}:${env.OAUTH_RS_CLIENT_SECRET}`);
    const response = await fetchBoundedHttp(env.OAUTH_INTROSPECTION_URL, {
      method: "POST",
      headers: { authorization: `Basic ${basic}`, "content-type": "application/x-www-form-urlencoded", accept: "application/json" },
      body: new URLSearchParams({ token }).toString(),
    }, 16_384, 5_000, resolveBindingFetch(env, env.OAUTH_INTROSPECTION_URL));
    if (!response?.ok || !response.contentType.toLowerCase().includes("application/json")) return false;
    const body = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(response.bytes)) as Record<string, unknown>;
    const scope = typeof body.scope === "string" ? body.scope.split(/\s+/) : [];
    return body.active === true
      && exactAudience(body.aud, env.OAUTH_AUDIENCE)
      && scope.includes(REQUIRED_SCOPE)
      && body.iss === payload.iss
      && body.sub === payload.sub
      && body.jti === payload.jti
      && body.exp === payload.exp
      && (body.token_type === undefined || String(body.token_type).toLowerCase() === "bearer")
      && (body.resource === undefined || body.resource === env.OAUTH_AUDIENCE)
      && (payload.client_id === undefined || body.client_id === payload.client_id);
  } catch {
    return false;
  }
}

let jwksCache: { keys: JSONWebKeySet; expiresAt: number } | undefined;

async function loadJwksLocal(env: Env): Promise<ReturnType<typeof createLocalJWKSet>> {
  if (jwksCache && jwksCache.expiresAt > Date.now()) return createLocalJWKSet(jwksCache.keys);
  const response = await fetchBoundedHttp(env.OAUTH_JWKS_URL, { method: "GET", headers: { accept: "application/json" } }, 128_000, 5_000, resolveBindingFetch(env, env.OAUTH_JWKS_URL));
  if (!response?.ok) throw new Error("jwks_unavailable");
  const jwks = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(response.bytes)) as JSONWebKeySet;
  jwksCache = { keys: jwks, expiresAt: Date.now() + 300_000 };
  return createLocalJWKSet(jwks);
}

async function authenticate(request: Request, env: Env): Promise<boolean> {
  const authorization = request.headers.get("authorization") ?? "";
  const match = /^Bearer ([A-Za-z0-9._~-]+)$/.exec(authorization);
  if (!match || !oauthConfigValid(env)) return false;
  try {
    const JWKS = await loadJwksLocal(env);
    const { payload } = await jwtVerify(match[1]!, JWKS, {
      issuer: env.OAUTH_ISSUER,
      audience: env.OAUTH_AUDIENCE,
      algorithms: ["RS256"],
      clockTolerance: 5,
      maxTokenAge: "15m",
    });
    const scope = typeof payload.scope === "string" ? payload.scope.split(/\s+/) : [];
    if (!scope.includes(REQUIRED_SCOPE) || payload.resource !== env.OAUTH_AUDIENCE || typeof payload.sub !== "string" || typeof payload.jti !== "string") return false;
    return await introspect(env, match[1]!, payload);
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
  const [metadata, jwks, tokenStatus] = await Promise.all([
    fetchBoundedHttp(`${env.OAUTH_ISSUER}/.well-known/oauth-authorization-server`, { method: "GET", headers: { accept: "application/json" } }, 64_000, 5_000, resolveBindingFetch(env, `${env.OAUTH_ISSUER}/.well-known/oauth-authorization-server`)),
    fetchBoundedHttp(env.OAUTH_JWKS_URL, { method: "GET", headers: { accept: "application/json" } }, 128_000, 5_000, resolveBindingFetch(env, env.OAUTH_JWKS_URL)),
    fetchBoundedHttp(env.OAUTH_INTROSPECTION_URL, {
      method: "POST",
      headers: {
        authorization: `Basic ${btoa(`${env.OAUTH_RS_CLIENT_ID}:${env.OAUTH_RS_CLIENT_SECRET}`)}`,
        "content-type": "application/x-www-form-urlencoded",
      },
      body: "token=readiness-probe-invalid-token",
    }, 16_384, 5_000, resolveBindingFetch(env, env.OAUTH_INTROSPECTION_URL)),
  ]);
  let metadataValid = false;
  if (metadata?.ok && metadata.contentType.toLowerCase().includes("application/json")) {
    try {
      const value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(metadata.bytes)) as Record<string, unknown>;
      const authorization = typeof value.authorization_endpoint === "string" ? exactHttpsUrl(value.authorization_endpoint) : null;
      const token = typeof value.token_endpoint === "string" ? exactHttpsUrl(value.token_endpoint) : null;
      const scopes = Array.isArray(value.scopes_supported) ? value.scopes_supported : [];
      metadataValid = value.issuer === env.OAUTH_ISSUER
        && value.jwks_uri === env.OAUTH_JWKS_URL
        && value.introspection_endpoint === env.OAUTH_INTROSPECTION_URL
        && authorization?.origin === new URL(env.OAUTH_ISSUER).origin
        && token?.origin === new URL(env.OAUTH_ISSUER).origin
        && scopes.includes(REQUIRED_SCOPE);
    } catch {
      metadataValid = false;
    }
  }
  let jwksValid = false;
  if (jwks?.ok && jwks.contentType.toLowerCase().includes("application/json")) {
    try {
      const value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(jwks.bytes)) as Record<string, unknown>;
      const keys = Array.isArray(value.keys) ? value.keys : [];
      const kids = new Set<string>();
      jwksValid = keys.length >= 1 && keys.length <= 10 && keys.every((candidate) => {
        if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) return false;
        const key = candidate as Record<string, unknown>;
        const kid = typeof key.kid === "string" ? key.kid : "";
        if (!kid || kids.has(kid)) return false;
        kids.add(kid);
        return key.kty === "RSA"
          && key.alg === "RS256"
          && key.use === "sig"
          && typeof key.n === "string"
          && base64UrlBytes(key.n) !== null
          && typeof key.e === "string"
          && base64UrlBytes(key.e) !== null
          && !["d", "p", "q", "dp", "dq", "qi", "oth"].some((field) => field in key);
      });
    } catch {
      jwksValid = false;
    }
  }
  let introspection = false;
  if (tokenStatus?.ok && tokenStatus.contentType.toLowerCase().includes("application/json")) {
    try {
      introspection = (JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(tokenStatus.bytes)) as Record<string, unknown>).active === false;
    } catch {
      introspection = false;
    }
  }
  return { configured: true, metadata: metadataValid, jwks: jwksValid, introspection };
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
  let authority: { summary: Record<string, unknown>; verifiedCount: number; operationalCount: number };
  let authorityProbeError = false;
  try {
    authority = await cloudSummary(env);
  } catch {
    authorityProbeError = true;
    authority = { summary: {}, verifiedCount: 0, operationalCount: 0 };
  }
  const oauthReady = Object.values(oauth).every((value) => value === true);
  const authorityReady = authority.verifiedCount === PRODUCTS.length && authority.operationalCount === PRODUCTS.length;
  const authorityProducts = Array.isArray(authority.summary.products)
    ? authority.summary.products.flatMap((value): Array<{
      productId: ProductId;
      state: ProductSummary["authorityVerification"]["state"];
      issue: AuthorityIssue | null;
    }> => {
      if (!value || typeof value !== "object" || Array.isArray(value)) return [];
      const product = value as Record<string, unknown>;
      const verification = product.authorityVerification;
      if (!isProductId(product.productId)
        || !verification
        || typeof verification !== "object"
        || Array.isArray(verification)) return [];
      const record = verification as Record<string, unknown>;
      if (!["verified", "rejected", "missing"].includes(String(record.state))) return [];
      if (!(record.issue === null || typeof record.issue === "string")) return [];
      return [{
        productId: product.productId,
        state: record.state as ProductSummary["authorityVerification"]["state"],
        issue: record.issue as AuthorityIssue | null,
      }];
    })
    : [];
  return json({
    ready: oauthReady && authorityReady,
    environment: env.ENVIRONMENT,
    deploymentRevision: env.DEPLOYMENT_REVISION,
    dependencies: {
      oauth: { ...oauth, probeError: oauthProbeError },
      authorities: {
        configured: parseAuthorityConfig(env.AUTHORITY_CONFIG_JSON).size,
        verified: authority.verifiedCount,
        operational: authority.operationalCount,
        required: PRODUCTS.length,
        probeError: authorityProbeError,
        products: authorityProducts,
      },
    },
  }, oauthReady && authorityReady ? 200 : 503);
}

async function mcp(request: Request, env: Env): Promise<Response> {
  if (!(await authenticate(request, env))) return challenge(request);
  const parsed = await readBoundedJson(request, 64_000);
  if (!parsed.ok) {
    if (parsed.tooLarge) return json({ error: "request_too_large" }, 413);
    return json({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" } }, 400);
  }
  if (!parsed.value || typeof parsed.value !== "object" || Array.isArray(parsed.value)) {
    return json({ jsonrpc: "2.0", id: null, error: { code: -32600, message: "Invalid Request" } }, 400);
  }
  const body = parsed.value as Record<string, unknown>;
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

export class AuthorityCheckpoint {
  constructor(
    private readonly state: DurableObjectState,
    private readonly env: Env,
  ) {}

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    if (request.method !== "POST" || url.pathname !== "/accept") return json({ accepted: false }, 404);
    const parsed = await readBoundedJson(request, 4_096);
    if (!parsed.ok) return json({ accepted: false }, parsed.tooLarge ? 413 : 400);
    const candidate = parsed.value;
    if (!isCheckpointRecord(candidate) || candidate.environment !== this.env.ENVIRONMENT) {
      return json({ accepted: false, reason: "checkpoint_invalid" }, 503);
    }
    try {
      const result = await this.state.storage.transaction(async (transaction) => {
        const prior = await transaction.get<unknown>("checkpoint-v3");
        if (prior !== undefined && !isCheckpointRecord(prior)) {
          return "unavailable" as const;
        }
        if (prior !== undefined && (
          prior.environment !== candidate.environment
          || prior.productId !== candidate.productId
          || prior.audience !== candidate.audience
        )) {
          return "unavailable" as const;
        }
        if (prior !== undefined) {
          if (candidate.revision < prior.revision) return "rollback" as const;
          if (candidate.publicKeyHash !== prior.publicKeyHash) return "rollback" as const;
          if (candidate.revision === prior.revision) {
            const identical = candidate.observationHash === prior.observationHash
              && candidate.truthHash === prior.truthHash
              && candidate.lastVerifiedAt === prior.lastVerifiedAt
              && candidate.sourceObservedAt === prior.sourceObservedAt
              && candidate.sourceExpiresAt === prior.sourceExpiresAt;
            return identical ? "accepted" as const : "rollback" as const;
          }
          if (Date.parse(candidate.lastVerifiedAt) < Date.parse(prior.lastVerifiedAt)) return "rollback" as const;
          if (Date.parse(candidate.sourceObservedAt) < Date.parse(prior.sourceObservedAt)) return "rollback" as const;
        }
        await transaction.put("checkpoint-v3", candidate);
        return "accepted" as const;
      });
      if (result === "rollback") return json({ accepted: false, reason: "rollback" }, 409);
      if (result === "unavailable") return json({ accepted: false, reason: "checkpoint_invalid" }, 503);
      return json({ accepted: true });
    } catch {
      return json({ accepted: false, reason: "checkpoint_unavailable" }, 503);
    }
  }
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
