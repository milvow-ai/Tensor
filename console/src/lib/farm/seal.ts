/**
 * Sealed input: ECDH-ES+HKDF-SHA256+A256GCM envelope encryption for sensitive values.
 * WebCrypto implementation byte-for-byte compatible with farm/connect/sealed.py.
 */

export const ALGORITHM = "ECDH-ES+HKDF-SHA256+A256GCM" as const;
export const HKDF_INFO = new TextEncoder().encode("harness-farm/connect/v1");

export interface EpkJwk {
  kty: "EC";
  crv: "P-256";
  x: string;
  y: string;
}

export interface SealedEnvelope {
  v: 1;
  alg: typeof ALGORITHM;
  epk: EpkJwk;
  iv: string;
  ct: string;
}

export interface ConnectPublicKeyJwk {
  kty: "EC";
  crv: "P-256";
  x: string;
  y: string;
  [key: string]: unknown;
}

export function b64urlEncode(bytes: Uint8Array): string {
  let binary = "";
  const len = bytes.byteLength;
  for (let i = 0; i < len; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function b64urlDecode(val: string): Uint8Array {
  let b64 = val.replace(/-/g, "+").replace(/_/g, "/");
  const pad = b64.length % 4;
  if (pad === 2) b64 += "==";
  else if (pad === 3) b64 += "=";
  else if (pad === 1) throw new Error("Invalid base64url string length");
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

export function getTenantId(): string {
  return process.env.NEXT_PUBLIC_FARM_TENANT_ID || "00000000-0000-0000-0000-000000000001";
}

export function buildAad(subject: string, purpose: string, tenantId = getTenantId()): string {
  return `${tenantId}|${subject}|${purpose}`;
}

/**
 * Seals plaintext into a v1 envelope encrypted for the Farm's public key.
 */
export async function seal(plaintext: string, publicJwk: ConnectPublicKeyJwk, aad: string): Promise<SealedEnvelope> {
  const recipientKey = await crypto.subtle.importKey(
    "jwk",
    {
      kty: "EC",
      crv: "P-256",
      x: publicJwk.x,
      y: publicJwk.y,
    },
    { name: "ECDH", namedCurve: "P-256" },
    true,
    [],
  );

  // Generate ephemeral P-256 key pair
  const epk = await crypto.subtle.generateKey({ name: "ECDH", namedCurve: "P-256" }, true, ["deriveBits"]);

  // Export ephemeral public key to JWK
  const epkJwk = (await crypto.subtle.exportKey("jwk", epk.publicKey)) as EpkJwk;

  // ECDH derive 256 bits shared secret
  const sharedSecret = await crypto.subtle.deriveBits({ name: "ECDH", public: recipientKey }, epk.privateKey, 256);

  // HKDF-SHA256 derivation with empty salt and HKDF_INFO
  const hkdfKey = await crypto.subtle.importKey("raw", sharedSecret, "HKDF", false, ["deriveKey"]);
  const aesKey = await crypto.subtle.deriveKey(
    {
      name: "HKDF",
      hash: "SHA-256",
      salt: new Uint8Array(0),
      info: HKDF_INFO,
    },
    hkdfKey,
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt"],
  );

  // AES-256-GCM encryption with 12-byte random IV
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const aadBytes = new TextEncoder().encode(aad);
  const ptBytes = new TextEncoder().encode(plaintext);

  const ctBuffer = await crypto.subtle.encrypt(
    {
      name: "AES-GCM",
      iv,
      additionalData: aadBytes,
    },
    aesKey,
    ptBytes,
  );

  return {
    v: 1,
    alg: ALGORITHM,
    epk: {
      kty: "EC",
      crv: "P-256",
      x: epkJwk.x,
      y: epkJwk.y,
    },
    iv: b64urlEncode(iv),
    ct: b64urlEncode(new Uint8Array(ctBuffer)),
  };
}

/**
 * Decrypts a sealed envelope using the private key and matching AAD.
 * Throws an Error on any decryption or authentication failure.
 */
export async function openSealed(envelope: SealedEnvelope, privateKey: CryptoKey, aad: string): Promise<string> {
  if (envelope.v !== 1 || envelope.alg !== ALGORITHM) {
    throw new Error("Unsupported envelope version or algorithm");
  }

  const epkPub = await crypto.subtle.importKey(
    "jwk",
    {
      kty: "EC",
      crv: "P-256",
      x: envelope.epk.x,
      y: envelope.epk.y,
    },
    { name: "ECDH", namedCurve: "P-256" },
    true,
    [],
  );

  const sharedSecret = await crypto.subtle.deriveBits({ name: "ECDH", public: epkPub }, privateKey, 256);

  const hkdfKey = await crypto.subtle.importKey("raw", sharedSecret, "HKDF", false, ["deriveKey"]);
  const aesKey = await crypto.subtle.deriveKey(
    {
      name: "HKDF",
      hash: "SHA-256",
      salt: new Uint8Array(0),
      info: HKDF_INFO,
    },
    hkdfKey,
    { name: "AES-GCM", length: 256 },
    false,
    ["decrypt"],
  );

  const iv = b64urlDecode(envelope.iv);
  if (iv.byteLength !== 12) {
    throw new Error("IV must be 12 bytes");
  }
  const ct = b64urlDecode(envelope.ct);
  const aadBytes = new TextEncoder().encode(aad);

  const decrypted = await crypto.subtle.decrypt(
    {
      name: "AES-GCM",
      iv: iv as unknown as BufferSource,
      additionalData: aadBytes,
    },
    aesKey,
    ct as unknown as BufferSource,
  );

  return new TextDecoder().decode(decrypted);
}

/**
 * Imports an EC P-256 PKCS#8 private key PEM into a CryptoKey for ECDH.
 */
export async function importPrivateKeyFromPem(pem: string): Promise<CryptoKey> {
  const cleanPem = pem
    .replace(/-----BEGIN PRIVATE KEY-----/g, "")
    .replace(/-----END PRIVATE KEY-----/g, "")
    .replace(/\s+/g, "");
  const der = b64urlDecode(cleanPem); // standard b64 decoded
  return crypto.subtle.importKey("pkcs8", der as unknown as BufferSource, { name: "ECDH", namedCurve: "P-256" }, true, [
    "deriveBits",
  ]);
}
