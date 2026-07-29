/**
 * Personal MCP Gateway authority signer.
 *
 * The signer never invents product state. For every signed document it reads a
 * strict, short-lived observation from the corresponding product Worker over a
 * Cloudflare service binding, authenticates that read with a per-product
 * capability, validates it, and checkpoints it before signing.
 */

const PRODUCTS = ["identity-focus", "journal", "watch", "suixin"] as const;
type ProductId = (typeof PRODUCTS)[number];

const OBSERVATION_MEDIA_TYPE = "application/vnd.poyi.authority-observation.v1+json";
const DOCUMENT_MEDIA_TYPE = "application/vnd.poyi.authority-document.v2+json";
const MAX_OBSERVATION_BYTES = 64_000;
const MAX_OBSERVATION_AGE_MS = 5 * 60 * 1_000;
const MAX_OBSERVATION_TTL_MS = 10 * 60 * 1_000;
const SIGNED_DOCUMENT_TTL_MS = 5 * 60 * 1_000;
const CLOCK_SKEW_MS = 60 * 1_000;

interface SignerEnv {
  ENVIRONMENT: string;
  AUTHORITY_SIGNING_KEY: string;
  AUTHORITY_ISSUER: DurableObjectNamespace;
  FOCUSLINK_AUTHORITY_SOURCE?: Fetcher;
  JOURNAL_OBSERVATION?: Fetcher;
  WATCH_OBSERVATION?: Fetcher;
  SUIXIN_OBSERVATION?: Fetcher;
  FOCUSLINK_OBSERVATION_CAPABILITY?: string;
  JOURNAL_AUTHORITY_OBSERVATION_CAPABILITY?: string;
  WATCH_AUTHORITY_CAPABILITY?: string;
  SUIXIN_AUTHORITY_CAPABILITY?: string;
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

export interface ProductAuthorityObservationV1 {
  schemaVersion: 1;
  productId: ProductId;
  audience: string;
  observedAt: string;
  expiresAt: string;
  truth: AuthorityTruth;
}

interface SignerCheckpointV1 {
  schemaVersion: 1;
  environment: string;
  productId: ProductId;
  audience: string;
  keyMaterialHash: string;
  revision: number;
  observationHash: string;
  truthHash: string;
  lastVerifiedAt: string;
  sourceObservedAt: string;
  sourceExpiresAt: string;
}

type ObservationSourceIssue =
  | "not_configured"
  | "redirect_rejected"
  | "http_rejected"
  | "invalid_response"
  | "transport_failed";

type ObservationFetchResult =
  | { observation: ProductAuthorityObservationV1; issue: null; status: null }
  | { observation: null; issue: ObservationSourceIssue; status: number | null };

const jsonHeaders = {
  "content-type": "application/json; charset=utf-8",
  "cache-control": "no-store",
  "x-content-type-options": "nosniff",
  "referrer-policy": "no-referrer",
};

function json(body: unknown, status = 200, contentType = jsonHeaders["content-type"]): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...jsonHeaders, "content-type": contentType },
  });
}

function isProductId(value: unknown): value is ProductId {
  return typeof value === "string" && PRODUCTS.includes(value as ProductId);
}

function hasExactKeys(value: Record<string, unknown>, expected: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  const sortedExpected = [...expected].sort();
  return actual.length === sortedExpected.length && actual.every((key, index) => key === sortedExpected[index]);
}

function parseTimestamp(value: unknown): number | null {
  if (typeof value !== "string" || value.length > 40 || !/(?:Z|[+-]\d{2}:\d{2})$/.test(value)) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function exactAuthorityAudience(value: string, productId: ProductId): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "https:"
      && !url.username
      && !url.password
      && !url.search
      && !url.hash
      && url.pathname === `/authority/${productId}`;
  } catch {
    return false;
  }
}

function validBlockerReason(value: unknown): value is string | null {
  return value === null || (
    typeof value === "string"
    && value.length > 0
    && value.length <= 256
    && !/[\u0000-\u001f\u007f]/.test(value)
  );
}

