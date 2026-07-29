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
import authorityWorker, {
  AuthorityIssuer,
  DOCUMENT_MEDIA_TYPE,
  OBSERVATION_MEDIA_TYPE,
  validateObservation,
} from "../authority/src/index";

const truth = {
  revision: 7,
  freshness: "fresh",
  lastVerifiedAt: "2026-07-29T00:00:00Z",
  pendingCount: 0,
  blockerReason: null,
  pcOff: { readAvailable: true, writeAvailable: false, continuedSync: false },
};

async function sha256(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest)).map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function signedDocument(
  keys: CryptoKeyPair,
  revision: number,
  overrides: Record<string, unknown> = {},
) {
  const unsignedBase = {
    schemaVersion: 2,
    productId: "journal",
    audience: "https://authority.example/authority/journal",
    issuedAt: "2026-07-29T00:04:01Z",
    expiresAt: "2026-07-29T00:09:01Z",
    sourceObservedAt: "2026-07-29T00:04:00Z",
    sourceExpiresAt: "2026-07-29T00:09:01Z",
    truth: { ...truth, revision, lastVerifiedAt: "2026-07-29T00:03:59Z" },
    ...overrides,
  };
  const observationHash = await sha256(canonicalJson({
    schemaVersion: 1,
    productId: unsignedBase.productId,
    audience: unsignedBase.audience,
    observedAt: unsignedBase.sourceObservedAt,
    expiresAt: unsignedBase.sourceExpiresAt,
    truth: unsignedBase.truth,
  }));
  const unsigned = { ...unsignedBase, observationHash };
  const signature = await crypto.subtle.sign(
    "Ed25519",
    keys.privateKey,
    new TextEncoder().encode(canonicalJson(unsigned)),
  );
  return { ...unsigned, signature: base64Url(new Uint8Array(signature)) };
}

