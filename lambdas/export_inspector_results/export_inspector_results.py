import os
import boto3
import json
import logging
import time
from datetime import datetime
from typing import Dict, Any

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

def export_inspector_results(event, context) -> Dict[str, Any]:
    """
    Trigger Amazon Inspector's native export functionality to generate two separate CSV reports.
    
    This function runs monthly to export Inspector findings to S3:
    1. Production EC2 instances (filtered by VPC ID) 
    2. All other resource types excluding EC2 instances
    
    Args:
        event: Lambda event data
        context: Lambda context object
        
    Returns:
        Dict containing status code and response body with both report IDs
        
    Raises:
        Exception: If the export process fails
    """
    try:
        inspector_client = boto3.client('inspector2')
        sts_client = boto3.client('sts')
        eventbridge_client = boto3.client('events')
        
        account_id = sts_client.get_caller_identity()['Account']
        region = boto3.Session().region_name
        
        bucket_name = os.environ.get('S3_BUCKET_NAME', 'inspector-exports-bucketname')
        vpc_id = os.environ.get('VPC_ID')
        current_date = datetime.now()
        base_key_prefix = f"inspector-reports/{current_date.strftime('%Y-%m')}"
        
        kms_key_arn = os.environ.get(
            'KMS_KEY_ARN',
            f"arn:aws:kms:{region}:{account_id}:alias/inspector-export-key"
        )
        
        logger.info(
            "Initiating Inspector findings report exports",
            extra={
                "bucket_name": bucket_name,
                "base_key_prefix": base_key_prefix,
                "kms_key_arn": kms_key_arn,
                "vpc_id": vpc_id
            }
        )
        
        reports_created = []
        
        
        
        reports_to_create = []
        
        if vpc_id:
            reports_to_create.append({
                'type': 'production-ec2',
                'filter_criteria': {
                    'findingStatus': [
                        {'comparison': 'EQUALS', 'value': 'ACTIVE'}
                    ],
                    'resourceType': [
                        {'comparison': 'EQUALS', 'value': 'AWS_EC2_INSTANCE'}
                    ],
                    'ec2InstanceVpcId': [
                        {'comparison': 'EQUALS', 'value': vpc_id}
                    ]
                },
                'key_prefix': f"{base_key_prefix}/production-ec2"
            })
        
        reports_to_create.append({
            'type': 'non-ec2-resources',
            'filter_criteria': {
                'findingStatus': [
                    {'comparison': 'EQUALS', 'value': 'ACTIVE'}
                ],
                'resourceType': [
                    {'comparison': 'NOT_EQUALS', 'value': 'AWS_EC2_INSTANCE'}
                ]
            },
            'key_prefix': f"{base_key_prefix}/non-ec2-resources"
        })
        
        
        for i, report_config in enumerate(reports_to_create):
            logger.info(
                f"Creating {report_config['type']} report",
                extra={
                    "report_type": report_config['type'],
                    "key_prefix": report_config['key_prefix'],
                    "sequence": f"{i+1}/{len(reports_to_create)}"
                }
            )
            
            try:
                response = inspector_client.create_findings_report(
                    reportFormat='CSV',
                    s3Destination={
                        'bucketName': bucket_name,
                        'keyPrefix': report_config['key_prefix'],
                        'kmsKeyArn': kms_key_arn
                    },
                    filterCriteria=report_config['filter_criteria']
                )
                
                reports_created.append({
                    'type': report_config['type'],
                    'reportId': response['reportId'],
                    'keyPrefix': report_config['key_prefix']
                })
                
                logger.info(
                    f"{report_config['type']} report creation initiated",
                    extra={
                        "report_id": response['reportId'],
                        "key_prefix": report_config['key_prefix']
                    }
                )
                
                
                if i < len(reports_to_create) - 1:
                    logger.info(
                        f"Waiting for {report_config['type']} report to complete before creating next report",
                        extra={
                            "current_report_id": response['reportId'],
                            "next_report": reports_to_create[i+1]['type']
                        }
                    )
                    
                    
                    wait_for_report_completion(inspector_client, response['reportId'], report_config['type'])
                    
            except Exception as e:
                if "Cannot have multiple reports in-progress" in str(e):
                    logger.error(
                        f"Report creation failed - another report is in progress",
                        extra={
                            "failed_report_type": report_config['type'],
                            "sequence": f"{i+1}/{len(reports_to_create)}",
                            "suggestion": "Try again later when previous report completes"
                        }
                    )
                    
                    break
                else:
                    
                    raise
        
        logger.info(
            "All Inspector reports creation initiated successfully",
            extra={
                "reports_created": reports_created,
                "bucket_name": bucket_name,
                "vpc_filter": vpc_id if vpc_id else "No VPC configured"
            }
        )
        
        
        if reports_created:
            completed_files = wait_for_all_reports_completion(
                inspector_client, reports_created, bucket_name
            )
            
            if completed_files:
                
                send_inspector_reports_ready_event(
                    eventbridge_client, completed_files, bucket_name, account_id, region
                )
                
                return {
                    'statusCode': 200,
                    'body': json.dumps({
                        'message': 'Inspector findings reports export completed successfully',
                        'reports': reports_created,
                        'completedFiles': completed_files,
                        'bucket': bucket_name,
                        'status': 'COMPLETED',
                        'timestamp': current_date.isoformat()
                    })
                }
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Inspector findings reports export initiated successfully',
                'reports': reports_created,
                'bucket': bucket_name,
                'status': 'INITIATED',
                'timestamp': current_date.isoformat()
            })
        }
        
    except Exception as e:
        logger.error(
            "Failed to trigger Inspector exports",
            extra={
                "error": str(e),
                "error_type": type(e).__name__
            },
            exc_info=True
        )
        raise


