#!/bin/bash
# EC2 first-boot script (templated by deploy.sh). Log: /var/log/avatar-studio-setup.log
exec > >(tee -a /var/log/avatar-studio-setup.log) 2>&1
set -euxo pipefail

mkdir -p /opt/avatar-studio && cd /opt/avatar-studio
curl -fsSL "__BUNDLE_URL__" | tar -xz

TOKEN=$(curl -fsS -X PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 300")
PUBLIC_IP=$(curl -fsS -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/public-ipv4)

cat > .env <<ENV
APP_PASSWORD=__APP_PASSWORD__
LLM_MODEL=__LLM_MODEL__
SITE_ADDRESS=${PUBLIC_IP//./-}.sslip.io
ENV

docker compose -f docker-compose.yml -f deploy/aws/compose.aws.yml up -d --build
# Pull the LLM once Ollama is listening.
for i in $(seq 1 60); do docker compose exec -T ollama ollama list && break; sleep 5; done
docker compose exec -T ollama ollama pull "__LLM_MODEL__"
echo "AVATAR_STUDIO_READY https://${PUBLIC_IP//./-}.sslip.io"
