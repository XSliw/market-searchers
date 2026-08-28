// Разведка фильтров категории: какие параметры несут лоты внутри cat=<id>.
// Поле `pu` в ad_parameters — это имя query-параметра (cat, rgn, ar…), значит
// через них можно фильтровать серверно: размер обуви, производитель GPU и т.п.
// Запуск: node tools/probe_filters.mjs
const BASE = "https://api.kufar.by/search-api/v2/search/rendered-paginated";

const CATS = [
  ["16010", "Комплектующие"],
  ["19020", "Мужская обувь"],
  ["19010", "Мужская одежда"],
  ["4050", "Велоспорт"],
  ["5040", "Игры и приставки"],
  ["16040", "Ноутбуки"],
];

async function probe(cat, label) {
  const u = new URL(BASE);
  u.searchParams.set("cat", cat);
  u.searchParams.set("rgn", "7");
  u.searchParams.set("size", "100");
  u.searchParams.set("sort", "lst.d");
  const r = await fetch(u, { headers: { "User-Agent": "market-searchers/1.0" } });
  const j = await r.json();
  const ads = j.ads || [];
  // pu -> {pl (человекочитаемое), значения}
  const params = new Map();
  for (const a of ads) {
    for (const p of a.ad_parameters || []) {
      const pu = p.pu;
      if (!pu) continue;
      if (!params.has(pu)) params.set(pu, { pl: p.pl || "", vals: new Map(), n: 0 });
      const e = params.get(pu);
      e.n++;
      const v = `${p.v}${p.vl && String(p.vl) !== String(p.v) ? ` (${p.vl})` : ""}`;
      e.vals.set(v, (e.vals.get(v) || 0) + 1);
    }
  }
  console.log(`\n=== cat=${cat} ${label} — ${ads.length} лотов, total=${j.total} ===`);
  for (const [pu, e] of [...params.entries()].sort((a, b) => b[1].n - a[1].n)) {
    const top = [...e.vals.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6)
      .map(([v, n]) => `${v}×${n}`).join(", ");
    console.log(`  ${pu.padEnd(8)} ${String(e.pl).padEnd(24)} ${e.vals.size} знач: ${top}`);
  }
}

for (const [c, l] of CATS) {
  try { await probe(c, l); } catch (e) { console.log(`\n=== cat=${c} — ОШИБКА ${e.message}`); }
  await new Promise(r => setTimeout(r, 400));
}
