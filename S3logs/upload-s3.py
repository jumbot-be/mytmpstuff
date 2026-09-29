#!/usr/bin/env python3
# KISS Log Extraction & S3 Upload Script
# Extracts logs for the previous full hour directly from log files based on timestamps
# and uploads extracted log files to Amazon S3.

import os
import sys
import re
import glob
import shutil
import socket
import subprocess
from datetime import datetime, timedelta

# 1. Configuration
BUCKET_NAME = os.environ.get("S3_LOG_BUCKET", "sdt-be-adr-dev-audit-logs")
STAGING_DIR = "/var/spool/s3-upload"

# Directories & Monitored Targets
SOURCES_CONFIG = [
    {
        "logsource": "syslog",
        "patterns": ["/var/log/syslog"]
    },
    {
        "logsource": "auth.log",
        "patterns": ["/var/log/auth.log"]
    },
    {
        "logsource": "kern.log",
        "patterns": ["/var/log/kern.log"]
    },
    {
        "logsource": "nginx",
        "patterns": ["/var/log/nginx/*.log"]
    },
    {
        "logsource": "crushftp",
        "patterns": ["/opt/CrushFTP11/*.log"]
    },
    {
        "logsource": "keycloak",
        "patterns": ["/opt/adr/apcm-keycloak-bundle-rh/keycloak/data/log/*.log"]
    },
    {
        "logsource": "ADR",
        "patterns": ["/opt/adr/apcm-payara-bundle/payara5/glassfish/domains/ampacimon-domain/logs/*.log"]
    }
]

# Server hostname
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

# 3. Timestamp Parsing Regex Patterns
TIMESTAMP_PATTERNS = [
    # ISO / Standard formats: 2026-03-30 14:05:01, 2026/03/30 14:05:01, 2026-03-30T14:05:01
    (re.compile(r'(?:^|\[)(\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2})(?:[.,]\d+)?'),
     ['%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y/%m/%d %H:%M:%S']),

    # Nginx combined / common log format: [30/Mar/2026:14:05:01 +0000]
    (re.compile(r'\[(\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2})'),
     ['%d/%b/%Y:%H:%M:%S']),

    # Syslog format: Mar 30 14:05:01
    (re.compile(r'^\s*([A-Za-z]{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})'),
     ['%b %d %H:%M:%S', '%b  %d %H:%M:%S']),
]

def parse_line_timestamp(line, reference_year=None):
    """
    Parses a timestamp from a log line.
    Returns datetime object if successfully parsed, or None.
    """
    if reference_year is None:
        reference_year = datetime.now().year

    for regex, fmt_list in TIMESTAMP_PATTERNS:
        m = regex.search(line)
        if m:
            dt_str = m.group(1)
            for fmt in fmt_list:
                try:
                    dt = datetime.strptime(dt_str, fmt)
                    if dt.year == 1900:
                        dt = dt.replace(year=reference_year)
                    return dt
                except ValueError:
                    continue
    return None

def extract_logs_for_target_hour(file_path, start_time, end_time):
    """
    Reads a log file line by line and extracts lines whose timestamps
    fall within [start_time, end_time].
    Supports multi-line log entries (e.g., stack traces) by attaching
    continuation lines to the timestamp of the preceding entry.
    """
    extracted_lines = []
    current_entry_in_range = False

    try:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                dt = parse_line_timestamp(line, reference_year=start_time.year)
                if dt is not None:
                    # New log entry with timestamp
                    if start_time <= dt <= end_time:
                        current_entry_in_range = True
                        extracted_lines.append(line)
                    else:
                        current_entry_in_range = False
                else:
                    # Multi-line continuation
                    if current_entry_in_range:
                        extracted_lines.append(line)
    except Exception as e:
        log_error(f"Error reading file {file_path}: {e}")

    return extracted_lines

