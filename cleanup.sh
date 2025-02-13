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

log "Using API key from environment"

# List all instances
log "Listing all instances..."
INSTANCES=$(morphcloud instance list | grep "morphvm_" | awk '{print $1}')
log "Current instances:"
echo "$INSTANCES"

# Delete each instance
log "Deleting all instances..."
for instance_id in $INSTANCES; do
    log "Deleting instance: $instance_id"
    morphcloud instance stop "$instance_id"
done

# Wait a bit for instances to be fully deleted
sleep 5

# List all snapshots
log "Listing all snapshots..."
SNAPSHOTS=$(morphcloud snapshot list | grep "snapshot_" | awk '{print $1}')
log "Current snapshots:"
echo "$SNAPSHOTS"

# Delete each snapshot
log "Deleting all snapshots..."
for snapshot_id in $SNAPSHOTS; do
    log "Deleting snapshot: $snapshot_id"
    morphcloud snapshot delete "$snapshot_id"
done

log "Cleanup complete!" 