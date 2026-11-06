#!/bin/bash

# Forge job container — runs document forge, derive-review-repair,
# or story generation tasks.
#
# TASK_TYPE determines what runs:
#   document-forge         — full pipeline: summarize → extract → derive → review → repair
#   derive-review-repair   — convergence loop on existing belief network
#   generate-stories       — generate Feed stories from beliefs
#   walk-commits           — incremental update from recent git commits
#   project-track          — track Jira issues and build beliefs from state changes
#
# Mode 1: Edge API (set EDGE_BASE_URL + EDGE_TOKEN + TASK_ID)
#   Downloads DB/sources from the edge API, reports progress,
#   and uploads results via the API.
#
# Mode 2: Presigned URLs (set SOURCE_URL + RESULT_URL)
#   Downloads a tar.gz of sources and uploads results to presigned URLs.
#
# Required:
#   OPENAI_API_KEY   — LLM API key
#
# Optional:
#   TASK_TYPE        — task type (default: document-forge)
#   MODEL            — LLM model (default: openai:gpt-5.6-luna)
#   FORGE_MODE       — extraction mode (default: general)
#   MAX_ROUNDS       — max convergence rounds (default: 3)
#   DOMAIN_DESC      — domain description for derive context
#   TASK_CONFIG      — JSON task config (source_slugs, domain name, etc.)

export PATH="$HOME/.local/bin:$PATH"

TASK_TYPE="${TASK_TYPE:-document-forge}"
MODEL="${MODEL:-openai:gpt-5.6-luna}"
FORGE_MODE="${FORGE_MODE:-general}"
MAX_ROUNDS="${MAX_ROUNDS:-3}"
PARALLEL="${PARALLEL:-1}"

WORK_DIR="/home/forge/workspace"
SOURCES_DIR="$WORK_DIR/sources"
DB_PATH="$WORK_DIR/reasons.db"

# ─── Helpers ───

post_status() {
    local status="$1"
    local stage="$2"
    local message="$3"
    echo "[$(date +%H:%M:%S)] [$status] $stage: $message"

    if [ -n "$EDGE_BASE_URL" ] && [ -n "$EDGE_TOKEN" ] && [ -n "$TASK_ID" ]; then
        curl -sf -X POST \
            "$EDGE_BASE_URL/tenants/$TENANT_ID/tasks/$TASK_ID/progress" \
            -H "Authorization: Bearer $EDGE_TOKEN" \
            -H "Content-Type: application/json" \
            -d "{\"stage\":\"$stage\",\"percent\":0}" \
            2>/dev/null || true
    elif [ -n "$CALLBACK_URL" ]; then
        curl -sf -X POST "$CALLBACK_URL" \
            -H "Content-Type: application/json" \
            -d "{\"status\":\"$status\",\"stage\":\"$stage\",\"message\":\"$message\"}" \
            2>/dev/null || true
    fi
}

report_complete() {
    local result_json="$1"
    if [ -n "$EDGE_BASE_URL" ] && [ -n "$EDGE_TOKEN" ] && [ -n "$TASK_ID" ]; then
        curl -sf -X POST \
            "$EDGE_BASE_URL/tenants/$TENANT_ID/tasks/$TASK_ID/result" \
            -H "Authorization: Bearer $EDGE_TOKEN" \
            -H "Content-Type: application/json" \
            -d "$result_json" \
            2>/dev/null || true
    fi
}

report_failure() {
    local error="$1"
    error=$(echo "$error" | tr '\n' ' ' | sed 's/"/\\"/g' | head -c 500)
    if [ -n "$EDGE_BASE_URL" ] && [ -n "$EDGE_TOKEN" ] && [ -n "$TASK_ID" ]; then
        curl -sf -X POST \
            "$EDGE_BASE_URL/tenants/$TENANT_ID/tasks/$TASK_ID/fail" \
            -H "Authorization: Bearer $EDGE_TOKEN" \
            -H "Content-Type: application/json" \
            -d "{\"error\":\"$error\"}" \
            2>/dev/null || true
    fi
}

FAILURE_REPORTED=0

