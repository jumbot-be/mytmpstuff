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
NGINX_LOG_DIR = "/var/log/nginx"
CRUSH_FTP_LOG_DIR = "/opt/CrushFTP11"
KEYCLOAK_LOG_DIR = "/opt/adr/apcm-keycloak-bundle-rh/keycloak/data/log"
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

def is_modified_recently(file_path, max_age_seconds=3600):
    """Check if the file was modified within the last max_age_seconds."""
    try:
        mtime = os.path.getmtime(file_path)
        now = datetime.now().timestamp()
        return (now - mtime) <= max_age_seconds
    except Exception:
        return False

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

# Helper tuple structure for staged items: (staged_filepath, logsource)
staged_items = []
staged_paths = set()

def stage_file(src_path, logsource):
    if not os.path.isfile(src_path):
        return
    filename = os.path.basename(src_path)
    dest_path = os.path.join(STAGING_DIR, filename)
    log_info(f"Staging log [{logsource}]: {filename}")
    try:
        shutil.copy(src_path, dest_path)
        if dest_path not in staged_paths:
            staged_items.append((dest_path, logsource))
            staged_paths.add(dest_path)
    except Exception as e:
        log_error(f"Failed to stage log file: {filename}. Error: {e}")

# 5. Locate & Stage System Logs (syslog, auth.log, kern.log)
# System logs rotated hourly by logrotate with dateext format -%Y-%m-%d-%H
log_info(f"Scanning for newly rotated system logs in {LOG_DIR}...")
system_log_bases = ["syslog", "auth.log", "kern.log"]
for base in system_log_bases:
    for file_path in glob.glob(os.path.join(LOG_DIR, f"{base}-*")):
        # Ensure it's a rotated log (has a date extension) and was modified in the last hour
        if os.path.isfile(file_path) and is_modified_recently(file_path):
            stage_file(file_path, base)

# 5b. Locate & Stage Nginx Logs
if os.path.exists(NGINX_LOG_DIR):
    log_info(f"Scanning for newly rotated nginx logs in {NGINX_LOG_DIR}...")
    for file_path in glob.glob(os.path.join(NGINX_LOG_DIR, "*.log-*")):
        if os.path.isfile(file_path) and is_modified_recently(file_path):
            stage_file(file_path, "nginx")

# 5c. Locate & Stage CrushFTP Logs
if os.path.exists(CRUSH_FTP_LOG_DIR):
    log_info(f"Scanning for newly rotated CrushFTP logs in {CRUSH_FTP_LOG_DIR}...")
    for file_path in glob.glob(os.path.join(CRUSH_FTP_LOG_DIR, "*.log-*")):
        if os.path.isfile(file_path) and is_modified_recently(file_path):
            stage_file(file_path, "crushftp")

# 5d. Locate & Stage Keycloak Logs
if os.path.exists(KEYCLOAK_LOG_DIR):
    log_info(f"Scanning for rotated Keycloak logs in {KEYCLOAK_LOG_DIR}...")
    for file_path in glob.glob(os.path.join(KEYCLOAK_LOG_DIR, "*")):
        filename = os.path.basename(file_path)
        # Skip active log if any (e.g. keycloak.log without rotation date suffix)
        if filename == "keycloak.log":
            continue
        if os.path.isfile(file_path) and is_modified_recently(file_path):
            stage_file(file_path, "keycloak")

# 6. Locate & Stage Payara / Logback Logs
# Active logs to skip
PAYARA_ACTIVE_LOGS = {
    "server.log",
    "ampacimon.log",
    "sce-ampacimon.log",
    "sce-audit.log",
    "ampacimon-audit.log",
    "ampacimon-current-errors.log",
    "ampacimon-apx-files.log",
    "ampacimon-debug.log"
}

if os.path.exists(PAYARA_LOG_DIR):
    log_info(f"Scanning for Payara logs from the last hour in {PAYARA_LOG_DIR}...")
    for file_path in glob.glob(os.path.join(PAYARA_LOG_DIR, "*")):
        filename = os.path.basename(file_path)
        if filename in PAYARA_ACTIVE_LOGS:
            continue
        if os.path.isfile(file_path) and is_modified_recently(file_path):
            stage_file(file_path, "ADR")
else:
    log_info(f"Payara log directory {PAYARA_LOG_DIR} does not exist. Skipping Payara log scanning.")

# 6b. Check for any leftover files in STAGING_DIR from a previous failed run
existing_staged_files = [os.path.join(STAGING_DIR, f) for f in os.listdir(STAGING_DIR)]
existing_staged_files = [f for f in existing_staged_files if os.path.isfile(f)]
for f in existing_staged_files:
    if f not in staged_paths:
        filename = os.path.basename(f)
        if filename.startswith(("syslog-", "auth.log-", "kern.log-")):
            ls = filename.split("-")[0]
        elif filename.startswith("keycloak"):
            ls = "keycloak"
        elif "adr" in filename or "ampacimon" in filename or "sce" in filename:
            ls = "ADR"
        else:
            ls = "general"
        staged_items.append((f, ls))
        staged_paths.add(f)

# 7. Upload Staged Files to S3
# We upload files individually and delete them locally ONLY if upload succeeds
log_info(f"Starting uploads from {STAGING_DIR} to S3 bucket {BUCKET_NAME}...")

if not staged_items:
    log_info("No files staged for S3 upload.")
else:
    for file_path, logsource in staged_items:
        if not os.path.exists(file_path):
            continue
        filename = os.path.basename(file_path)
        
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

log_info("KISS hourly log rotation and S3 upload process complete.")
