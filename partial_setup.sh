#!/bin/bash

set -e

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}
WORKER_INSTANCE_ID="morphvm_1p6cnqdx"
LB_INSTANCE_ID="morphvm_08x56tj7"

# Copy load balancer files
log "Copying load balancer files..."
morphcloud instance copy load_balancer.py "$LB_INSTANCE_ID:/root/hashservice/"
morphcloud instance copy requirements.txt "$LB_INSTANCE_ID:/root/hashservice/"
morphcloud instance copy hash-balancer.service "$LB_INSTANCE_ID:/etc/systemd/system/"

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
    "template_worker_url": "$WORKER_URL"
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

