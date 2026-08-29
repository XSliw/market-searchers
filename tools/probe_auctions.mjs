// Разведка ОТКРЫТЫХ источников японских аукционов. Sendico API закрыт наглухо:
// из Беларуси и из Actions (US) одинаково — челлендж на главной и прикладной
// 403 "Access denied" на всех /api/*, включая POST /api/login с пустым телом.
// Обходить защиту не будем — ищем источник, который отдаёт лоты без логина.
// Приоритет: АУКЦИОННЫЕ лоты (текущая ставка, время до конца, цена выкупа).
// Запуск: node tools/probe_auctions.mjs
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36";
const KW = "nintendo switch";

const TARGETS = [
  ["Yahoo Auctions (первоисточник)", `https://auctions.yahoo.co.jp/search/search?p=${encodeURIComponent(KW)}&n=50`],
  ["Yahoo Auctions категория",       `https://auctions.yahoo.co.jp/category/list/23632/?p=${encodeURIComponent(KW)}`],
  ["Jauce",                          `https://www.jauce.com/search?q=${encodeURIComponent(KW)}`],
  ["Jauce (alt)",                    `https://www.jauce.com/auction/search?keyword=${encodeURIComponent(KW)}`],
  ["Buyee yahoo",                    `https://buyee.jp/item/search/query/${encodeURIComponent(KW)}`],
  ["Buyee yahoo (cat)",              `https://buyee.jp/yahoo/auction/search/query/${encodeURIComponent(KW)}`],
  ["Zenmarket yahoo",                `https://zenmarket.jp/en/yahoo.aspx?q=${encodeURIComponent(KW)}`],
  ["Mercari (для сверки)",           `https://jp.mercari.com/search?keyword=${encodeURIComponent(KW)}`],
];

function analyze(html) {
  const out = {};
  out.len = html.length;
  // цены: ¥1,234 / 1,234円
  const yen = [...html.matchAll(/(?:¥|&yen;)\s?([0-9][0-9,]{2,})|([0-9][0-9,]{2,})\s?円/g)]
    .map(m => Number((m[1] || m[2] || "").replace(/,/g, ""))).filter(n => n > 0);
  out.prices = yen.length;
  out.priceSample = yen.slice(0, 6);
  // ссылки на лоты
  out.yahooItems = new Set([...html.matchAll(/auctions\.yahoo\.co\.jp\/(?:jp\/)?auction\/([a-zA-Z0-9]+)/g)].map(m => m[1])).size;
  out.buyeeItems = new Set([...html.matchAll(/\/item\/yahoo\/auction\/([a-zA-Z0-9]+)/g)].map(m => m[1])).size;
  out.jauceItems = new Set([...html.matchAll(/\/auction\/(?:item\/)?([a-zA-Z][0-9]{6,})/g)].map(m => m[1])).size;
  out.zenItems = new Set([...html.matchAll(/yahoo\.aspx\?itemCode=([a-zA-Z0-9]+)/g)].map(m => m[1])).size;
  // аукционные признаки
  out.bidWords = (html.match(/入札|bid|Bids/gi) || []).length;
  out.endWords = (html.match(/残り|終了|time left|ends/gi) || []).length;
  // встроенный JSON — самый удобный путь разбора
  out.nextData = /__NEXT_DATA__/.test(html);
  out.nuxtData = /__NUXT__/.test(html);
  const pre = html.match(/window\.__PRELOADED_STATE__|preloadedState|"itemList"|"auctionList"/);
  out.stateHint = pre ? pre[0] : "";
  return out;
}

for (const [label, url] of TARGETS) {
  let res, html = "";
  try {
    res = await fetch(url, {
      redirect: "follow",
      headers: {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
      },
    });
    html = await res.text();
  } catch (e) {
    console.log(`\n### ${label}\n  сеть: ${e.message}`);
    continue;
  }
  const cf = res.headers.get("cf-mitigated");
  console.log(`\n### ${label}`);
  console.log(`  ${res.status} ${(res.headers.get("content-type") || "").split(";")[0]}` +
    `  server=${res.headers.get("server") || "?"}${cf ? `  cf-mitigated=${cf}` : ""}`);
  if (!res.ok) { console.log(`  тело: ${html.replace(/\s+/g, " ").slice(0, 140)}`); continue; }
  const a = analyze(html);
  console.log(`  HTML=${a.len} б  цен=${a.prices} ${a.priceSample.length ? "(" + a.priceSample.join(", ") + ")" : ""}`);
  console.log(`  лотов: yahoo=${a.yahooItems} buyee=${a.buyeeItems} jauce=${a.jauceItems} zen=${a.zenItems}`);
  console.log(`  аукцион: слов «ставка»=${a.bidWords} «до конца»=${a.endWords}`);
  console.log(`  встроенный JSON: __NEXT_DATA__=${a.nextData} __NUXT__=${a.nuxtData} ${a.stateHint}`);
  await new Promise(r => setTimeout(r, 600));
}

// Отдельно: у Yahoo Auctions есть открытая JSON-выдача для «похожих» и мобильных
// клиентов. Проверяем несколько известных путей — если отдаст JSON, разбор будет
// надёжным, без парсинга HTML.
console.log("\n\n=== JSON-пути Yahoo Auctions ===");
const JSONS = [
  `https://auctions.yahoo.co.jp/search/search?p=${encodeURIComponent(KW)}&output=json`,
  `https://auctions.yahoo.co.jp/api/search?p=${encodeURIComponent(KW)}`,
  `https://auctions.yahooapis.jp/AuctionWebService/V2/json/search?query=${encodeURIComponent(KW)}`,
  `https://auctions.yahoo.co.jp/jp/show/json/searchresult?p=${encodeURIComponent(KW)}`,
];
for (const u of JSONS) {
  try {
    const r = await fetch(u, { headers: { "User-Agent": UA, "Accept": "application/json" } });
    const t = (await r.text()).slice(0, 200);
    console.log(`  ${r.status} ${(r.headers.get("content-type") || "").split(";")[0].padEnd(24)} ${u.replace(/https:\/\//, "").slice(0, 60)}`);
    if (r.ok) console.log(`      ${t.replace(/\s+/g, " ")}`);
  } catch (e) { console.log(`  сеть: ${e.message}`); }
  await new Promise(r => setTimeout(r, 400));
}
