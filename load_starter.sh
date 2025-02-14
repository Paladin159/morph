#!/bin/bash
set -e

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

# Load environment variables from .env file
if [ -f .env ]; then
    export $(cat .env | xargs)
fi

# Check if API key is set
if [ -z "$MORPH_API_KEY" ]; then
    log "Error: MORPH_API_KEY environment variable is not set"
    exit 1
fi

if [ -z "$LOAD_BALANCER_SNAPSHOT_ID" ]; then
    log "Error: LOAD_BALANCER_SNAPSHOT_ID environment variable is not set"
    exit 1
fi

# Start load balancer instance
log "Starting load balancer instance from snapshot $LOAD_BALANCER_SNAPSHOT_ID..."
INSTANCE_ID=$(morphcloud instance start "$LOAD_BALANCER_SNAPSHOT_ID")
log "Started load balancer instance: $INSTANCE_ID"

# Export environment variables on the instance
log "Setting environment variables..."
morphcloud instance exec "$INSTANCE_ID" "sudo systemctl set-environment WORKER_SNAPSHOT_ID=$WORKER_SNAPSHOT_ID && sudo systemctl set-environment MORPH_API_KEY=$MORPH_API_KEY && sudo systemctl restart hash-balancer"

# Get instance info and extract URL
LOAD_BALANCER_URL=$(morphcloud instance expose-http "$INSTANCE_ID" web 8000)
log "Load balancer URL: $LOAD_BALANCER_URL"