on_error() {
    local exit_code=$?
    local line_no=$1
    echo "[$(date +%H:%M:%S)] [FATAL] Script failed at line $line_no with exit code $exit_code"
    if [ "$FAILURE_REPORTED" -eq 0 ]; then
        FAILURE_REPORTED=1
        report_failure "Container failed at line $line_no (exit $exit_code)"
    fi
    exit $exit_code
}

fail() {
    FAILURE_REPORTED=1
    report_failure "$1"
    exit 1
}

trap 'on_error $LINENO' ERR
set -eE

# ─── Common: claim task ───

if [ -n "$EDGE_BASE_URL" ] && [ -n "$EDGE_TOKEN" ] && [ -n "$TASK_ID" ]; then
    echo "[$(date +%H:%M:%S)] Claiming task $TASK_ID (type=$TASK_TYPE)"
    curl -sf -X POST \
        "$EDGE_BASE_URL/tenants/$TENANT_ID/tasks/$TASK_ID/claim" \
        -H "Authorization: Bearer $EDGE_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{}' \
        2>/dev/null || echo "[warn] Could not claim task (may already be claimed)"
fi

# ─── Common: download existing DB ───

download_db() {
    if [ -n "$EDGE_BASE_URL" ] && [ -n "$EDGE_TOKEN" ] && [ -n "$DOMAIN_ID" ]; then
        curl -sf \
            "$EDGE_BASE_URL/tenants/$TENANT_ID/domains/$DOMAIN_ID/download-db" \
            -H "Authorization: Bearer $EDGE_TOKEN" \
            -o "$DB_PATH" 2>/dev/null || true
        if [ -f "$DB_PATH" ] && [ -s "$DB_PATH" ]; then
            echo "[$(date +%H:%M:%S)] Downloaded existing DB ($(stat -c%s "$DB_PATH" 2>/dev/null || stat -f%z "$DB_PATH") bytes)"
        else
            rm -f "$DB_PATH"
        fi
    fi
}

# ─── Common: upload DB ───

upload_db() {
    if [ -n "$EDGE_BASE_URL" ] && [ -n "$EDGE_TOKEN" ] && [ -n "$DOMAIN_ID" ]; then
        post_status "running" "upload" "Uploading reasons.db to edge service"
        curl -sf -X POST \
            "$EDGE_BASE_URL/tenants/$TENANT_ID/domains/$DOMAIN_ID/import-reasons" \
            -H "Authorization: Bearer $EDGE_TOKEN" \
            -F "file=@$DB_PATH;type=application/octet-stream"
    elif [ -n "$RESULT_URL" ]; then
        post_status "running" "upload" "Uploading reasons.db"
        curl -sfL -X PUT "$RESULT_URL" \
            -H "Content-Type: application/octet-stream" \
            --data-binary @"$DB_PATH"
    fi
}

# ─── Common: extract domain desc from config ───