def wait_for_report_completion(inspector_client, report_id, report_type):
    """
    Wait for a single Inspector report to complete
    """
    max_wait_time = 1800  
    check_interval = 30   
    start_time = time.time()
    
    logger.info(f"Waiting for {report_type} report to complete", extra={"report_id": report_id})
    
    while time.time() - start_time < max_wait_time:
        try:
            status_response = inspector_client.get_findings_report_status(
                reportId=report_id
            )
            
            status = status_response.get('status')
            logger.info(f"Report {report_type} status: {status}")
            
            if status == 'SUCCEEDED':
                logger.info(
                    f"Report {report_type} completed successfully",
                    extra={
                        "report_id": report_id,
                        "wait_time_seconds": int(time.time() - start_time)
                    }
                )
                return True
                
            elif status in ['FAILED', 'CANCELLED']:
                logger.error(f"Report {report_type} failed with status: {status}", extra={"report_id": report_id})
                return False
                
            elif status == 'IN_PROGRESS':
                logger.info(
                    f"Report {report_type} still in progress",
                    extra={
                        "report_id": report_id,
                        "status": status,
                        "elapsed_seconds": int(time.time() - start_time)
                    }
                )
                
        except Exception as e:
            logger.error(f"Error checking status for report {report_type}: {str(e)}")
            
        time.sleep(check_interval)
    
    logger.warning(f"Timeout waiting for report {report_type} to complete")
    return False


def wait_for_all_reports_completion(inspector_client, reports_created, bucket_name):
    """
    Wait for all Inspector reports to complete and return the S3 file locations
    """
    logger.info("Waiting for all reports to complete")
    
    completed_files = []
    max_wait_time = 1800  
    check_interval = 30   
    start_time = time.time()
    
    
    pending_reports = {report['reportId']: report for report in reports_created}
    
    while pending_reports and (time.time() - start_time) < max_wait_time:
        reports_to_remove = []
        
        for report_id, report in pending_reports.items():
            report_type = report['type']
            
            try:
                status_response = inspector_client.get_findings_report_status(
                    reportId=report_id
                )
                
                status = status_response.get('status')
                logger.info(
                    f"Report {report_type} status: {status}",
                    extra={
                        "report_id": report_id,
                        "elapsed_seconds": int(time.time() - start_time)
                    }
                )
                
                if status == 'SUCCEEDED':
                    destination = status_response.get('destination', {})
                    bucket = destination.get('bucketName', bucket_name)
                    key_prefix = destination.get('keyPrefix', '')
                    
                    if key_prefix:
                        s3_object_key, file_size = find_latest_inspector_report_file(bucket, key_prefix, report_id)
                        
                        if s3_object_key and file_size is not None:
                            completed_files.append({
                                'reportType': report_type,
                                'reportId': report_id,
                                's3Bucket': bucket,
                                's3Key': s3_object_key,
                                'fileName': s3_object_key.split('/')[-1],
                                'fileSize': file_size,
                                'lastModified': datetime.now().isoformat()
                            })
                            
                            logger.info(
                                f"Found completed file for {report_type}",
                                extra={
                                    "report_id": report_id,
                                    "s3_key": s3_object_key,
                                    "file_size": file_size,
                                    "file_size_mb": f"{file_size / (1024*1024):.2f}",
                                    "key_prefix": key_prefix
                                }
                            )
                        else:
                            logger.warning(
                                f"Report {report_type} completed but couldn't find file in S3",
                                extra={
                                    "report_id": report_id,
                                    "bucket": bucket,
                                    "key_prefix": key_prefix
                                }
                            )
                    else:
                        logger.warning(
                            f"Report {report_type} completed but no key prefix in response",
                            extra={"destination": destination}
                        )
                    
                    
                    reports_to_remove.append(report_id)
                    
                elif status in ['FAILED', 'CANCELLED']:
                    logger.error(
                        f"Report {report_type} failed with status: {status}",
                        extra={"report_id": report_id}
                    )
                    reports_to_remove.append(report_id)
                    
                elif status == 'IN_PROGRESS':
                    logger.info(
                        f"Report {report_type} still in progress",
                        extra={
                            "report_id": report_id,
                            "elapsed_seconds": int(time.time() - start_time)
                        }
                    )
                    
            except Exception as e:
                logger.error(
                    f"Error checking status for report {report_type}: {str(e)}",
                    extra={"report_id": report_id}
                )
        
        
        for report_id in reports_to_remove:
            del pending_reports[report_id]
        
        
        if not pending_reports:
            break
            
        
        if pending_reports:
            logger.info(
                f"Still waiting for {len(pending_reports)} reports to complete",
                extra={
                    "pending_reports": [r['type'] for r in pending_reports.values()],
                    "elapsed_seconds": int(time.time() - start_time)
                }
            )
            time.sleep(check_interval)
    
    
    if pending_reports:
        logger.warning(
            f"Timeout waiting for reports to complete",
            extra={
                "pending_reports": [r['type'] for r in pending_reports.values()],
                "total_wait_seconds": int(time.time() - start_time)
            }
        )
    
    logger.info(
        f"Completed files collection finished",
        extra={
            "total_files": len(completed_files),
            "total_reports": len(reports_created),
            "total_wait_seconds": int(time.time() - start_time)
        }
    )
    
    return completed_files


