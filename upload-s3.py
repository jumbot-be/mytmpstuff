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
import argparse
import re
from datetime import datetime, timedelta

# 1. Configuration
BUCKET_NAME = os.environ.get("S3_LOG_BUCKET", "sdt-be-adr-dev-audit-logs")
STAGING_DIR = "/var/spool/s3-upload"
PAYARA_LOG_DIR = "/opt/adr/apcm-payara-bundle/payara5/glassfish/domains/ampacimon-domain/logs"

# Dynamically retrieve server name
try:
    SERVER_NAME = socket.gethostname()
except Exception:
    SERVER_NAME = "unknown-host"

# Base log patterns for services
LOG_PATTERNS = [
    "/var/log/syslog",
    "/var/log/auth.log",
    "/var/log/kern.log",
    "/var/log/nginx/*.log",
    "/opt/CrushFTP11/*.log"
]

# 2. Argument Parsing
parser = argparse.ArgumentParser(description="KISS Log Rotation & S3 Upload Script")
parser.add_argument("-d", "--debug", action="store_true", help="Run in debug mode (no upload, only displays staging content and cleans up)")
args = parser.parse_args()
DEBUG_MODE = args.debug

# 3. Logging Helpers
def log_info(msg):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    prefix = "[DEBUG] " if DEBUG_MODE else ""
    print(f"[{timestamp}] {prefix}INFO: {msg}")
    if not DEBUG_MODE:
        subprocess.run(["logger", "-t", "upload-s3", f"INFO: {msg}"], check=False)

def log_error(msg):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    prefix = "[DEBUG] " if DEBUG_MODE else ""
    print(f"[{timestamp}] {prefix}ERROR: {msg}", file=sys.stderr)
    if not DEBUG_MODE:
        subprocess.run(["logger", "-t", "upload-s3", f"ERROR: {msg}"], check=False)

log_info(f"Starting hourly S3 log upload process on {SERVER_NAME}.")

# 4. Environment Checks & Staging Area Setup
if not DEBUG_MODE and not shutil.which("aws"):
    log_error("AWS CLI (aws) is not installed or not in PATH. Aborting.")
    sys.exit(1)

# Ensure secure staging directory exists with root-only permissions (700)
if not os.path.exists(STAGING_DIR):
    try:
        os.makedirs(STAGING_DIR, mode=0o700)
        log_info(f"Created secure staging directory at {STAGING_DIR} with 700 permissions.")
    except Exception as e:
        log_error(f"Failed to create staging directory {STAGING_DIR}: {e}")
        sys.exit(1)
else:
    try:
        os.chmod(STAGING_DIR, 0o700)
    except Exception as e:
        log_error(f"Failed to chmod staging directory {STAGING_DIR}: {e}")

# 5. Trigger Logrotate
# In debug mode, we do NOT trigger actual logrotate since it modifies state
if not DEBUG_MODE:
    log_info("Triggering logrotate with hourly configuration...")
    res = subprocess.run(["logrotate", "-s", "/var/lib/logrotate/hourly-status", "/etc/logrotate.d/hourly/all-logs"], capture_output=True, text=True)
    if res.returncode == 0:
        log_info("Logrotate successfully ran.")
    else:
        log_error(f"Logrotate encountered errors, but continuing to process. Error: {res.stderr}")
else:
    log_info("Debug mode: skipping logrotate execution.")

# 6. Locate & Stage System and Service Logs (via .1 rotated files)
log_info("Scanning for newly rotated .1 log files...")

# Expand glob patterns to find active and rotated log files
target_files = []
for pattern in LOG_PATTERNS:
    if "*" in pattern:
        matched = glob.glob(pattern)
        for filepath in matched:
            if os.path.isfile(filepath):
                target_files.append(filepath)
    else:
        if os.path.isfile(pattern):
            target_files.append(pattern)

# For each target file, we check if its .1 rotated companion exists
for filepath in target_files:
    rotated_path = filepath + ".1"
    if os.path.isfile(rotated_path):
        filename = os.path.basename(rotated_path)
        base_log_name = os.path.basename(filepath)

        # Get the mtime of the .1 file
        try:
            mtime_ts = os.path.getmtime(rotated_path)
            mtime_dt = datetime.fromtimestamp(mtime_ts)
        except Exception as e:
            log_error(f"Failed to read modification time of {rotated_path}: {e}")
            continue

        # Compute the "last hour of logs" by subtracting 1 hour from rotation completion time
        log_hour_dt = mtime_dt - timedelta(hours=1)
        dt_str = log_hour_dt.strftime("%Y-%m-%d-%H")

        # Determine the staged filename format: <name>-YYYY-MM-DD-HH<ext>
        name_part, ext_part = os.path.splitext(base_log_name)
        target_filename = f"{name_part}-{dt_str}{ext_part}"

        log_info(f"Found rotated file {filename}. Staging as {target_filename}...")
        
        try:
            shutil.copy2(rotated_path, os.path.join(STAGING_DIR, target_filename))
            if not DEBUG_MODE:
                # In production mode, rename original .1 file in its folder to `<name>-YYYY-MM-DD-HH<ext>` for local retention
                dest_retention_path = os.path.join(os.path.dirname(rotated_path), target_filename)
                os.rename(rotated_path, dest_retention_path)
                log_info(f"Renamed original rotated file to {dest_retention_path}")
            else:
                log_info(f"Debug mode: copied {rotated_path} to staging. Original left intact.")
        except Exception as e:
            log_error(f"Failed to process rotated file {rotated_path}: {e}")

