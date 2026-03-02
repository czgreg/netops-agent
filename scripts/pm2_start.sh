#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/Users/chengzhe/Desktop/ab-agent"
cd "$ROOT_DIR"

pm2 start ecosystem.config.js --update-env
pm2 save
pm2 status

