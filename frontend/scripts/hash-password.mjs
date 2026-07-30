import { randomBytes, scryptSync } from "node:crypto";

const password = process.argv[2];
if (!password) {
  console.error("Usage: npm run hash-password -- <password>");
  process.exit(1);
}

const N = 16384;
const r = 8;
const p = 1;
const salt = randomBytes(16).toString("base64url");
const derived = scryptSync(password, salt, 64, {
  N,
  r,
  p,
  maxmem: 64 * 1024 * 1024,
}).toString("base64url");

console.log(`scrypt:${N}:${r}:${p}:${salt}:${derived}`);
