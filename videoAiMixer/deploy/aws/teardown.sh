#!/usr/bin/env bash
# Deletes everything deploy.sh created (instance, security group, deploy bucket). Avatar data on the instance is lost.
set -euo pipefail
export AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}" AWS_DEFAULT_REGION="${AWS_REGION}"
STACK="${STACK:-video-ai-mixer}"
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)

IDS=$(aws ec2 describe-instances --filters Name=tag:Name,Values="$STACK" Name=instance-state-name,Values=pending,running,stopping,stopped \
  --query 'Reservations[].Instances[].InstanceId' --output text)
if [ -n "$IDS" ]; then
  echo "Terminating $IDS"
  aws ec2 terminate-instances --instance-ids $IDS >/dev/null
  aws ec2 wait instance-terminated --instance-ids $IDS
fi
SG=$(aws ec2 describe-security-groups --filters Name=group-name,Values="$STACK" --query 'SecurityGroups[0].GroupId' --output text)
[ "$SG" != "None" ] && aws ec2 delete-security-group --group-id "$SG" && echo "Deleted security group $SG"
BUCKET="${STACK}-deploy-${ACCOUNT}-${AWS_REGION}"
if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  aws s3 rb "s3://$BUCKET" --force >/dev/null && echo "Deleted bucket $BUCKET"
fi
echo "Done."
