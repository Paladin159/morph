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

# Check if API key is set
if [ -z "$MORPH_API_KEY" ]; then
    log "Error: MORPH_API_KEY environment variable is not set"
    exit 1
fi

log "Using API key from environment"

# Step 1: Create and set up load balancer
log "Creating initial load balancer snapshot..."
INITIAL_LB_SNAPSHOT=$(morphcloud snapshot create --vcpus 1 --memory 512 --disk-size 2056 --image-id morphvm-minimal)
log "Created initial load balancer snapshot: $INITIAL_LB_SNAPSHOT"

log "Starting load balancer instance..."
LB_INSTANCE_ID=$(morphcloud instance start "$INITIAL_LB_SNAPSHOT")
log "Started load balancer instance: $LB_INSTANCE_ID"

# Set up load balancer
log "Setting up load balancer..."
morphcloud instance exec "$LB_INSTANCE_ID" "sudo apt-get update && sudo apt-get install -y --no-install-recommends ntp && sudo service ntp stop && sudo ntpd -gq && sudo service ntp start"
morphcloud instance exec "$LB_INSTANCE_ID" "export DEBIAN_FRONTEND=noninteractive && sudo -E apt-get update && sudo -E apt-get install -y apt-utils python3-full"
morphcloud instance exec "$LB_INSTANCE_ID" "sudo mkdir -p /root/hashservice"

# Copy load balancer files
log "Copying load balancer files..."
scp load_balancer.py "$LB_INSTANCE_ID@ssh.cloud.morph.so:/root/hashservice/"
scp requirements.txt "$LB_INSTANCE_ID@ssh.cloud.morph.so:/root/hashservice/"
scp hash-balancer.service "$LB_INSTANCE_ID@ssh.cloud.morph.so:/etc/systemd/system/"
scp .env "$LB_INSTANCE_ID@ssh.cloud.morph.so:/root/hashservice/"

# Set up virtual environment with Python packages
log "Installing Python packages on load balancer..."
morphcloud instance exec "$LB_INSTANCE_ID" "cd /root/hashservice && python3 -m venv venv && source venv/bin/activate && pip install --no-cache-dir -r requirements.txt"

# Start load balancer service
log "Starting load balancer service..."
morphcloud instance exec "$LB_INSTANCE_ID" "sudo systemctl daemon-reload"
morphcloud instance exec "$LB_INSTANCE_ID" "sudo systemctl enable hash-balancer.service"
morphcloud instance exec "$LB_INSTANCE_ID" "sudo systemctl start hash-balancer.service"

# Create final load balancer snapshot
log "Creating final load balancer snapshot..."
LOAD_BALANCER_SNAPSHOT=$(morphcloud instance snapshot "$LB_INSTANCE_ID")
log "Created load balancer snapshot: $LOAD_BALANCER_SNAPSHOT"

# Stop load balancer instance
log "Stopping load balancer instance..."
morphcloud instance stop "$LB_INSTANCE_ID"

# Step 2: Create and set up worker
log "Creating initial worker snapshot..."
INITIAL_WORKER_SNAPSHOT=$(morphcloud snapshot create --vcpus 1 --memory 128 --disk-size 700 --image-id morphvm-minimal)
log "Created initial worker snapshot: $INITIAL_WORKER_SNAPSHOT"

log "Starting worker instance..."
WORKER_INSTANCE_ID=$(morphcloud instance start "$INITIAL_WORKER_SNAPSHOT")
log "Started worker instance: $WORKER_INSTANCE_ID"

# Set up worker
log "Setting up worker..."
morphcloud instance exec "$WORKER_INSTANCE_ID" "sudo fallocate -l 128M /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile"
morphcloud instance exec "$WORKER_INSTANCE_ID" "sudo apt-get update && sudo apt-get install -y --no-install-recommends ntp && sudo service ntp stop && sudo ntpd -gq && sudo service ntp start"
morphcloud instance exec "$WORKER_INSTANCE_ID" "export DEBIAN_FRONTEND=noninteractive && sudo -E apt-get update && sudo -E apt-get install -y --no-install-recommends python3-minimal python3-fastapi python3-uvicorn"
morphcloud instance exec "$WORKER_INSTANCE_ID" "sudo mkdir -p /root/hashservice"

# Copy worker files
log "Copying worker files..."
scp worker.py "$WORKER_INSTANCE_ID@ssh.cloud.morph.so:/root/hashservice/"
scp worker.service "$WORKER_INSTANCE_ID@ssh.cloud.morph.so:/etc/systemd/system/"

# Start worker service
log "Starting worker service..."
morphcloud instance exec "$WORKER_INSTANCE_ID" "sudo systemctl daemon-reload"
morphcloud instance exec "$WORKER_INSTANCE_ID" "sudo systemctl enable worker.service"
morphcloud instance exec "$WORKER_INSTANCE_ID" "sudo systemctl start worker.service"

# Create final worker snapshot
log "Creating final worker snapshot..."
WORKER_SNAPSHOT=$(morphcloud instance snapshot "$WORKER_INSTANCE_ID")
log "Created worker snapshot: $WORKER_SNAPSHOT"

# Stop worker instance
log "Stopping worker instance..."
morphcloud instance stop "$WORKER_INSTANCE_ID"

# Update snapshot IDs in .env file
log "Updating snapshot IDs in .env file..."
if grep -q "WORKER_SNAPSHOT_ID=" .env; then
    sed -i "s/WORKER_SNAPSHOT_ID=.*/WORKER_SNAPSHOT_ID=$WORKER_SNAPSHOT/" .env
else
    echo "WORKER_SNAPSHOT_ID=$WORKER_SNAPSHOT" >> .env
fi

if grep -q "LOAD_BALANCER_SNAPSHOT_ID=" .env; then
    sed -i "s/LOAD_BALANCER_SNAPSHOT_ID=.*/LOAD_BALANCER_SNAPSHOT_ID=$LOAD_BALANCER_SNAPSHOT/" .env
else
    echo "LOAD_BALANCER_SNAPSHOT_ID=$LOAD_BALANCER_SNAPSHOT" >> .env
fi

# Delete initial snapshots
log "Cleaning up initial snapshots..."
log "Deleting initial worker snapshot: $INITIAL_WORKER_SNAPSHOT"
morphcloud snapshot delete "$INITIAL_WORKER_SNAPSHOT"
log "Deleting initial load balancer snapshot: $INITIAL_LB_SNAPSHOT"
morphcloud snapshot delete "$INITIAL_LB_SNAPSHOT"

log "Setup complete!"
log "Load balancer snapshot: $LOAD_BALANCER_SNAPSHOT"
log "Worker snapshot: $WORKER_SNAPSHOT"