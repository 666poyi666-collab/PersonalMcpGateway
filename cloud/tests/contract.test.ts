import { describe, expect, it } from "vitest";

import worker, { canonicalJson, parseAuthorityConfig, unknownProduct, validateAuthorityShape } from "../src/index";

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
});
