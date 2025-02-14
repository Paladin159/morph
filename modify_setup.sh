#!/bin/bash

set -e

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

wait_for_service() {
    local max_attempts=30
    local attempt=1
    local url=$1
    
    log "Waiting for service at $url..."
    while [ $attempt -le $max_attempts ]; do
        if curl -s -f "$url/health" > /dev/null; then
            log "Service is ready!"
            return 0
        fi
        log "Attempt $attempt/$max_attempts - Service not ready, waiting..."
        sleep 5
        ((attempt++))
    done
    log "Service failed to become ready"
    return 1
}

# Load environment variables from .env file
if [ -f .env ]; then
    export $(cat .env | xargs)
fi

# Check if required environment variables are set
if [ -z "$MORPH_API_KEY" ]; then
    log "Error: MORPH_API_KEY environment variable is not set"
    exit 1
fi

if [ -z "$WORKER_SNAPSHOT_ID" ] || [ -z "$LOAD_BALANCER_SNAPSHOT_ID" ]; then
    log "Error: WORKER_SNAPSHOT_ID and/or LOAD_BALANCER_SNAPSHOT_ID not found in .env file"
    exit 1
fi

log "Using API key from environment"

# Step 1: Update load balancer
log "Starting load balancer instance from snapshot $LOAD_BALANCER_SNAPSHOT_ID..."
LB_INSTANCE_ID=$(morphcloud instance start "$LOAD_BALANCER_SNAPSHOT_ID")
log "Started load balancer instance: $LB_INSTANCE_ID"

# Wait for instance to be ready
log "Waiting for load balancer instance to be ready..."
sleep 1

# Ensure directories exist
log "Creating required directories..."
morphcloud instance exec "$LB_INSTANCE_ID" "sudo mkdir -p /root/hashservice && sudo chown -R root:root /root/hashservice"

# Re-copy load balancer files
log "Re-copying load balancer files..."
scp load_balancer.py "$LB_INSTANCE_ID@ssh.cloud.morph.so:/root/hashservice/"
scp requirements.txt "$LB_INSTANCE_ID@ssh.cloud.morph.so:/root/hashservice/"
scp hash-balancer.service "$LB_INSTANCE_ID@ssh.cloud.morph.so:/etc/systemd/system/"
scp .env "$LB_INSTANCE_ID@ssh.cloud.morph.so:/root/hashservice/"

# Restart load balancer service
log "Restarting load balancer service..."
morphcloud instance exec "$LB_INSTANCE_ID" "sudo systemctl daemon-reload"
morphcloud instance exec "$LB_INSTANCE_ID" "sudo systemctl restart hash-balancer.service"

# Create new load balancer snapshot
log "Creating new load balancer snapshot..."
NEW_LB_SNAPSHOT=$(morphcloud instance snapshot "$LB_INSTANCE_ID")
log "Created new load balancer snapshot: $NEW_LB_SNAPSHOT"

# Stop load balancer instance
log "Stopping load balancer instance..."
morphcloud instance stop "$LB_INSTANCE_ID"

# Step 2: Update worker
log "Starting worker instance from snapshot $WORKER_SNAPSHOT_ID..."
WORKER_INSTANCE_ID=$(morphcloud instance start "$WORKER_SNAPSHOT_ID")
log "Started worker instance: $WORKER_INSTANCE_ID"

# Wait for instance to be ready
log "Waiting for worker instance to be ready..."
sleep 1

# Ensure directories exist
log "Creating required directories..."
morphcloud instance exec "$WORKER_INSTANCE_ID" "sudo mkdir -p /root/hashservice && sudo chown -R root:root /root/hashservice"

# Re-copy worker files
log "Re-copying worker files..."
scp worker.py "$WORKER_INSTANCE_ID@ssh.cloud.morph.so:/root/hashservice/"
scp worker.service "$WORKER_INSTANCE_ID@ssh.cloud.morph.so:/etc/systemd/system/"

# Restart worker service
log "Restarting worker service..."
morphcloud instance exec "$WORKER_INSTANCE_ID" "sudo systemctl daemon-reload"
morphcloud instance exec "$WORKER_INSTANCE_ID" "sudo systemctl restart worker.service"

# Create new worker snapshot
log "Creating new worker snapshot..."
NEW_WORKER_SNAPSHOT=$(morphcloud instance snapshot "$WORKER_INSTANCE_ID")
log "Created new worker snapshot: $NEW_WORKER_SNAPSHOT"

# Stop worker instance
log "Stopping worker instance..."
morphcloud instance stop "$WORKER_INSTANCE_ID"

# Update snapshot IDs in .env file
log "Updating snapshot IDs in .env file..."
if grep -q "WORKER_SNAPSHOT_ID=" .env; then
    sed -i "s/WORKER_SNAPSHOT_ID=.*/WORKER_SNAPSHOT_ID=$NEW_WORKER_SNAPSHOT/" .env
else
    echo "WORKER_SNAPSHOT_ID=$NEW_WORKER_SNAPSHOT" >> .env
fi

if grep -q "LOAD_BALANCER_SNAPSHOT_ID=" .env; then
    sed -i "s/LOAD_BALANCER_SNAPSHOT_ID=.*/LOAD_BALANCER_SNAPSHOT_ID=$NEW_LB_SNAPSHOT/" .env
else
    echo "LOAD_BALANCER_SNAPSHOT_ID=$NEW_LB_SNAPSHOT" >> .env
fi

# Delete old snapshots
log "Cleaning up old snapshots..."
log "Deleting old worker snapshot: $WORKER_SNAPSHOT_ID"
morphcloud snapshot delete "$WORKER_SNAPSHOT_ID"
log "Deleting old load balancer snapshot: $LOAD_BALANCER_SNAPSHOT_ID"
morphcloud snapshot delete "$LOAD_BALANCER_SNAPSHOT_ID"

log "Modification complete!"
log "New load balancer snapshot: $NEW_LB_SNAPSHOT"
log "New worker snapshot: $NEW_WORKER_SNAPSHOT"

