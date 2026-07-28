import { describe, expect, it } from "vitest";

import worker, {
  AuthorityCheckpoint,
  base64Url,
  canonicalJson,
  parseAuthorityConfig,
  unknownProduct,
  validateAuthorityShape,
  verifyAuthority,
} from "../src/index";

const truth = {
  revision: 7,
  freshness: "fresh",
  lastVerifiedAt: "2026-07-29T00:00:00Z",
  pendingCount: 0,
  blockerReason: null,
  pcOff: { readAvailable: true, writeAvailable: false, continuedSync: false },
};

describe("authority fail-closed contract", () => {
  it("accepts only the exact signed status shape", () => {
    const value = {
      schemaVersion: 1,
      productId: "journal",
      issuedAt: "2026-07-29T00:00:01Z",
      expiresAt: "2026-07-29T00:10:01Z",
      truth,
      signature: "A".repeat(86),
    };
    expect(validateAuthorityShape(value)).toBe(true);
    expect(validateAuthorityShape({ ...value, secret: "must-not-pass" })).toBe(false);
    expect(validateAuthorityShape({ ...value, truth: { ...truth, pendingCount: 1 } })).toBe(false);
    expect(validateAuthorityShape({ ...value, truth: { ...truth, pcOff: { readAvailable: false, writeAvailable: false, continuedSync: true } } })).toBe(false);
  });

  it("projects every missing authority as unknown and all pc-off capabilities false", () => {
    expect(unknownProduct("watch", "authority_not_configured")).toMatchObject({
      revision: null,
      freshness: "unknown",
      lastVerifiedAt: null,
      pendingCount: null,
      pcOff: { readAvailable: false, writeAvailable: false, continuedSync: false },
      authorityVerification: { state: "missing", issue: "authority_not_configured" },
    });
  });

  it("rejects incomplete, duplicate, non-HTTPS, and malformed authority configuration", () => {
    const key = "A".repeat(43);
    const valid = { productId: "journal", url: "https://journal.example/status", publicKey: key, maxAgeSeconds: 900 };
    expect(parseAuthorityConfig(JSON.stringify([valid])).size).toBe(1);
    expect(parseAuthorityConfig(JSON.stringify([valid, valid])).size).toBe(0);
    expect(parseAuthorityConfig(JSON.stringify([{ ...valid, url: "http://journal.example/status" }])).size).toBe(0);
    expect(parseAuthorityConfig("not-json").size).toBe(0);
  });

  it("uses Python-compatible recursive sorted-key canonical JSON", () => {
    expect(canonicalJson({ z: 1, a: { y: true, x: null } })).toBe('{"a":{"x":null,"y":true},"z":1}');
    expect(canonicalJson({ blockerReason: "设备离线 🚫" })).toBe('{"blockerReason":"\\u8bbe\\u5907\\u79bb\\u7ebf \\ud83d\\udeab"}');
  });
});

