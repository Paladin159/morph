#!/bin/bash

set -e

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

WORKER_INSTANCE_ID="morphvm_e8f9qdv2"

# Set API key
export MORPH_API_KEY="morph_fdBw4OOQ9NwU6REhYRtmnv"
log "Set MORPH_API_KEY"

# Set up permanent worker
log "Setting up permanent worker..."

# Create and enable swap based on memeater results
 log "Setting up swap for worker..."
 morphcloud instance exec "$WORKER_INSTANCE_ID" "sudo fallocate -l 128M /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile"

# Install minimal packages on worker and fix time sync
log "Setting up worker environment..."
morphcloud instance exec "$WORKER_INSTANCE_ID" "sudo apt-get update && sudo apt-get install -y --no-install-recommends ntp && sudo service ntp stop && sudo ntpd -gq && sudo service ntp start"
morphcloud instance exec "$WORKER_INSTANCE_ID" "export DEBIAN_FRONTEND=noninteractive && sudo -E apt-get update && sudo -E apt-get install -y --no-install-recommends python3-minimal python3-fastapi python3-uvicorn"
morphcloud instance exec "$WORKER_INSTANCE_ID" "sudo mkdir -p /root/hashservice"

# Copy worker files
log "Copying worker files..."
morphcloud instance copy worker.py "$WORKER_INSTANCE_ID:/root/hashservice/"
morphcloud instance copy worker.service "$WORKER_INSTANCE_ID:/etc/systemd/system/"

# Start worker service
log "Starting worker service..."
morphcloud instance exec "$WORKER_INSTANCE_ID" "sudo systemctl daemon-reload"
morphcloud instance exec "$WORKER_INSTANCE_ID" "sudo systemctl enable worker.service"
morphcloud instance exec "$WORKER_INSTANCE_ID" "sudo systemctl start worker.service"

# Expose worker HTTP port
log "Exposing worker HTTP port..."
morphcloud instance expose-http "$WORKER_INSTANCE_ID" "worker" 5000

# Get worker URL
log "Getting worker details..."
WORKER_INFO=$(morphcloud instance get "$WORKER_INSTANCE_ID")
WORKER_URL=$(echo "$WORKER_INFO" | python3 -c "
import sys, json
info = json.load(sys.stdin)
services = info['networking']['http_services']
for service in services:
    if service['name'] == 'worker':
        print(service['url'])
        break
")

if [ -z "$WORKER_URL" ]; then
    log "Failed to get worker URL"
    exit 1
fi

log "Setup complete!"
log "Worker is running at $WORKER_URL" 