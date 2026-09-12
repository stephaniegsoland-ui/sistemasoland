#!/usr/bin/env bash
set -euo pipefail

# ===============================================================
# SOLAND VPS DEPLOY SCRIPT
# ===============================================================
# Run this script on an Ubuntu/Debian VPS as root or with sudo.
# It installs Docker, Nginx, Certbot, configures domains,
# and prepares the app for https://soland.com and https://api.soland.com
#
# Before running:
#   1. Point DNS A records for soland.com, www.soland.com and api.soland.com to this VPS IP
#   2. Have the project in /opt/soland or clone it there
# ===============================================================

APP_DIR="/opt/soland"
BACKEND_DIR="${APP_DIR}/solandBackend"
FRONTEND_DIR="${APP_DIR}/solandFrontend"

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this script as root or with sudo."
  exit 1
fi

echo "[1/9] Installing system dependencies..."
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates \
  curl \
  git \
  nginx \
  certbot \
  python3-certbot-nginx \
  software-properties-common \
  unzip \
  jq

if ! command -v docker >/dev/null 2>&1; then
  echo "[2/9] Installing Docker..."
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

systemctl enable docker
systemctl start docker

if ! getent group docker >/dev/null; then
  groupadd docker
fi
usermod -aG docker "${SUDO_USER:-root}"

mkdir -p "${APP_DIR}"

if [[ ! -d "${BACKEND_DIR}" || ! -d "${FRONTEND_DIR}" ]]; then
  echo "[3/9] Project folders not found."
  echo "Please clone or upload the project to ${APP_DIR} first, so that:"
  echo "  ${BACKEND_DIR}"
  echo "  ${FRONTEND_DIR}"
  exit 1
fi

cat > "${BACKEND_DIR}/.env" <<'EOF'
SECRET="cambia_esta_clave_por_una_segura"
DEEPSEEK_API_KEY="tu_api_key"
DEEPSEEK_MODEL="deepseek-chat"
DB_URL="mysql+aiomysql://soland:soland_password@db/soland_db"
DB_URL_DOCKER="mysql+aiomysql://soland:soland_password@db/soland_db"

MYSQL_USER="soland"
MYSQL_PASSWORD="soland_password"
MYSQL_ROOT_PASSWORD="soland_root_password"
MYSQL_DATABASE="soland_db"
MYSQL_HOST="localhost"
MYSQL_PORT="3306"

CORS_ALLOWED_ORIGINS="https://soland.com,https://www.soland.com"
EOF

cat > "${FRONTEND_DIR}/.env.local" <<'EOF'
NEXT_PUBLIC_API_URL=https://api.soland.com
EOF

if [[ ! -f "${FRONTEND_DIR}/package.json" ]]; then
  echo "Frontend package.json not found at ${FRONTEND_DIR}."
  exit 1
fi

if [[ ! -f "${BACKEND_DIR}/compose.yml" ]]; then
  echo "Missing Docker Compose file at ${BACKEND_DIR}/compose.yml"
  exit 1
fi

echo "[4/9] Building backend containers..."
cd "${BACKEND_DIR}"
docker compose up --build -d

echo "[5/9] Installing frontend dependencies..."
cd "${FRONTEND_DIR}"
if [[ -f package-lock.json || -f yarn.lock || -f pnpm-lock.yaml ]]; then
  npm install --no-fund --no-audit
else
  npm install --no-fund --no-audit
fi
npm run build

cat > /etc/nginx/sites-available/soland-frontend <<'EOF'
server {
    listen 80;
    server_name soland.com www.soland.com;

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF

cat > /etc/nginx/sites-available/soland-api <<'EOF'
server {
    listen 80;
    server_name api.soland.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF

rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/soland-frontend /etc/nginx/sites-enabled/
ln -sf /etc/nginx/sites-available/soland-api /etc/nginx/sites-enabled/

nginx -t
systemctl restart nginx

cat > /etc/systemd/system/soland-frontend.service <<'EOF'
[Unit]
Description=SOLAND Frontend
After=network.target

[Service]
WorkingDirectory=/opt/soland/solandFrontend
ExecStart=/usr/bin/npm start
Restart=always
RestartSec=5
Environment=NODE_ENV=production
Environment=PORT=3000

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable soland-frontend.service
systemctl start soland-frontend.service

echo "[6/9] Waiting for the API to start..."
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/docs >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo "[7/9] Generating HTTPS certificates with Let's Encrypt..."
certbot --nginx -d soland.com -d www.soland.com -d api.soland.com --non-interactive --agree-tos -m admin@soland.com

echo "[8/9] Restarting services..."
nginx -t
systemctl restart nginx
systemctl restart soland-frontend.service

echo "[9/9] Deployment complete."
echo "Frontend: https://soland.com"
echo "API: https://api.soland.com"
echo "Important: Make sure DNS A records exist for soland.com, www.soland.com and api.soland.com before certbot runs."
EOF

chmod +x "${BACKEND_DIR}/scripts/deploy_vps.sh"

echo "Deployment script created at ${BACKEND_DIR}/scripts/deploy_vps.sh"