extract_domain_desc() {
    if [ -z "$DOMAIN_DESC" ] && [ -n "$TASK_CONFIG" ]; then
        DOMAIN_DESC=$(echo "$TASK_CONFIG" | python3 -c "
import sys, json
cfg = json.load(sys.stdin)
print(cfg.get('domain', ''))
" 2>/dev/null || true)
    fi
}

# ═══════════════════════════════════════════
# Task: document-forge
# ═══════════════════════════════════════════

run_document_forge() {
    post_status "running" "init" "Starting forge pipeline"

    mkdir -p "$SOURCES_DIR"

    if [ -n "$EDGE_BASE_URL" ] && [ -n "$EDGE_TOKEN" ] && [ -n "$TASK_CONFIG" ]; then
        post_status "running" "download" "Downloading source documents from edge API"
        SLUGS=$(echo "$TASK_CONFIG" | python3 -c "
import sys, json
cfg = json.load(sys.stdin)
for s in cfg.get('source_slugs', []):
    print(s)
" 2>/dev/null)

        if [ -n "$SLUGS" ]; then
            while IFS= read -r slug; do
                echo "  Downloading: $slug"
                CONTENT=$(curl -sf \
                    "$EDGE_BASE_URL/tenants/$TENANT_ID/domains/$DOMAIN_ID/sources/$slug" \
                    -H "Authorization: Bearer $EDGE_TOKEN" \
                    2>/dev/null)
                if [ -n "$CONTENT" ]; then
                    echo "$CONTENT" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(data.get('content', ''))
" > "$SOURCES_DIR/${slug}.md" 2>/dev/null
                fi
            done <<< "$SLUGS"
        fi
    elif [ -n "$SOURCE_URL" ]; then
        post_status "running" "download" "Downloading source documents"
        curl -sfL "$SOURCE_URL" -o /tmp/sources.tar.gz
        tar xzf /tmp/sources.tar.gz -C "$SOURCES_DIR"
        rm /tmp/sources.tar.gz
    elif [ -d "$SOURCES_DIR" ] && [ "$(ls -A "$SOURCES_DIR" 2>/dev/null)" ]; then
        : # sources already mounted
    else
        fail "No source documents: set EDGE_BASE_URL+TASK_CONFIG or SOURCE_URL"
    fi

    DOC_COUNT=$(find "$SOURCES_DIR" -type f | wc -l | tr -d ' ')
    if [ "$DOC_COUNT" -eq 0 ]; then
        fail "No documents were downloaded"
    fi
    post_status "running" "download" "Ready: $DOC_COUNT source documents"

    extract_domain_desc
    download_db

    cd "$WORK_DIR"
    reasonsforge forge init 2>/dev/null || true

    post_status "running" "pipeline" "Running forge pipeline (model=$MODEL, mode=$FORGE_MODE)"

    reasonsforge forge pipeline \
        --sources-dir "$SOURCES_DIR" \
        --recursive \
        --model "$MODEL" \
        --mode "$FORGE_MODE" \
        --rounds "$MAX_ROUNDS" \
        --parallel "$PARALLEL" \
        ${DOMAIN_DESC:+--domain "$DOMAIN_DESC"} \
        2>&1 | while IFS= read -r line; do
        echo "$line"
        case "$line" in
            *"Summarizing"*)  post_status "running" "summarize" "$line" ;;
            *"Extracting"*)   post_status "running" "extract" "$line" ;;
            *"Deriving"*)     post_status "running" "derive" "$line" ;;
            *"Reviewing"*)    post_status "running" "review" "$line" ;;
            *"Repairing"*)    post_status "running" "repair" "$line" ;;
            *"Deduplicat"*)   post_status "running" "deduplicate" "$line" ;;
            *"Indexing"*)     post_status "running" "index" "$line" ;;
        esac
    done

    if [ ! -f "$DB_PATH" ]; then
        fail "Pipeline completed but no reasons.db produced"
    fi

    BELIEF_COUNT=$(reasons --db "$DB_PATH" list 2>/dev/null | wc -l | tr -d ' ' || echo "0")
    post_status "running" "upload" "Pipeline complete: $BELIEF_COUNT beliefs"

    upload_db
    report_complete "{\"belief_count\":$BELIEF_COUNT}"
    post_status "completed" "done" "Domain ready: $BELIEF_COUNT beliefs"
}

# ═══════════════════════════════════════════
# Task: derive-review-repair
# ═══════════════════════════════════════════

