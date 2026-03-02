#!/usr/bin/env bash
set -euo pipefail

pm2 stop netops-web-ui || true
pm2 delete netops-web-ui || true
pm2 save
pm2 status

