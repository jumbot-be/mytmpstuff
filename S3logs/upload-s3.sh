#!/usr/bin/env bash
# KISS Log Extraction & S3 Upload Script (Bash / AWK implementation)
# Extracts logs for the previous full hour directly from log files based on timestamps
# and uploads extracted log files to Amazon S3.

set -eo pipefail

# Parse CLI flags
VERBOSE=0
for arg in "$@"; do
    case "$arg" in
        -v|--verbose)
            VERBOSE=1
            ;;
    esac
done

# 1. Configuration
BUCKET_NAME="${S3_LOG_BUCKET:-sdt-be-adr-dev-audit-logs}"
STAGING_DIR="/var/spool/s3-upload"
SERVER_NAME="$(hostname 2>/dev/null || echo "unknown-host")"

# 2. Logging Helpers
log_info() {
    local msg="$1"
    local timestamp
    timestamp="$(date +'%Y-%m-%d %H:%M:%S')"
    echo "[$timestamp] INFO: $msg"
    logger -t "upload-s3" "INFO: $msg" 2>/dev/null || true
}

log_debug() {
    if [ "$VERBOSE" -eq 1 ]; then
        local msg="$1"
        local timestamp
        timestamp="$(date +'%Y-%m-%d %H:%M:%S')"
        echo "[$timestamp] DEBUG: $msg"
    fi
}

log_error() {
    local msg="$1"
    local timestamp
    timestamp="$(date +'%Y-%m-%d %H:%M:%S')"
    echo "[$timestamp] ERROR: $msg" >&2
    logger -t "upload-s3" "ERROR: $msg" 2>/dev/null || true
}

log_info "Starting hourly S3 log extraction and upload process on ${SERVER_NAME} (Bash/AWK)."
if [ "$VERBOSE" -eq 1 ]; then
    log_info "Verbose mode enabled."
fi

# Environment Checks
if ! command -v aws >/dev/null 2>&1; then
    log_error "AWS CLI (aws) is not installed or not in PATH. Aborting."
    exit 1
fi

# Ensure secure staging directory exists with root-only permissions (700)
if [ ! -d "$STAGING_DIR" ]; then
    mkdir -p "$STAGING_DIR"
    chmod 700 "$STAGING_DIR"
    log_info "Created secure staging directory at $STAGING_DIR with 700 permissions."
else
    chmod 700 "$STAGING_DIR"
fi

# Calculate target extraction hour (the previous full hour)
TARGET_YEAR="$(date -d '1 hour ago' +'%Y')"
TARGET_TAG="$(date -d '1 hour ago' +'%Y-%m-%d-%H')"

# Calculate minimum mtime timestamp in epoch seconds (target hour start minus 5 min buffer)
MIN_MTIME_EPOCH="$(date -d '1 hour ago' +'%Y-%m-%d %H:00:00')"
MIN_MTIME_SEC="$(date -d "$MIN_MTIME_EPOCH - 5 minutes" +%s)"

log_info "Target extraction hour tag: ${TARGET_TAG}"

# Embedded AWK extractor script (POSIX AWK compatible)
read -r -d '' AWK_EXTRACTOR << 'EOF' || true
BEGIN {
    months["Jan"]="01"; months["Feb"]="02"; months["Mar"]="03"; months["Apr"]="04";
    months["May"]="05"; months["Jun"]="06"; months["Jul"]="07"; months["Aug"]="08";
    months["Sep"]="09"; months["Oct"]="10"; months["Nov"]="11"; months["Dec"]="12";
    in_target = 0;
}

