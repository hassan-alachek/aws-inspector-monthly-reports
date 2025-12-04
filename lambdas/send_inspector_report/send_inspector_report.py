import os
import boto3
import tempfile
import base64
import logging
import gc
import gzip
import io
from datetime import datetime
from typing import Dict, Any, List, Tuple
import mailchimp_transactional as MailchimpTransactional
from mailchimp_transactional.api_client import ApiClientError



logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


try:
    ssm = boto3.client("ssm")
    s3 = boto3.client("s3")

    ssm_parameter_prefix = os.environ.get(
        "SSM_PARAMETER_PREFIX",
        "",
    )

    mailchimp_api_key_param = ssm.get_parameter(
        Name=f"{ssm_parameter_prefix}/API_KEY",
        WithDecryption=True,
    )
    MAILCHIMP_API_KEY = mailchimp_api_key_param["Parameter"]["Value"]

    MAILCHIMP_FROM_EMAIL = os.environ["MAILCHIMP_FROM_EMAIL_PARAM"]
    MAILCHIMP_FROM_NAME = os.environ.get("MAILCHIMP_FROM_NAME_PARAM", "")
    MAILCHIMP_TO_EMAIL = os.environ["MAILCHIMP_TO_EMAIL"]
    MAILCHIMP_CC_EMAIL = os.environ.get("MAILCHIMP_CC_EMAIL", "")

    logger.info(
        "Loaded configuration",
        extra={
            "ssm_parameter_prefix": ssm_parameter_prefix,
            "from_email": MAILCHIMP_FROM_EMAIL,
            "to_email": MAILCHIMP_TO_EMAIL,
            "cc_email": MAILCHIMP_CC_EMAIL,
        },
    )
except Exception as e:
    logger.error(f"Couldn't load environment secrets or initialize AWS clients: {str(e)}")
    raise


def process_file_in_chunks(s3_bucket: str, s3_key: str, chunk_size: int = 1024 * 1024) -> str:
    """
    Process large S3 files in chunks to avoid memory issues.
    Returns base64 encoded content.
    """
    logger.info(f"Processing file in chunks: s3://{s3_bucket}/{s3_key}")
    
    # Get file size first
    try:
        response = s3.head_object(Bucket=s3_bucket, Key=s3_key)
        file_size = response['ContentLength']
        logger.info(f"File size: {file_size} bytes")
    except Exception as e:
        logger.error(f"Failed to get file size: {str(e)}")
        raise
    
    # Use streaming approach for large files
    if file_size > 10 * 1024 * 1024:  # 10MB threshold
        logger.info("Using streaming approach for large file")
        return process_large_file_streaming(s3_bucket, s3_key, file_size)
    else:
        logger.info("Using standard approach for smaller file")
        return process_small_file(s3_bucket, s3_key)


