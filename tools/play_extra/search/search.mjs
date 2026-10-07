import gplay from "@mradex77/google-play-scraper";

const term = process.argv[2] || "";
if (!term.trim()) {
  process.stdout.write("[]");
  process.exit(0);
}

const apps = await gplay.search({
  term,
  num: 250,
  lang: "en",
  country: "us",
});

const rows = [];
for (const app of apps || []) {
  const appId = String(app.appId || app.app_id || "").trim();
  if (!appId) {
    continue;
  }
  rows.push({
    appId,
    title: app.title || "",
    developer: app.developer || "",
    summary: app.summary || "",
    free: app.free,
    genre: app.genre || "",
    genreId: app.genreId || "",
    installs: app.installs,
    minInstalls: app.minInstalls,
  });
}
process.stdout.write(JSON.stringify(rows));