describe("authority fail-closed contract", () => {
  it("accepts only the exact signed status shape", () => {
    const value = {
      schemaVersion: 2,
      productId: "journal",
      audience: "https://authority.example/authority/journal",
      issuedAt: "2026-07-29T00:00:01Z",
      expiresAt: "2026-07-29T00:10:01Z",
      sourceObservedAt: "2026-07-29T00:00:00Z",
      sourceExpiresAt: "2026-07-29T00:10:01Z",
      observationHash: "a".repeat(64),
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
    const valid = { productId: "journal", url: "https://authority.example/authority/journal", publicKey: key, maxAgeSeconds: 900 };
    expect(parseAuthorityConfig(JSON.stringify([valid])).size).toBe(1);
    expect(parseAuthorityConfig(JSON.stringify([valid, valid])).size).toBe(0);
    expect(parseAuthorityConfig(JSON.stringify([{ ...valid, url: "http://authority.example/authority/journal" }])).size).toBe(0);
    expect(parseAuthorityConfig(JSON.stringify([{ ...valid, url: "https://authority.example/status" }])).size).toBe(0);
    expect(parseAuthorityConfig(JSON.stringify([{ ...valid, url: `${valid.url}?token=forbidden` }])).size).toBe(0);
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
    expect(body.dependencies.authorities).toMatchObject({
      products: [
        { productId: "identity-focus", state: "missing", issue: "authority_not_configured" },
        { productId: "journal", state: "missing", issue: "authority_not_configured" },
        { productId: "watch", state: "missing", issue: "authority_not_configured" },
        { productId: "suixin", state: "missing", issue: "authority_not_configured" },
      ],
    });
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

  it("checkpoint rejects key rotation, observation divergence, and source-time rollback", async () => {
    const records = new Map<string, unknown>();
    const state = {
      storage: {
        async transaction<T>(callback: (transaction: {
          get(key: string): Promise<unknown>;
          put(key: string, value: unknown): Promise<void>;
        }) => Promise<T>) {
          return await callback({
            async get(key: string) { return records.get(key); },
            async put(key: string, value: unknown) { records.set(key, value); },
          });
        },
      },
    };
    const checkpoint = new AuthorityCheckpoint(state as never, { ENVIRONMENT: "staging" } as never);
    const candidate = {
      schemaVersion: 3,
      environment: "staging",
      productId: "journal",
      audience: "https://authority.example/authority/journal",
      publicKeyHash: "a".repeat(64),
      revision: 7,
      observationHash: "b".repeat(64),
      truthHash: "b".repeat(64),
      lastVerifiedAt: "2026-07-29T00:03:59Z",
      sourceObservedAt: "2026-07-29T00:04:00Z",
      sourceExpiresAt: "2026-07-29T00:09:00Z",
    };
    const accept = async (value: unknown) => await checkpoint.fetch(new Request(
      "https://authority-checkpoint.internal/accept",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(value),
      },
    ));

    expect((await accept(candidate)).status).toBe(200);
    expect((await accept(candidate)).status).toBe(200);
    expect((await accept({ ...candidate, revision: 8, publicKeyHash: "c".repeat(64) })).status).toBe(409);
    expect((await accept({ ...candidate, truthHash: "d".repeat(64) })).status).toBe(409);
    expect((await accept({
      ...candidate,
      revision: 8,
      observationHash: "e".repeat(64),
      truthHash: "e".repeat(64),
      lastVerifiedAt: "2026-07-29T00:03:58Z",
    })).status).toBe(409);
    expect((await accept({
      ...candidate,
      revision: 8,
      truthHash: "e".repeat(64),
      observationHash: "e".repeat(64),
      lastVerifiedAt: "2026-07-29T00:04:00Z",
      sourceObservedAt: "2026-07-29T00:04:01Z",
    })).status).toBe(200);
  });

  it("accepts a valid Ed25519 document and rejects tamper, expiry, product mismatch, and rollback", async () => {
    const keys = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
    const publicKey = base64Url(new Uint8Array(await crypto.subtle.exportKey("raw", keys.publicKey)));
    const now = Date.parse("2026-07-29T00:05:00Z");
    let body = await signedDocument(keys as CryptoKeyPair, 7);
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
    const config = { productId: "journal" as const, url: "https://authority.example/authority/journal", publicKey, maxAgeSeconds: 900 };
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () => { throw new Error("public fetch must not be used for authority truth"); };
    const authorityService = {
      async fetch() {
        return new Response(JSON.stringify(body), {
          status: 200,
          headers: { "content-type": DOCUMENT_MEDIA_TYPE },
        });
      },
    };
    try {
      expect(await verifyAuthority({ ...env, AUTHORITY_SERVICE: authorityService } as never, config, now)).toMatchObject({ issue: null, verified: { truth: { revision: 7 } } });

      expect(await verifyAuthority(env as never, config, now)).toMatchObject({
        verified: null,
        issue: "authority_fetch_failed",
      });

      body = structuredClone(body);
      body.truth.revision = 8;
      expect(await verifyAuthority({ ...env, AUTHORITY_SERVICE: authorityService } as never, config, now)).toMatchObject({ verified: null, issue: "authority_signature_invalid" });

      body = await signedDocument(keys as CryptoKeyPair, 8, {
        issuedAt: "2026-07-28T23:40:00Z",
        expiresAt: "2026-07-28T23:50:00Z",
        sourceObservedAt: "2026-07-28T23:40:00Z",
        sourceExpiresAt: "2026-07-28T23:50:00Z",
      });
      expect(await verifyAuthority({ ...env, AUTHORITY_SERVICE: authorityService } as never, config, now)).toMatchObject({ verified: null, issue: "authority_status_expired" });

      body = await signedDocument(keys as CryptoKeyPair, 8, {
        productId: "watch",
        audience: "https://authority.example/authority/watch",
      });
      expect(await verifyAuthority({ ...env, AUTHORITY_SERVICE: authorityService } as never, config, now)).toMatchObject({ verified: null, issue: "authority_product_mismatch" });

      body = await signedDocument(keys as CryptoKeyPair, 6);
      expect(await verifyAuthority({ ...env, AUTHORITY_SERVICE: authorityService } as never, config, now)).toMatchObject({ verified: null, issue: "authority_revision_rollback" });

      checkpoints.set("checkpoint-v3", {});
      body = await signedDocument(keys as CryptoKeyPair, 9);
      expect(await verifyAuthority({ ...env, AUTHORITY_SERVICE: authorityService } as never, config, now)).toMatchObject({ verified: null, issue: "authority_checkpoint_unavailable" });
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("rejects wrong content type and a stale lastVerifiedAt labelled fresh", async () => {
    const keys = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
    const publicKey = base64Url(new Uint8Array(await crypto.subtle.exportKey("raw", keys.publicKey)));
    const body = JSON.stringify(await signedDocument(keys as CryptoKeyPair, 7, {
      truth: { ...truth, revision: 7, lastVerifiedAt: "2026-07-28T23:00:00Z" },
    }));
    const config = {
      productId: "journal" as const,
      url: "https://authority.example/authority/journal",
      publicKey,
      maxAgeSeconds: 900,
    };
    const baseEnv = {
      ENVIRONMENT: "staging",
      AUTHORITY_CHECKPOINTS: {},
    };
    expect(await verifyAuthority({
      ...baseEnv,
      AUTHORITY_SERVICE: { fetch: async () => new Response(body, { headers: { "content-type": "text/html" } }) },
    } as never, config, Date.parse("2026-07-29T00:05:00Z"))).toMatchObject({
      verified: null,
      issue: "authority_signature_invalid",
    });
    expect(await verifyAuthority({
      ...baseEnv,
      AUTHORITY_SERVICE: { fetch: async () => new Response(body, { headers: { "content-type": DOCUMENT_MEDIA_TYPE } }) },
    } as never, config, Date.parse("2026-07-29T00:05:00Z"))).toMatchObject({
      verified: null,
      issue: "authority_status_expired",
    });
  });

  it("signs only a strict service-bound product observation and rejects same-revision drift", async () => {
    const keys = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
    const signingKey = base64Url(new Uint8Array(await crypto.subtle.exportKey("pkcs8", keys.privateKey)));
    const records = new Map<string, unknown>();
    const state = {
      storage: {
        async transaction<T>(callback: (transaction: { get(key: string): Promise<unknown>; put(key: string, value: unknown): Promise<void> }) => Promise<T>) {
          return await callback({
            async get(key: string) { return records.get(key); },
            async put(key: string, value: unknown) { records.set(key, value); },
          });
        },
      },
    };
    const signerEnv = { ENVIRONMENT: "test", AUTHORITY_SIGNING_KEY: signingKey };
    const issuer = new AuthorityIssuer(state as never, signerEnv as never);
    const observedAt = new Date(Date.now() - 1_000).toISOString();
    let observation = {
      schemaVersion: 1,
      productId: "journal",
      audience: "https://authority.example/authority/journal",
      observedAt,
      expiresAt: new Date(Date.now() + 120_000).toISOString(),
      truth: { ...truth, revision: 1, lastVerifiedAt: observedAt },
    };
    let calls = 0;
    const env = {
      ...signerEnv,
      JOURNAL_AUTHORITY_OBSERVATION_CAPABILITY: "c".repeat(48),
      JOURNAL_OBSERVATION: {
        async fetch(request: Request) {
          calls += 1;
          expect(new URL(request.url).pathname).toBe("/internal/authority-observation/v1");
          expect(request.headers.get("authorization")).toBe(`Capability ${"c".repeat(48)}`);
          expect(request.headers.get("x-poyi-authority-audience")).toBe(observation.audience);
          return new Response(JSON.stringify(observation), { headers: { "content-type": OBSERVATION_MEDIA_TYPE } });
        },
      },
      AUTHORITY_ISSUER: {
        idFromName(name: string) { return name; },
        get() {
          return { fetch: async (input: string, init: RequestInit) => await issuer.fetch(new Request(input, init)) };
        },
      },
    };
    expect(validateObservation(observation, "journal", observation.audience)).toBe(true);
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () => { throw new Error("public fetch must not be used for authority source"); };
    try {
      const first = await authorityWorker.fetch(new Request(observation.audience), env as never);
      expect(first.status).toBe(200);
      expect(first.headers.get("content-type")).toContain(DOCUMENT_MEDIA_TYPE);
      expect(validateAuthorityShape(await first.json())).toBe(true);
      expect(calls).toBe(1);

      observation = { ...observation, truth: { ...observation.truth, lastVerifiedAt: new Date(Date.now() - 2_000).toISOString() } };
      const drift = await authorityWorker.fetch(new Request(observation.audience), env as never);
      expect(drift.status).toBe(409);

      const missingCapability = await authorityWorker.fetch(new Request(observation.audience), {
        ...env,
        JOURNAL_AUTHORITY_OBSERVATION_CAPABILITY: undefined,
      } as never);
      expect(missingCapability.status).toBe(503);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
