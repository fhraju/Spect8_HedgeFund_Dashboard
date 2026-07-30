import {
  createHmac,
  scryptSync,
  timingSafeEqual,
} from "node:crypto";

export const SESSION_COOKIE = "spect8_session";
export const SESSION_TTL_SECONDS = 8 * 60 * 60;

type SessionPayload = {
  sub: "single-client";
  exp: number;
};

function safeEqual(left: Buffer, right: Buffer): boolean {
  return left.length === right.length && timingSafeEqual(left, right);
}

export function createPasswordHash(
  password: string,
  salt: string,
  N = 16384,
  r = 8,
  p = 1,
): string {
  const derived = scryptSync(password, salt, 64, {
    N,
    r,
    p,
    maxmem: 64 * 1024 * 1024,
  });
  return `scrypt:${N}:${r}:${p}:${salt}:${derived.toString("base64url")}`;
}

export function verifyPassword(password: string, encodedHash: string): boolean {
  const [algorithm, rawN, rawR, rawP, salt, encodedExpected] =
    encodedHash.split(":");
  if (
    algorithm !== "scrypt" ||
    !rawN ||
    !rawR ||
    !rawP ||
    !salt ||
    !encodedExpected
  ) {
    return false;
  }
  const N = Number(rawN);
  const r = Number(rawR);
  const p = Number(rawP);
  if (
    !Number.isSafeInteger(N) ||
    !Number.isSafeInteger(r) ||
    !Number.isSafeInteger(p) ||
    N < 16384 ||
    r < 8 ||
    p < 1
  ) {
    return false;
  }
  try {
    const expected = Buffer.from(encodedExpected, "base64url");
    const actual = scryptSync(password, salt, expected.length, {
      N,
      r,
      p,
      maxmem: 64 * 1024 * 1024,
    });
    return safeEqual(actual, expected);
  } catch {
    return false;
  }
}

function signature(payload: string, secret: string): string {
  return createHmac("sha256", secret).update(payload).digest("base64url");
}

export function createSessionToken(
  secret: string,
  nowSeconds = Math.floor(Date.now() / 1000),
): string {
  const payload: SessionPayload = {
    sub: "single-client",
    exp: nowSeconds + SESSION_TTL_SECONDS,
  };
  const encoded = Buffer.from(JSON.stringify(payload)).toString("base64url");
  return `${encoded}.${signature(encoded, secret)}`;
}

export function verifySessionToken(
  token: string | undefined,
  secret: string,
  nowSeconds = Math.floor(Date.now() / 1000),
): boolean {
  if (!token || secret.length < 32) {
    return false;
  }
  const [encoded, suppliedSignature, extra] = token.split(".");
  if (!encoded || !suppliedSignature || extra !== undefined) {
    return false;
  }
  const expected = Buffer.from(signature(encoded, secret));
  const supplied = Buffer.from(suppliedSignature);
  if (!safeEqual(expected, supplied)) {
    return false;
  }
  try {
    const payload = JSON.parse(
      Buffer.from(encoded, "base64url").toString("utf8"),
    ) as Partial<SessionPayload>;
    return payload.sub === "single-client" && Number(payload.exp) > nowSeconds;
  } catch {
    return false;
  }
}
