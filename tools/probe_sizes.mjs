// Точная карта id размеров обуви (mss) и одежды (mcs). Прошлый перебор врал:
// у объявления с рядом размеров (41-46) в mss лежит СПИСОК значений, и первый
// лот не показывает, что именно отфильтровано. Берём ПЕРЕСЕЧЕНИЕ списков по всем
// лотам выборки — значение, которое есть у каждого, и есть значение фильтра.
// Запуск: node tools/probe_sizes.mjs
const BASE = "https://api.kufar.by/search-api/v2/search/rendered-paginated";

async function mapParam(cat, pu, from, to, label) {
  console.log(`\n=== cat=${cat} ${pu} (${label}) ===`);
  const found = [];
  for (let v = from; v <= to; v++) {
    const u = new URL(BASE);
    u.searchParams.set("cat", cat);
    u.searchParams.set(pu, String(v));
    u.searchParams.set("size", "25");
    u.searchParams.set("sort", "lst.d");
    let j;
    try {
      const r = await fetch(u, { headers: { "User-Agent": "market-searchers/1.0" } });
      if (!r.ok) { await new Promise(x => setTimeout(x, 120)); continue; }
      j = await r.json();
    } catch { continue; }
    const ads = j.ads || [];
    // по каждому лоту — множество значений параметра
    const sets = [];
    for (const a of ads) {
      for (const p of a.ad_parameters || []) if (p.pu === pu) {
        const vals = Array.isArray(p.v) ? p.v.map(String) : String(p.v).split(",").map(s => s.trim());
        const labels = Array.isArray(p.vl) ? p.vl.map(String) : String(p.vl).split(",").map(s => s.trim());
        sets.push(new Map(vals.map((x, i) => [x, labels[i] ?? ""])));
      }
    }
    if (!sets.length) { await new Promise(x => setTimeout(x, 120)); continue; }
    // пересечение
    let inter = new Map(sets[0]);
    for (const s of sets.slice(1)) {
      for (const k of [...inter.keys()]) if (!s.has(k)) inter.delete(k);
      if (!inter.size) break;
    }
    const hit = [...inter.entries()].map(([k, l]) => `${k}=${l}`).join(" / ");
    if (hit) { console.log(`  ${pu}=${String(v).padEnd(3)} → ${hit.padEnd(16)} total=${j.total}`); found.push([v, hit]); }
    else console.log(`  ${pu}=${String(v).padEnd(3)} → (пересечение пусто, лотов ${sets.length}) total=${j.total}`);
    await new Promise(x => setTimeout(x, 130));
  }
  return found;
}

await mapParam("19020", "mss", 1, 32, "размер мужской обуви — нужны 45 и 46");
await mapParam("19010", "mcs", 1, 14, "размер мужской одежды");