run_derive_review_repair() {
    post_status "running" "init" "Starting derive-review-repair"

    extract_domain_desc
    download_db

    if [ ! -f "$DB_PATH" ]; then
        fail "No reasons.db available for derive-review-repair"
    fi

    BEFORE_COUNT=$(reasons --db "$DB_PATH" list 2>/dev/null | wc -l | tr -d ' ' || echo "0")

    cd "$WORK_DIR"
    reasonsforge forge init 2>/dev/null || true

    ROUNDS=$(echo "$TASK_CONFIG" | python3 -c "
import sys, json
cfg = json.load(sys.stdin)
print(cfg.get('rounds', 3))
" 2>/dev/null || echo "3")

    MAX_DERIVE=$(echo "$TASK_CONFIG" | python3 -c "
import sys, json
cfg = json.load(sys.stdin)
print(cfg.get('max_derive_rounds', 10))
" 2>/dev/null || echo "10")

    post_status "running" "derive" "Running derive-review-repair (model=$MODEL, rounds=$ROUNDS)"

    reasonsforge forge derive-review-repair \
        --model "$MODEL" \
        --rounds "$ROUNDS" \
        --max-derive-rounds "$MAX_DERIVE" \
        ${DOMAIN_DESC:+--domain "$DOMAIN_DESC"} \
        2>&1 | while IFS= read -r line; do
        echo "$line"
        case "$line" in
            *"Derive round"*) post_status "running" "derive" "$line" ;;
            *"Review"*)       post_status "running" "review" "$line" ;;
            *"Repair"*)       post_status "running" "repair" "$line" ;;
            *"Deduplicat"*)   post_status "running" "deduplicate" "$line" ;;
        esac
    done

    AFTER_COUNT=$(reasons --db "$DB_PATH" list 2>/dev/null | wc -l | tr -d ' ' || echo "0")
    post_status "running" "upload" "Derive complete: $BEFORE_COUNT → $AFTER_COUNT beliefs"

    upload_db
    report_complete "{\"before_count\":$BEFORE_COUNT,\"after_count\":$AFTER_COUNT}"
    post_status "completed" "done" "Derive complete: $BEFORE_COUNT → $AFTER_COUNT beliefs"
}

# ═══════════════════════════════════════════
# Task: generate-stories
# ═══════════════════════════════════════════

run_generate_stories() {
    post_status "running" "init" "Starting story generation"

    download_db

    if [ ! -f "$DB_PATH" ]; then
        fail "No reasons.db available for story generation"
    fi

    extract_domain_desc

    MAX_STORIES=$(echo "$TASK_CONFIG" | python3 -c "
import sys, json
cfg = json.load(sys.stdin)
print(cfg.get('max_stories', 8))
" 2>/dev/null || echo "8")

    ALGORITHMS=$(echo "$TASK_CONFIG" | python3 -c "
import sys, json
cfg = json.load(sys.stdin)
print(','.join(cfg.get('algorithms', ['insight', 'gated', 'recent', 'lesson'])))
" 2>/dev/null || echo "insight,gated,recent,lesson")

    post_status "running" "generating" "Generating stories (model=$MODEL, max=$MAX_STORIES)"

    python3 -I /home/forge/generate_stories.py \
        --db "$DB_PATH" \
        --model "$MODEL" \
        --domain "${DOMAIN_DESC:-}" \
        --domain-id "$DOMAIN_ID" \
        --tenant-id "$TENANT_ID" \
        --max-stories "$MAX_STORIES" \
        --algorithms "$ALGORITHMS" \
        --edge-url "$EDGE_BASE_URL" \
        --edge-token "$EDGE_TOKEN" \
        --task-id "$TASK_ID" \
        2>&1 | while IFS= read -r line; do
        echo "$line"
        case "$line" in
            *"generating"*) post_status "running" "generating" "$line" ;;
            *"created"*)    post_status "running" "generating" "$line" ;;
        esac
    done

    post_status "completed" "done" "Story generation complete"
}

# ═══════════════════════════════════════════
# Task: walk-commits
# ═══════════════════════════════════════════

