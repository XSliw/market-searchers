// Sendico: почему ВСЁ отдаёт 403 "Access denied", включая POST /api/login.
// Это не Cloudflare (нет cf-mitigated, есть x-powered-by PHP) — прикладной гард.
// Ищем, какой заголовок/маршрут/локаль его открывает, и смотрим фронтенд-бандл:
// SPA откуда-то знает правильный базовый URL и заголовки.
// Запуск: node tools/probe_sendico_gate.mjs
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36";

async function raw(url, init = {}) {
  try {
    const res = await fetch(url, { ...init, redirect: "manual" });
    const ct = (res.headers.get("content-type") || "").split(";")[0];
    let body = "";
    try { body = (await res.text()).slice(0, 400); } catch { }
    return { status: res.status, ct, body, headers: res.headers, ok: res.ok };
  } catch (e) { return { status: 0, ct: "", body: `сеть: ${e.message}`, headers: new Headers(), ok: false }; }
}

// 1. Достаём главную: под Cloudflare или нет, и какие скрипты грузит
console.log("=== 1. главная страница ===");
const home = await raw("https://sendico.com/", { headers: { "User-Agent": UA, "Accept": "text/html" } });
console.log(`  GET / -> ${home.status} ${home.ct}  cf-mitigated=${home.headers.get("cf-mitigated") || "нет"}  server=${home.headers.get("server") || "?"}`);
const scripts = [...home.body.matchAll(/["'](\/(?:_nuxt|build|assets|js)\/[^"']+\.m?js)["']/g)].map(m => m[1]);
console.log(`  скриптов в HTML: ${scripts.length}${scripts.length ? " → " + scripts.slice(0, 3).join(", ") : ""}`);

// Полный HTML (не обрезанный) для поиска ссылок на аукционы
const homeFull = await fetch("https://sendico.com/", { headers: { "User-Agent": UA, "Accept": "text/html" } })
  .then(r => r.ok ? r.text() : "").catch(() => "");
