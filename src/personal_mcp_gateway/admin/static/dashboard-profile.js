(() => {
  "use strict";

  // This client owns only presentation preferences. It intentionally has no
  // endpoint, token, cookie, or diagnostics input; a future authority can use
  // prepareExchange/applyExchange without widening this data boundary.
  const DB_NAME = "poyi-dashboard-profile-v1";
  const DB_VERSION = 1;
  const PRODUCT = "personal-mcp-gateway";
  const ENTITY_TYPE = "dashboard_profile";
  const ENTITY_ID = "profile";
  const KEY_ID = "root-v1";
  const META_ID = "profile-meta";
  const encoder = new TextEncoder();
  const decoder = new TextDecoder();
  const MAX_PROFILE_BYTES = 32_768;
  const SIZE_VALUES = new Set(["compact", "standard", "expanded"]);
  const DENSITY_VALUES = new Set(["full", "compact", "minimal"]);
  const THEME_VALUES = new Set(["light", "dark"]);

  function isRecord(value) {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
  }

  function stableJson(value) {
    if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
    if (!isRecord(value)) return JSON.stringify(value);
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(",")}}`;
  }

  function base64url(value) {
    const bytes = value instanceof Uint8Array ? value : new Uint8Array(value);
    let binary = "";
    for (const byte of bytes) binary += String.fromCharCode(byte);
    return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
  }

  function fromBase64url(value) {
    if (typeof value !== "string" || !/^[A-Za-z0-9_-]+$/.test(value)) throw new TypeError("invalid base64url");
    const padded = value.replaceAll("-", "+").replaceAll("_", "/") + "=".repeat((4 - value.length % 4) % 4);
    const binary = atob(padded);
    return Uint8Array.from(binary, (char) => char.charCodeAt(0));
  }

  function hex(value) {
    return Array.from(new Uint8Array(value), (byte) => byte.toString(16).padStart(2, "0")).join("");
  }

  async function sha256(value) {
    return hex(await crypto.subtle.digest("SHA-256", encoder.encode(value)));
  }

  function cleanIds(value, limit) {
    if (!Array.isArray(value)) return [];
    const seen = new Set();
    for (const item of value) {
      if (typeof item !== "string" || !/^[a-z][a-z0-9_]*$/.test(item)) continue;
      if (seen.size >= limit) break;
      seen.add(item);
    }
    return [...seen];
  }

  function normalizeProfile(value) {
    const source = isRecord(value) ? value : {};
    const tileSizes = {};
    if (isRecord(source.tileSizes)) {
      for (const [id, size] of Object.entries(source.tileSizes)) {
        if (/^[a-z][a-z0-9_]*$/.test(id) && SIZE_VALUES.has(size)) tileSizes[id] = size;
      }
    }
    return {
      version: 1,
      theme: THEME_VALUES.has(source.theme) ? source.theme : "light",
      density: DENSITY_VALUES.has(source.density) ? source.density : "full",
      layout: cleanIds(source.layout, 64),
      pinnedProjectIds: cleanIds(source.pinnedProjectIds, 64),
      tileSizes,
    };
  }

  function requestValue(request) {
    return new Promise((resolve, reject) => {
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error("IndexedDB request failed"));
    });
  }

  function transactionDone(transaction) {
    return new Promise((resolve, reject) => {
      transaction.oncomplete = () => resolve();
      transaction.onabort = () => reject(transaction.error || new Error("IndexedDB transaction aborted"));
      transaction.onerror = () => reject(transaction.error || new Error("IndexedDB transaction failed"));
    });
  }

  function openDatabase() {
    return new Promise((resolve, reject) => {
      if (!globalThis.indexedDB || !globalThis.crypto || !crypto.subtle) {
        reject(new Error("Encrypted dashboard profile requires IndexedDB and WebCrypto"));
        return;
      }
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onerror = () => reject(request.error || new Error("Unable to open dashboard profile"));
      request.onupgradeneeded = () => {
        const database = request.result;
        if (!database.objectStoreNames.contains("keys")) database.createObjectStore("keys", { keyPath: "id" });
        if (!database.objectStoreNames.contains("meta")) database.createObjectStore("meta", { keyPath: "id" });
        if (!database.objectStoreNames.contains("entities")) database.createObjectStore("entities", { keyPath: "entityId" });
        if (!database.objectStoreNames.contains("outbox")) {
          const outbox = database.createObjectStore("outbox", { keyPath: "opId" });
          outbox.createIndex("entityId", "entityId", { unique: false });
        }
        if (!database.objectStoreNames.contains("conflicts")) database.createObjectStore("conflicts", { keyPath: "conflictId" });
      };
      request.onsuccess = () => resolve(request.result);
    });
  }

  function validCursor(value) {
    return value === null || (typeof value === "string" && /^c[0-9a-z]+$/.test(value));
  }

  function mutationAad(revision, operation) {
    return {
      envelopeVersion: 1,
      entityId: ENTITY_ID,
      entityType: ENTITY_TYPE,
      keyVersion: 1,
      operation,
      product: PRODUCT,
      revision,
    };
  }

  class DashboardProfileStore {
    constructor() {
      this.databasePromise = null;
      this.bootstrapPromise = null;
      this.writeQueue = Promise.resolve();
    }

    async database() {
      if (!this.databasePromise) this.databasePromise = openDatabase();
      return this.databasePromise;
    }

    async bootstrap() {
      if (this.bootstrapPromise) return this.bootstrapPromise;
      this.bootstrapPromise = (async () => {
        const database = await this.database();
        const readTransaction = database.transaction(["keys", "meta"], "readonly");
        const existingKey = await requestValue(readTransaction.objectStore("keys").get(KEY_ID));
        const existingMetadata = await requestValue(readTransaction.objectStore("meta").get(META_ID));
        await transactionDone(readTransaction);
        if (existingKey && existingMetadata) return;

        const key = existingKey ? null : await crypto.subtle.generateKey(
          { name: "AES-GCM", length: 256 },
          false,
          ["encrypt", "decrypt"],
        );
        const transaction = database.transaction(["keys", "meta"], "readwrite");
        if (!existingKey) transaction.objectStore("keys").put({ id: KEY_ID, keyVersion: 1, key });
        if (!existingMetadata) {
          transaction.objectStore("meta").put({
            id: META_ID,
            deviceId: `dashboard-${crypto.randomUUID()}`,
            cursor: null,
            flightOpId: null,
          });
        }
        await transactionDone(transaction);
      })();
      try {
        await this.bootstrapPromise;
      } catch (error) {
        this.bootstrapPromise = null;
        throw error;
      }
    }

    async identity() {
      const database = await this.database();
      await this.bootstrap();
      const transaction = database.transaction(["keys", "meta"], "readonly");
      const keyRecord = await requestValue(transaction.objectStore("keys").get(KEY_ID));
      const metadata = await requestValue(transaction.objectStore("meta").get(META_ID));
      await transactionDone(transaction);
      if (!keyRecord || !metadata || !keyRecord.key) throw new Error("Dashboard profile initialization failed");
      return { database, key: keyRecord.key, metadata };
    }

    async encrypt(profile, key, revision) {
      const plaintext = encoder.encode(stableJson(normalizeProfile(profile)));
      if (plaintext.byteLength > MAX_PROFILE_BYTES) throw new RangeError("Dashboard profile is too large");
      const nonce = crypto.getRandomValues(new Uint8Array(12));
      const aad = stableJson(mutationAad(revision, "upsert"));
      const ciphertext = await crypto.subtle.encrypt(
        { name: "AES-GCM", iv: nonce, additionalData: encoder.encode(aad), tagLength: 128 },
        key,
        plaintext,
      );
      return {
        ciphertext: base64url(ciphertext),
        nonce: base64url(nonce),
        aadHash: await sha256(aad),
      };
    }

    async decrypt(entity, key) {
      if (!entity || entity.operation !== "upsert" || entity.entityType !== ENTITY_TYPE || entity.entityId !== ENTITY_ID) {
        return null;
      }
      if (!Number.isSafeInteger(entity.revision) || entity.revision < 1 || entity.keyVersion !== 1) return null;
      const aad = stableJson(mutationAad(entity.revision, "upsert"));
      if (entity.aadHash !== await sha256(aad)) return null;
      try {
        const plaintext = await crypto.subtle.decrypt(
          { name: "AES-GCM", iv: fromBase64url(entity.nonce), additionalData: encoder.encode(aad), tagLength: 128 },
          key,
          fromBase64url(entity.ciphertext),
        );
        if (plaintext.byteLength > MAX_PROFILE_BYTES) return null;
        return normalizeProfile(JSON.parse(decoder.decode(plaintext)));
      } catch (_) {
        return null;
      }
    }

    async load() {
      const { database, key } = await this.identity();
      const transaction = database.transaction("entities", "readonly");
      const entity = await requestValue(transaction.objectStore("entities").get(ENTITY_ID));
      await transactionDone(transaction);
      return await this.decrypt(entity, key) || normalizeProfile(null);
    }

    enqueue(task) {
      const next = this.writeQueue.then(task, task);
      this.writeQueue = next.catch(() => undefined);
      return next;
    }

    async update(profile) {
      return this.enqueue(async () => {
        const { database, key, metadata } = await this.identity();
        const readTransaction = database.transaction("entities", "readonly");
        const existing = await requestValue(readTransaction.objectStore("entities").get(ENTITY_ID));
        await transactionDone(readTransaction);
        const normalized = normalizeProfile(profile);
        const current = await this.decrypt(existing, key);
        if (current && stableJson(current) === stableJson(normalized)) return { queued: false, revision: existing.revision };

        const revision = Number(existing?.revision || 0) + 1;
        const encrypted = await this.encrypt(normalized, key, revision);
        const opId = crypto.randomUUID();
        const mutation = {
          opId,
          entityType: ENTITY_TYPE,
          entityId: ENTITY_ID,
          baseRevision: revision - 1,
          operation: "upsert",
          keyVersion: 1,
          ciphertext: encrypted.ciphertext,
          nonce: encrypted.nonce,
          aadHash: encrypted.aadHash,
          objects: [],
        };

        // Entity, replacement outbox mutation, cursor, and flight marker share
        // one durable boundary. A crash leaves either the old profile or a fully
        // retryable mutation, never an acknowledged-looking partial update.
        const transaction = database.transaction(["entities", "outbox", "meta"], "readwrite");
        const outbox = transaction.objectStore("outbox");
        const prior = await requestValue(outbox.index("entityId").getAll(ENTITY_ID));
        for (const item of prior) outbox.delete(item.opId);
        transaction.objectStore("entities").put({
          entityType: ENTITY_TYPE,
          entityId: ENTITY_ID,
          revision,
          operation: "upsert",
          keyVersion: 1,
          ...encrypted,
          updatedAt: new Date().toISOString(),
        });
        outbox.put(mutation);
        transaction.objectStore("meta").put({ ...metadata, flightOpId: opId });
        await transactionDone(transaction);
        return { queued: true, revision, opId };
      });
    }

    async prepareExchange() {
      const { database, metadata } = await this.identity();
      const transaction = database.transaction("outbox", "readonly");
      const mutations = (await requestValue(transaction.objectStore("outbox").getAll()))
        .filter((mutation) => mutation.entityType === ENTITY_TYPE && mutation.entityId === ENTITY_ID)
        .sort((left, right) => left.baseRevision - right.baseRevision || left.opId.localeCompare(right.opId))
        .slice(0, 25);
      await transactionDone(transaction);
      return {
        protocolVersion: 2,
        envelopeVersion: 1,
        product: PRODUCT,
        deviceId: metadata.deviceId,
        cursor: validCursor(metadata.cursor) ? metadata.cursor : null,
        mutations,
      };
    }

    validateResponse(response) {
      if (!isRecord(response) || response.protocolVersion !== 2 || response.envelopeVersion !== 1 || response.product !== PRODUCT) {
        throw new TypeError("Invalid dashboard profile exchange response");
      }
      if (
        !Array.isArray(response.acknowledged) ||
        !Array.isArray(response.conflicts) ||
        !Array.isArray(response.changes) ||
        typeof response.nextCursor !== "string" ||
        !/^c[0-9a-z]+$/.test(response.nextCursor) ||
        typeof response.hasMore !== "boolean"
      ) {
        throw new TypeError("Invalid dashboard profile exchange response");
      }
      return response;
    }

    async applyExchange(response) {
      return this.enqueue(async () => {
        const verified = this.validateResponse(response);
        const { database, key, metadata } = await this.identity();
        const materialized = [];
        for (const change of verified.changes) {
          if (!isRecord(change) || change.entityType !== ENTITY_TYPE || change.entityId !== ENTITY_ID) continue;
          if (change.operation === "delete") {
            if (!Number.isSafeInteger(change.revision) || change.revision < 1) throw new TypeError("Invalid tombstone");
            materialized.push({ ...change, ciphertext: null, nonce: null, objects: [] });
            continue;
          }
          const profile = await this.decrypt(change, key);
          if (!profile) throw new TypeError("Invalid encrypted dashboard profile change");
          materialized.push({ ...change, profile });
        }

        const transaction = database.transaction(["entities", "outbox", "conflicts", "meta"], "readwrite");
        const entities = transaction.objectStore("entities");
        const outbox = transaction.objectStore("outbox");
        for (const change of materialized) {
          const current = await requestValue(entities.get(ENTITY_ID));
          if (!current || Number(change.revision) > Number(current.revision || 0)) {
            entities.put({
              entityType: ENTITY_TYPE,
              entityId: ENTITY_ID,
              revision: Number(change.revision),
              operation: change.operation,
              keyVersion: change.keyVersion,
              ciphertext: change.ciphertext,
              nonce: change.nonce,
              aadHash: change.aadHash,
              objects: [],
              updatedAt: change.changedAt || new Date().toISOString(),
              deletedAt: change.operation === "delete" ? change.changedAt || new Date().toISOString() : null,
            });
          }
        }
        for (const acknowledgement of verified.acknowledged) {
          if (!isRecord(acknowledgement) || acknowledgement.outcome !== "acknowledged") continue;
          const mutation = await requestValue(outbox.get(acknowledgement.opId));
          const entity = await requestValue(entities.get(ENTITY_ID));
          if (
            mutation &&
            mutation.entityType === ENTITY_TYPE &&
            mutation.entityId === ENTITY_ID &&
            acknowledgement.revision === mutation.baseRevision + 1 &&
            entity && Number(entity.revision) >= acknowledgement.revision
          ) outbox.delete(acknowledgement.opId);
        }
        for (const conflict of verified.conflicts) {
          if (!isRecord(conflict) || conflict.entityType !== ENTITY_TYPE || conflict.entityId !== ENTITY_ID || typeof conflict.opId !== "string") continue;
          transaction.objectStore("conflicts").put({ conflictId: conflict.opId, receivedAt: new Date().toISOString(), conflict });
        }
        transaction.objectStore("meta").put({ ...metadata, cursor: verified.nextCursor, flightOpId: null });
        await transactionDone(transaction);
        return await this.load();
      });
    }

    async diagnostics() {
      const { database, metadata } = await this.identity();
      const transaction = database.transaction("outbox", "readonly");
      const pending = await requestValue(transaction.objectStore("outbox").count());
      await transactionDone(transaction);
      return { deviceId: metadata.deviceId, cursor: metadata.cursor, pendingCount: Number(pending) };
    }
  }

  const store = new DashboardProfileStore();
  window.PoyiDashboardProfile = Object.freeze({
    load: () => store.load(),
    update: (profile) => store.update(profile),
    prepareExchange: () => store.prepareExchange(),
    applyExchange: (response) => store.applyExchange(response),
    diagnostics: () => store.diagnostics(),
  });
})();
