import os
import boto3
import tempfile
import base64
import logging
from datetime import datetime
from urllib.parse import unquote_plus
from typing import Dict, Any
import mailchimp_transactional as MailchimpTransactional
from mailchimp_transactional.api_client import ApiClientError



logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


try:
    ssm = boto3.client("ssm")
    s3 = boto3.client("s3")

    ssm_parameter_prefix = os.environ.get(
        "SSM_PARAMETER_PREFIX",
        "/mailchimp/inspectorreport",
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
        },
    )
except Exception as e:
    logger.error(f"Couldn't load environment secrets or initialize AWS clients: {str(e)}")
    raise


def send_inspector_report(event, context) -> Dict[str, Any]:
    """
    Send Inspector report as email attachment via Mailchimp Transactional.

    Event: S3 EventBridge (Object Created) with
      event['detail']['bucket']['name']
      event['detail']['object']['key']  (URL-encoded)
    """
    try:
        bucket = event["detail"]["bucket"]["name"]
        raw_key = event["detail"]["object"]["key"]
        key = unquote_plus(raw_key)

        logger.info("Processing Inspector report email", extra={"bucket": bucket, "key": key})

        today_str = datetime.now().strftime("%Y-%m-%d")

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            s3.download_fileobj(bucket, key, tmp)
            tmp_path = tmp.name

        logger.info("Downloaded Inspector report file from S3")

        # try:
        client = MailchimpTransactional.Client(MAILCHIMP_API_KEY)
        # ping = client.users.ping()
        # logger.info("Mailchimp Transactional ping OK", extra={"ping": ping})
        # except ApiClientError as error:
        #    logger.error(f"Mailchimp Transactional connection failed: {error.text}")
        #    raise

        try:

            with open(tmp_path, "rb") as f:
                csv_bytes = f.read()
            encoded_csv = base64.b64encode(csv_bytes).decode("ascii")

            attachment_filename = f"inspector-report-{today_str}.csv"
            
            # Parse comma-separated emails for "to" recipients
            to_emails = [email.strip() for email in MAILCHIMP_TO_EMAIL.split(",") if email.strip()]
            recipients = [{"email": email, "type": "to"} for email in to_emails]
            
            # Add CC recipients if specified
            if MAILCHIMP_CC_EMAIL:
                cc_emails = [email.strip() for email in MAILCHIMP_CC_EMAIL.split(",") if email.strip()]
                recipients.extend([{"email": email, "type": "cc"} for email in cc_emails])
            

            message = {
                "from_email": MAILCHIMP_FROM_EMAIL,
                "from_name": MAILCHIMP_FROM_NAME or None,
                "to": recipients,
                "subject": f"Inspector Report - {today_str}",
                "text": (
                    f"Hello,\n\n"
                    f"Please find attached the Amazon Inspector report generated on {today_str}.\n"
                    f"Attachment: {attachment_filename}\n"
                    f"This report includes findings, affected resources, severities, and remediation notes.\n\n"
                    f"Best regards,\nDevSecOps Team"
                ),
                "tags": ["inspector-report", "security"],
                "metadata": {"report_date": today_str, "s3_key": key},
                "attachments": [
                    {
                        "type": "text/csv",
                        "name": attachment_filename,
                        "content": encoded_csv,
                    }
                ],
            }

            result = client.messages.send({"message": message})

            logger.info(
                "Inspector report email sent",
                extra={
                    "to_recipients": to_emails, 
                    "cc_recipients": cc_emails if MAILCHIMP_CC_EMAIL else [],
                    "report_filename": os.path.basename(key), 
                    "result": result
                },
            )

        except ApiClientError as e:
            logger.error(f"Mailchimp Transactional API error: {e.text}")
            raise
        except Exception as e:
            logger.error(f"Failed to send email: {str(e)}")
            raise
        finally:
            try:
                os.remove(tmp_path)
                logger.info("Cleaned up temporary file")
            except Exception:
                logger.warning("Could not remove temporary file", exc_info=True)

        return {
            "statusCode": 200,
            "body": f"Inspector report sent successfully for {os.path.basename(key)}",
        }

    except Exception as e:
        logger.error(
            "Failed to process Inspector report email",
            extra={"error": str(e), "error_type": type(e).__name__},
            exc_info=True,
        )
        raise