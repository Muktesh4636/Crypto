#!/usr/bin/env bash

set -euo pipefail

SERVER_HOST="${SERVER_HOST:-72.61.148.117}"
SERVER_USER="${SERVER_USER:-root}"
SERVER_PASS="${SERVER_PASS:-}"
DOMAIN="${DOMAIN:-crypto.pravoo.in}"
APP_NAME="${APP_NAME:-crypto-dashboard}"
APP_PORT="${APP_PORT:-8001}"

APP_DIR="/var/www/${DOMAIN}"
CURRENT_DIR="${APP_DIR}/current"
SHARED_DIR="${APP_DIR}/shared"
VENV_DIR="${APP_DIR}/venv"
ENV_FILE="${SHARED_DIR}/app.env"
SERVICE_NAME="${APP_NAME}"
PAPER_SERVICE_NAME="${APP_NAME}-paper-trader"
RETRAIN_SERVICE_NAME="${APP_NAME}-model-retrain"
RETRAIN_TIMER_NAME="${RETRAIN_SERVICE_NAME}.timer"
REMOTE_ARCHIVE="/tmp/${APP_NAME}.tar.gz"
REMOTE_ENV="/tmp/${APP_NAME}.env"
REMOTE_SERVICE="/tmp/${SERVICE_NAME}.service"
REMOTE_PAPER_SERVICE="/tmp/${PAPER_SERVICE_NAME}.service"
REMOTE_RETRAIN_SERVICE="/tmp/${RETRAIN_SERVICE_NAME}.service"
REMOTE_RETRAIN_TIMER="/tmp/${RETRAIN_TIMER_NAME}"
REMOTE_NGINX="/tmp/${DOMAIN}.nginx"
NGINX_SITE="/etc/nginx/sites-available/${DOMAIN}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()   { echo -e "${GREEN}[✓]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[✗]${NC} $*"; exit 1; }
step()  { echo -e "\n${BLUE}══ $* ${NC}"; }

SSH_OPTS=(-o StrictHostKeyChecking=no -o ConnectTimeout=10)
USE_SSHPASS=0

MODE="install"
case "${1:-}" in
  "") ;;
  --update) MODE="update" ;;
  --status) MODE="status" ;;
  *) error "Unknown flag: ${1}" ;;
esac

cleanup() {
  rm -f "${LOCAL_ARCHIVE:-}" "${LOCAL_ENV:-}" "${LOCAL_SERVICE:-}" "${LOCAL_PAPER_SERVICE:-}" "${LOCAL_RETRAIN_SERVICE:-}" "${LOCAL_RETRAIN_TIMER:-}" "${LOCAL_NGINX:-}"
}
trap cleanup EXIT

make_secret() {
  python3 - <<'INNERPY'
import secrets
print(secrets.token_urlsafe(50))
INNERPY
}

_setup_ssh() {
  if ssh "${SSH_OPTS[@]}" -o BatchMode=yes -o PasswordAuthentication=no     "${SERVER_USER}@${SERVER_HOST}" "echo ok" >/dev/null 2>&1; then
    log "SSH key authentication: OK"
    USE_SSHPASS=0
    return
  fi

  if ! command -v sshpass >/dev/null 2>&1; then
    error "SSH key login failed and sshpass is not installed. Install sshpass or configure an SSH key."
  fi

  if [[ -z "${SERVER_PASS}" ]]; then
    error "SSH key login failed. Export SERVER_PASS for password auth, then re-run."
  fi

  if sshpass -p "${SERVER_PASS}" ssh "${SSH_OPTS[@]}"     "${SERVER_USER}@${SERVER_HOST}" "echo ok" >/dev/null 2>&1; then
    log "Password authentication via sshpass: OK"
    USE_SSHPASS=1
    return
  fi

  error "Authentication failed for ${SERVER_USER}@${SERVER_HOST}."
}

ssh_run() {
  if [[ "${USE_SSHPASS}" == "1" ]]; then
    sshpass -p "${SERVER_PASS}" ssh "${SSH_OPTS[@]}" "${SERVER_USER}@${SERVER_HOST}" "$@"
  else
    ssh "${SSH_OPTS[@]}" "${SERVER_USER}@${SERVER_HOST}" "$@"
  fi
}

scp_up() {
  local src="$1"
  local dst="$2"
  if [[ "${USE_SSHPASS}" == "1" ]]; then
    sshpass -p "${SERVER_PASS}" scp "${SSH_OPTS[@]}" "${src}" "${SERVER_USER}@${SERVER_HOST}:${dst}"
  else
    scp "${SSH_OPTS[@]}" "${src}" "${SERVER_USER}@${SERVER_HOST}:${dst}"
  fi
}