function parse_dt(line,   mon, day, idx, str) {
    # 1) ISO / Standard: 2026-03-30 14:05:01, 2026/03/30 14:05:01, 2026-03-30T14:05:01
    if (line ~ /(^|\[)[0-9]{4}[-\/][0-9]{2}[-\/][0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}/) {
        gsub(/^[ \t]*\[?/, "", line);
        return substr(line, 1, 4) "-" substr(line, 6, 2) "-" substr(line, 9, 2) "-" substr(line, 12, 2);
    }
    # 2) Nginx: [30/Mar/2026:14:05:01 +0000]
    if (line ~ /\[[0-9]{2}\/[A-Za-z]{3}\/[0-9]{4}:[0-9]{2}:[0-9]{2}:[0-9]{2}/) {
        idx = index(line, "[");
        str = substr(line, idx + 1);
        mon = months[substr(str, 4, 3)];
        return substr(str, 8, 4) "-" mon "-" substr(str, 1, 2) "-" substr(str, 13, 2);
    }
    # 3) Syslog: Mar 30 14:05:01 or Mar  5 14:05:01
    if (line ~ /^[ \t]*[A-Za-z]{3}[ \t]+[0-9]{1,2}[ \t]+[0-9]{2}:[0-9]{2}:[0-9]{2}/) {
        gsub(/^[ \t]+/, "", line);
        mon = months[substr(line, 1, 3)];
        rest = substr(line, 5);
        sub(/[ \t]+/, " ", rest);
        split(rest, parts, " ");
        day = sprintf("%02d", parts[1]);
        split(parts[2], timeparts, ":");
        return target_year "-" mon "-" day "-" timeparts[1];
    }
    return "";
}

{
    dt = parse_dt($0);
    if (dt != "") {
        if (dt == target_tag) {
            in_target = 1;
            print $0;
        } else {
            in_target = 0;
        }
    } else {
        if (in_target) {
            print $0;
        }
    }
}
EOF

STAGED_MANIFEST="${STAGING_DIR}/.staged_manifest"

stage_item() {
    local file_path="$1"
    local logsource="$2"
    echo "${file_path}|${logsource}" >> "$STAGED_MANIFEST"
}

process_source() {
    local logsource="$1"
    shift
    local patterns=("$@")

    for pattern in "${patterns[@]}"; do
        shopt -s nullglob
        local files=($pattern)
        shopt -u nullglob

        for file_path in "${files[@]}"; do
            [ -f "$file_path" ] || continue

            # Skip compressed or temporary extensions
            case "$file_path" in
                *.gz|*.bz2|*.xz|*.zip|*.tar|*tmp|*TMP|*.swp)
                    log_debug "Skipping compressed/temp file: $file_path"
                    continue
                    ;;
            esac

            # Filter by file modification time
            local file_mtime_sec
            file_mtime_sec="$(date -r "$file_path" +%s 2>/dev/null || echo 0)"
            if [ "$file_mtime_sec" -lt "$MIN_MTIME_SEC" ]; then
                log_debug "Skipping old file (mtime < target hour): $file_path"
                continue
            fi

            local filename
            filename="$(basename "$file_path")"
            local base_name="${filename%.*}"
            local target_filename="${base_name}-${TARGET_TAG}.log"
            local dest_path="${STAGING_DIR}/${target_filename}"

            log_debug "Scanning candidate log file ${file_path} for logsource ${logsource}..."

            local tmp_out
            tmp_out="$(mktemp "${STAGING_DIR}/tmp.XXXXXX")"

            awk -v target_year="$TARGET_YEAR" -v target_tag="$TARGET_TAG" "$AWK_EXTRACTOR" "$file_path" > "$tmp_out" || true

            if [ -s "$tmp_out" ]; then
                cat "$tmp_out" >> "$dest_path"
                stage_item "$dest_path" "$logsource"
                log_info "Staged extracted logs [${logsource}]: ${target_filename}"
                rm -f "$tmp_out"
            else
                rm -f "$tmp_out"
            fi
        done
    done
}

# Process monitored sources
log_info "Extracting system logs..."
process_source "syslog" "/var/log/syslog*"
process_source "auth.log" "/var/log/auth.log*"
process_source "kern.log" "/var/log/kern.log*"

log_info "Extracting Nginx logs..."
process_source "nginx" "/var/log/nginx/*"

log_info "Extracting CrushFTP logs..."
process_source "crushftp" "/opt/CrushFTP11/*.log"

log_info "Extracting Keycloak logs..."
process_source "keycloak" "/opt/adr/apcm-keycloak-bundle-rh/keycloak/data/log/*"

log_info "Extracting Payara logs..."
process_source "ADR" "/opt/adr/apcm-payara-bundle/payara5/glassfish/domains/ampacimon-domain/logs/*"

# Upload Staged Files to S3
log_info "Starting uploads from ${STAGING_DIR} to S3 bucket ${BUCKET_NAME}..."

if [ ! -f "$STAGED_MANIFEST" ] || [ ! -s "$STAGED_MANIFEST" ]; then
    log_info "No files staged for S3 upload."
else
    sort -u "$STAGED_MANIFEST" | while IFS='|' read -r file_path logsource; do
        [ -f "$file_path" ] || continue
        filename="$(basename "$file_path")"

        s3_uri="s3://${BUCKET_NAME}/logs/${SERVER_NAME}/${logsource}/${filename}"
        log_info "Uploading ${filename} to ${s3_uri}"

        if aws s3 cp "$file_path" "$s3_uri"; then
            log_info "Successfully uploaded ${filename}. Removing local staged copy."
            rm -f "$file_path"
        else
            log_error "Failed to upload ${filename} to S3. Retaining local staged file for retry."
        fi
    done
    rm -f "$STAGED_MANIFEST"
fi

log_info "Hourly log extraction and S3 upload process complete (Bash/AWK)."
