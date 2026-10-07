const SEARCH_RPC = "lGYRle";
const PAGE_RPC = "qnKhOb";
const USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36";
const BATCH_URL = "https://play.google.com/_/PlayStoreUi/data/batchexecute";
const CLUSTER_QUERY = "rpcids=qnKhOb&f.sid=-697906427155521722&bl=boq_playuiserver_20190903.08_p0";
const CLUSTER_TRAIL = "authuser&soc-app=121&soc-platform=1&soc-device=1&_reqid=1065213";

const GRID = {
  appId: [0, 0, 0],
  title: [0, 3],
  developer: [0, 14],
  summary: [0, 13, 1],
  price: [0, 8, 1, 0, 0],
};
const PAGE = {
  appId: [0, 0],
  title: [3],
  developer: [14],
  summary: [13, 1],
  price: [8, 1, 0, 0],
};

export function getPath(value, path) {
  let current = value;
  for (const segment of path) {
    if (current === undefined || current === null) return undefined;
    if (typeof segment === "number") {
      if (!Array.isArray(current)) return undefined;
      current = segment < 0 ? current[current.length + segment] : current[segment];
    } else {
      current = current[segment];
    }
  }
  return current;
}

export function sectionsFromHtml(html) {
  const table = html.match(/; var AF_dataServiceRequests[\s\S]*?; var AF_initDataChunkQueue/)?.[0] ?? "";
  let dsKey = "ds:4";
  for (const pair of table.matchAll(/'(ds:\d+)'\s*:\s*\{\s*id:\s*'([^']+)'/g)) {
    if (pair[2] === SEARCH_RPC) dsKey = pair[1];
  }
  let root;
  for (const block of html.matchAll(/>AF_initDataCallback[\s\S]*?<\/script/g)) {
    const key = /(ds:.*?)'/.exec(block[0]);
    const payload = /data:([\s\S]*?), sideChannel: {}}\);<\//.exec(block[0]);
    if (!key || !payload || key[1] !== dsKey) continue;
    try {
      root = JSON.parse(payload[1]);
    } catch {
      continue;
    }
  }
  const sections = getPath(root, [0, 1]);
  return Array.isArray(sections) ? sections : [];
}

export function appRow(item, layout) {
  const appId = String(getPath(item, layout.appId) ?? "").trim();
  if (!appId) return null;
  const price = getPath(item, layout.price);
  return {
    appId,
    title: String(getPath(item, layout.title) ?? ""),
    developer: String(getPath(item, layout.developer) ?? ""),
    summary: String(getPath(item, layout.summary) ?? ""),
    free: typeof price === "number" ? price === 0 : true,
    genre: "",
    genreId: "",
  };
}

function pushRows(rows, seen, apps, layout) {
  if (!Array.isArray(apps)) return;
  for (const item of apps) {
    const row = appRow(item, layout);
    if (!row || seen.has(row.appId)) continue;
    seen.add(row.appId);
    rows.push(row);
  }
}

export async function collectSearchApps(sections, fetchPage) {
  const rows = [];
  const seen = new Set();
  for (const section of sections || []) {
    const apps = getPath(section, [22, 0]);
    if (!Array.isArray(apps) || apps.length === 0) continue;
    pushRows(rows, seen, apps, GRID);
    let token = tokenOf(getPath(section, [22, 1, 3, 1]));
    const seenTokens = new Set();
    while (token) {
      if (seenTokens.has(token)) break;
      seenTokens.add(token);
      const page = await fetchPage(token);
      const more = page?.apps;
      if (!Array.isArray(more) || more.length === 0) break;
      pushRows(rows, seen, more, PAGE);
      token = tokenOf(page.token);
    }
  }
  return rows;
}

function tokenOf(value) {
  return typeof value === "string" && value.length > 0 ? value : "";
}

export function buildClusterBody(count, token) {
  return `f.req=%5B%5B%5B%22qnKhOb%22%2C%22%5B%5Bnull%2C%5B%5B10%2C%5B10%2C${count}%5D%5D%2Ctrue%2Cnull%2C%5B96%2C27%2C4%2C8%2C57%2C30%2C110%2C79%2C11%2C16%2C49%2C1%2C3%2C9%2C12%2C104%2C55%2C56%2C51%2C10%2C34%2C77%5D%5D%2Cnull%2C%5C%22${token}%5C%22%5D%5D%22%2Cnull%2C%22generic%22%5D%5D%5D`;
}

export function parseContinuation(text) {
  const start = text.indexOf("[");
  if (start === -1) return { apps: [], token: "" };
  for (const line of text.slice(start).split("\n")) {
    const trimmed = line.trim();
    if (!trimmed.startsWith("[")) continue;
    let frames;
    try {
      frames = JSON.parse(trimmed);
    } catch {
      continue;
    }
    if (!Array.isArray(frames)) continue;
    for (const frame of frames) {
      if (!Array.isArray(frame) || frame[0] !== "wrb.fr" || frame[1] !== PAGE_RPC) continue;
      if (typeof frame[2] !== "string") return { apps: [], token: "" };
      try {
        const payload = JSON.parse(frame[2]);
        const apps = getPath(payload, [0, 0, 0]);
        const token = getPath(payload, [0, 0, 7, 1]);
        return { apps: Array.isArray(apps) ? apps : [], token: tokenOf(token) };
      } catch {
        return { apps: [], token: "" };
      }
    }
  }
  return { apps: [], token: "" };
}

export async function fetchContinuation(token, request = fetch) {
  const url = `${BATCH_URL}?${CLUSTER_QUERY}&hl=en&gl=us&${CLUSTER_TRAIL}`;
  const response = await request(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
      "User-Agent": USER_AGENT,
    },
    body: buildClusterBody(100, token),
  });
  return parseContinuation(await response.text());
}

export function searchUrl(term) {
  const params = new URLSearchParams({
    c: "apps",
    q: term,
    hl: "en",
    gl: "us",
    price: "1",
  });
  return `https://play.google.com/store/search?${params.toString()}`;
}
