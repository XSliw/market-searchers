// Разведка Sendico: вход и поиск конечных точек, ОСОБЕННО аукционных лотов
// (Yahoo Auctions — ставки и время окончания, в отличие от Mercari fixed-price).
// Креды берутся ТОЛЬКО из окружения, в файл/репозиторий не попадают:
//   SENDICO_EMAIL=... SENDICO_PASSWORD=... node tools/probe_sendico.mjs
const BASE = "https://sendico.com";
const EMAIL = process.env.SENDICO_EMAIL || "";
const PASSWORD = process.env.SENDICO_PASSWORD || "";
if (!EMAIL || !PASSWORD) { console.log("нет SENDICO_EMAIL/SENDICO_PASSWORD в окружении"); process.exit(1); }

const jar = new Map();                       // имя -> значение cookie
function putCookies(res) {
  for (const raw of res.headers.getSetCookie?.() || []) {
    const [pair] = raw.split(";");
    const i = pair.indexOf("=");
    if (i > 0) jar.set(pair.slice(0, i).trim(), pair.slice(i + 1).trim());
  }
}
const cookieHeader = () => [...jar].map(([k, v]) => `${k}=${v}`).join("; ");
const xsrf = () => decodeURIComponent(jar.get("XSRF-TOKEN") || "");

const H = () => ({
  "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
  "Accept": "application/json",
  "Cookie": cookieHeader(),
  "X-XSRF-TOKEN": xsrf(),
  "Origin": BASE,
  "Referer": BASE + "/",
  "X-Requested-With": "XMLHttpRequest",
});

async function call(path, init = {}) {
  const res = await fetch(BASE + path, { ...init, headers: { ...H(), ...(init.headers || {}) }, redirect: "manual" });
  putCookies(res);
  const ct = res.headers.get("content-type") || "";
  let body = null;
  try { body = ct.includes("json") ? await res.json() : (await res.text()).slice(0, 200); }
  catch { body = "(не разобрано)"; }
  return { status: res.status, ct, body };
}

// 1. Поднять сессию и CSRF
console.log("=== 1. сессия ===");
for (const p of ["/api/auth/login", "/sanctum/csrf-cookie", "/api/csrf-cookie"]) {
  const r = await call(p);
  console.log(`  GET ${p} -> ${r.status} ${r.ct.split(";")[0]}`);
  if (jar.has("XSRF-TOKEN")) break;
}
console.log("  cookies:", [...jar.keys()].join(", ") || "(нет)");

// 2. Войти. Пробуем несколько форм полей — точная неизвестна до первого успеха.
console.log("\n=== 2. вход ===");
let logged = false;
for (const payload of [
  { email: EMAIL, password: PASSWORD },
  { email: EMAIL, password: PASSWORD, remember: true },
  { login: EMAIL, password: PASSWORD },
]) {
  const r = await call("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const keys = payload.login ? "login+password" : Object.keys(payload).join("+");
  // печатаем ТОЛЬКО статус и форму ответа, без тела с токеном
  const shape = r.body && typeof r.body === "object" ? Object.keys(r.body).join(",") : String(r.body).slice(0, 120);
  console.log(`  POST /api/login (${keys}) -> ${r.status}  поля ответа: ${shape}`);
  if (r.status >= 200 && r.status < 300) { logged = true; break; }
}
console.log("  вход:", logged ? "УСПЕХ" : "не удался");

// 3. Кто мы
console.log("\n=== 3. профиль ===");
for (const p of ["/api/user", "/api/me", "/api/auth/user", "/api/profile"]) {
  const r = await call(p);
  if (r.status === 200) {
    const b = r.body;
    console.log(`  GET ${p} -> 200  поля: ${b && typeof b === "object" ? Object.keys(b).join(",") : "?"}`);
    break;
  } else console.log(`  GET ${p} -> ${r.status}`);
}

// 4. Поиск конечных точек. Аукционы — приоритет.
console.log("\n=== 4. точки поиска (аукционы в приоритете) ===");
const KW = encodeURIComponent("nintendo switch");
const CANDIDATES = [
  // Yahoo Auctions — аукционные лоты со ставками
  `/api/yahoo/items?keyword=${KW}`,
  `/api/yahoo-auction/items?keyword=${KW}`,
  `/api/yahooauction/items?keyword=${KW}`,
  `/api/auctions/items?keyword=${KW}`,
  `/api/auction/items?keyword=${KW}`,
  `/api/yahoo/search?keyword=${KW}`,
  `/api/items/yahoo?keyword=${KW}`,
  // Mercari — фиксированная цена (для сверки)
  `/api/mercari/items?keyword=${KW}`,
  // прочие площадки Sendico
  `/api/rakuten/items?keyword=${KW}`,
  `/api/rakuma/items?keyword=${KW}`,
  `/api/paypay/items?keyword=${KW}`,
  `/api/amazon/items?keyword=${KW}`,
];
const alive = [];
for (const p of CANDIDATES) {
  const r = await call(p);
  const mark = r.status === 200 ? "ЖИВА" : (r.status === 404 ? "нет маршрута" : `${r.status}`);
  let extra = "";
  if (r.status === 200 && r.body && typeof r.body === "object") {
    const b = r.body;
    const arr = Array.isArray(b) ? b : (b.data || b.items || b.results || []);
    extra = `  элементов=${Array.isArray(arr) ? arr.length : "?"}  верх.поля=${Object.keys(b).join(",")}`;
    alive.push([p, b, arr]);
  } else if (r.status !== 404 && typeof r.body === "object" && r.body) {
    extra = `  ${JSON.stringify(r.body).slice(0, 120)}`;
  }
  console.log(`  ${p.split("?")[0].padEnd(30)} -> ${mark}${extra}`);
  await new Promise(r => setTimeout(r, 250));
}

// 5. Форма аукционного лота: ищем ставки/окончание/цену-выкупа
console.log("\n=== 5. форма лота ===");
for (const [p, , arr] of alive) {
  if (!Array.isArray(arr) || !arr.length) continue;
  const it = arr[0];
  console.log(`\n  ${p.split("?")[0]}  поля лота:`);
  console.log("   ", Object.keys(it).join(", "));
  const AUCTION_HINTS = /bid|end|expire|time|left|buyout|buy_now|current|auction|watch/i;
  const hits = Object.keys(it).filter(k => AUCTION_HINTS.test(k));
  if (hits.length) {
    console.log("    аукционные признаки:");
    for (const k of hits) console.log(`      ${k} = ${JSON.stringify(it[k]).slice(0, 80)}`);
  } else console.log("    аукционных признаков нет — похоже, фиксированная цена");
  for (const it2 of arr.slice(0, 3)) {
    const t = it2.name || it2.title || it2.subject || "";
    const pr = it2.price ?? it2.current_price ?? it2.price_yen ?? "?";
    console.log(`      ${String(pr).padStart(9)} ¥  ${String(t).slice(0, 60)}`);
  }
}
