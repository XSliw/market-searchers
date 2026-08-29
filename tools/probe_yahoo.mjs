// Структура выдачи Yahoo Auctions: что именно вытаскивать под аукционные лоты
// (текущая ставка, цена немедленного выкупа 即決, число ставок, время до конца).
// Работает только с не-EEA IP → запускать в Actions (target=auctions).
// Запуск: node tools/probe_yahoo.mjs
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36";
const H = { "User-Agent": UA, "Accept": "text/html", "Accept-Language": "ja,en;q=0.8" };

async function get(url) {
  const r = await fetch(url, { headers: H, redirect: "follow" });
  return { status: r.status, html: r.ok ? await r.text() : "" };
}

const KW = encodeURIComponent("nintendo switch");
const { status, html } = await get(`https://auctions.yahoo.co.jp/search/search?p=${KW}&n=50`);
console.log(`выдача: ${status}, ${html.length} б`);

// 1. Есть ли data-атрибуты у ссылок на лот — тогда разбор надёжный, без вёрстки
console.log("\n=== 1. data-атрибуты ===");
const ATTRS = ["data-auction-id", "data-auction-title", "data-auction-price", "data-auction-buynowprice",
  "data-auction-bids", "data-auction-endtime", "data-auction-img", "data-auction-seller",
  "data-auction-category", "data-auction-condition", "data-cl-params", "data-elabel"];
for (const a of ATTRS) {
  const n = (html.match(new RegExp(a, "g")) || []).length;
  if (n) {
    const m = html.match(new RegExp(`${a}="([^"]{0,90})"`));
    console.log(`  ${a.padEnd(26)} ×${String(n).padEnd(4)} пример: ${m ? m[1] : ""}`);
  } else console.log(`  ${a.padEnd(26)} нет`);
}

// 2. Классы-контейнеры
console.log("\n=== 2. классы ===");
for (const c of ["Product", "Product__titleLink", "Product__priceValue", "Product__bid", "Product__time",
  "Product__image", "Product__buyNow", "Product__prices", "Product__seller", "Product__title"]) {
  const n = (html.match(new RegExp(`class="[^"]*\\b${c}\\b`, "g")) || []).length;
  console.log(`  ${c.padEnd(22)} ×${n}`);
}

// 3. Первый блок лота целиком — по нему видно всю вёрстку
console.log("\n=== 3. первый блок лота ===");
const li = html.match(/<li class="Product[^"]*"[\s\S]{0,2600}?<\/li>/);
console.log(li ? li[0].replace(/\s+/g, " ") : "(не нашли <li class=\"Product\">)");

// 4. Пробный разбор: собираем лоты и печатаем
console.log("\n=== 4. пробный разбор ===");
function parse(html) {
  const out = [];
  const blocks = html.split(/<li class="Product/).slice(1);
  for (const b of blocks) {
    const id = (b.match(/auction\/([a-zA-Z]?[0-9]{6,})/) || [])[1];
    if (!id) continue;
    const title = (b.match(/Product__titleLink[^>]*>\s*([^<]{1,140})/) || [])[1]
      || (b.match(/data-auction-title="([^"]{1,140})"/) || [])[1] || "";
    const prices = [...b.matchAll(/Product__priceValue[^>]*>\s*([0-9,]+)/g)].map(m => Number(m[1].replace(/,/g, "")));
    const bids = (b.match(/Product__bid[^>]*>\s*([0-9]+)/) || [])[1];
    const time = (b.match(/Product__time[^>]*>\s*([^<]{1,24})/) || [])[1];
    const img = (b.match(/<img[^>]+src="(https?:\/\/[^"]+)"/) || [])[1];
    const buynow = /即決|即落/.test(b);
    out.push({ id, title: title.trim(), cur: prices[0] ?? null, alt: prices[1] ?? null, bids, time: (time || "").trim(), img, buynow });
  }
  return out;
}
const lots = parse(html);
console.log(`  разобрано лотов: ${lots.length}`);
for (const l of lots.slice(0, 8)) {
  console.log(`  ${String(l.cur ?? "?").padStart(8)} ¥` +
    `${l.alt !== null ? ` (второе ${String(l.alt).padStart(7)})` : "               "}` +
    `  ставок=${String(l.bids ?? "-").padStart(3)}  ${String(l.time).padEnd(10)}` +
    `  ${l.buynow ? "即決" : "  "}  ${l.title.slice(0, 46)}`);
}
console.log(`  без цены: ${lots.filter(l => l.cur === null).length}, без картинки: ${lots.filter(l => !l.img).length}`);

// 5. Параметры поиска: сортировка, диапазон цены, страница, только-выкуп
console.log("\n=== 5. параметры выдачи ===");
const VARIANTS = [
  ["база                 ", `p=${KW}&n=50`],
  ["сорт: заканчиваются  ", `p=${KW}&n=50&s1=end&o1=a`],
  ["сорт: дешёвые        ", `p=${KW}&n=50&s1=cbids&o1=a`],
  ["сорт: новые          ", `p=${KW}&n=50&s1=new&o1=d`],
  ["цена 1000-5000       ", `p=${KW}&n=50&aucminprice=1000&aucmaxprice=5000`],
  ["страница 2 (b=51)    ", `p=${KW}&n=50&b=51`],
  ["только выкуп fixed=1 ", `p=${KW}&n=50&fixed=1`],
  ["только аукцион fixed=2", `p=${KW}&n=50&fixed=2`],
  ["новое istatus=1      ", `p=${KW}&n=50&istatus=1`],
  ["100 на страницу      ", `p=${KW}&n=100`],
];
for (const [label, qs] of VARIANTS) {
  try {
    const r = await get(`https://auctions.yahoo.co.jp/search/search?${qs}`);
    const ls = parse(r.html);
    const cur = ls.map(l => l.cur).filter(Boolean).sort((a, b) => a - b);
    const med = cur.length ? cur[Math.floor(cur.length / 2)] : null;
    const ids = new Set(ls.map(l => l.id));
    console.log(`  ${label} ${r.status}  лотов=${String(ls.length).padStart(3)} уник=${String(ids.size).padStart(3)}` +
      `  медиана=${med ?? "—"}  первый=${ls[0]?.cur ?? "—"}¥ ${(ls[0]?.title || "").slice(0, 34)}`);
  } catch (e) { console.log(`  ${label} сеть: ${e.message}`); }
  await new Promise(x => setTimeout(x, 700));
}

// 6. Ссылка для заказа через Sendico: принимает ли он URL лота Yahoo
console.log("\n=== 6. как отдать лот в Sendico ===");
const someId = lots[0]?.id;
if (someId) {
  console.log(`  лот Yahoo:  https://page.auctions.yahoo.co.jp/jp/auction/${someId}`);
  for (const u of [
    `https://sendico.com/en/yahoo/item/${someId}`,
    `https://sendico.com/yahoo/${someId}`,
    `https://sendico.com/en/order?url=https://page.auctions.yahoo.co.jp/jp/auction/${someId}`,
  ]) {
    try {
      const r = await fetch(u, { headers: H, redirect: "manual" });
      console.log(`  ${r.status} ${u.replace("https://sendico.com", "")}`);
    } catch (e) { console.log(`  сеть: ${e.message}`); }
    await new Promise(x => setTimeout(x, 300));
  }
}
