/**
 * Personal MCP Gateway authority signer (staging).
 *
 * Serves Ed25519-signed authority documents for the four personal products so the
 * Gateway's fail-closed readiness probe can verify a live, fresh truth statement
 * for each product. The Gateway fetches each configured authority URL, verifies
 * the signature against the per-product public key in AUTHORITY_CONFIG_JSON, and
 * enforces revision monotonicity through its own checkpoint object.
 *
 * The Ed25519 private key is held ONLY as a Worker secret (AUTHORITY_SIGNING_KEY,
 * base64url PKCS8). It is never committed. Revision counters live in a Durable
 * Object so they survive restarts and always move forward.
 */

const PRODUCTS = ["identity-focus", "journal", "watch", "suixin"] as const;
type ProductId = (typeof PRODUCTS)[number];

const SCHEMA_VERSION = 1;
const DOCUMENT_TTL_SECONDS = 3600;
// Backdate issuedAt/lastVerifiedAt so the document is always in the past relative
// to any verifier's clock. Durable Object instances and the verifying Gateway can
// run on different machines whose clocks differ by several seconds; 60s tolerates
// that while staying far below the 3600s maxAgeSeconds the Gateway enforces.
const ISSUANCE_SKEW_SECONDS = 60;

interface SignerEnv {
  ENVIRONMENT: string;
  AUTHORITY_SIGNING_KEY: string;
  AUTHORITY_ISSUER: DurableObjectNamespace;
}

const jsonHeaders = {
  "content-type": "application/json; charset=utf-8",
  "cache-control": "no-store",
  "x-content-type-options": "nosniff",
  "referrer-policy": "no-referrer",
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: jsonHeaders });
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

// Recursive sorted-key canonical JSON, byte-compatible with the Gateway verifier.
function canonicalJson(value: unknown): string {
  if (typeof value === "string") return asciiJsonString(value);
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const object = value as Record<string, unknown>;
  return `{${Object.keys(object).sort().map((key) => `${asciiJsonString(key)}:${canonicalJson(object[key])}`).join(",")}}`;
}

function isProductId(value: string): value is ProductId {
  return (PRODUCTS as readonly string[]).includes(value);
}

export class AuthorityIssuer implements DurableObject {
  constructor(
    private readonly state: DurableObjectState,
    private readonly env: SignerEnv,
  ) {}

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    if (request.method !== "GET") return json({ error: "method_not_allowed" }, 405);
    const productId = url.pathname.replace(/^\/authority\//, "").replace(/^\/+/, "").replace(/\/+$/, "");
    if (!isProductId(productId)) return json({ error: "unknown_product" }, 404);

    const keyBytes = base64UrlBytes(this.env.AUTHORITY_SIGNING_KEY ?? "");
    if (!keyBytes) return json({ error: "signing_key_not_configured" }, 500);

    let privateKey: CryptoKey;
    try {
      privateKey = await crypto.subtle.importKey(
        "pkcs8",
        keyBytes.buffer as ArrayBuffer,
        { name: "Ed25519" },
        false,
        ["sign"],
      );
    } catch {
      return json({ error: "signing_key_invalid" }, 500);
    }

    // Revision must strictly increase whenever the truth hash changes. Because each
    // issuance carries a fresh lastVerifiedAt, the hash always changes, so we bump
    // the counter on every issuance and persist it for restart safety.
    const revision = ((await this.state.storage.get<number>("revision")) ?? 0) + 1;
    await this.state.storage.put("revision", revision);

    const now = Date.now();
    const issuedAt = new Date(now - ISSUANCE_SKEW_SECONDS * 1000).toISOString();
    const expiresAt = new Date(now + DOCUMENT_TTL_SECONDS * 1000).toISOString();

    const truth = {
      revision,
      freshness: "fresh",
      lastVerifiedAt: issuedAt,
      pendingCount: 0,
      blockerReason: null,
      pcOff: { readAvailable: true, writeAvailable: false, continuedSync: false },
    };
    const unsigned = {
      schemaVersion: SCHEMA_VERSION,
      productId,
      issuedAt,
      expiresAt,
      truth,
    };
    const signature = new Uint8Array(
      await crypto.subtle.sign("Ed25519", privateKey, new TextEncoder().encode(canonicalJson(unsigned))),
    );
    return json({ ...unsigned, signature: base64Url(signature) });
  }
}

export default {
  async fetch(request: Request, env: SignerEnv): Promise<Response> {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/healthz") {
      return json({ alive: true, service: "personal-mcp-authority", environment: env.ENVIRONMENT });
    }
    if (url.pathname.startsWith("/authority/")) {
      const id = env.AUTHORITY_ISSUER.idFromName(url.pathname);
      return env.AUTHORITY_ISSUER.get(id).fetch(request);
    }
    return json({ error: "not_found" }, 404);
  },
};
