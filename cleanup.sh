#!/bin/bash

# Inspector Monthly Report Cleanup Script
# Usage: ./cleanup.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "🧹 Cleaning up Inspector Monthly Report environment..."

# Check prerequisites
if ! command -v aws &> /dev/null; then
    echo "❌ AWS CLI not found. Please install AWS CLI first."
    exit 1
fi

# Check AWS credentials
if ! aws sts get-caller-identity &> /dev/null; then
    echo "❌ AWS credentials not configured. Please run 'aws configure' first."
    exit 1
fi

STACK_NAME="inspector-export"

# Check if stack exists
if ! aws cloudformation describe-stacks --stack-name $STACK_NAME &> /dev/null; then
    echo "ℹ️  Stack $STACK_NAME does not exist. Nothing to clean up."
    exit 0
fi

# Get S3 bucket name before deletion
S3_BUCKET=$(aws cloudformation describe-stacks \
    --stack-name $STACK_NAME \
    --query 'Stacks[0].Outputs[?OutputKey==`S3Bucket`].OutputValue' \
    --output text 2>/dev/null || echo "")

# Confirmation prompt
echo "⚠️  You are about to delete the Inspector Monthly Report environment!"
echo "   Stack: $STACK_NAME"
if [ -n "$S3_BUCKET" ]; then
    echo "   S3 Bucket: $S3_BUCKET"
fi
echo ""
read -p "Are you sure you want to continue? (yes/no): " -r
if [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
    echo "❌ Cleanup cancelled."
    exit 1
fi

# Empty S3 bucket if it exists
if [ -n "$S3_BUCKET" ] && [ "$S3_BUCKET" != "Not found" ]; then
    echo "🪣 Emptying S3 bucket: $S3_BUCKET"
    
    # Delete all objects and versions
    aws s3api list-object-versions --bucket "$S3_BUCKET" --output json | \
    jq -r '.Versions[] | "\(.Key) \(.VersionId)"' | \
    while read key versionId; do
        if [ "$key" != "null" ] && [ "$versionId" != "null" ]; then
            aws s3api delete-object --bucket "$S3_BUCKET" --key "$key" --version-id "$versionId" || true
        fi
    done
    
    # Delete delete markers
    aws s3api list-object-versions --bucket "$S3_BUCKET" --output json | \
    jq -r '.DeleteMarkers[] | "\(.Key) \(.VersionId)"' | \
    while read key versionId; do
        if [ "$key" != "null" ] && [ "$versionId" != "null" ]; then
            aws s3api delete-object --bucket "$S3_BUCKET" --key "$key" --version-id "$versionId" || true
        fi
    done
    
    echo "✅ S3 bucket emptied"
fi

# Delete CloudFormation stack
echo "🗂️  Deleting CloudFormation stack: $STACK_NAME"
aws cloudformation delete-stack --stack-name $STACK_NAME

echo "⏳ Waiting for stack deletion to complete..."
aws cloudformation wait stack-delete-complete --stack-name $STACK_NAME

# Clean up local files
cd "$SCRIPT_DIR"
if [ -d ".aws-sam" ]; then
    echo "🧹 Cleaning up local SAM build artifacts..."
    rm -rf .aws-sam
fi

# Remove SSM parameter
echo "🔐 Cleaning up SSM parameter..."
aws ssm delete-parameter \
    --name "/mailchimp/inspectorreport/API_KEY" \
    2>/dev/null || echo "ℹ️  SSM parameter not found or already deleted"

echo ""
echo "🎉 Cleanup completed successfully!"
echo ""
echo "📊 Summary:"
echo "  Stack: $STACK_NAME (deleted)"
if [ -n "$S3_BUCKET" ] && [ "$S3_BUCKET" != "Not found" ]; then
    echo "  S3 Bucket: $S3_BUCKET (emptied and deleted)"
fi
echo "  Local artifacts: cleaned up"
echo "  SSM parameters: cleaned up"
echo ""
echo "✅ Inspector Monthly Report environment has been completely removed!"
