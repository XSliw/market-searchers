// Kufar: синтаксис МУЛЬТИЗНАЧЕНИЯ фильтра. Через запятую (`bca=1,5`) отдало 0 лотов,
// значит форма другая. Проверяем варианты на заведомо непустых фильтрах.
// Запуск: node tools/probe_multi.mjs
const BASE = "https://api.kufar.by/search-api/v2/search/rendered-paginated";

async function q(params, label) {
  const u = new URL(BASE);
  for (const [k, v] of Object.entries(params)) {
    if (Array.isArray(v)) for (const x of v) u.searchParams.append(k, x);
    else u.searchParams.set(k, v);
  }
  u.searchParams.set("size", "100");
  u.searchParams.set("sort", "lst.d");
  let r;
  try { r = await fetch(u, { headers: { "User-Agent": "market-searchers/1.0" } }); }
  catch (e) { console.log(`  ${label.padEnd(26)} сеть: ${e.message}`); return null; }
  if (!r.ok) { console.log(`  ${label.padEnd(26)} HTTP ${r.status}`); return null; }
  const j = await r.json();
  const ads = j.ads || [];
  console.log(`  ${label.padEnd(26)} total=${String(j.total).padStart(6)} got=${String(ads.length).padStart(3)}`);
  return { j, ads };
}
function mix(ads, pu) {
  const m = new Map();
  for (const a of ads) for (const p of a.ad_parameters || []) if (p.pu === pu) {
    const k = `${p.v}=${p.vl}`; m.set(k, (m.get(k) || 0) + 1);
  }
  return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6).map(([k, n]) => `${k}×${n}`).join(", ");
}

// Одиночные значения как опора: сколько лотов у каждого по отдельности
console.log("\n### опора: велоклассы по одному (cat=4050&btc=1)");
const base = { cat: "4050", rgn: "7", btc: "1" };
await q(base, "без bca");
await q({ ...base, bca: "1" }, "bca=1 горный");
await q({ ...base, bca: "5" }, "bca=5 шоссейный");

console.log("\n### варианты мультизначения bca (ждём ~сумму двух выше)");
const VARIANTS = [
  ["v.or:1,5", { ...base, bca: "v.or:1,5" }],
  ["1,5", { ...base, bca: "1,5" }],
  ["repeat bca=1&bca=5", { ...base, bca: ["1", "5"] }],
  ["bca[]=1&bca[]=5", { cat: "4050", rgn: "7", btc: "1", "bca[]": ["1", "5"] }],
  ["1|5", { ...base, bca: "1|5" }],
  ["v.in:1,5", { ...base, bca: "v.in:1,5" }],
  ["or:1,5", { ...base, bca: "or:1,5" }],
];
for (const [label, params] of VARIANTS) {
  const r = await q(params, label);
  if (r && r.ads.length) console.log(`      состав bca: ${mix(r.ads, "bca") || "(нет)"}`);
  await new Promise(x => setTimeout(x, 350));
}

// То же на длине стельки — это и есть «29.0–30.0 см» из задачи
console.log("\n### опора: длина стельки по одной (cat=19020&mst=9)");
const sh = { cat: "19020", rgn: "7", mst: "9" };
for (const v of ["90", "95", "100"]) await q({ ...sh, shlgt: v }, `shlgt=${v}`);
console.log("\n### мультизначение shlgt");
for (const [label, val] of [["v.or:90,95,100", "v.or:90,95,100"], ["r:290,300 диапазон?", "r:290,300"], ["r:90,100", "r:90,100"]]) {
  const r = await q({ ...sh, shlgt: val }, label);
  if (r && r.ads.length) {
    console.log(`      состав shlgt: ${mix(r.ads, "shlgt") || "(нет)"}`);
    for (const a of r.ads.slice(0, 4)) console.log(`        ${(Number(a.price_byn) / 100).toFixed(0).padStart(5)} Br  ${a.subject}`);
  }
  await new Promise(x => setTimeout(x, 350));
}
const rep = await q({ cat: "19020", rgn: "7", mst: "9", shlgt: ["90", "95", "100"] }, "repeat shlgt×3");
if (rep && rep.ads.length) console.log(`      состав shlgt: ${mix(rep.ads, "shlgt") || "(нет)"}`);

// Размер обуви: ищем id для 46 (в прошлой разведке нашлись 41..45)
console.log("\n### mss: ищем id размера 46 (перебор 1..30)");
for (let v = 1; v <= 30; v++) {
  const u = new URL(BASE);
  u.searchParams.set("cat", "19020"); u.searchParams.set("mss", String(v));
  u.searchParams.set("size", "5"); u.searchParams.set("sort", "lst.d");
  try {
    const r = await fetch(u, { headers: { "User-Agent": "market-searchers/1.0" } });
    if (!r.ok) continue;
    const j = await r.json();
    let label = "";
    for (const a of j.ads || []) for (const p of a.ad_parameters || []) if (p.pu === "mss") { label = p.vl; break; }
    if (label) console.log(`  mss=${String(v).padEnd(3)} ${String(label).padEnd(8)} total=${j.total}`);
  } catch { }
  await new Promise(x => setTimeout(x, 120));
}