# 7. Locate & Stage Payara / Logback Logs
# We look for:
# - adr-YYYY-MM-DD-HH-*.log files matching previous hour.
# - ampacimon-YYYY-MM-DD.log daily file from previous day (when now.hour == 0 or in debug mode).
if os.path.exists(PAYARA_LOG_DIR):
    log_info(f"Scanning for Payara logs in {PAYARA_LOG_DIR}...")
    now = datetime.now()

    # previous hour search
    prev_hour = now - timedelta(hours=1)
    prev_hour_str = prev_hour.strftime("%Y-%m-%d-%H")

    # Glob for adr-YYYY-MM-DD-HH-*.log
    adr_pattern = os.path.join(PAYARA_LOG_DIR, f"adr-{prev_hour_str}-*.log")
    for file_path in glob.glob(adr_pattern):
        if os.path.isfile(file_path):
            filename = os.path.basename(file_path)
            log_info(f"Staging Payara log: {filename}")
            try:
                shutil.copy2(file_path, os.path.join(STAGING_DIR, filename))
            except Exception as e:
                log_error(f"Failed to stage Payara log file: {filename}. Error: {e}")

    # previous day search (for daily ampacimon-YYYY-MM-DD.log)
    if now.hour == 0 or DEBUG_MODE:
        yesterday = now - timedelta(days=1)
        yesterday_str = yesterday.strftime("%Y-%m-%d")
        ampacimon_pattern = os.path.join(PAYARA_LOG_DIR, f"ampacimon-{yesterday_str}.log")
        for file_path in glob.glob(ampacimon_pattern):
            if os.path.isfile(file_path):
                filename = os.path.basename(file_path)
                log_info(f"Staging daily Payara log: {filename}")
                try:
                    shutil.copy2(file_path, os.path.join(STAGING_DIR, filename))
                except Exception as e:
                    log_error(f"Failed to stage daily Payara log file: {filename}. Error: {e}")
else:
    log_info(f"Payara log directory {PAYARA_LOG_DIR} does not exist. Skipping Payara log scanning.")

# 8. Handle Staged Files
staged_files = [os.path.join(STAGING_DIR, f) for f in os.listdir(STAGING_DIR)]
staged_files = [f for f in staged_files if os.path.isfile(f)]

if DEBUG_MODE:
    log_info("--- DEBUG MODE: Content of STAGING_DIR ---")
    if not staged_files:
        print("(Staging directory is empty)")
    else:
        for f in sorted(staged_files):
            print(f" - {os.path.basename(f)} ({os.path.getsize(f)} bytes)")
    log_info("------------------------------------------")

    # Clean up STAGING_DIR in debug mode
    log_info("Cleaning up STAGING_DIR in debug mode...")
    for f in staged_files:
        try:
            os.remove(f)
        except Exception as e:
            log_error(f"Failed to delete {f} during debug cleanup: {e}")
    log_info("Staging directory cleaned.")
else:
    # Production S3 Upload
    log_info(f"Starting uploads from {STAGING_DIR} to S3 bucket {BUCKET_NAME}...")
    if not staged_files:
        log_info("No files staged for S3 upload.")
    else:
        for file_path in staged_files:
            filename = os.path.basename(file_path)

            # Determine correct S3 logsource category
            if filename.startswith("syslog"):
                logsource = "syslog"
            elif filename.startswith("auth"):
                logsource = "auth"
            elif filename.startswith("kern"):
                logsource = "kern"
            elif filename.startswith("adr"):
                logsource = "adr"
            elif filename.startswith("ampacimon"):
                logsource = "ampacimon"
            else:
                # Fallback to base name without extension and timestamp
                parts = filename.split('-')
                if parts:
                    logsource = parts[0]
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

# 9. Local Retention Cleanup (Delete logs older than 7 days)
if not DEBUG_MODE:
    log_info("Running local retention cleanup for historical logs...")
    now_ts = datetime.now().timestamp()
    seven_days_sec = 7 * 24 * 60 * 60

    # Scan all LOG_PATTERNS directories for renamed/historic logs matching <name>-YYYY-MM-DD-HH<ext>
    historic_pattern = re.compile(r"^.*-(\d{4}-\d{2}-\d{2}-\d{2})(\..*)?$")

    # We extract unique directories from LOG_PATTERNS to scan
    scan_dirs = set()
    for pattern in LOG_PATTERNS:
        dir_name = os.path.dirname(pattern)
        if os.path.exists(dir_name):
            scan_dirs.add(dir_name)

    for d in scan_dirs:
        try:
            for entry in os.listdir(d):
                full_path = os.path.join(d, entry)
                if os.path.isfile(full_path) and historic_pattern.match(entry):
                    try:
                        mtime = os.path.getmtime(full_path)
                        if (now_ts - mtime) > seven_days_sec:
                            log_info(f"Retention Cleanup: Removing old local log {entry} (older than 7 days)")
                            os.remove(full_path)
                    except Exception as e:
                        log_error(f"Failed to evaluate/remove {full_path}: {e}")
        except Exception as e:
            log_error(f"Failed to scan directory {d} for retention cleanup: {e}")
else:
    log_info("Debug mode: skipping local retention cleanup.")

log_info("KISS hourly log rotation and S3 upload process complete.")
