import os
import boto3
import json
import logging
from datetime import datetime
from typing import Dict, Any

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

def export_inspector_results(event, context) -> Dict[str, Any]:
    """
    Trigger Amazon Inspector's native export functionality to generate CSV report.
    
    This function runs monthly to export all Inspector findings directly to S3
    using AWS Inspector's built-in CreateFindingsReport API.
    
    Args:
        event: Lambda event data
        context: Lambda context object
        
    Returns:
        Dict containing status code and response body
        
    Raises:
        Exception: If the export process fails
    """
    try:
        inspector_client = boto3.client('inspector2')
        sts_client = boto3.client('sts')
        
        account_id = sts_client.get_caller_identity()['Account']
        region = boto3.Session().region_name
        
        bucket_name = os.environ.get('S3_BUCKET_NAME', 'inspector-exports-bucket')
        current_date = datetime.now()
        key_prefix = f"inspector-reports/{current_date.strftime('%Y-%m')}"
        
        # KMS key ARN is required by Inspector API for report encryption
        kms_key_arn = os.environ.get(
            'KMS_KEY_ARN',
            f"arn:aws:kms:{region}:{account_id}:alias/inspector-export-key"
        )
        
        logger.info(
            "Initiating Inspector findings report export",
            extra={
                "bucket_name": bucket_name,
                "key_prefix": key_prefix,
                "kms_key_arn": kms_key_arn
            }
        )
        

        response = inspector_client.create_findings_report(
            reportFormat='CSV',
            s3Destination={
                'bucketName': bucket_name,
                'keyPrefix': key_prefix,
                'kmsKeyArn': kms_key_arn
            },
            filterCriteria={
                'findingStatus': [
                    {'comparison': 'EQUALS', 'value': 'ACTIVE'}
                ]
            }
        )
        
        report_id = response['reportId']
        
        logger.info(
            "Inspector report creation initiated successfully",
            extra={
                "report_id": report_id,
                "bucket_name": bucket_name,
                "key_prefix": key_prefix
            }
        )
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Inspector findings report export initiated successfully',
                'reportId': report_id,
                'bucket': bucket_name,
                'keyPrefix': key_prefix,
                'status': 'INITIATED',
                'timestamp': current_date.isoformat()
            })
        }
        
    except Exception as e:
        logger.error(
            "Failed to trigger Inspector export",
            extra={
                "error": str(e),
                "error_type": type(e).__name__
            },
            exc_info=True
        )
        raise