if ! command -v ssh >/dev/null 2>&1; then
  error "OpenSSH client is required."
fi
if ! command -v tar >/dev/null 2>&1; then
  error "tar is required."
fi
if ! command -v python3 >/dev/null 2>&1; then
  error "python3 is required."
fi

echo ""
echo "  Deploy mode: ${MODE}"
echo "  Target: ${SERVER_USER}@${SERVER_HOST}"
echo "  Domain: ${DOMAIN}"
echo ""

_setup_ssh

if [[ "${MODE}" == "status" ]]; then
  step "Remote service status"
  ssh_run "
    set -e
    echo 'Service states:'
    printf '  ${SERVICE_NAME}: '
    systemctl is-active ${SERVICE_NAME} || true
    printf '  ${PAPER_SERVICE_NAME}: '
    systemctl is-active ${PAPER_SERVICE_NAME} || true
    printf '  ${RETRAIN_TIMER_NAME}: '
    systemctl is-active ${RETRAIN_TIMER_NAME} || true
    printf '  postgresql: '
    systemctl is-active postgresql || true
    printf '  nginx: '
    systemctl is-active nginx || true
    echo ''
    echo 'Health check:'
    curl -fsS -H 'Host: ${DOMAIN}' http://127.0.0.1/api/health/ || true
    echo ''
    echo ''
    echo 'Recent logs:'
    journalctl -u ${SERVICE_NAME} -n 20 --no-pager || true
    echo ''
    echo 'Shared data:'
    du -sh ${SHARED_DIR} 2>/dev/null || true
    ls -lh ${SHARED_DIR}/db.sqlite3 2>/dev/null || true
  "
  exit 0
fi

step "Checking server connectivity"
ssh_run "echo ok" >/dev/null
log "Server reachable"

DJANGO_SECRET_KEY="${DJANGO_SECRET_KEY:-$(make_secret)}"
DJANGO_DEBUG="${DJANGO_DEBUG:-False}"
DJANGO_ALLOWED_HOSTS="${DJANGO_ALLOWED_HOSTS:-${DOMAIN},127.0.0.1,localhost}"
DJANGO_CSRF_TRUSTED_ORIGINS="${DJANGO_CSRF_TRUSTED_ORIGINS:-https://${DOMAIN},http://${DOMAIN}}"
GUARDIAN_API_KEY="${GUARDIAN_API_KEY:-}"
FRED_API_KEY="${FRED_API_KEY:-}"
POSTGRES_DB="${POSTGRES_DB:-crypto_dashboard}"
POSTGRES_USER="${POSTGRES_USER:-crypto_dashboard}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-$(make_secret)}"
POSTGRES_HOST="${POSTGRES_HOST:-127.0.0.1}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
PAPER_TRADER_SYMBOL="${PAPER_TRADER_SYMBOL:-}"
PAPER_TRADER_UNIVERSE="${PAPER_TRADER_UNIVERSE:-20}"
PAPER_TRADER_INTERVAL="${PAPER_TRADER_INTERVAL:-1h}"
PAPER_TRADER_MARKET="${PAPER_TRADER_MARKET:-futures}"
MODEL_TRAIN_UNIVERSE="${MODEL_TRAIN_UNIVERSE:-20}"
AUTO_RETRAIN_CALENDAR="${AUTO_RETRAIN_CALENDAR:-daily}"

LOCAL_ARCHIVE="$(mktemp "/tmp/${APP_NAME}.XXXXXX.tar.gz")"
LOCAL_ENV="$(mktemp "/tmp/${APP_NAME}.XXXXXX.env")"
LOCAL_SERVICE="$(mktemp "/tmp/${SERVICE_NAME}.XXXXXX.service")"
LOCAL_PAPER_SERVICE="$(mktemp "/tmp/${PAPER_SERVICE_NAME}.XXXXXX.service")"
LOCAL_RETRAIN_SERVICE="$(mktemp "/tmp/${RETRAIN_SERVICE_NAME}.XXXXXX.service")"
LOCAL_RETRAIN_TIMER="$(mktemp "/tmp/${RETRAIN_TIMER_NAME}.XXXXXX")"
LOCAL_NGINX="$(mktemp "/tmp/${DOMAIN}.XXXXXX.nginx")"

step "Packaging local source tree"
tar   --exclude=".venv"   --exclude="__pycache__"   --exclude="*.pyc"   --exclude="db.sqlite3"   --exclude=".DS_Store"   --exclude=".git"   -czf "${LOCAL_ARCHIVE}" .
log "Archive created"

