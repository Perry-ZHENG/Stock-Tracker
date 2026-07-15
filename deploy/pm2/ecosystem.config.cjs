module.exports = {
  apps: [
    {
      name: "stock-agent-worker",
      script: process.env.STOCK_AGENT_BIN || "stock-agent",
      args: `worker --interval-sec ${process.env.STOCK_AGENT_INTERVAL_SEC || "30"}`,
      cwd: process.env.STOCK_AGENT_WORKDIR || process.cwd(),
      autorestart: true,
      max_restarts: Number(process.env.STOCK_AGENT_PM2_MAX_RESTARTS || 5),
      env: {
        STOCK_AGENT_WORKDIR: process.env.STOCK_AGENT_WORKDIR || process.cwd(),
        STOCK_AGENT_CONFIG: process.env.STOCK_AGENT_CONFIG || "configs/config.yaml",
        TWELVE_DATA_API_KEY: process.env.TWELVE_DATA_API_KEY || "",
        GEMINI_API_KEY: process.env.GEMINI_API_KEY || "",
        OPENROUTER_API_KEY: process.env.OPENROUTER_API_KEY || "",
        TELEGRAM_BOT_TOKEN: process.env.TELEGRAM_BOT_TOKEN || "",
        NEWS_API_KEY: process.env.NEWS_API_KEY || "",
      },
    },
  ],
};