export function validateObservation(
  value: unknown,
  expectedProductId: ProductId,
  expectedAudience: string,
  now = Date.now(),
): value is ProductAuthorityObservationV1 {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const observation = value as Record<string, unknown>;
  if (!hasExactKeys(observation, ["schemaVersion", "productId", "audience", "observedAt", "expiresAt", "truth"])) return false;
  if (observation.schemaVersion !== 1 || observation.productId !== expectedProductId || observation.audience !== expectedAudience) return false;
  if (typeof observation.audience !== "string" || !exactAuthorityAudience(observation.audience, expectedProductId)) return false;

  const observedAt = parseTimestamp(observation.observedAt);
  const expiresAt = parseTimestamp(observation.expiresAt);
  if (observedAt === null || expiresAt === null) return false;
  if (observedAt > now + CLOCK_SKEW_MS || now - observedAt > MAX_OBSERVATION_AGE_MS) return false;
  if (expiresAt <= observedAt || expiresAt <= now || expiresAt - observedAt > MAX_OBSERVATION_TTL_MS) return false;

  if (!observation.truth || typeof observation.truth !== "object" || Array.isArray(observation.truth)) return false;
  const truth = observation.truth as Record<string, unknown>;
  if (!hasExactKeys(truth, ["revision", "freshness", "lastVerifiedAt", "pendingCount", "blockerReason", "pcOff"])) return false;
  if (!Number.isSafeInteger(truth.revision) || (truth.revision as number) < 0) return false;
  if (!["fresh", "stale", "offline", "blocked", "unknown"].includes(String(truth.freshness))) return false;
  const lastVerifiedAt = parseTimestamp(truth.lastVerifiedAt);
  if (lastVerifiedAt === null || lastVerifiedAt > observedAt) return false;
  if (!Number.isSafeInteger(truth.pendingCount) || (truth.pendingCount as number) < 0) return false;
  if (!validBlockerReason(truth.blockerReason)) return false;
  if (!truth.pcOff || typeof truth.pcOff !== "object" || Array.isArray(truth.pcOff)) return false;
  const pcOff = truth.pcOff as Record<string, unknown>;
  if (!hasExactKeys(pcOff, ["readAvailable", "writeAvailable", "continuedSync"])) return false;
  if (![pcOff.readAvailable, pcOff.writeAvailable, pcOff.continuedSync].every((item) => typeof item === "boolean")) return false;
  if (pcOff.continuedSync && !(pcOff.readAvailable && pcOff.writeAvailable)) return false;

  const freshness = truth.freshness as AuthorityTruth["freshness"];
  const pendingCount = truth.pendingCount as number;
  if (freshness === "fresh" && (pendingCount !== 0 || truth.blockerReason !== null || now - lastVerifiedAt > MAX_OBSERVATION_AGE_MS)) return false;
  if (pendingCount > 0 && freshness !== "blocked") return false;
  if ((truth.blockerReason === null) !== (freshness !== "blocked")) return false;
  return true;
}

function isSignerCheckpoint(value: unknown): value is SignerCheckpointV1 {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  return hasExactKeys(record, [
    "schemaVersion", "environment", "productId", "audience", "keyMaterialHash", "revision",
    "observationHash", "truthHash", "lastVerifiedAt", "sourceObservedAt", "sourceExpiresAt",
  ])
    && record.schemaVersion === 1
    && typeof record.environment === "string"
    && isProductId(record.productId)
    && typeof record.audience === "string"
    && exactAuthorityAudience(record.audience, record.productId)
    && typeof record.keyMaterialHash === "string" && /^[0-9a-f]{64}$/.test(record.keyMaterialHash)
    && Number.isSafeInteger(record.revision) && (record.revision as number) >= 0
    && typeof record.observationHash === "string" && /^[0-9a-f]{64}$/.test(record.observationHash)
    && typeof record.truthHash === "string" && /^[0-9a-f]{64}$/.test(record.truthHash)
    && parseTimestamp(record.lastVerifiedAt) !== null
    && parseTimestamp(record.sourceObservedAt) !== null
    && parseTimestamp(record.sourceExpiresAt) !== null;
}

function base64UrlBytes(value: string): Uint8Array | null {
  if (!value || !/^[A-Za-z0-9_-]+$/.test(value)) return null;
  try {
    const padded = value.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (value.length % 4)) % 4);
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

