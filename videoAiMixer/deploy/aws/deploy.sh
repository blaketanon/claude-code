#!/usr/bin/env bash
# Deploys Video AI Mixer (app + Ollama + voice server + HTTPS) to one GPU EC2 instance.
#
#   APP_PASSWORD=... ./deploy/aws/deploy.sh          # from the videoAiMixer directory
#
# Env: AWS_REGION (us-east-1), INSTANCE_TYPE (g5.xlarge, ~$1/hr), LLM_MODEL (llama3.1:8b), STACK (video-ai-mixer)
# Remove everything with ./deploy/aws/teardown.sh
set -euo pipefail
cd "$(dirname "$0")/../.."

: "${APP_PASSWORD:?Set APP_PASSWORD - the password you will type on your phone}"
# Kept to a shell/.env-safe character set so it survives user-data and docker compose interpolation.
[[ "$APP_PASSWORD" =~ ^[A-Za-z0-9._~+=-]{10,}$ ]] || {
  echo "APP_PASSWORD must be 10+ characters of letters, digits or . _ ~ + = -" >&2; exit 1; }
export AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}" AWS_DEFAULT_REGION="${AWS_REGION}"
INSTANCE_TYPE="${INSTANCE_TYPE:-g5.xlarge}"
LLM_MODEL="${LLM_MODEL:-llama3.1:8b}"
STACK="${STACK:-video-ai-mixer}"

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
echo "Deploying '$STACK' to account $ACCOUNT, region $AWS_REGION, instance $INSTANCE_TYPE"

# 1. Ship the code: tarball in a private S3 bucket, fetched by the instance via a presigned URL (no IAM role needed).
BUCKET="${STACK}-deploy-${ACCOUNT}-${AWS_REGION}"
if ! aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  if [ "$AWS_REGION" = us-east-1 ]; then aws s3api create-bucket --bucket "$BUCKET" >/dev/null
  else aws s3api create-bucket --bucket "$BUCKET" --create-bucket-configuration LocationConstraint="$AWS_REGION" >/dev/null; fi
  aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
fi
BUNDLE=$(mktemp -d)/bundle.tgz
tar --exclude=node_modules --exclude=data --exclude=.env --exclude=__pycache__ --exclude=voice-data -czf "$BUNDLE" .
aws s3 cp "$BUNDLE" "s3://$BUCKET/bundle.tgz" --only-show-errors
BUNDLE_URL=$(aws s3 presign "s3://$BUCKET/bundle.tgz" --expires-in 86400)

# 2. Network: default VPC + a security group allowing only HTTP/HTTPS.
VPC=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)
[ "$VPC" != "None" ] || { echo "No default VPC in $AWS_REGION; set AWS_REGION to one that has one." >&2; exit 1; }
SG=$(aws ec2 describe-security-groups --filters Name=group-name,Values="$STACK" Name=vpc-id,Values="$VPC" \
  --query 'SecurityGroups[0].GroupId' --output text)
if [ "$SG" = "None" ]; then
  SG=$(aws ec2 create-security-group --group-name "$STACK" --description "Video AI Mixer HTTP/HTTPS" --vpc-id "$VPC" --query GroupId --output text)
  for port in 80 443; do
    aws ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port $port --cidr 0.0.0.0/0 >/dev/null
  done
fi

# 3. Instance: AWS Deep Learning Base AMI (NVIDIA driver, Docker, NVIDIA Container Toolkit preinstalled).
AMI=$(aws ssm get-parameter --name /aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id \
  --query Parameter.Value --output text)
USER_DATA=$(mktemp)
sed -e "s|__BUNDLE_URL__|${BUNDLE_URL//&/\\&}|" -e "s|__APP_PASSWORD__|${APP_PASSWORD}|" -e "s|__LLM_MODEL__|${LLM_MODEL}|g" \
  deploy/aws/user-data.sh > "$USER_DATA"

EXISTING=$(aws ec2 describe-instances --filters Name=tag:Name,Values="$STACK" Name=instance-state-name,Values=pending,running,stopped \
  --query 'Reservations[].Instances[].InstanceId' --output text)
if [ -n "$EXISTING" ]; then
  echo "An instance tagged $STACK already exists ($EXISTING). Run teardown.sh first, or set STACK to another name." >&2
  exit 1
fi
INSTANCE=$(aws ec2 run-instances --image-id "$AMI" --instance-type "$INSTANCE_TYPE" --security-group-ids "$SG" \
  --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=150,VolumeType=gp3,DeleteOnTermination=true}' \
  --metadata-options HttpTokens=required,HttpEndpoint=enabled \
  --user-data "file://$USER_DATA" \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$STACK}]" \
  --query 'Instances[0].InstanceId' --output text)
rm -f "$USER_DATA"
echo "Launched $INSTANCE; waiting for it to boot..."
aws ec2 wait instance-running --instance-ids "$INSTANCE"
IP=$(aws ec2 describe-instances --instance-ids "$INSTANCE" --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
URL="https://${IP//./-}.sslip.io"

echo "Instance is up at $IP. Building containers and downloading models (~15-25 min on first boot)..."
for i in $(seq 1 90); do
  if curl -fsS -o /dev/null -m 5 -u "phone:$APP_PASSWORD" "$URL/api/config" 2>/dev/null; then
    echo; echo "Ready: $URL  (any username, password = APP_PASSWORD)"
    echo "Models may still be downloading for a few more minutes; the header badges turn green when the LLM and voice are up."
    exit 0
  fi
  printf .; sleep 20
done
echo; echo "Not reachable yet. Check progress with:"
echo "  aws ec2 get-console-output --instance-id $INSTANCE --latest --output text | tail -40"
echo "URL will be: $URL"