def find_latest_inspector_report_file(bucket_name, key_prefix, report_id):
    """
    Find the most recent Inspector report file in S3 using the key prefix.
    Inspector API only provides keyPrefix, not the complete objectKey.
    
    Returns:
        Tuple of (s3_key, file_size) if found, or (None, None) if not found
    """
    try:
        s3_client = boto3.client('s3')
        
        logger.info(
            f"Searching for Inspector report file",
            extra={
                "bucket": bucket_name,
                "key_prefix": key_prefix,
                "report_id": report_id
            }
        )
        
        
        response = s3_client.list_objects_v2(
            Bucket=bucket_name,
            Prefix=key_prefix
        )
        
        if 'Contents' in response:
            
            csv_files = [
                obj for obj in response['Contents'] 
                if obj['Key'].endswith('.csv')
            ]
            
            if csv_files:
                
                latest_file = max(csv_files, key=lambda x: x['LastModified'])
                
                logger.info(
                    f"Found latest Inspector report file",
                    extra={
                        "s3_key": latest_file['Key'],
                        "last_modified": latest_file['LastModified'].isoformat(),
                        "report_id": report_id,
                        "file_size": latest_file['Size']
                    }
                )
                return latest_file['Key'], latest_file['Size']
            else:
                logger.warning(
                    f"No CSV files found under prefix",
                    extra={
                        "key_prefix": key_prefix,
                        "total_objects": len(response['Contents']),
                        "all_objects": [obj['Key'] for obj in response['Contents'][:5]]  
                    }
                )
        else:
            logger.warning(
                f"No objects found under prefix",
                extra={"key_prefix": key_prefix}
            )
            
    except Exception as e:
        logger.error(
            f"Error searching for Inspector report file: {str(e)}",
            extra={
                "bucket": bucket_name,
                "key_prefix": key_prefix,
                "report_id": report_id
            },
            exc_info=True
        )
    
    return None, None


def send_inspector_reports_ready_event(eventbridge_client, completed_files, bucket_name, account_id, region):
    """
    Send custom EventBridge event when Inspector reports are ready
    """
    try:
        event_detail = {
            'source': 'inspector.export.completed',
            'reportFiles': completed_files,
            'bucket': bucket_name,
            'totalFiles': len(completed_files),
            'timestamp': datetime.now().isoformat(),
            'environment': 'management'
        }
        
        response = eventbridge_client.put_events(
            Entries=[
                {
                    'Source': 'event.inspector.export',
                    'DetailType': 'Inspector Reports Ready',
                    'Detail': json.dumps(event_detail),
                    'Resources': [
                        f"arn:aws:inspector2:{region}:{account_id}:report/*"
                    ]
                }
            ]
        )
        
        logger.info(
            "Sent Inspector reports ready event",
            extra={
                "event_detail": event_detail,
                "eventbridge_response": response
            }
        )
        
    except Exception as e:
        logger.error(f"Failed to send Inspector reports ready event: {str(e)}")
        
