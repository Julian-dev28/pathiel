#!/usr/bin/env node
/**
 * Hermes-Trader MCP Server
 *
 * Bridges the FastAPI REST API (localhost:8000) into an MCP server
 * so Hermes Agent can call scan/research/execute/state/config tools.
 *
 * Usage: node scripts/hermes-mcp-server.mjs
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

const API = "http://localhost:8000";

// ── Helpers ────────────────────────────────────────────────────────────

async function apiFetch(path, options = {}) {
  const url = `${API}${path}`;
  const res = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
    signal: AbortSignal.timeout(60_000),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API ${path} ${res.status}: ${text}`);
  }
  return res.json();
}

// ── Server ─────────────────────────────────────────────────────────────

const server = new McpServer({
  name: "hermes-trader",
  version: "0.2.0",
});

// ── Tool: scan ─────────────────────────────────────────────────────────

server.tool(
  "scan",
  "Scan all Hyperliquid markets for trading signals. Returns triggered candidates above a score threshold.",
  {
    minScore: z.number().optional().describe("Minimum composite trigger score (0-100, default 75)"),
  },
  async ({ minScore = 75 }) => {
    try {
      const results = await apiFetch("/api/agent/scan", {
        method: "POST",
        body: JSON.stringify({ minScore }),
      });
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(results, null, 2),
          },
        ],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text",
            text: `Scan failed: ${err.message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ── Tool: research ─────────────────────────────────────────────────────

server.tool(
  "research",
  "Run deep AI analysis on a specific coin. Returns verdict, confidence, entry/stop/TP prices.",
  {
    coin: z.string().describe("Coin ticker (e.g., BTC, ETH, SOL)"),
    perceptionId: z.string().optional().describe("Optional perception ID from a prior scan"),
  },
  async ({ coin, perceptionId }) => {
    try {
      const results = await apiFetch(`/api/agent/research/${coin}`, {
        method: "POST",
        body: JSON.stringify({ perceptionId }),
      });
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(results, null, 2),
          },
        ],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text",
            text: `Research failed: ${err.message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ── Tool: execute ──────────────────────────────────────────────────────

server.tool(
  "execute",
  "Execute a trade based on a prior analysis. Passes through all risk gates.",
  {
    analysisId: z.string().describe("Analysis ID from a research call"),
  },
  async ({ analysisId }) => {
    try {
      const results = await apiFetch("/api/agent/execute", {
        method: "POST",
        body: JSON.stringify({ analysisId }),
      });
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(results, null, 2),
          },
        ],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text",
            text: `Execute failed: ${err.message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ── Tool: state ────────────────────────────────────────────────────────

server.tool(
  "state",
  "Get the full agent state: watchlist, recent perceptions, AI analyses, trades, config, and operating mode.",
  {},
  async () => {
    try {
      const results = await apiFetch("/api/agent/state");
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(results, null, 2),
          },
        ],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text",
            text: `State fetch failed: ${err.message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ── Tool: config ───────────────────────────────────────────────────────

server.tool(
  "config",
  "Get or set agent configuration. Pass mode, risk caps, thresholds, coin filters.",
  {
    mode: z.enum(["OFF", "LIVE"]).optional().describe("Operating mode"),
    autoAnalyzeThreshold: z.number().optional().describe("Min composite score to trigger AI analysis"),
    minAiConfidence: z.number().optional().describe("Min AI confidence for execution"),
    maxConcurrent: z.number().optional().describe("Max concurrent positions"),
    maxTradeNotionalUsd: z.number().optional().describe("Max notional per trade in USD"),
    maxDailyLossUsd: z.number().optional().describe("Max daily loss before kill switch"),
    minMarketVolumeUsd: z.number().optional().describe("Min 24h market volume floor"),
    maxTotalNotionalPct: z.number().optional().describe("Max total notional as % of equity"),
    cooldownMin: z.number().optional().describe("Cooldown minutes between trades on same market"),
    coinAllowlist: z.array(z.string()).optional().describe("Allowed coins (empty = all)"),
    coinBlocklist: z.array(z.string()).optional().describe("Blocked coins"),
  },
  async ({
    mode,
    autoAnalyzeThreshold,
    minAiConfidence,
    maxConcurrent,
    maxTradeNotionalUsd,
    maxDailyLossUsd,
    minMarketVolumeUsd,
    maxTotalNotionalPct,
    cooldownMin,
    coinAllowlist,
    coinBlocklist,
  }) => {
    try {
      // If no params, just GET current config
      const hasParams =
        mode !== undefined ||
        autoAnalyzeThreshold !== undefined ||
        minAiConfidence !== undefined ||
        maxConcurrent !== undefined ||
        maxTradeNotionalUsd !== undefined ||
        maxDailyLossUsd !== undefined ||
        minMarketVolumeUsd !== undefined ||
        maxTotalNotionalPct !== undefined ||
        cooldownMin !== undefined ||
        coinAllowlist !== undefined ||
        coinBlocklist !== undefined;

      if (!hasParams) {
        const results = await apiFetch("/api/agent/config");
        return {
          content: [
            {
              type: "text",
              text: JSON.stringify(results, null, 2),
            },
          ],
        };
      }

      // Build config object from provided params
      const config = {};
      if (mode !== undefined) config.mode = mode;
      if (autoAnalyzeThreshold !== undefined) config.autoAnalyzeThreshold = autoAnalyzeThreshold;
      if (minAiConfidence !== undefined) config.minAiConfidence = minAiConfidence;
      if (maxConcurrent !== undefined) config.maxConcurrent = maxConcurrent;
      if (maxTradeNotionalUsd !== undefined) config.maxTradeNotionalUsd = maxTradeNotionalUsd;
      if (maxDailyLossUsd !== undefined) config.maxDailyLossUsd = maxDailyLossUsd;
      if (minMarketVolumeUsd !== undefined) config.minMarketVolumeUsd = minMarketVolumeUsd;
      if (maxTotalNotionalPct !== undefined) config.maxTotalNotionalPct = maxTotalNotionalPct;
      if (cooldownMin !== undefined) config.cooldownMin = cooldownMin;
      if (coinAllowlist !== undefined) config.coinAllowlist = coinAllowlist;
      if (coinBlocklist !== undefined) config.coinBlocklist = coinBlocklist;

      const results = await apiFetch("/api/agent/config", {
        method: "POST",
        body: JSON.stringify(config),
      });
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(results, null, 2),
          },
        ],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text",
            text: `Config failed: ${err.message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ── Tool: hl_account ───────────────────────────────────────────────────

server.tool(
  "hl_account",
  "Get Hyperliquid account state: equity, open positions, spot balances.",
  {
    user: z.string().optional().describe("Wallet address (defaults to env var)"),
  },
  async ({ user }) => {
    try {
      const url = user ? `/api/hl/account?user=${encodeURIComponent(user)}` : "/api/hl/account";
      const results = await apiFetch(url);
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(results, null, 2),
          },
        ],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text",
            text: `Account fetch failed: ${err.message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ── Tool: hl_all_mids ──────────────────────────────────────────────────

server.tool(
  "hl_all_mids",
  "Get current mid prices for all Hyperliquid markets.",
  {},
  async () => {
    try {
      const results = await apiFetch("/api/hl/all-mids");
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(results, null, 2),
          },
        ],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text",
            text: `Mids fetch failed: ${err.message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ── Tool: hl_universe ──────────────────────────────────────────────────

server.tool(
  "hl_universe",
  "Get the full market universe (perp + spot) from Hyperliquid.",
  {},
  async () => {
    try {
      const results = await apiFetch("/api/hl/universe");
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(results, null, 2),
          },
        ],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text",
            text: `Universe fetch failed: ${err.message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ── Tool: hl_candles ───────────────────────────────────────────────────

server.tool(
  "hl_candles",
  "Get OHLCV candlestick data for a coin.",
  {
    coin: z.string().describe("Coin ticker (e.g., BTC)"),
    interval: z.string().optional().describe("Interval: 1m, 5m, 15m, 1h, 4h, 1d (default 5m)"),
    count: z.number().optional().describe("Number of candles (default 100)"),
  },
  async ({ coin, interval = "5m", count = 100 }) => {
    try {
      const results = await apiFetch(
        `/api/hl/candles?coin=${encodeURIComponent(coin)}&interval=${interval}&count=${count}`
      );
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(results, null, 2),
          },
        ],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text",
            text: `Candles fetch failed: ${err.message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ── Tool: hl_price ─────────────────────────────────────────────────────

server.tool(
  "hl_price",
  "Get current price for a specific coin.",
  {
    coin: z.string().describe("Coin ticker (e.g., BTC)"),
  },
  async ({ coin }) => {
    try {
      const results = await apiFetch(`/api/hl/price?coin=${encodeURIComponent(coin)}`);
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(results, null, 2),
          },
        ],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text",
            text: `Price fetch failed: ${err.message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ── Tool: hl_place_order ───────────────────────────────────────────────

server.tool(
  "hl_place_order",
  "Place a new order on Hyperliquid.",
  {
    coin: z.string().describe("Coin ticker"),
    isBuy: z.boolean().describe("True for buy/long, false for sell/short"),
    size: z.number().describe("Size in base units"),
    limitPx: z.number().describe("Limit price"),
    reduceOnly: z.boolean().optional().describe("If true, only reduce existing position"),
    slippage: z.number().optional().describe("Slippage tolerance in bps (default 10)"),
  },
  async ({ coin, isBuy, size, limitPx, reduceOnly = false, slippage = 10 }) => {
    try {
      const results = await apiFetch("/api/hl/place-order", {
        method: "POST",
        body: JSON.stringify({ coin, isBuy, size, limitPx, reduceOnly, slippage }),
      });
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(results, null, 2),
          },
        ],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text",
            text: `Place order failed: ${err.message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ── Tool: hl_close_position ────────────────────────────────────────────

server.tool(
  "hl_close_position",
  "Close an open position on Hyperliquid.",
  {
    coin: z.string().describe("Coin ticker to close"),
    slippage: z.number().optional().describe("Slippage tolerance in bps (default 10)"),
  },
  async ({ coin, slippage = 10 }) => {
    try {
      const results = await apiFetch("/api/hl/close-position", {
        method: "POST",
        body: JSON.stringify({ coin, slippage }),
      });
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(results, null, 2),
          },
        ],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text",
            text: `Close position failed: ${err.message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ── Tool: hl_orderbook ─────────────────────────────────────────────────

server.tool(
  "hl_orderbook",
  "Get orderbook depth for a coin.",
  {
    coin: z.string().describe("Coin ticker"),
  },
  async ({ coin }) => {
    try {
      const results = await apiFetch(`/api/hl/orderbook?coin=${encodeURIComponent(coin)}`);
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(results, null, 2),
          },
        ],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text",
            text: `Orderbook fetch failed: ${err.message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ── Tool: agent_trades ─────────────────────────────────────────────────

server.tool(
  "agent_trades",
  "Get trade history from the persistent journal.",
  {
    limit: z.number().optional().describe("Max trades to return (default 20)"),
  },
  async ({ limit = 20 }) => {
    try {
      const results = await apiFetch(`/api/agent/trades?limit=${limit}`);
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(results, null, 2),
          },
        ],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text",
            text: `Trades fetch failed: ${err.message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ── Tool: agent_start ──────────────────────────────────────────────────

server.tool(
  "agent_start",
  "Start autonomous agent mode (sends periodic scan→research→execute cycles).",
  {},
  async () => {
    try {
      const results = await apiFetch("/api/agent/start", { method: "POST" });
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(results, null, 2),
          },
        ],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text",
            text: `Agent start failed: ${err.message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ── Tool: agent_stop ───────────────────────────────────────────────────

server.tool(
  "agent_stop",
  "Stop autonomous agent mode.",
  {},
  async () => {
    try {
      const results = await apiFetch("/api/agent/stop", { method: "POST" });
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(results, null, 2),
          },
        ],
      };
    } catch (err) {
      return {
        content: [
          {
            type: "text",
            text: `Agent stop failed: ${err.message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ── Connection ─────────────────────────────────────────────────────────

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error("Hermes-Trader MCP server running on stdio");
}

main().catch((err) => {
  console.error("MCP server failed:", err);
  process.exit(1);
});
