// Access-code middleware: run with `npm test`.
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import middleware from "../middleware.js";

const HOST = "https://dash.example.com";

beforeEach(() => {
  process.env.ACCESS_CODES = "client-a:ABCD-1234-EFGH, client-b:ZZZZ-9999-YYYY";
  process.env.ACCESS_COOKIE_SECRET = "x".repeat(40);
});

const passes = (res) => res.headers.get("x-middleware-next") === "1";

async function login(code) {
  const body = new URLSearchParams({ code });
  return middleware(new Request(HOST + "/__access", { method: "POST", body }));
}

function cookieFrom(res) {
  return res.headers.get("set-cookie").split(";")[0];
}

test("vercel.app hosts pass through (Vercel Authentication covers them)", async () => {
  assert.ok(passes(await middleware(new Request("https://win-grp.vercel.app/"))));
});

test("custom domain without a cookie gets the code page", async () => {
  const res = await middleware(new Request(HOST + "/"));
  assert.equal(res.status, 401);
  assert.match(await res.text(), /Access code/);
  assert.equal(res.headers.get("cache-control"), "no-store");
});

test("data files are gated too", async () => {
  assert.equal((await middleware(new Request(HOST + "/data/ma-01.js"))).status, 401);
});

test("right code (any case) sets a cookie that lets the visitor through", async () => {
  const res = await login("abcd-1234-efgh");
  assert.equal(res.status, 303);
  const cookie = cookieFrom(res);
  assert.match(res.headers.get("set-cookie"), /HttpOnly; Secure; SameSite=Lax/);
  assert.ok(passes(await middleware(new Request(HOST + "/", { headers: { cookie } }))));
});

test("wrong code is rejected", async () => {
  const res = await login("NOPE-0000-0000");
  assert.equal(res.status, 401);
  assert.match(await res.text(), /didn't work/);
});

test("tampered cookie is rejected", async () => {
  const cookie = cookieFrom(await login("ABCD-1234-EFGH")).replace("client-a", "client-b");
  assert.equal((await middleware(new Request(HOST + "/", { headers: { cookie } }))).status, 401);
});

test("removing a client's code revokes its cookie", async () => {
  const cookie = cookieFrom(await login("ABCD-1234-EFGH"));
  process.env.ACCESS_CODES = "client-b:ZZZZ-9999-YYYY";
  assert.equal((await middleware(new Request(HOST + "/", { headers: { cookie } }))).status, 401);
});

test("fails closed when not configured", async () => {
  delete process.env.ACCESS_CODES;
  assert.equal((await middleware(new Request(HOST + "/"))).status, 503);
});

test("logout clears the cookie", async () => {
  const res = await middleware(new Request(HOST + "/__access/logout"));
  assert.equal(res.status, 303);
  assert.match(res.headers.get("set-cookie"), /Max-Age=0/);
});