run_walk_commits() {
    post_status "running" "init" "Starting walk-commits"

    REPO_DIR="/home/forge/repo"
    SINCE="${SINCE:-}"
    SINCE_COMMIT="${SINCE_COMMIT:-}"

    if [ ! -d "$REPO_DIR/.git" ]; then
        fail "No repo mounted at $REPO_DIR"
    fi

    download_db

    cd "$WORK_DIR"
    reasonsforge forge init 2>/dev/null || true

    BEFORE_COUNT=$(reasonsforge --db "$DB_PATH" list 2>/dev/null | wc -l | tr -d ' ' || echo "0")

    post_status "running" "walk" "Walking commits (model=$MODEL)"

    WALK_ARGS="--repo $REPO_DIR --model $MODEL --parallel $PARALLEL"
    if [ -n "$SINCE" ]; then
        WALK_ARGS="$WALK_ARGS --since $SINCE"
    elif [ -n "$SINCE_COMMIT" ]; then
        WALK_ARGS="$WALK_ARGS --since-commit $SINCE_COMMIT"
    else
        WALK_ARGS="$WALK_ARGS --since-last"
    fi

    reasonsforge code walk-commits $WALK_ARGS 2>&1 | while IFS= read -r line; do
        echo "$line"
        case "$line" in
            *"commit"*) post_status "running" "walk" "$line" ;;
            *"explor"*) post_status "running" "explore" "$line" ;;
        esac
    done

    post_status "running" "propose" "Proposing beliefs from entries"
    reasonsforge code propose-beliefs --model "$MODEL" 2>&1

    post_status "running" "accept" "Accepting proposals"
    reasonsforge code accept-beliefs 2>&1 || true

    post_status "running" "derive" "Deriving reasoning chains"
    reasonsforge code derive --model "$MODEL" 2>&1

    AFTER_COUNT=$(reasonsforge --db "$DB_PATH" list 2>/dev/null | wc -l | tr -d ' ' || echo "0")
    post_status "running" "upload" "Walk complete: $BEFORE_COUNT → $AFTER_COUNT beliefs"

    upload_db
    report_complete "{\"before_count\":$BEFORE_COUNT,\"after_count\":$AFTER_COUNT}"
    post_status "completed" "done" "Walk-commits complete: $BEFORE_COUNT → $AFTER_COUNT beliefs"
}

# ═══════════════════════════════════════════
# Task: project-track
# ═══════════════════════════════════════════

run_project_track() {
    post_status "running" "init" "Starting project-track"

    download_db

    cd "$WORK_DIR"

    if [ ! -f "$DB_PATH" ]; then
        reasonsforge forge init 2>/dev/null || true
    fi

    BEFORE_COUNT=$(reasons --db "$DB_PATH" list 2>/dev/null | wc -l | tr -d ' ' || echo "0")

    TRACK_ARGS="--model $MODEL --auto"
    if [ "${FORCE:-}" = "1" ] || [ "${FORCE:-}" = "true" ]; then
        TRACK_ARGS="$TRACK_ARGS --force"
    fi
    if [ "${NO_DERIVE:-}" = "1" ] || [ "${NO_DERIVE:-}" = "true" ]; then
        TRACK_ARGS="$TRACK_ARGS --no-derive"
    fi

    post_status "running" "track" "Processing tracked issues (model=$MODEL)"

    reasonsforge project track $TRACK_ARGS 2>&1 | while IFS= read -r line; do
        echo "$line"
        case "$line" in
            *"Analyzing"*) post_status "running" "analyze" "$line" ;;
            *"propose"*)   post_status "running" "propose" "$line" ;;
            *"deriv"*)     post_status "running" "derive" "$line" ;;
            *"accept"*)    post_status "running" "accept" "$line" ;;
            *"skip"*)      post_status "running" "track" "$line" ;;
        esac
    done

    AFTER_COUNT=$(reasons --db "$DB_PATH" list 2>/dev/null | wc -l | tr -d ' ' || echo "0")
    post_status "running" "upload" "Track complete: $BEFORE_COUNT → $AFTER_COUNT beliefs"

    upload_db
    report_complete "{\"before_count\":$BEFORE_COUNT,\"after_count\":$AFTER_COUNT}"
    post_status "completed" "done" "Project-track complete: $BEFORE_COUNT → $AFTER_COUNT beliefs"
}

# ═══════════════════════════════════════════
# Dispatch
# ═══════════════════════════════════════════

case "$TASK_TYPE" in
    document-forge)
        run_document_forge
        ;;
    derive-review-repair)
        run_derive_review_repair
        ;;
    generate-stories)
        run_generate_stories
        ;;
    walk-commits)
        run_walk_commits
        ;;
    project-track)
        run_project_track
        ;;
    *)
        fail "Unknown task type: $TASK_TYPE"
        ;;
esac
