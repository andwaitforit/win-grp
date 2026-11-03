// Access-code gate for the dashboard on custom domains.
//
// *.vercel.app hosts are already behind Vercel Authentication, so they pass
// straight through. On any other host (the client-facing custom domain),
// visitors enter an access code once and get a signed cookie.
//
// Env (set in Vercel, never in the repo):
//   ACCESS_CODES          comma-separated label:code pairs, e.g.
//                         "client-a:7QX4-M2PD-91KF,client-b:..."
//                         Removing a pair revokes that client's cookies.
//   ACCESS_COOKIE_SECRET  random string used to sign cookies; rotating it
//                         signs everyone out.
//
// This protects the page, not the data: the dashboard_* views it reads are
// public FCC data behind the publishable key. Org-private data needs real
// per-user auth (Supabase Auth), not this.
import { next } from "@vercel/functions";

export const config = { matcher: "/:path*" };

const COOKIE = "wg_access";
const MAX_AGE_S = 30 * 24 * 3600;
const enc = new TextEncoder();

function parseCodes(raw) {
  const codes = new Map();
  for (const pair of (raw || "").split(",")) {
    const i = pair.indexOf(":");
    if (i > 0 && pair.slice(i + 1).trim()) codes.set(pair.slice(0, i).trim(), pair.slice(i + 1).trim());
  }
  return codes;
}

async function hmac(secret, msg) {
  const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = new Uint8Array(await crypto.subtle.sign("HMAC", key, enc.encode(msg)));
  return btoa(String.fromCharCode(...sig)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// Compare digests rather than raw strings so timing doesn't leak the value.
async function same(a, b) {
  const [x, y] = await Promise.all([a, b].map((s) => crypto.subtle.digest("SHA-256", enc.encode(s))));
  const u = new Uint8Array(x), v = new Uint8Array(y);
  let d = 0;
  for (let i = 0; i < u.length; i++) d |= u[i] ^ v[i];
  return d === 0;
}

function readCookie(request) {
  const m = (request.headers.get("cookie") || "").match(new RegExp("(?:^|;\\s*)" + COOKIE + "=([^;]+)"));
  return m ? decodeURIComponent(m[1]) : null;
}

async function validCookie(value, codes, secret) {
  const [label, exp, sig] = (value || "").split(".");
  if (!label || !exp || !sig || !codes.has(label) || Number(exp) * 1000 < Date.now()) return false;
  return same(sig, await hmac(secret, label + "." + exp));
}

function page(status, message) {
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>win-grp · Access</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--fg:#14171c;--muted:#5d6673;--line:#d9dde3;--accent:#2457c5;--bad:#b42318}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a20;--fg:#e8eaee;--muted:#9aa3af;--line:#2a2f38;--accent:#7aa2ff;--bad:#ff8a80}}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--fg);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;padding:16px}
form{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:28px;width:100%;max-width:360px}
h1{font-size:18px;margin:0 0 4px}p{margin:0 0 18px;color:var(--muted)}
label{display:block;font-weight:600;margin-bottom:6px}
input{width:100%;padding:10px 12px;font:inherit;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg)}
button{margin-top:14px;width:100%;padding:10px;font:inherit;font-weight:600;border:0;border-radius:8px;background:var(--accent);color:#fff;cursor:pointer}
.err{color:var(--bad);margin:10px 0 0}
</style></head><body>
<form method="post" action="/__access">
<h1>win-grp dashboard</h1><p>Enter the access code you were given.</p>
<label for="code">Access code</label>
<input id="code" name="code" autocomplete="off" autocapitalize="characters" spellcheck="false" required autofocus>
${message ? `<p class="err" role="alert">${message}</p>` : ""}
<button type="submit">Continue</button>
</form></body></html>`;
  return new Response(html, {
    status,
    headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store", "x-robots-tag": "noindex" },
  });
}

export default async function middleware(request) {
  const url = new URL(request.url);
  if (url.hostname.endsWith(".vercel.app") || url.hostname === "localhost") return next();

  const codes = parseCodes(process.env.ACCESS_CODES);
  const secret = process.env.ACCESS_COOKIE_SECRET;
  if (!codes.size || !secret || secret.length < 32) {
    // Fail closed: never serve the dashboard ungated on a custom domain.
    return new Response("Access gate is not configured.", { status: 503, headers: { "cache-control": "no-store" } });
  }

  if (url.pathname === "/__access/logout") {
    return new Response(null, {
      status: 303,
      headers: { location: "/", "set-cookie": `${COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax` },
    });
  }

  if (url.pathname === "/__access" && request.method === "POST") {
    const form = await request.formData().catch(() => null);
    const entered = String((form && form.get("code")) || "").trim();
    let label = null;
    for (const [l, c] of codes) if (await same(entered.toUpperCase(), c.toUpperCase())) label = l;
    if (!label) {
      await new Promise((r) => setTimeout(r, 800)); // slow down guessing
      return page(401, "That code didn't work. Check it and try again.");
    }
    const exp = Math.floor(Date.now() / 1000) + MAX_AGE_S;
    const value = `${label}.${exp}.${await hmac(secret, label + "." + exp)}`;
    return new Response(null, {
      status: 303,
      headers: {
        location: "/",
        "cache-control": "no-store",
        "set-cookie": `${COOKIE}=${encodeURIComponent(value)}; Path=/; Max-Age=${MAX_AGE_S}; HttpOnly; Secure; SameSite=Lax`,
      },
    });
  }

  if (await validCookie(readCookie(request), codes, secret)) return next();
  return page(401, "");
}
