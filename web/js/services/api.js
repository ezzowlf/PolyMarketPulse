// Thin fetch wrapper for the read-only PolymarketPulse REST API.
// The dashboard never talks to providers directly — only to this API,
// which itself only ever reads from SQLite (see cli.py / storage.py).
const Api = {
  async _get(path) {
    const res = await fetch(path);
    if (!res.ok) throw await Api._error(path, res);
    return res.json();
  },
  async _send(path, method, body) {
    const res = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) throw await Api._error(path, res);
    return res.json();
  },
  async _error(path, res) {
    let detail = "";
    try {
      detail = (await res.json()).detail || "";
    } catch {
      /* body wasn't JSON */
    }
    const err = new Error(`${path} -> HTTP ${res.status}${detail ? `: ${detail}` : ""}`);
    err.status = res.status;
    err.detail = detail;
    return err;
  },
  health: () => Api._get("/health"),
  providers: () => Api._get("/providers"),
  provider: (name) => Api._get(`/provider/${encodeURIComponent(name)}`),
  markets: (params = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "")
    );
    return Api._get(`/markets?${qs.toString()}`);
  },
  market: (id) => Api._get(`/market/${encodeURIComponent(id)}`),
  signals: (params = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "")
    );
    return Api._get(`/signals?${qs.toString()}`);
  },
  signal: (id) => Api._get(`/signal/${id}`),
  stats: () => Api._get("/stats"),
  news: (params = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "")
    );
    return Api._get(`/news?${qs.toString()}`);
  },
  history: (marketId) => Api._get(`/history/${encodeURIComponent(marketId)}`),
  watchlist: () => Api._get("/watchlist"),
  addWatchlist: (payload) => Api._send("/watchlist", "POST", payload),
  removeWatchlist: (id) => Api._send(`/watchlist/${id}`, "DELETE"),
  calendar: () => Api._get("/calendar"),
  heatmap: () => Api._get("/heatmap"),
  analytics: () => Api._get("/analytics"),
  settings: () => Api._get("/settings"),
  quality: (provider) => Api._get(`/quality${provider ? `?provider=${encodeURIComponent(provider)}` : ""}`),
  performance: () => Api._get("/performance"),
  simulation: (limit) => Api._get(`/simulation${limit ? `?limit=${limit}` : ""}`),
  resolutions: (params = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "")
    );
    return Api._get(`/resolutions?${qs.toString()}`);
  },
  providersStatus: () => Api._get("/providers/status"),
  search: (q) => Api._get(`/search?q=${encodeURIComponent(q)}`),
  compare: () => Api._get("/compare"),
  historyFull: (marketId) => Api._get(`/history/full/${encodeURIComponent(marketId)}`),
  explain: (marketId, mode) => Api._get(`/explain/${encodeURIComponent(marketId)}?mode=${mode}`),
  aiStatus: () => Api._get("/ai/status"),
  aiExplainMarket: (marketId) => Api._send(`/ai/explain-market/${encodeURIComponent(marketId)}`, "POST"),
  aiExplainSignal: (signalId) => Api._send(`/ai/explain-signal/${signalId}`, "POST"),
  aiAnalyzeNews: (marketId) => Api._send(`/ai/analyze-news/${encodeURIComponent(marketId)}`, "POST"),
  aiCompare: (marketIds) => Api._send("/ai/compare", "POST", { market_ids: marketIds }),
  aiAsk: (question, marketId) => Api._send("/ai/ask", "POST", { question, market_id: marketId || null }),
  home: () => Api._get("/home"),
  shadowSetups: (status) => Api._get(`/shadow-setups${status ? `?status=${encodeURIComponent(status)}` : ""}`),
  shadowSetup: (id) => Api._get(`/shadow-setup/${id}`),
  prediction: (marketId) => Api._get(`/prediction/${encodeURIComponent(marketId)}`),
  explainRecommendation: (marketId) => Api._get(`/ai/explain-recommendation/${encodeURIComponent(marketId)}`),
  explainRecommendationRecompute: (marketId) =>
    Api._send(`/ai/explain-recommendation/${encodeURIComponent(marketId)}/recompute`, "POST"),
  costReport: (days) => Api._get(`/ai/cost-report${days ? `?days=${days}` : ""}`),
  evaluation: () => Api._get("/evaluation"),
  backtest: (params = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "")
    );
    return Api._get(`/backtest?${qs.toString()}`);
  },
};
