# Inspector Monthly Report

This is a standalone SAM template for Amazon Inspector export and email functionality that automatically generates and sends monthly security reports.

## 🏗️ Architecture

<img width="1070" height="413" alt="image" src="https://github.com/user-attachments/assets/5cb074c1-bfac-4a1f-8ca8-e2908e6e940f" />

The template creates:

- **Export Lambda**: `export_inspector_results` - Exports Inspector findings to S3 monthly
- **Email Lambda**: `send_inspector_report` - Sends CSV reports via email when files are uploaded
- **S3 Bucket**: Stores encrypted Inspector export files
- **KMS Key**: Customer-managed key for Inspector encryption
- **IAM Roles**: Proper permissions for both Lambda functions
- **EventBridge Rules**: Scheduling and S3 event triggers

## 📋 Prerequisites

1. **AWS CLI** configured with appropriate permissions
2. **SAM CLI** installed
3. **Mailchimp API Key** for sending emails
4. **Amazon Inspector** enabled in your AWS account

## 🚀 Deployment

### 1. Install Dependencies

```bash
# Ensure SAM CLI is installed
sam --version

# Navigate to the project directory
cd "Inspector Monethly Report"
```

### 2. Configure Parameters

Edit the `samconfig.toml` file to customize deployment settings if needed. The deployment script will automatically generate a unique S3 bucket name, but you can also set:

- Stack name and region preferences
- SAM deployment options

### 3. Configure Mailchimp API Key

**Option A: Set SSM parameter after deployment (Recommended)**
```bash
# Replace with your actual Mailchimp API key
aws ssm put-parameter \
  --name "/mailchimp/inspectorreport/API_KEY" \
  --value "YOUR_MAILCHIMP_API_KEY" \
  --type "SecureString" \
  --overwrite
```

### 4. Deploy Using the Script (Recommended)

The easiest way to deploy is using the provided deployment script:

```bash
# Make the script executable
chmod +x deploy.sh

# Run the deployment script
./deploy.sh
```

The deployment script will:
- ✅ Check prerequisites (AWS CLI, SAM CLI, credentials)
- 🪣 Generate a unique S3 bucket name to avoid conflicts
- 🔨 Build the SAM application
- 🚀 Deploy the stack (guided mode for first deployment)
- 📊 Display all created resources and next steps

### 5. Manual Deployment (Alternative)

If you prefer manual deployment:

```bash
# Build the application
sam build

# Deploy with guided prompts (first time)
sam deploy --guided --parameter-overrides S3BucketName=your-unique-bucket-name

# Or deploy with saved configuration
sam deploy --parameter-overrides S3BucketName=your-unique-bucket-name
```

## 🧪 Testing

### Manual Testing

1. **Test Export Function**:
   ```bash
   sam local invoke ExportInspectorResults
   ```

2. **Test Email Function**:
   ```bash
   # Create a test S3 event
   sam local invoke SendInspectorReport --event events/s3-test-event.json
   ```

### Monitoring

1. **Check CloudWatch Logs**:
   - `/aws/lambda/export_inspector_results`
   - `/aws/lambda/send_inspector_report`

2. **Monitor S3 Bucket**:
   - Check for exported CSV files
   - Verify encryption with KMS

3. **EventBridge Rules**:
   - Monthly schedule for export
   - S3 events for email triggers

## 📧 Email Configuration

The email function uses Mailchimp Transactional API with environment-specific configuration stored in AWS Systems Manager Parameter Store:

### SSM Parameters Created:
- `/mailchimp/inspectorreport/API_KEY` - Mailchimp API Key (SecureString)

### Environment Variables:
The email function uses the following environment variables configured in the template:
- **MAILCHIMP_FROM_EMAIL_PARAM**: Sender email address
- **MAILCHIMP_FROM_NAME_PARAM**: Sender display name  
- **MAILCHIMP_TO_EMAIL**: Recipient email addresses (comma-separated)
- **MAILCHIMP_CC_EMAIL**: CC email addresses (comma-separated, optional)
- **SSM_PARAMETER_PREFIX**: SSM parameter prefix for Mailchimp API key

## 🔧 Configuration

### Environment Variables

**Export Lambda**:
- `S3_BUCKET_NAME`: Target S3 bucket
- `KMS_KEY_ARN`: KMS key for encryption
- `ENVIRONMENT`: Environment name

**Email Lambda**:
- `MAILCHIMP_FROM_EMAIL_PARAM`: Sender email address
- `MAILCHIMP_FROM_NAME_PARAM`: Sender display name  
- `MAILCHIMP_TO_EMAIL`: Recipient email addresses (comma-separated)
- `MAILCHIMP_CC_EMAIL`: CC email addresses (comma-separated, optional)
- `SSM_PARAMETER_PREFIX`: SSM parameter prefix for API key

### Schedule Configuration

The export function runs monthly:
- **Cron**: `cron(0 2 1 * ? *)` (1st day of month at 2 AM UTC)
- **Timezone**: UTC

## 🔒 Security

### KMS Encryption
- Customer-managed KMS key
- Separate key per environment
- Proper IAM policies for Inspector and Lambda

### IAM Permissions
- Least privilege access
- Separate roles for each function
- No cross-environment access

### S3 Security
- Private bucket with encryption
- EventBridge notifications
- Versioning enabled

## 🔍 Troubleshooting

### Common Issues

1. **KMS Permissions**:
   ```
   Error: Access denied to KMS key
   ```
   - Check KMS key policy includes Lambda roles
   - Verify Inspector service has permissions

2. **Mailchimp API**:
   ```
   Error: Invalid API key
   ```
   - Verify SSM parameter value
   - Check Mailchimp server prefix

3. **S3 Access**:
   ```
   Error: Access denied to S3 bucket
   ```
   - Ensure bucket exists and is accessible
   - Check IAM role permissions

### Debugging

```bash
# Check function logs
sam logs -n ExportInspectorResults --stack-name inspector-export

# Tail logs in real-time
sam logs -n SendInspectorReport --stack-name inspector-export --tail
```

## 🧹 Cleanup

```bash
# Run the cleanup script
./cleanup.sh

# Or manually delete the stack
sam delete --stack-name inspector-export
```

## 📁 File Structure

```
Inspector Monethly Report/
├── template.yaml              # SAM template
├── samconfig.toml             # SAM configuration  
├── README.md                  # This file
├── deploy.sh                  # Deployment script
├── cleanup.sh                 # Cleanup script
├── lambdas/
│   ├── export_inspector_results/
│   │   ├── export_inspector_results.py
│   │   └── requirements.txt
│   └── send_inspector_report/
│       ├── send_inspector_report.py
│       └── requirements.txt
├── events/                    # Test events
│   └── s3-test-event.json
└── DEBUG/                     # Debug and test files
    ├── debug_email.py
    ├── test_email.py
    ├── run_test.py
    └── EMAIL_TEST_INSTRUCTIONS.md
```

## 🤝 Support

For issues or questions:
1. Check CloudWatch Logs
2. Review IAM permissions
3. Verify Mailchimp configuration
4. Test individual components

---

**Note**: Remember to replace placeholder values with actual configuration before deploying to production!