describe("public staging boundary", () => {
  const env = {
    ENVIRONMENT: "staging",
    DEPLOYMENT_REVISION: "test",
    OAUTH_ISSUER: "https://oauth.example",
    OAUTH_AUDIENCE: "https://gateway.example/mcp",
    OAUTH_JWKS_URL: "https://oauth.example/jwks.json",
    OAUTH_INTROSPECTION_URL: "https://oauth.example/introspect",
    OAUTH_RS_CLIENT_ID: "gateway-staging",
    AUTHORITY_CONFIG_JSON: "[]",
    AUTHORITY_CHECKPOINTS: {},
  };

  it("publishes exact protected-resource metadata without credentials", async () => {
    const response = await worker.fetch(new Request("https://gateway.example/.well-known/oauth-protected-resource/mcp"), env as never);
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      resource: "https://gateway.example/mcp",
      authorization_servers: ["https://oauth.example"],
      jwks_uri: "https://oauth.example/jwks.json",
      scopes_supported: ["gateway:read"],
      bearer_methods_supported: ["header"],
    });
  });

  it("fails closed before parsing anonymous MCP requests", async () => {
    const response = await worker.fetch(new Request("https://gateway.example/mcp", { method: "POST", body: "{}" }), env as never);
    expect(response.status).toBe(401);
    expect(response.headers.get("www-authenticate")).toContain("/.well-known/oauth-protected-resource/mcp");
  });

  it("reports missing OAuth secret and authorities as not ready", async () => {
    const response = await worker.fetch(new Request("https://gateway.example/readyz"), env as never);
    expect(response.status).toBe(503);
    const body = await response.json() as { ready: boolean; dependencies: { authorities: { verified: number; required: number } } };
    expect(body.ready).toBe(false);
    expect(body.dependencies.authorities).toMatchObject({ verified: 0, required: 4 });
  });

  it("reports unavailable configured OAuth dependencies as 503 instead of throwing", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () => new Response('{"active":false}', { status: 404, headers: { "content-type": "application/json" } });
    try {
      const response = await worker.fetch(
        new Request("https://gateway.example/readyz"),
        { ...env, OAUTH_RS_CLIENT_SECRET: "s".repeat(64) } as never,
      );
      expect(response.status).toBe(503);
      expect(await response.json()).toMatchObject({ ready: false });
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("rejects 200 HTML and semantically invalid OAuth metadata/JWKS", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () => new Response("<html>not oauth</html>", { status: 200, headers: { "content-type": "text/html" } });
    try {
      const response = await worker.fetch(
        new Request("https://gateway.example/readyz"),
        { ...env, OAUTH_RS_CLIENT_SECRET: "s".repeat(64) } as never,
      );
      const body = await response.json() as { dependencies: { oauth: { metadata: boolean; jwks: boolean; introspection: boolean } } };
      expect(response.status).toBe(503);
      expect(body.dependencies.oauth).toMatchObject({ metadata: false, jwks: false, introspection: false });
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});

describe("signed staging authority verification", () => {
  it("bounds checkpoint JSON without trusting Content-Length", async () => {
    const checkpointObject = new AuthorityCheckpoint({ storage: {} } as never, { ENVIRONMENT: "staging" } as never);
    const request = new Request("https://authority-checkpoint.internal/accept", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ padding: "x".repeat(5_000) }),
    });
    expect(request.headers.get("content-length")).toBeNull();
    expect((await checkpointObject.fetch(request)).status).toBe(413);
  });

  it("accepts a valid Ed25519 document and rejects tamper, expiry, product mismatch, and rollback", async () => {
    const keys = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
    const publicKey = base64Url(new Uint8Array(await crypto.subtle.exportKey("raw", keys.publicKey)));
    const now = Date.parse("2026-07-29T00:05:00Z");
    const makeDocument = async (revision: number, overrides: Record<string, unknown> = {}) => {
      const unsigned = {
        schemaVersion: 1,
        productId: "journal",
        issuedAt: "2026-07-29T00:04:00Z",
        expiresAt: "2026-07-29T00:14:00Z",
        truth: { ...truth, revision, lastVerifiedAt: "2026-07-29T00:03:59Z" },
        ...overrides,
      };
      const signature = await crypto.subtle.sign(
        "Ed25519",
        keys.privateKey,
        new TextEncoder().encode(canonicalJson(unsigned)),
      );
      return { ...unsigned, signature: base64Url(new Uint8Array(signature)) };
    };
    let body = await makeDocument(7);
    const checkpoints = new Map<string, unknown>();
    const state = {
      storage: {
        async transaction<T>(callback: (transaction: { get(key: string): Promise<unknown>; put(key: string, value: unknown): Promise<void> }) => Promise<T>) {
          return await callback({
            async get(key: string) { return checkpoints.get(key); },
            async put(key: string, value: unknown) { checkpoints.set(key, value); },
          });
        },
      },
    };
    const checkpointObject = new AuthorityCheckpoint(state as never, { ENVIRONMENT: "staging" } as never);
    const env = {
      ENVIRONMENT: "staging",
      AUTHORITY_CHECKPOINTS: {
        idFromName(name: string) { return name; },
        get() {
          return {
            async fetch(input: string, init: RequestInit) {
              return await checkpointObject.fetch(new Request(input, init));
            },
          };
        },
      },
    };
    const config = { productId: "journal" as const, url: "https://journal.example/sync/v2/status", publicKey, maxAgeSeconds: 900 };
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () => new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
    try {
      expect(await verifyAuthority(env as never, config, now)).toMatchObject({ issue: null, verified: { truth: { revision: 7 } } });

      body = structuredClone(body);
      body.truth.revision = 8;
      expect(await verifyAuthority(env as never, config, now)).toMatchObject({ verified: null, issue: "authority_signature_invalid" });

      body = await makeDocument(8, { issuedAt: "2026-07-28T23:40:00Z", expiresAt: "2026-07-28T23:50:00Z" });
      expect(await verifyAuthority(env as never, config, now)).toMatchObject({ verified: null, issue: "authority_status_expired" });

      body = await makeDocument(8, { productId: "watch" });
      expect(await verifyAuthority(env as never, config, now)).toMatchObject({ verified: null, issue: "authority_product_mismatch" });

      body = await makeDocument(6);
      expect(await verifyAuthority(env as never, config, now)).toMatchObject({ verified: null, issue: "authority_revision_rollback" });

      checkpoints.set("checkpoint", {});
      body = await makeDocument(9);
      expect(await verifyAuthority(env as never, config, now)).toMatchObject({ verified: null, issue: "authority_checkpoint_unavailable" });
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
