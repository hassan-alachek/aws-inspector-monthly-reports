#!/bin/bash

# Inspector Export Test Environment Deployment Script
# Usage: ./deploy.sh [environment]
# Environment options: test (default), staging, prod

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "🚀 Deploying Inspector Export..."

# Check prerequisites
echo "🔍 Checking prerequisites..."

if ! command -v sam &> /dev/null; then
    echo "❌ SAM CLI not found. Please install SAM CLI first."
    exit 1
fi

if ! command -v aws &> /dev/null; then
    echo "❌ AWS CLI not found. Please install AWS CLI first."
    exit 1
fi

# Check AWS credentials
if ! aws sts get-caller-identity &> /dev/null; then
    echo "❌ AWS credentials not configured. Please run 'aws configure' first."
    exit 1
fi

echo "✅ Prerequisites check passed"

# Navigate to script directory
cd "$SCRIPT_DIR"

# Generate unique bucket name for test environment
if [ "$ENVIRONMENT" = "test" ]; then
    TIMESTAMP=$(date +%s)
    BUCKET_NAME="inspector-exports-test-$TIMESTAMP"
    echo "🪣 Using unique bucket name: $BUCKET_NAME"
    
    # Update samconfig.toml with unique bucket name
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS
        sed -i '' "s/S3BucketName=inspector-exports-test-unique/S3BucketName=$BUCKET_NAME/" samconfig.toml
    else
        # Linux
        sed -i "s/S3BucketName=inspector-exports-test-unique/S3BucketName=$BUCKET_NAME/" samconfig.toml
    fi
fi

# Build the application
echo "🔨 Building SAM application..."
sam build

# Deploy the stack
echo "🚀 Deploying..."

STACK_NAME="inspector-export-test"

# Check if stack already exists
if aws cloudformation describe-stacks --stack-name $STACK_NAME &> /dev/null; then
    echo "📋 Stack exists - updating..."
    sam deploy
else
    echo "📋 First deployment - using guided mode..."
    sam deploy --guided
fi

# Get stack outputs
echo "📋 Getting stack outputs..."

EXPORT_FUNCTION=$(aws cloudformation describe-stacks \
    --stack-name $STACK_NAME \
    --query 'Stacks[0].Outputs[?OutputKey==`ExportInspectorResultsFunction`].OutputValue' \
    --output text 2>/dev/null || echo "Not found")

EMAIL_FUNCTION=$(aws cloudformation describe-stacks \
    --stack-name $STACK_NAME \
    --query 'Stacks[0].Outputs[?OutputKey==`SendInspectorReportFunction`].OutputValue' \
    --output text 2>/dev/null || echo "Not found")

S3_BUCKET=$(aws cloudformation describe-stacks \
    --stack-name $STACK_NAME \
    --query 'Stacks[0].Outputs[?OutputKey==`S3Bucket`].OutputValue' \
    --output text 2>/dev/null || echo "Not found")

KMS_KEY=$(aws cloudformation describe-stacks \
    --stack-name $STACK_NAME \
    --query 'Stacks[0].Outputs[?OutputKey==`KMSKey`].OutputValue' \
    --output text 2>/dev/null || echo "Not found")

# Display results
echo ""
echo "🎉 Deployment completed successfully!"
echo ""
echo "📊 Stack Information:"
echo "  Stack Name: $STACK_NAME"
echo "  Region: $(aws configure get region)"
echo ""
echo "🔧 Resources Created:"
echo "  Export Function: $EXPORT_FUNCTION"
echo "  Email Function: $EMAIL_FUNCTION"
echo "  S3 Bucket: $S3_BUCKET"
echo "  KMS Key: $KMS_KEY"
echo ""
echo "📋 Next Steps:"
echo "  1. Set up Mailchimp API Key (if not done already):"
echo "     ./setup-mailchimp.sh [your-api-key]"
echo ""
echo "  2. Monitor logs:"
echo "     sam logs -n ExportInspectorResults --stack-name $STACK_NAME --tail"
echo ""
echo "  3. Test the functions:"
echo "     sam local invoke ExportInspectorResults"
echo "     sam local invoke SendInspectorReport --event events/s3-test-event.json"
echo ""
echo "✅ Inspector Export Test Environment is ready!"