def main():
    log_info(f"Starting hourly S3 log extraction and upload process on {SERVER_NAME}.")

    # Environment check
    if not shutil.which("aws"):
        log_error("AWS CLI (aws) is not installed or not in PATH. Aborting.")
        sys.exit(1)

    # Ensure secure staging directory exists with root-only permissions (700)
    if not os.path.exists(STAGING_DIR):
        os.makedirs(STAGING_DIR, mode=0o700)
        log_info(f"Created secure staging directory at {STAGING_DIR} with 700 permissions.")
    else:
        os.chmod(STAGING_DIR, 0o700)

    # Determine target hour: the previous full hour
    now = datetime.now()
    target_dt = now - timedelta(hours=1)
    target_hour_str = target_dt.strftime("%Y-%m-%d-%H")

    start_time = target_dt.replace(minute=0, second=0, microsecond=0)
    end_time = target_dt.replace(minute=59, second=59, microsecond=999999)

    log_info(f"Target extraction window: {start_time} to {end_time} (Hour tag: {target_hour_str})")

    staged_items = []
    staged_paths = set()

    def stage_extracted_content(basename, logsource, lines):
        if not lines:
            return
        
        target_filename = f"{basename}-{target_hour_str}.log"
        dest_path = os.path.join(STAGING_DIR, target_filename)

        log_info(f"Staging extracted logs [{logsource}]: {target_filename} ({len(lines)} lines)")
        try:
            with open(dest_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)
            if dest_path not in staged_paths:
                staged_items.append((dest_path, logsource))
                staged_paths.add(dest_path)
        except Exception as e:
            log_error(f"Failed to write staged log file: {target_filename}. Error: {e}")

    # Extract logs for all configured sources
    for source in SOURCES_CONFIG:
        logsource = source["logsource"]
        for pattern in source["patterns"]:
            matched_files = glob.glob(pattern)
            for file_path in matched_files:
                if not os.path.isfile(file_path):
                    continue
                filename = os.path.basename(file_path)
                # derive base name without extension for clean output file naming
                base_name, _ = os.path.splitext(filename)

                log_info(f"Extracting logs from {file_path} for logsource {logsource}...")
                extracted_lines = extract_logs_for_target_hour(file_path, start_time, end_time)
                stage_extracted_content(base_name, logsource, extracted_lines)

    # Check for any leftover/pre-existing files in STAGING_DIR
    existing_staged_files = [os.path.join(STAGING_DIR, f) for f in os.listdir(STAGING_DIR)]
    existing_staged_files = [f for f in existing_staged_files if os.path.isfile(f)]
    for f in existing_staged_files:
        if f not in staged_paths:
            filename = os.path.basename(f)
            if filename.startswith(("syslog", "auth", "kern")):
                ls = "auth.log" if filename.startswith("auth") else ("kern.log" if filename.startswith("kern") else "syslog")
            elif "nginx" in filename:
                ls = "nginx"
            elif "crushftp" in filename:
                ls = "crushftp"
            elif "keycloak" in filename:
                ls = "keycloak"
            elif any(k in filename for k in ["adr", "ampacimon", "sce", "server"]):
                ls = "ADR"
            else:
                ls = "general"
            staged_items.append((f, ls))
            staged_paths.add(f)

    # Upload Staged Files to S3
    log_info(f"Starting uploads from {STAGING_DIR} to S3 bucket {BUCKET_NAME}...")

    if not staged_items:
        log_info("No files staged for S3 upload.")
    else:
        for file_path, logsource in staged_items:
            if not os.path.exists(file_path):
                continue
            filename = os.path.basename(file_path)

            # S3 URI: s3://<bucket>/logs/<hostname>/<logsource>/<filename>
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
                log_error(f"Failed to upload {filename} to S3. Retaining local staged copy for retry. Error: {upload_res.stderr}")

    log_info("Hourly log extraction and S3 upload process complete.")

if __name__ == "__main__":
    main()
