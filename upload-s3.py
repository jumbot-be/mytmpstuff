#!/usr/bin/env python3
# KISS Log Rotation & S3 Upload Script
# Designed for robust, secure, and automated hourly log handling.
# Resolves security risks (uses secure staging), race conditions, and prevents data loss.

import os
import sys
import subprocess
import glob
import shutil
import socket
from datetime import datetime

# 1. Configuration
# Default bucket name; can be overridden via environment variable S3_LOG_BUCKET
BUCKET_NAME = os.environ.get("S3_LOG_BUCKET", "sdt-be-adr-dev-audit-logs")
STAGING_DIR = "/var/spool/s3-upload"
LOG_DIR = "/var/log"
PAYARA_LOG_DIR = "/opt/adr/apcm-payara-bundle/payara5/glassfish/domains/ampacimon-domain/logs"

# Dynamically retrieve server name
try:
    SERVER_NAME = socket.gethostname()
except Exception:
    SERVER_NAME = "unknown-host"

# 2. Logging Helpers
def log_info(msg):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] INFO: {msg}")
    subprocess.run(["logger", "-t", "upload-s3", f"INFO: {msg}"], check=False)

def log_error(msg):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] ERROR: {msg}", file=sys.stderr)
    subprocess.run(["logger", "-t", "upload-s3", f"ERROR: {msg}"], check=False)

log_info(f"Starting hourly S3 log upload process on {SERVER_NAME}.")

# 3. Environment Checks & Staging Area Setup
if not shutil.which("aws"):
    log_error("AWS CLI (aws) is not installed or not in PATH. Aborting.")
    sys.exit(1)

# Ensure secure staging directory exists with root-only permissions (700)
if not os.path.exists(STAGING_DIR):
    os.makedirs(STAGING_DIR, mode=0o700)
    log_info(f"Created secure staging directory at {STAGING_DIR} with 700 permissions.")
else:
    os.chmod(STAGING_DIR, 0o700)

# 4. Trigger Logrotate
# Using dedicated status file to prevent conflicts with daily/system logrotate
log_info("Triggering logrotate with hourly configuration...")
res = subprocess.run(["logrotate", "-s", "/var/lib/logrotate/hourly-status", "/etc/logrotate.d/s3-hourly-logs"], capture_output=True, text=True)
if res.returncode == 0:
    log_info("Logrotate successfully ran.")
else:
    log_error(f"Logrotate encountered errors, but continuing to process staged/rotated files. Error: {res.stderr}")

# 5. Locate & Stage System Logs (syslog, auth.log, kern.log)
# System logs rotated hourly are formatted with SCETMP-%Y-%m-%d-%H
# We look for files matching *-SCETMP-*
log_info(f"Scanning for newly rotated system logs in {LOG_DIR}...")
for file_path in glob.glob(os.path.join(LOG_DIR, "*-SCETMP-*")):
    if os.path.isfile(file_path):
        filename = os.path.basename(file_path)
        # Extract base log name (e.g., syslog)
        logsource = filename.split("-SCETMP-")[0]
        # Create target filename with SCE instead of SCETMP
        target_filename = filename.replace("-SCETMP-", "-SCE-")
        
        log_info(f"Staging system log: {filename} -> {target_filename}")
        try:
            shutil.copy(file_path, os.path.join(STAGING_DIR, target_filename))
            os.rename(file_path, os.path.join(LOG_DIR, target_filename))
        except Exception as e:
            log_error(f"Failed to stage system log file: {filename}. Error: {e}")

# 6. Locate & Stage Payara / Logback Logs
# Payara active logs are apcm.log, apcm-debug.log, apcm.logs, apcm-debug.logs
# We process already rotated logs (which contain rotation/date suffixes)
if os.path.exists(PAYARA_LOG_DIR):
    log_info(f"Scanning for rotated Payara logs in {PAYARA_LOG_DIR}...")
    for file_path in glob.glob(os.path.join(PAYARA_LOG_DIR, "apcm*")):
        if os.path.isfile(file_path):
            filename = os.path.basename(file_path)
            
            # Skip active logs
            if filename in ["apcm.log", "apcm-debug.log", "apcm.logs", "apcm-debug.logs"]:
                continue
            
            # Skip already processed logs
            if filename.endswith(".uploaded"):
                continue
            
            log_info(f"Staging Payara log: {filename}")
            try:
                shutil.copy(file_path, os.path.join(STAGING_DIR, filename))
                os.rename(file_path, file_path + ".uploaded")
            except Exception as e:
                log_error(f"Failed to stage Payara log file: {filename}. Error: {e}")
else:
    log_info(f"Payara log directory {PAYARA_LOG_DIR} does not exist. Skipping Payara log scanning.")

# 7. Upload Staged Files to S3
# We upload files individually and delete them locally ONLY if upload succeeds
log_info(f"Starting uploads from {STAGING_DIR} to S3 bucket {BUCKET_NAME}...")
staged_files = [os.path.join(STAGING_DIR, f) for f in os.listdir(STAGING_DIR)]
staged_files = [f for f in staged_files if os.path.isfile(f)]

if not staged_files:
    log_info("No files staged for S3 upload.")
else:
    for file_path in staged_files:
        filename = os.path.basename(file_path)
        
        # Determine correct S3 logsource category
        if "-SCE-" in filename:
            logsource = filename.split("-SCE-")[0]
        elif filename.startswith("apcm-debug"):
            logsource = "apcm-debug"
        elif filename.startswith("apcm"):
            logsource = "apcm"
        else:
            logsource = "general"
        
        # Construct destination S3 URI: logs/$Servername/$logsource/filename
        s3_uri = f"s3://{BUCKET_NAME}/logs/{SERVER_NAME}/{logsource}/{filename}"
        
        log_info(f"Uploading {filename} to {s3_uri}")
        upload_res = subprocess.run(["aws", "s3", "cp", file_path, s3_uri], capture_output=True, text=True)
        if upload_res.returncode == 0:
            log_info(f"Successfully uploaded {filename}. Removing local staged copy.")
            try:
                os.remove(file_path)
            except Exception as e:
                log_error(f"Failed to remove local staged file {filename}: {e}")
        else:
            log_error(f"Failed to upload {filename} to S3. Retaining local staged file for retry. Error: {upload_res.stderr}")

# 8. Local Retention Cleanup (Delete logs older than 7 days)
log_info("Running local retention cleanup for historical logs...")

now_ts = datetime.now().timestamp()
seven_days_sec = 7 * 24 * 60 * 60

# Clean up processed system logs in LOG_DIR older than 7 days
for file_path in glob.glob(os.path.join(LOG_DIR, "*-SCE-*")):
    try:
        if os.path.isfile(file_path) and (now_ts - os.path.getmtime(file_path)) > seven_days_sec:
            os.remove(file_path)
    except Exception:
        pass

# Clean up processed Payara logs older than 7 days
if os.path.exists(PAYARA_LOG_DIR):
    for file_path in glob.glob(os.path.join(PAYARA_LOG_DIR, "*.uploaded")):
        try:
            if os.path.isfile(file_path) and (now_ts - os.path.getmtime(file_path)) > seven_days_sec:
                os.remove(file_path)
        except Exception:
            pass

log_info("KISS hourly log rotation and S3 upload process complete.")