cat > "${LOCAL_ENV}" <<EOF
DJANGO_SECRET_KEY=${DJANGO_SECRET_KEY}
DJANGO_DEBUG=${DJANGO_DEBUG}
DJANGO_ALLOWED_HOSTS=${DJANGO_ALLOWED_HOSTS}
DJANGO_CSRF_TRUSTED_ORIGINS=${DJANGO_CSRF_TRUSTED_ORIGINS}
POSTGRES_DB=${POSTGRES_DB}
POSTGRES_USER=${POSTGRES_USER}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
POSTGRES_HOST=${POSTGRES_HOST}
POSTGRES_PORT=${POSTGRES_PORT}
STATIC_ROOT=${SHARED_DIR}/static
SIGNAL_MODEL_DIR=${SHARED_DIR}/model_store
GUARDIAN_API_KEY=${GUARDIAN_API_KEY}
FRED_API_KEY=${FRED_API_KEY}
PYTHONUNBUFFERED=1
EOF

cat > "${LOCAL_SERVICE}" <<EOF
[Unit]
Description=Crypto dashboard Django service
After=network.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=${CURRENT_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${VENV_DIR}/bin/gunicorn config.wsgi:application --bind 127.0.0.1:${APP_PORT} --workers 3 --timeout 120
Restart=always
RestartSec=5
StandardOutput=append:${SHARED_DIR}/logs/gunicorn.log
StandardError=append:${SHARED_DIR}/logs/gunicorn.log

[Install]
WantedBy=multi-user.target
EOF

cat > "${LOCAL_PAPER_SERVICE}" <<EOF
[Unit]
Description=Crypto dashboard paper trader
After=network.target ${SERVICE_NAME}.service

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=${CURRENT_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${VENV_DIR}/bin/python manage.py run_paper_trader --universe ${PAPER_TRADER_UNIVERSE} --interval ${PAPER_TRADER_INTERVAL} --market ${PAPER_TRADER_MARKET}
Restart=always
RestartSec=10
StandardOutput=append:${SHARED_DIR}/logs/paper-trader.log
StandardError=append:${SHARED_DIR}/logs/paper-trader.log

[Install]
WantedBy=multi-user.target
EOF

cat > "${LOCAL_RETRAIN_SERVICE}" <<EOF
[Unit]
Description=Crypto dashboard model retraining job
After=network.target ${SERVICE_NAME}.service

[Service]
Type=oneshot
User=www-data
Group=www-data
WorkingDirectory=${CURRENT_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${VENV_DIR}/bin/python manage.py retrain_model
StandardOutput=append:${SHARED_DIR}/logs/model-retrain.log
StandardError=append:${SHARED_DIR}/logs/model-retrain.log
EOF

cat > "${LOCAL_RETRAIN_TIMER}" <<EOF
[Unit]
Description=Schedule Crypto dashboard model retraining

[Timer]
OnCalendar=${AUTO_RETRAIN_CALENDAR}
Persistent=true
Unit=${RETRAIN_SERVICE_NAME}.service

[Install]
WantedBy=timers.target
EOF

cat > "${LOCAL_NGINX}" <<EOF
server {
    listen 80;
    server_name ${DOMAIN};

    client_max_body_size 10m;

    location /static/ {
        alias ${SHARED_DIR}/static/;
        expires 1h;
        add_header Cache-Control "public";
    }

    location / {
        proxy_pass http://127.0.0.1:${APP_PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120;
    }
}
EOF

step "Uploading deployment assets"
scp_up "${LOCAL_ARCHIVE}" "${REMOTE_ARCHIVE}"
scp_up "${LOCAL_ENV}" "${REMOTE_ENV}"
scp_up "${LOCAL_SERVICE}" "${REMOTE_SERVICE}"
scp_up "${LOCAL_PAPER_SERVICE}" "${REMOTE_PAPER_SERVICE}"
scp_up "${LOCAL_RETRAIN_SERVICE}" "${REMOTE_RETRAIN_SERVICE}"
scp_up "${LOCAL_RETRAIN_TIMER}" "${REMOTE_RETRAIN_TIMER}"
scp_up "${LOCAL_NGINX}" "${REMOTE_NGINX}"
log "Upload complete"

step "Installing system packages"
ssh_run "
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq python3 python3-pip python3-venv nginx curl postgresql postgresql-contrib
"
log "System packages ready"

step "Deploying application files"
ssh_run "
  set -e
  mkdir -p ${CURRENT_DIR} ${SHARED_DIR}/static ${SHARED_DIR}/logs ${SHARED_DIR}/model_store
  bash -lc 'shopt -s dotglob && rm -rf ${CURRENT_DIR:?}/*'
  tar -xzf ${REMOTE_ARCHIVE} -C ${CURRENT_DIR}
  rm -f ${REMOTE_ARCHIVE}
  mv ${REMOTE_ENV} ${ENV_FILE}
  chmod 600 ${ENV_FILE}
  if [ ! -x ${VENV_DIR}/bin/python ]; then
    python3 -m venv ${VENV_DIR}
  fi
  ${VENV_DIR}/bin/pip install -q --upgrade pip
  ${VENV_DIR}/bin/pip install -q -r ${CURRENT_DIR}/requirements.txt
  chown -R www-data:www-data ${APP_DIR}
"
log "Application files ready"

step "Provisioning PostgreSQL"
ssh_run "
  set -e
  systemctl enable postgresql >/dev/null 2>&1 || true
  systemctl restart postgresql
  su postgres -c \"psql <<'SQL'
DO \\\$\\\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '${POSTGRES_USER}') THEN
    CREATE ROLE ${POSTGRES_USER} LOGIN PASSWORD '${POSTGRES_PASSWORD}';
  ELSE
    ALTER ROLE ${POSTGRES_USER} WITH LOGIN PASSWORD '${POSTGRES_PASSWORD}';
  END IF;
END
\\\$\\\$;
SQL\"
  [ \"\$(su postgres -c \"psql -tAc \\\"SELECT 1 FROM pg_database WHERE datname='${POSTGRES_DB}'\\\"\" | tr -d '[:space:]')\" = \"1\" ] || \
    su postgres -c \"createdb -O ${POSTGRES_USER} ${POSTGRES_DB}\"
  su postgres -c \"psql -c \\\"GRANT ALL PRIVILEGES ON DATABASE ${POSTGRES_DB} TO ${POSTGRES_USER};\\\"\"
"
log "PostgreSQL ready"

step "Running Django migrations and collecting static files"
ssh_run "bash -lc 'set -e; cd ${CURRENT_DIR}; set -a; source ${ENV_FILE}; set +a; ${VENV_DIR}/bin/python manage.py migrate --noinput; ${VENV_DIR}/bin/python manage.py collectstatic --noinput'"
log "Django setup complete"

step "Writing systemd and nginx configuration"
ssh_run "
  set -e
  mv ${REMOTE_SERVICE} /etc/systemd/system/${SERVICE_NAME}.service
  mv ${REMOTE_PAPER_SERVICE} /etc/systemd/system/${PAPER_SERVICE_NAME}.service
  mv ${REMOTE_RETRAIN_SERVICE} /etc/systemd/system/${RETRAIN_SERVICE_NAME}.service
  mv ${REMOTE_RETRAIN_TIMER} /etc/systemd/system/${RETRAIN_TIMER_NAME}
  mv ${REMOTE_NGINX} ${NGINX_SITE}
  ln -sfn ${NGINX_SITE} /etc/nginx/sites-enabled/${DOMAIN}
  rm -f /etc/nginx/sites-enabled/default
  nginx -t
  systemctl daemon-reload
  systemctl enable ${SERVICE_NAME} ${PAPER_SERVICE_NAME} ${RETRAIN_TIMER_NAME} nginx
  fuser -k ${APP_PORT}/tcp 2>/dev/null || true
  systemctl restart ${SERVICE_NAME}
  systemctl stop ${PAPER_SERVICE_NAME} || true
  systemctl restart ${RETRAIN_TIMER_NAME}
  systemctl reload nginx
"
log "Services reloaded"

step "Warming initial stored data"
ssh_run "bash -lc 'set -e; cd ${CURRENT_DIR}; set -a; source ${ENV_FILE}; set +a; ${VENV_DIR}/bin/python manage.py ingest_news --max-total 120 --per-feed 40 || true; if [ -n "${FRED_API_KEY:-}" ]; then ${VENV_DIR}/bin/python manage.py ingest_fred_macro --days 1095 || true; fi'"
log "Initial ingest completed"

step "Seeding AI signal model if missing"
ssh_run "bash -lc 'set -e; cd ${CURRENT_DIR}; set -a; source ${ENV_FILE}; set +a; if ! compgen -G \"${SHARED_DIR}/model_store/signal_model_*.pkl\" > /dev/null; then ${VENV_DIR}/bin/python manage.py seed_model --days 1095 --universe ${MODEL_TRAIN_UNIVERSE}; fi; systemctl restart ${PAPER_SERVICE_NAME}'"
log "AI model ready"

step "Final health check"
ssh_run "
  set -e
  curl -fsS -H 'Host: ${DOMAIN}' http://127.0.0.1/api/health/
"
log "Deployment successful"

echo ""
echo "Next commands:"
echo "  ./deploy.sh --status"
echo "  ssh ${SERVER_USER}@${SERVER_HOST}"
echo ""
