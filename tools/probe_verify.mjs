// Проверка, что параметры категорий РАБОТАЮТ как серверные фильтры, а не просто
// присутствуют в ответе. Плюс поиск оставшихся id категорий и значений.
// Запуск: node tools/probe_verify.mjs
const BASE = "https://api.kufar.by/search-api/v2/search/rendered-paginated";

async function q(params, label) {
  const u = new URL(BASE);
  for (const [k, v] of Object.entries(params)) u.searchParams.set(k, v);
  u.searchParams.set("size", "100");
  u.searchParams.set("sort", "lst.d");
  const r = await fetch(u, { headers: { "User-Agent": "market-searchers/1.0" } });
  if (!r.ok) { console.log(`  ${label}: HTTP ${r.status}`); return null; }
  const j = await r.json();
  const ads = j.ads || [];
  const prices = ads.map(a => Number(a.price_byn) / 100).filter(p => p > 0).sort((a, b) => a - b);
  const med = prices.length ? prices[Math.floor(prices.length / 2)] : null;
  console.log(`  ${label}: total=${String(j.total).padStart(6)} got=${String(ads.length).padStart(3)}` +
    ` медиана=${med === null ? "—" : med.toFixed(0).padStart(5)}`);
  return { j, ads };
}

function typeMix(ads, pu) {
  const m = new Map();
  for (const a of ads) for (const p of a.ad_parameters || []) if (p.pu === pu) {
    const k = `${p.v}=${p.vl}`; m.set(k, (m.get(k) || 0) + 1);
  }
  return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5)
    .map(([k, n]) => `${k}×${n}`).join(", ");
}

console.log("\n### 1. Работает ли cct (тип комплектующих)? cat=16010, cct=11=Видеокарты");
await q({ cat: "16010", rgn: "7" }, "без cct        ");
const gpu = await q({ cat: "16010", rgn: "7", cct: "11" }, "cct=11         ");
if (gpu) console.log("     состав cct:", typeMix(gpu.ads, "cct") || "(нет)");

console.log("\n### 2. Работает ли ccvc (модель GPU)? 18=RTX 4070");
const g4070 = await q({ cat: "16010", rgn: "7", cct: "11", ccvc: "18" }, "ccvc=18        ");
if (g4070) {
  console.log("     состав ccvc:", typeMix(g4070.ads, "ccvc") || "(нет)");
  for (const a of g4070.ads.slice(0, 5))
    console.log(`       ${(Number(a.price_byn) / 100).toFixed(0).padStart(5)} Br  ${a.subject}`);
}

console.log("\n### 3. Работает ли cte (приставки vs игры)? cat=5040, cte=1=Приставки");
await q({ cat: "5040", rgn: "7" }, "без cte        ");
const cons = await q({ cat: "5040", rgn: "7", cte: "1" }, "cte=1          ");
if (cons) console.log("     состав cte:", typeMix(cons.ads, "cte") || "(нет)");
const ps5 = await q({ cat: "5040", rgn: "7", cte: "1", cbc: "31" }, "cte=1&cbc=31Sony");
if (ps5) for (const a of ps5.ads.slice(0, 5))
  console.log(`       ${(Number(a.price_byn) / 100).toFixed(0).padStart(5)} Br  ${a.subject}`);

console.log("\n### 4. Мультизначение через запятую? bcys=1 взрослый, bca=1,5 горный+шоссе");
await q({ cat: "4050", rgn: "7", btc: "1" }, "btc=1 велосип. ");
const adult = await q({ cat: "4050", rgn: "7", btc: "1", bcys: "1" }, "+bcys=1 взрослый");
if (adult) console.log("     состав bcys:", typeMix(adult.ads, "bcys") || "(нет)");
const road = await q({ cat: "4050", rgn: "7", btc: "1", bca: "1,5" }, "+bca=1,5       ");
if (road) console.log("     состав bca:", typeMix(road.ads, "bca") || "(нет)");

console.log("\n### 5. Обувь: длина стельки 290-300мм (29-30см) — shlgt=90,100");
await q({ cat: "19020", rgn: "7", mst: "9" }, "кроссовки      ");
const big = await q({ cat: "19020", rgn: "7", mst: "9", shlgt: "90,100" }, "+shlgt=90,100  ");
if (big) {
  console.log("     состав shlgt:", typeMix(big.ads, "shlgt") || "(нет)");
  console.log("     состав mss:  ", typeMix(big.ads, "mss") || "(нет)");
  for (const a of big.ads.slice(0, 6))
    console.log(`       ${(Number(a.price_byn) / 100).toFixed(0).padStart(5)} Br  ${a.subject}`);
}

console.log("\n### 6. Состояние: cnd=1 Б/у — отсекает новый ритейл?");
await q({ cat: "16010", rgn: "7", cct: "11" }, "все            ");
await q({ cat: "16010", rgn: "7", cct: "11", cnd: "1" }, "cnd=1 б/у      ");
