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

# Create snapshot
log "Creating snapshot..."
SNAPSHOT_ID=$(morphcloud snapshot create --memory 512 --disk-size 2056 --image-id morphvm-minimal)
log "Created snapshot: $SNAPSHOT_ID"

# Start instance
log "Starting instance..."
INSTANCE_ID=$(morphcloud instance start "$SNAPSHOT_ID")
log "Started instance: $INSTANCE_ID"

# Wait for instance to be ready
log "Waiting for instance to be ready..."
sleep 5

# Copy files to instance
log "Copying files..."
morphcloud instance copy load_balancer.py "$INSTANCE_ID:~/load_balancer.py"
log "Copied load_balancer.py"
morphcloud instance copy worker.py "$INSTANCE_ID:~/worker.py"
log "Copied worker.py"

# Create service file with correct uvicorn command
log "Creating service file..."
cat > hash-balancer.service << EOF
[Unit]
Description=Hash Load Balancer Service
After=network.target

[Service]
User=root
WorkingDirectory=/root
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=/root
ExecStart=/root/venv/bin/uvicorn load_balancer:app --host 0.0.0.0 --port 8000 --log-level debug
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Copy the newly created service file
morphcloud instance copy hash-balancer.service "$INSTANCE_ID:/etc/systemd/system/hash-balancer.service"
log "Copied hash-balancer.service"

# Install dependencies and setup services
log "Installing dependencies..."
morphcloud instance exec "$INSTANCE_ID" "sudo apt-get update"
morphcloud instance exec "$INSTANCE_ID" "sudo apt-get install -y python3-full python3-venv"

# Create and setup virtual environment
log "Setting up Python virtual environment..."
morphcloud instance exec "$INSTANCE_ID" "python3 -m venv ~/venv"
morphcloud instance exec "$INSTANCE_ID" "~/venv/bin/pip install fastapi uvicorn[standard] aiohttp pydantic morphcloud"

# Start the service
log "Setting up services..."
morphcloud instance exec "$INSTANCE_ID" "sudo systemctl daemon-reload"
morphcloud instance exec "$INSTANCE_ID" "sudo systemctl enable hash-balancer.service"
morphcloud instance exec "$INSTANCE_ID" "sudo systemctl start hash-balancer.service"

# Service status check
log "Checking service status..."
morphcloud instance exec "$INSTANCE_ID" "sudo systemctl status hash-balancer.service"

log "Checking detailed service logs..."
morphcloud instance exec "$INSTANCE_ID" "sudo journalctl -u hash-balancer.service -n 50 --no-pager"

log "Checking if files exist and have correct permissions..."
morphcloud instance exec "$INSTANCE_ID" "ls -la /root/load_balancer.py /root/venv/bin/uvicorn"

log "Checking Python environment..."
morphcloud instance exec "$INSTANCE_ID" "/root/venv/bin/python3 -c 'import fastapi, uvicorn; print(\"Imports OK\")'"

# Expose HTTP service
log "Exposing HTTP service..."
morphcloud instance expose-http "$INSTANCE_ID" "web" 8000

# Get instance details
log "Getting instance details..."
INSTANCE_INFO=$(morphcloud instance get "$INSTANCE_ID")
log "Instance info: $INSTANCE_INFO"

# Parse JSON using Python to get the URL
INSTANCE_URL=$(echo "$INSTANCE_INFO" | python3 -c "
import sys, json
info = json.load(sys.stdin)
services = info['networking']['http_services']
for service in services:
    if service['name'] == 'web':
        print(service['url'])
        break
")

if [ -z "$INSTANCE_URL" ]; then
    log "Failed to get instance URL"
    exit 1
fi

log "Setup complete! Load balancer is running at $INSTANCE_URL"

log "Checking service status..."
morphcloud instance exec "$INSTANCE_ID" "sudo systemctl status hash-balancer.service"