import { describe, expect, it } from "vitest";

import {
  ALGORITHM,
  buildAad,
  type ConnectPublicKeyJwk,
  importPrivateKeyFromPem,
  openSealed,
  type SealedEnvelope,
  seal,
} from "../seal";
import fs from "node:fs";
import path from "node:path";

interface VectorFile {
  comment: string;
  private_key_pem: string;
  public_jwk: ConnectPublicKeyJwk;
  vectors: Array<{
    description: string;
    tenant_id: string;
    subject: string;
    purpose: string;
    aad: string;
    plaintext: string;
    envelope: SealedEnvelope;
  }>;
}

const vectorsPath = path.resolve(__dirname, "../../../../../tests/connect/fixtures/sealed_vectors.json");
const testData: VectorFile = JSON.parse(fs.readFileSync(vectorsPath, "utf-8"));

describe("Connect sealed envelope encryption (seal.ts)", () => {
  it("imports the TEST private key with WebCrypto and opens every Python-made vector", async () => {
    const privKey = await importPrivateKeyFromPem(testData.private_key_pem);
    expect(privKey).toBeDefined();

    for (const v of testData.vectors) {
      const opened = await openSealed(v.envelope, privKey, v.aad);
      expect(opened, `Failed vector: ${v.description}`).toBe(v.plaintext);
    }
  });

  it("performs seal -> open round trip", async () => {
    const privKey = await importPrivateKeyFromPem(testData.private_key_pem);
    const pubJwk = testData.public_jwk;
    const testPlaintext = "secret-oauth-token-12345!@#$%^&*()_+~`";
    const aad = buildAad("session-xyz-42", "login_input");

    const envelope = await seal(testPlaintext, pubJwk, aad);
    expect(envelope.v).toBe(1);
    expect(envelope.alg).toBe(ALGORITHM);
    expect(envelope.epk.kty).toBe("EC");
    expect(envelope.epk.crv).toBe("P-256");

    const opened = await openSealed(envelope, privKey, aad);
    expect(opened).toBe(testPlaintext);
  });

  it("fails when AAD is wrong", async () => {
    const privKey = await importPrivateKeyFromPem(testData.private_key_pem);
    const pubJwk = testData.public_jwk;
    const testPlaintext = "super-secret-key-999";
    const aad = buildAad("OPENAI_KEY_01", "secret");
    const wrongAad = buildAad("OPENAI_KEY_02", "secret");

    const envelope = await seal(testPlaintext, pubJwk, aad);

    await expect(openSealed(envelope, privKey, wrongAad)).rejects.toThrow();
  });

  it("fails when ciphertext (ct) is tampered with", async () => {
    const privKey = await importPrivateKeyFromPem(testData.private_key_pem);
    const pubJwk = testData.public_jwk;
    const testPlaintext = "tamper-proof-content";
    const aad = buildAad("session-tamper", "login_input");

    const envelope = await seal(testPlaintext, pubJwk, aad);

    // Tamper with ciphertext by modifying one character
    const tamperedCt = envelope.ct.endsWith("A") ? `${envelope.ct.slice(0, -1)}B` : `${envelope.ct.slice(0, -1)}A`;

    const tamperedEnvelope = { ...envelope, ct: tamperedCt };

    await expect(openSealed(tamperedEnvelope, privKey, aad)).rejects.toThrow();
  });

  it("envelope shape matches the contract exactly", async () => {
    const pubJwk = testData.public_jwk;
    const aad = buildAad("session-1", "login_input");
    const envelope = await seal("test", pubJwk, aad);

    expect(envelope).toHaveProperty("v", 1);
    expect(envelope).toHaveProperty("alg", "ECDH-ES+HKDF-SHA256+A256GCM");
    expect(envelope).toHaveProperty("epk");
    expect(envelope.epk).toHaveProperty("kty", "EC");
    expect(envelope.epk).toHaveProperty("crv", "P-256");
    expect(typeof envelope.epk.x).toBe("string");
    expect(typeof envelope.epk.y).toBe("string");
    expect(typeof envelope.iv).toBe("string");
    expect(typeof envelope.ct).toBe("string");

    // base64url characters only: [A-Za-z0-9_-], no padding =
    const b64urlRegex = /^[A-Za-z0-9_-]+$/;
    expect(envelope.epk.x).toMatch(b64urlRegex);
    expect(envelope.epk.y).toMatch(b64urlRegex);
    expect(envelope.iv).toMatch(b64urlRegex);
    expect(envelope.ct).toMatch(b64urlRegex);
  });
});