if (homeFull) {
  console.log(`  HTML длиной ${homeFull.length}`);
  const hrefs = [...new Set([...homeFull.matchAll(/href="(\/[a-z]{2}\/[a-z-]*(?:auction|yahoo|mercari|rakuten)[a-z-]*[^"]*)"/gi)].map(m => m[1]))];
  console.log(`  ссылки на площадки: ${hrefs.slice(0, 12).join(", ") || "(нет)"}`);
  const apiHints = [...new Set([...homeFull.matchAll(/https?:\/\/[a-z0-9.-]*sendico[a-z0-9.-]*/gi)].map(m => m[0]))];
  console.log(`  домены sendico в HTML: ${apiHints.slice(0, 8).join(", ") || "(нет)"}`);
  const nuxtScripts = [...new Set([...homeFull.matchAll(/src="(\/_nuxt\/[^"]+)"/g)].map(m => m[1]))];
  console.log(`  _nuxt скриптов: ${nuxtScripts.length}`);
  globalThis.__nuxt = nuxtScripts;
}

// 2. Матрица заголовков на одном маршруте
console.log("\n=== 2. матрица заголовков на /api/mercari/items ===");
const jar = new Map();
function putCookies(h) {
  for (const rawc of h.getSetCookie?.() || []) {
    const [pair] = rawc.split(";"); const i = pair.indexOf("=");
    if (i > 0) jar.set(pair.slice(0, i).trim(), pair.slice(i + 1).trim());
  }
}
const s = await raw("https://sendico.com/api/auth/login", { headers: { "User-Agent": UA, "Accept": "application/json" } });
putCookies(s.headers);
console.log(`  сессия: GET /api/auth/login -> ${s.status}, cookies=${[...jar.keys()].join(",") || "нет"}`);
const cookie = () => [...jar].map(([k, v]) => `${k}=${v}`).join("; ");
const xsrf = () => decodeURIComponent(jar.get("XSRF-TOKEN") || "");

const URLQ = "https://sendico.com/api/mercari/items?keyword=switch";
const MATRIX = [
  ["только UA", { "User-Agent": UA }],
  ["UA+Accept json", { "User-Agent": UA, "Accept": "application/json" }],
  ["+cookie+xsrf", { "User-Agent": UA, "Accept": "application/json", "Cookie": cookie(), "X-XSRF-TOKEN": xsrf() }],
  ["+Origin/Referer", { "User-Agent": UA, "Accept": "application/json", "Cookie": cookie(), "X-XSRF-TOKEN": xsrf(), "Origin": "https://sendico.com", "Referer": "https://sendico.com/en/mercari" }],
  ["+X-Requested-With", { "User-Agent": UA, "Accept": "application/json", "Cookie": cookie(), "X-XSRF-TOKEN": xsrf(), "X-Requested-With": "XMLHttpRequest" }],
  ["+Accept-Language en", { "User-Agent": UA, "Accept": "application/json", "Accept-Language": "en-US,en;q=0.9", "Cookie": cookie(), "X-XSRF-TOKEN": xsrf() }],
  ["+X-Locale en", { "User-Agent": UA, "Accept": "application/json", "X-Locale": "en", "Cookie": cookie() }],
  ["+Sec-Fetch", { "User-Agent": UA, "Accept": "application/json", "Cookie": cookie(), "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty" }],
];
for (const [label, h] of MATRIX) {
  const r = await raw(URLQ, { headers: h });
  console.log(`  ${label.padEnd(22)} -> ${r.status}  ${r.body.replace(/\s+/g, " ").slice(0, 90)}`);
  await new Promise(x => setTimeout(x, 250));
}

// 3. Варианты маршрутов: локаль в пути, версии, поиск аукционов
console.log("\n=== 3. варианты маршрутов ===");
const ROUTES = [
  "/api/en/mercari/items?keyword=switch",
  "/api/v1/mercari/items?keyword=switch",
  "/api/v2/mercari/items?keyword=switch",
  "/api/mercari/search?keyword=switch",
  "/api/mercari/items/search?keyword=switch",
  "/api/yahoo/items?keyword=switch",          // аукционы: маршрут существует (403, не 404)
  "/api/yahoo/item/123456",
  "/api/yahoo/categories",
  "/api/yahoo/brands",
  "/api/categories",
  "/api/config",
  "/api/settings",
  "/api/exchange-rate",
  "/api/exchange-rates",
  "/api/currency",
  "/api/shops",
  "/api/sources",
];
for (const p of ROUTES) {
  const r = await raw("https://sendico.com" + p, {
    headers: { "User-Agent": UA, "Accept": "application/json", "Cookie": cookie(), "X-XSRF-TOKEN": xsrf(), "Origin": "https://sendico.com", "Referer": "https://sendico.com/" },
  });
  const kind = r.status === 404 || /could not be found/.test(r.body) ? "нет маршрута"
    : r.status === 200 ? "ОТКРЫТО" : String(r.status);
  console.log(`  ${p.split("?")[0].padEnd(34)} -> ${kind}${r.status === 200 ? "  " + r.body.replace(/\s+/g, " ").slice(0, 100) : ""}`);
  await new Promise(x => setTimeout(x, 220));
}

// 4. Форма ошибки на /api/login: 403 до валидации или после?
console.log("\n=== 4. что именно отвергает /api/login ===");
for (const [label, body] of [
  ["пустое тело", {}],
  ["мусорный email", { email: "not-an-email", password: "x" }],
]) {
  const r = await raw("https://sendico.com/api/login", {
    method: "POST",
    headers: {
      "User-Agent": UA, "Accept": "application/json", "Content-Type": "application/json",
      "Cookie": cookie(), "X-XSRF-TOKEN": xsrf(), "Origin": "https://sendico.com", "Referer": "https://sendico.com/en/login",
    },
    body: JSON.stringify(body),
  });
  console.log(`  ${label.padEnd(16)} -> ${r.status}  ${r.body.replace(/\s+/g, " ").slice(0, 160)}`);
  await new Promise(x => setTimeout(x, 250));
}
// Пустое тело даёт 422 «email обязателен» → гард пропускает, проблема в кредах.
// Пустое тело даёт 403 → гард срабатывает до валидации, дело не в кредах.

// 5. Фронтенд-бандл: как SPA обращается к API (базовый URL, заголовки, маршрут аукционов)
console.log("\n=== 5. фронтенд-бандл ===");
const nuxt = globalThis.__nuxt || [];
if (!nuxt.length) console.log("  скриптов не нашли (главная могла отдать челлендж)");
let scanned = 0;
for (const src of nuxt.slice(0, 12)) {
  const r = await raw("https://sendico.com" + src, { headers: { "User-Agent": UA, "Referer": "https://sendico.com/" } });
  if (r.status !== 200) { console.log(`  ${src} -> ${r.status}`); continue; }
  const full = await fetch("https://sendico.com" + src, { headers: { "User-Agent": UA, "Referer": "https://sendico.com/" } }).then(x => x.text()).catch(() => "");
  scanned++;
  const apiPaths = [...new Set([...full.matchAll(/["'`](\/api\/[a-z0-9\-_/{}$.]+)["'`]/gi)].map(m => m[1]))];
  const auction = apiPaths.filter(p => /yahoo|auction|bid/i.test(p));
  if (apiPaths.length) {
    console.log(`  ${src}  (${full.length} б) маршрутов: ${apiPaths.length}`);
    if (auction.length) console.log(`     АУКЦИОННЫЕ: ${auction.join(", ")}`);
    else console.log(`     примеры: ${apiPaths.slice(0, 8).join(", ")}`);
  }
  await new Promise(x => setTimeout(x, 200));
}
console.log(`  просмотрено бандлов: ${scanned}`);
