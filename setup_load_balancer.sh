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

# Set API key
export MORPH_API_KEY="morph_fdBw4OOQ9NwU6REhYRtmnv"
log "Set MORPH_API_KEY"

# Create snapshot for load balancer
log "Creating load balancer snapshot..."
LB_SNAPSHOT_ID=$(morphcloud snapshot create --memory 512 --disk-size 2056 --image-id morphvm-minimal)
log "Created load balancer snapshot: $LB_SNAPSHOT_ID"

# Create snapshot for permanent worker
log "Creating permanent worker snapshot..."
WORKER_SNAPSHOT_ID=$(morphcloud snapshot create --memory 128 --disk-size 700 --image-id morphvm-minimal)
log "Created worker snapshot: $WORKER_SNAPSHOT_ID"

# Start load balancer instance
log "Starting load balancer instance..."
LB_INSTANCE_ID=$(morphcloud instance start "$LB_SNAPSHOT_ID")
log "Started load balancer instance: $LB_INSTANCE_ID"

# Start permanent worker instance
log "Starting permanent worker instance..."
WORKER_INSTANCE_ID=$(morphcloud instance start "$WORKER_SNAPSHOT_ID")
log "Started worker instance: $WORKER_INSTANCE_ID"

# Wait for instances to be ready
log "Waiting for instances to be ready..."
sleep 5

# Set up load balancer and fix time sync
log "Setting up load balancer..."
morphcloud instance exec "$LB_INSTANCE_ID" "sudo apt-get update && sudo apt-get install -y --no-install-recommends ntp && sudo service ntp stop && sudo ntpd -gq && sudo service ntp start"
morphcloud instance exec "$LB_INSTANCE_ID" "export DEBIAN_FRONTEND=noninteractive && sudo -E apt-get update && sudo -E apt-get install -y apt-utils python3-full"
morphcloud instance exec "$LB_INSTANCE_ID" "sudo mkdir -p /root/hashservice"

# Copy load balancer files
log "Copying load balancer files..."
morphcloud instance copy load_balancer.py "$LB_INSTANCE_ID:/root/hashservice/"
morphcloud instance copy requirements.txt "$LB_INSTANCE_ID:/root/hashservice/"
morphcloud instance copy hash-balancer.service "$LB_INSTANCE_ID:/etc/systemd/system/"

# Set up virtual environment with Python packages
log "Installing Python packages on load balancer..."
morphcloud instance exec "$LB_INSTANCE_ID" "cd /root/hashservice && python3 -m venv venv && source venv/bin/activate && pip install --no-cache-dir -r requirements.txt"

# Set up permanent worker
log "Setting up permanent worker..."

# Create and enable swap
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

# Configure load balancer with worker info
log "Configuring load balancer with worker info..."
cat > worker_config.json << EOL
{
    "template_worker_id": "$WORKER_INSTANCE_ID",
    "template_worker_url": "$WORKER_URL",
    "load_balancer_id": "$LB_INSTANCE_ID",
    "worker_snapshot_id": "$WORKER_SNAPSHOT_ID",
    "load_balancer_snapshot_id": "$LB_SNAPSHOT_ID"
}
EOL
morphcloud instance copy worker_config.json "$LB_INSTANCE_ID:/root/hashservice/"
rm worker_config.json

# Start load balancer service
log "Starting load balancer service..."
morphcloud instance exec "$LB_INSTANCE_ID" "sudo systemctl daemon-reload"
morphcloud instance exec "$LB_INSTANCE_ID" "sudo systemctl enable hash-balancer.service"
morphcloud instance exec "$LB_INSTANCE_ID" "sudo systemctl start hash-balancer.service"

# Expose load balancer HTTP port
log "Exposing load balancer HTTP port..."
morphcloud instance expose-http "$LB_INSTANCE_ID" "web" 8000

# Get load balancer URL
log "Getting load balancer details..."
LB_INFO=$(morphcloud instance get "$LB_INSTANCE_ID")
LB_URL=$(echo "$LB_INFO" | python3 -c "
import sys, json
info = json.load(sys.stdin)
services = info['networking']['http_services']
for service in services:
    if service['name'] == 'web':
        print(service['url'])
        break
")

if [ -z "$LB_URL" ]; then
    log "Failed to get load balancer URL"
    exit 1
fi

log "Setup complete!"
log "Load balancer is running at $LB_URL"
log "Permanent worker is running at $WORKER_URL"