function asciiJsonString(value: string): string {
  return JSON.stringify(value).replace(/[\u007f-\uffff]/g, (character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`);
}

export function canonicalAuthorityJson(value: unknown): string {
  if (typeof value === "string") return asciiJsonString(value);
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalAuthorityJson).join(",")}]`;
  const object = value as Record<string, unknown>;
  return `{${Object.keys(object).sort().map((key) => `${asciiJsonString(key)}:${canonicalAuthorityJson(object[key])}`).join(",")}}`;
}

async function sha256(value: Uint8Array | string): Promise<string> {
  const bytes = typeof value === "string" ? new TextEncoder().encode(value) : value;
  const digest = await crypto.subtle.digest("SHA-256", Uint8Array.from(bytes).buffer);
  return Array.from(new Uint8Array(digest)).map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function sourceFor(env: SignerEnv, productId: ProductId): { fetcher: Fetcher | undefined; capability: string | undefined; path: string } {
  switch (productId) {
    case "identity-focus": return {
      fetcher: env.FOCUSLINK_AUTHORITY_SOURCE,
      capability: env.FOCUSLINK_OBSERVATION_CAPABILITY,
      path: "/internal/authority-observation/v1",
    };
    case "journal": return {
      fetcher: env.JOURNAL_OBSERVATION,
      capability: env.JOURNAL_AUTHORITY_OBSERVATION_CAPABILITY,
      path: "/internal/authority-observation/v1",
    };
    case "watch": return {
      fetcher: env.WATCH_OBSERVATION,
      capability: env.WATCH_AUTHORITY_CAPABILITY,
      path: "/_internal/v1/authority-observation",
    };
    case "suixin": return {
      fetcher: env.SUIXIN_OBSERVATION,
      capability: env.SUIXIN_AUTHORITY_CAPABILITY,
      path: "/internal/sync-overview/v1",
    };
  }
}

function validCapability(value: string | undefined): value is string {
  return typeof value === "string" && value.length >= 32 && value.length <= 512 && /^[A-Za-z0-9._~-]+$/.test(value);
}

async function readBoundedResponse(response: Response): Promise<unknown | null> {
  const mediaType = (response.headers.get("content-type") ?? "").split(";", 1)[0]?.trim().toLowerCase();
  if (!response.ok || mediaType !== OBSERVATION_MEDIA_TYPE) {
    await response.body?.cancel();
    return null;
  }
  const contentLength = response.headers.get("content-length");
  if (contentLength !== null) {
    const declared = Number(contentLength);
    if (!Number.isSafeInteger(declared) || declared < 0 || declared > MAX_OBSERVATION_BYTES) {
      await response.body?.cancel();
      return null;
    }
  }
  if (!response.body) return null;
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > MAX_OBSERVATION_BYTES) {
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
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) as unknown;
  } catch {
    return null;
  } finally {
    reader.releaseLock();
  }
}

async function fetchObservation(
  env: SignerEnv,
  productId: ProductId,
  expectedAudience: string,
  now = Date.now(),
): Promise<ObservationFetchResult> {
  const source = sourceFor(env, productId);
  if (!source.fetcher || !validCapability(source.capability)) {
    return { observation: null, issue: "not_configured", status: null };
  }
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5_000);
  try {
    const request = new Request(`https://${productId}.authority-source.internal${source.path}`, {
      method: "GET",
      redirect: "manual",
      signal: controller.signal,
      headers: {
        accept: OBSERVATION_MEDIA_TYPE,
        authorization: `Capability ${source.capability}`,
        "x-poyi-authority-audience": expectedAudience,
      },
    });
    const response = await source.fetcher.fetch(request);
    if (response.status >= 300 && response.status < 400) {
      await response.body?.cancel();
      return { observation: null, issue: "redirect_rejected", status: response.status };
    }
    if (!response.ok) {
      await response.body?.cancel();
      return { observation: null, issue: "http_rejected", status: response.status };
    }
    const value = await readBoundedResponse(response);
    return validateObservation(value, productId, expectedAudience, now)
      ? { observation: value, issue: null, status: null }
      : { observation: null, issue: "invalid_response", status: response.status };
  } catch {
    return { observation: null, issue: "transport_failed", status: null };
  } finally {
    clearTimeout(timeout);
  }
}

async function readBoundedRequest(request: Request): Promise<unknown | null> {
  const contentType = (request.headers.get("content-type") ?? "").split(";", 1)[0]?.trim().toLowerCase();
  if (contentType !== OBSERVATION_MEDIA_TYPE) return null;
  const response = new Response(request.body, { headers: { "content-type": OBSERVATION_MEDIA_TYPE } });
  return await readBoundedResponse(response);
}

export class AuthorityIssuer implements DurableObject {
  constructor(
    private readonly state: DurableObjectState,
    private readonly env: SignerEnv,
  ) {}

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    if (request.method !== "POST" || url.pathname !== "/sign") return json({ error: "not_found" }, 404);
    const value = await readBoundedRequest(request);
    if (!value || typeof value !== "object" || Array.isArray(value)) return json({ error: "invalid_observation" }, 503);
    const productId = (value as Record<string, unknown>).productId;
    const audience = (value as Record<string, unknown>).audience;
    if (!isProductId(productId) || typeof audience !== "string" || !validateObservation(value, productId, audience)) {
      return json({ error: "invalid_observation" }, 503);
    }
    const observation = value;

    const keyBytes = base64UrlBytes(this.env.AUTHORITY_SIGNING_KEY ?? "");
    if (!keyBytes) return json({ error: "signing_key_not_configured" }, 503);
    let privateKey: CryptoKey;
    try {
      privateKey = await crypto.subtle.importKey("pkcs8", keyBytes.buffer as ArrayBuffer, { name: "Ed25519" }, false, ["sign"]);
    } catch {
      return json({ error: "signing_key_invalid" }, 503);
    }

