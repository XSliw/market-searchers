// Разведка дерева категорий Kufar: по текстовому запросу смотрим, из каких
// подкатегорий приходят лоты. Нужно, чтобы искать фильтром cat=<id>, а не
// словом в названии (слово тянет чужие категории — «rtx 3080» отдаёт
// системные блоки, и медиана врёт).
// Запуск: node tools/probe_cats.mjs
const BASE = "https://api.kufar.by/search-api/v2/search/rendered-paginated";

const QUERIES = [
  "видеокарта", "процессор", "оперативная память", "материнская плата",
  "блок питания", "ssd", "кроссовки", "велосипед", "наушники", "куртка",
];

async function probe(query) {
  const u = new URL(BASE);
  u.searchParams.set("query", query);
  u.searchParams.set("size", "100");
  u.searchParams.set("sort", "lst.d");
  const r = await fetch(u, { headers: { "User-Agent": "market-searchers/1.0" } });
  const j = await r.json();
  const ads = j.ads || [];
  const seen = new Map();
  for (const a of ads) {
    const id = a.category || "?";
    let name = "";
    for (const p of a.ad_parameters || []) if (p.pu === "cat") name = p.vl || "";
    const key = `${id}\t${name}`;
    seen.set(key, (seen.get(key) || 0) + 1);
  }
  console.log(`\n=== ${query} — ${ads.length} лотов, total=${j.total} ===`);
  for (const [k, n] of [...seen.entries()].sort((a, b) => b[1] - a[1])) {
    const [id, name] = k.split("\t");
    console.log(`  ${String(n).padStart(3)}  cat=${id.padEnd(6)} ${name}`);
  }
}

for (const q of QUERIES) {
  try { await probe(q); } catch (e) { console.log(`\n=== ${q} — ОШИБКА ${e.message}`); }
  await new Promise(r => setTimeout(r, 400));
}