def process_small_file(s3_bucket: str, s3_key: str) -> str:
    """Process smaller files using standard approach."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.csv') as tmp:
        s3.download_fileobj(s3_bucket, s3_key, tmp)
        tmp_path = tmp.name
    
    try:
        with open(tmp_path, "rb") as f:
            csv_bytes = f.read()
        return base64.b64encode(csv_bytes).decode("ascii")
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            logger.warning(f"Could not remove temporary file: {tmp_path}")


def process_large_file_streaming(s3_bucket: str, s3_key: str, file_size: int) -> str:
    """
    Process large files using streaming to minimize memory usage.
    """
    logger.info(f"Streaming large file: {file_size} bytes")
    
    # Use streaming response
    response = s3.get_object(Bucket=s3_bucket, Key=s3_key)
    stream = response['Body']
    
    # Process in chunks
    chunk_size = 1024 * 1024  # 1MB chunks
    encoded_chunks = []
    
    try:
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                break
            
            # Encode chunk and add to list
            encoded_chunk = base64.b64encode(chunk).decode("ascii")
            encoded_chunks.append(encoded_chunk)
            
            # Log progress for large files
            if len(encoded_chunks) % 10 == 0:  # Every 10MB
                processed = len(encoded_chunks) * chunk_size
                logger.info(f"Processed {processed}/{file_size} bytes ({processed/file_size*100:.1f}%)")
    
    except Exception as e:
        logger.error(f"Error streaming file: {str(e)}")
        raise
    
    # Combine all chunks
    logger.info(f"Combining {len(encoded_chunks)} chunks")
    return ''.join(encoded_chunks)


def compress_csv_content(csv_bytes: bytes) -> Tuple[bytes, float]:
    """
    Compress CSV content using gzip.
    Returns compressed bytes and compression ratio.
    """
    original_size = len(csv_bytes)
    
    # Compress using gzip
    compressed = io.BytesIO()
    with gzip.GzipFile(fileobj=compressed, mode='wb', compresslevel=9) as gz:
        gz.write(csv_bytes)
    
    compressed_bytes = compressed.getvalue()
    compressed_size = len(compressed_bytes)
    compression_ratio = (1 - compressed_size / original_size) * 100
    
    logger.info(
        f"Compression complete",
        extra={
            "original_size_bytes": original_size,
            "original_size_mb": f"{original_size / (1024*1024):.2f}",
            "compressed_size_bytes": compressed_size,
            "compressed_size_mb": f"{compressed_size / (1024*1024):.2f}",
            "compression_ratio": f"{compression_ratio:.1f}%"
        }
    )
    
    return compressed_bytes, compression_ratio


def should_compress_file(file_size: int, mailchimp_limit_mb: int = 18) -> bool:
    """
    Determine if a file should be compressed based on size.
    Mailchimp has a 25MB total message limit, but we use 18MB as safe threshold
    to account for base64 encoding overhead (33% increase) and email headers.
    
    18MB * 1.33 (base64) ≈ 24MB, leaving room for headers
    """
    threshold_bytes = mailchimp_limit_mb * 1024 * 1024
    return file_size > threshold_bytes


def get_file_size_from_s3_or_event(report_file: Dict[str, Any], s3_bucket: str, s3_key: str, report_type: str) -> int:
    """
    Get file size from event or fetch from S3 if not provided.
    
    Args:
        report_file: The report file dictionary from the event
        s3_bucket: S3 bucket name
        s3_key: S3 object key
        report_type: Type of report (for logging)
        
    Returns:
        File size in bytes
        
    Raises:
        Exception: If unable to retrieve file size from either source
    """
    file_size = report_file.get('fileSize', 0)
    
    if file_size == 0:
        logger.info(
            f"File size not provided in event, fetching from S3",
            extra={
                "report_type": report_type,
                "s3_bucket": s3_bucket,
                "s3_key": s3_key
            }
        )
        
        try:
            response = s3.head_object(Bucket=s3_bucket, Key=s3_key)
            file_size = response['ContentLength']
            
            logger.info(
                f"Retrieved file size from S3: {file_size} bytes ({file_size / (1024*1024):.2f} MB)",
                extra={
                    "report_type": report_type,
                    "s3_bucket": s3_bucket,
                    "s3_key": s3_key
                }
            )
        except Exception as e:
            logger.error(
                f"Failed to get file size from S3 for {report_type}: {str(e)}",
                extra={
                    "s3_bucket": s3_bucket,
                    "s3_key": s3_key,
                    "error_type": type(e).__name__
                },
                exc_info=True
            )
            raise
    else:
        logger.info(
            f"Using file size from event: {file_size} bytes ({file_size / (1024*1024):.2f} MB)",
            extra={"report_type": report_type}
        )
    
    return file_size


def send_inspector_report(event, context) -> Dict[str, Any]:
    """
    Send Inspector reports as email attachments via Mailchimp Transactional.
    
    This function is triggered by EventBridge when Inspector reports are ready.
    The event contains the exact files to send, eliminating duplicate emails
    and ensuring only newly created files are processed.

    Optimized version that handles large files efficiently by:
    1. Processing files one at a time
    2. Using streaming for large files
    3. Cleaning up memory between files
    4. Compressing large files (>18MB) with gzip to stay within Mailchimp's 25MB limit

    Event: EventBridge custom event with
      event['detail']['reportFiles'] - array of completed report files
      event['detail']['bucket'] - S3 bucket name
      event['detail']['testMode'] - (optional) boolean to enable test mode
      event['detail']['testToEmail'] - (optional) comma-separated test email addresses
      event['detail']['testCcEmail'] - (optional) comma-separated test CC email addresses
    
    Test Mode:
    When testMode=true, the function will:
    - Use testToEmail instead of environment variable for recipients
    - Add [TEST] prefix to email subject
    - Include test indicators in email content
    - Add "test" tag to email metadata
    
    File Compression:
    Files larger than 18MB are automatically compressed with gzip to ensure email delivery.
    Compressed files have .csv.gz extension and can be opened with any ZIP/GZIP tool.
    """
    try:

        event_detail = event.get('detail', {})
        logger.info(f"Event detail: {event_detail}")
        report_files = event_detail.get('reportFiles', [])
        logger.info(f"Report files: {report_files}")
        bucket = event_detail.get('bucket', '')
        environment = event_detail.get('environment', 'management')
        
        # Check for test mode
        is_test_mode = event_detail.get('testMode', False)
        test_to_email = event_detail.get('testToEmail', '')
        test_cc_email = event_detail.get('testCcEmail', '')
        
        logger.info(f"Environment: {environment}")
        logger.info(f"Bucket: {bucket}")
        logger.info(f"Test mode: {is_test_mode}")
        
        if is_test_mode:
            logger.info(f"Test TO email: {test_to_email}")
            logger.info(f"Test CC email: {test_cc_email}")
        
        
        if not report_files:
            logger.warning("No report files provided in event")
            return {"statusCode": 200, "body": "No report files to process"}
        
        logger.info(
            "Processing Inspector reports from EventBridge",
            extra={
                "bucket": bucket,
                "total_files": len(report_files),
                "environment": environment,
                "report_files": report_files
            }
        )

        today_str = datetime.now().strftime("%Y-%m-%d")
        
        
        attachments = []
        processed_count = 0
        failed_files = []
        total_files = len(report_files)
        
        for report_file in report_files:
            try:
                report_type = report_file.get('reportType')
                s3_bucket = report_file.get('s3Bucket')
                s3_key = report_file.get('s3Key')
                file_name = report_file.get('fileName')
                
                logger.info(
                    f"Processing {report_type} report ({processed_count + 1}/{total_files})",
                    extra={
                        "s3_bucket": s3_bucket,
                        "s3_key": s3_key,
                        "file_name": file_name,
                        "file_size": report_file.get('fileSize', 0)
                    }
                )
                
                if not s3_key or not s3_bucket:
                    logger.error(f"Missing S3 information for {report_type} report", extra={"report_file": report_file})
                    failed_files.append(f"{report_type} (missing S3 info)")
                    continue
                
                try:
                    file_size = get_file_size_from_s3_or_event(report_file, s3_bucket, s3_key, report_type)
                except Exception as e:
                    logger.error(f"Cannot proceed without file size for {report_type}")
                    failed_files.append(f"{report_type} (failed to get file size)")
                    continue
                
                needs_compression = should_compress_file(file_size)
                
                if needs_compression:
                    logger.info(f"File size ({file_size / (1024*1024):.2f} MB) exceeds threshold, will compress")
                    
                    # For compression, download the complete file first (not chunked Base64)
                    logger.info("Downloading file for compression...")
                    with tempfile.NamedTemporaryFile(delete=False, suffix='.csv') as tmp:
                        s3.download_fileobj(s3_bucket, s3_key, tmp)
                        tmp_path = tmp.name
                    
                    try:
                        # Read the complete file
                        with open(tmp_path, "rb") as f:
                            csv_bytes = f.read()
                        
                        # Compress it
                        logger.info("Compressing CSV file...")
                        compressed_bytes, compression_ratio = compress_csv_content(csv_bytes)
                        
                        # Encode the compressed data
                        encoded_csv = base64.b64encode(compressed_bytes).decode("ascii")
                        
                        # Clean up temp file
                        os.remove(tmp_path)
                    except Exception as e:
                        # Clean up on error
                        try:
                            os.remove(tmp_path)
                        except:
                            pass
                        raise
                else:
                    # For small files, use the existing chunked approach
                    encoded_csv = process_file_in_chunks(s3_bucket, s3_key)
                
                # Set filename and MIME type based on compression
                if needs_compression:
                    # Compressed file
                    if report_type == 'production-ec2':
                        attachment_filename = f"inspector-production-ec2-{today_str}.csv.gz"
                    elif report_type == 'non-ec2-resources':
                        attachment_filename = f"inspector-non-ec2-resources-{today_str}.csv.gz"
                    else:
                        attachment_filename = f"inspector-{report_type}-{today_str}.csv.gz"
                    mime_type = "application/gzip"
                else:
                    # Uncompressed file
                    if report_type == 'production-ec2':
                        attachment_filename = f"inspector-production-ec2-{today_str}.csv"
                    elif report_type == 'non-ec2-resources':
                        attachment_filename = f"inspector-non-ec2-resources-{today_str}.csv"
                    else:
                        attachment_filename = f"inspector-{report_type}-{today_str}.csv"
                    mime_type = "text/csv"
                
                attachments.append({
                    "type": mime_type,
                    "name": attachment_filename,
                    "content": encoded_csv,
                })
                
                logger.info(
                    f"Prepared attachment: {attachment_filename}", 
                    extra={
                        "attachment_size_bytes": len(encoded_csv),
                        "report_type": report_type,
                        "total_attachments_so_far": len(attachments)
                    }
                )
                
                processed_count += 1
                

                gc.collect()
                
            except Exception as e:
                logger.error(f"Failed to process {report_type} report: {str(e)}", exc_info=True)
                failed_files.append(f"{report_type} (error: {str(e)})")
                continue

        # Check if all files were processed successfully
        if failed_files:
            logger.error(
                "Some files failed to process - not sending email notification",
                extra={
                    "total_files": total_files,
                    "processed_files": processed_count,
                    "failed_files": failed_files,
                    "success_rate": f"{processed_count}/{total_files}"
                }
            )
            return {
                "statusCode": 500,
                "body": f"Failed to process {len(failed_files)} out of {total_files} files. No email sent. Failed files: {', '.join(failed_files)}"
            }

        if not attachments:
            logger.warning("No valid attachments prepared")
            return {"statusCode": 200, "body": "No reports to send"}

        logger.info(
            "Successfully processed all Inspector report files from S3",
            extra={
                "total_attachments_prepared": len(attachments),
                "attachment_names": [att['name'] for att in attachments],
                "total_size_bytes": sum(len(att['content']) for att in attachments),
                "success_rate": f"{processed_count}/{total_files}"
            }
        )

        # Send email
        client = MailchimpTransactional.Client(MAILCHIMP_API_KEY)
        
        # Determine email recipients based on test mode
        if is_test_mode:
            if not test_to_email:
                logger.error("Test mode enabled but no testToEmail provided in event")
                return {
                    "statusCode": 400,
                    "body": "Test mode enabled but testToEmail is required in event"
                }
            
            to_emails = [email.strip() for email in test_to_email.split(",") if email.strip()]
            recipients = [{"email": email, "type": "to"} for email in to_emails]
            
            if test_cc_email:
                cc_emails = [email.strip() for email in test_cc_email.split(",") if email.strip()]
                recipients.extend([{"email": email, "type": "cc"} for email in cc_emails])
            
            logger.info(f"Using TEST email configuration: TO={to_emails}, CC={test_cc_email if test_cc_email else 'None'}")
        else:
            to_emails = [email.strip() for email in MAILCHIMP_TO_EMAIL.split(",") if email.strip()]
            recipients = [{"email": email, "type": "to"} for email in to_emails]
            
            if MAILCHIMP_CC_EMAIL:
                cc_emails = [email.strip() for email in MAILCHIMP_CC_EMAIL.split(",") if email.strip()]
                recipients.extend([{"email": email, "type": "cc"} for email in cc_emails])
            
            logger.info(f"Using PRODUCTION email configuration: TO={to_emails}, CC={MAILCHIMP_CC_EMAIL if MAILCHIMP_CC_EMAIL else 'None'}")
            
        
        attachment_list = "\n".join([f"- {att['name']}" for att in attachments])
        
        # Check if any files are compressed
        has_compressed = any(att['name'].endswith('.gz') for att in attachments)
        compression_note = ""
        if has_compressed:
            compression_note = "\n\nNote: Large files have been compressed (.csv.gz). Extract them using any ZIP/GZIP tool:\n- Windows: 7-Zip, WinRAR, or built-in extraction\n- Mac: Double-click the file\n- Linux: gunzip command or Archive Manager\n"
        
        # Customize subject and content for test mode
        if is_test_mode:
            subject = f"[TEST] Inspector Reports - {today_str}"
            email_text = (
                f"Hello,\n\n"
                f"**THIS IS A TEST EMAIL**\n\n"
                f"Please find attached the Amazon Inspector reports generated on {today_str}.\n\n"
                f"Attachments:\n{attachment_list}{compression_note}\n"
                f"These reports include:\n"
                f"1. Production EC2 instances findings\n"
                f"2. All other AWS resource types findings (Lambda, ECR, etc.)\n\n"
                f"Each report contains findings, affected resources, severities, and remediation notes.\n\n"
                f"**Note: This email was sent in TEST MODE for verification purposes.**\n\n"
                f"Best regards,\nNBK Capital Smart Wealth DevSecOps Team"
            )
            tags = ["inspector-report", "security", "multi-report", "test"]
        else:
            subject = f"Inspector Reports - {today_str}"
            email_text = (
                f"Hello,\n\n"
                f"Please find attached the Amazon Inspector reports generated on {today_str}.\n\n"
                f"Attachments:\n{attachment_list}{compression_note}\n"
                f"These reports include:\n"
                f"1. Production EC2 instances findings\n"
                f"2. All other AWS resource types findings (Lambda, ECR, etc.)\n\n"
                f"Each report contains findings, affected resources, severities, and remediation notes.\n\n"
                f"Best regards,\nNBK Capital Smart Wealth DevSecOps Team"
            )
            tags = ["inspector-report", "security", "multi-report"]
        
        message = {
            "from_email": MAILCHIMP_FROM_EMAIL,
            "from_name": MAILCHIMP_FROM_NAME or None,
            "to": recipients,
            "subject": subject,
            "text": email_text,
            "tags": tags,
            "metadata": {
                "report_date": today_str, 
                "environment": environment,
                "attachment_count": len(attachments),
                "test_mode": is_test_mode
            },
            "attachments": attachments,
        }

        result = client.messages.send({"message": message})

        # Parse email delivery states from Mailchimp response
        email_states = {}
        for email_result in result:
            email_address = email_result.get('email', 'unknown')
            status = email_result.get('status', 'unknown')
            message_id = email_result.get('_id', 'N/A')
            reject_reason = email_result.get('reject_reason', None)
            
            email_states[email_address] = {
                'status': status,
                'message_id': message_id,
                'reject_reason': reject_reason
            }
            
            # Log individual email status
            if status == 'sent':
                logger.info(f"Email SENT to {email_address} (ID: {message_id})")
            elif status == 'queued':
                logger.info(f"Email QUEUED for {email_address} (ID: {message_id})")
            elif status == 'rejected':
                logger.warning(f"Email REJECTED for {email_address} - Reason: {reject_reason} (ID: {message_id})")
            elif status == 'invalid':
                logger.error(f"Email INVALID for {email_address} - Reason: {reject_reason} (ID: {message_id})")
            else:
                logger.info(f"Email status '{status}' for {email_address} (ID: {message_id})")

        logger.info(
            "Inspector reports email sent to Mailchimp",
            extra={
                "to_recipients": to_emails, 
                "cc_recipients": cc_emails if (test_cc_email if is_test_mode else MAILCHIMP_CC_EMAIL) else [],
                "attachment_count": len(attachments),
                "test_mode": is_test_mode,
                "email_states": email_states,
                "result": result
            },
        )

        return {
            "statusCode": 200,
            "body": f"Inspector reports sent successfully with {len(attachments)} attachments" + (" (TEST MODE)" if is_test_mode else ""),
        }

    except ApiClientError as e:
        logger.error(f"Mailchimp Transactional API error: {e.text}")
        raise
    except Exception as e:
        logger.error(
            "Failed to process Inspector report email",
            extra={"error": str(e), "error_type": type(e).__name__},
            exc_info=True,
        )
        raise