    const observationHash = await sha256(canonicalAuthorityJson(observation));
    const truthHash = await sha256(canonicalAuthorityJson(observation.truth));
    const keyMaterialHash = await sha256(keyBytes);
    const candidate: SignerCheckpointV1 = {
      schemaVersion: 1,
      environment: this.env.ENVIRONMENT,
      productId,
      audience,
      keyMaterialHash,
      revision: observation.truth.revision,
      observationHash,
      truthHash,
      lastVerifiedAt: observation.truth.lastVerifiedAt,
      sourceObservedAt: observation.observedAt,
      sourceExpiresAt: observation.expiresAt,
    };

    try {
      const result = await this.state.storage.transaction(async (transaction) => {
        const prior = await transaction.get<unknown>("source-checkpoint-v1");
        if (prior !== undefined && !isSignerCheckpoint(prior)) return "invalid" as const;
        if (prior !== undefined) {
          if (prior.environment !== candidate.environment || prior.productId !== candidate.productId || prior.audience !== candidate.audience) return "invalid" as const;
          if (prior.keyMaterialHash !== candidate.keyMaterialHash || candidate.revision < prior.revision) return "rollback" as const;
          if (candidate.revision === prior.revision) {
            const identical = candidate.observationHash === prior.observationHash
              && candidate.truthHash === prior.truthHash
              && candidate.lastVerifiedAt === prior.lastVerifiedAt
              && candidate.sourceObservedAt === prior.sourceObservedAt
              && candidate.sourceExpiresAt === prior.sourceExpiresAt;
            return identical ? "accepted" as const : "rollback" as const;
          }
          if (Date.parse(candidate.lastVerifiedAt) < Date.parse(prior.lastVerifiedAt)
            || Date.parse(candidate.sourceObservedAt) < Date.parse(prior.sourceObservedAt)) return "rollback" as const;
        }
        await transaction.put("source-checkpoint-v1", candidate);
        return "accepted" as const;
      });
      if (result === "rollback") return json({ error: "observation_rollback" }, 409);
      if (result === "invalid") return json({ error: "checkpoint_invalid" }, 503);
    } catch {
      return json({ error: "checkpoint_unavailable" }, 503);
    }

    const issuedAtMs = Date.now();
    const expiresAtMs = Math.min(issuedAtMs + SIGNED_DOCUMENT_TTL_MS, Date.parse(observation.expiresAt));
    if (expiresAtMs <= issuedAtMs) return json({ error: "observation_expired" }, 503);
    const unsigned = {
      schemaVersion: 2 as const,
      productId,
      audience,
      issuedAt: new Date(issuedAtMs).toISOString(),
      expiresAt: new Date(expiresAtMs).toISOString(),
      sourceObservedAt: observation.observedAt,
      sourceExpiresAt: observation.expiresAt,
      observationHash,
      truth: observation.truth,
    };
    try {
      const signature = new Uint8Array(await crypto.subtle.sign(
        "Ed25519",
        privateKey,
        new TextEncoder().encode(canonicalAuthorityJson(unsigned)),
      ));
      return json({ ...unsigned, signature: base64Url(signature) }, 200, `${DOCUMENT_MEDIA_TYPE}; charset=utf-8`);
    } catch {
      return json({ error: "signing_failed" }, 503);
    }
  }
}

export default {
  async fetch(request: Request, env: SignerEnv): Promise<Response> {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/healthz") {
      return json({ alive: true, service: "personal-mcp-authority", environment: env.ENVIRONMENT });
    }
    if (request.method !== "GET") return json({ error: "method_not_allowed" }, 405);
    const match = /^\/authority\/(identity-focus|journal|watch|suixin)$/.exec(url.pathname);
    if (!match || !isProductId(match[1])) return json({ error: "not_found" }, 404);
    const productId = match[1];
    const expectedAudience = `${url.origin}/authority/${productId}`;
    const source = await fetchObservation(env, productId, expectedAudience);
    if (!source.observation) {
      return json({
        error: "authority_source_unavailable",
        sourceIssue: source.issue,
        sourceStatus: source.status,
      }, 503);
    }
    const observation = source.observation;
    try {
      const id = env.AUTHORITY_ISSUER.idFromName(`source-v1:${env.ENVIRONMENT}:${productId}`);
      return await env.AUTHORITY_ISSUER.get(id).fetch("https://authority-issuer.internal/sign", {
        method: "POST",
        headers: { "content-type": OBSERVATION_MEDIA_TYPE },
        body: JSON.stringify(observation),
      });
    } catch {
      return json({ error: "authority_signer_unavailable" }, 503);
    }
  },
};

export { DOCUMENT_MEDIA_TYPE, OBSERVATION_MEDIA_TYPE };
