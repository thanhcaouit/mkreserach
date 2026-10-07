import gplay from "@mradex77/google-play-scraper";

import { collectSearchApps, fetchContinuation, searchUrl, sectionsFromHtml } from "./pages.mjs";

const term = process.argv[2] || "";
if (!term.trim()) {
  process.stdout.write("[]");
  process.exit(0);
}

const html = await fetch(searchUrl(term), {
  headers: {
    "User-Agent":
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
  },
}).then((response) => response.text());

const sections = sectionsFromHtml(html);
let rows = [];
if (sections.length > 0) {
  rows = await collectSearchApps(sections, (token) => fetchContinuation(token));
}
if (rows.length === 0) {
  const apps = await gplay.search({ term, num: 250, lang: "en", country: "us", price: "free" });
  rows = (apps || []).map((app) => ({
    appId: String(app.appId || "").trim(),
    title: app.title || "",
    developer: app.developer || "",
    summary: app.summary || "",
    free: app.free,
    genre: app.genre || "",
    genreId: app.genreId || "",
    installs: app.installs,
    minInstalls: app.minInstalls,
  })).filter((app) => app.appId);
}
process.stdout.write(JSON.stringify(rows));
