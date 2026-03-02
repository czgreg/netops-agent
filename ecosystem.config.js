module.exports = {
  apps: [
    {
      name: "netops-web-ui",
      script: "./scripts/start_web_with_open.sh",
      cwd: "/Users/chengzhe/Desktop/ab-agent",
      interpreter: "bash",
      autorestart: true,
      watch: false,
      max_restarts: 10,
      restart_delay: 2000,
      out_file: "./logs/pm2-out.log",
      error_file: "./logs/pm2-error.log",
      merge_logs: true,
      time: true,
      env: {
        PYTHONUNBUFFERED: "1",
      },
    },
  ],
};

