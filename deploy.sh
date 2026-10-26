#!/usr/bin/env bash
# Kodu sunucuya gönderip botu Docker ile yeniden başlatır.
# Sunucu bilgileri deploy.env içinde (bkz. deploy.env.example, repoya girmez).
# Kullanım (Git Bash): ./deploy.sh        .env de gitsin: ./deploy.sh --env
set -euo pipefail
cd "$(dirname "$0")"

[ -f deploy.env ] || { echo "deploy.env yok: deploy.env.example'ı kopyalayıp doldur"; exit 1; }
source deploy.env
: "${DEPLOY_HOST:?deploy.env içinde DEPLOY_HOST yok}"
: "${DEPLOY_KEY:?deploy.env içinde DEPLOY_KEY yok}"
DEPLOY_DIR=${DEPLOY_DIR:-em-bot}

FILES="Dockerfile docker-compose.yml .dockerignore requirements.txt levels.py engine.py feed.py tg.py chart.py news.py main.py README.md"
[ "${1:-}" = "--env" ] && FILES="$FILES .env"

ssh -i "$DEPLOY_KEY" "$DEPLOY_HOST" "mkdir -p $DEPLOY_DIR/data && chmod 700 $DEPLOY_DIR"
scp -q -i "$DEPLOY_KEY" $FILES "$DEPLOY_HOST:$DEPLOY_DIR/"
ssh -i "$DEPLOY_KEY" "$DEPLOY_HOST" "cd $DEPLOY_DIR && chmod 600 .env && docker compose up -d --build && docker compose logs --tail 5"
