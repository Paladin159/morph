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

sleep 2

# Run load test
TOTAL_REQUESTS=16384
CONCURRENT_REQUESTS=4000
SUCCESSFUL=0
FAILED=0

log "Starting load test with $TOTAL_REQUESTS requests..."
START_TIME=$(date +%s)

# Send all requests as quickly as possible
for ((i=0; i<TOTAL_REQUESTS; i+=$CONCURRENT_REQUESTS)); do
    BATCH_SIZE=$((TOTAL_REQUESTS - i))
    if [ $BATCH_SIZE -gt $CONCURRENT_REQUESTS ]; then
        BATCH_SIZE=$CONCURRENT_REQUESTS
    fi
    
    # Launch concurrent requests
    for ((j=0; j<BATCH_SIZE; j++)); do
        REQUEST_NUM=$((i + j))
        (
            RESPONSE=$(curl -s -w "\n%{http_code}" -X POST \
                -H "Content-Type: application/json" \
                -d "{\"input_string\":\"test_string_$REQUEST_NUM\"}" \
                "$LOAD_BALANCER_URL/hash")
            STATUS_CODE=$(echo "$RESPONSE" | tail -n1)
            RESPONSE_BODY=$(echo "$RESPONSE" | head -n1)
            
            if [ "$STATUS_CODE" = "200" ]; then
                echo "success $REQUEST_NUM"
            else
                echo "fail $REQUEST_NUM $STATUS_CODE"
            fi
        ) &
    done
    wait
done | while read line; do
    if [[ $line == success* ]]; then
        SUCCESSFUL=$((SUCCESSFUL + 1))
    else
        FAILED=$((FAILED + 1))
        echo "$line"
    fi
    TOTAL=$((SUCCESSFUL + FAILED))
    if [ $((TOTAL % 50)) -eq 0 ]; then
        log "Progress: $TOTAL/$TOTAL_REQUESTS (Success: $SUCCESSFUL, Failed: $FAILED)"
    fi
done

END_TIME=$(date +%s)
TOTAL_TIME=$((END_TIME - START_TIME))

# Print results
log "Load Test Results:"
log "Total Requests: $TOTAL_REQUESTS"
log "Successful Requests: $SUCCESSFUL"
log "Failed Requests: $FAILED"
log "Total Time: $TOTAL_TIME seconds"
if [ $TOTAL_TIME -gt 0 ]; then
    RPS=$(echo "scale=2; $TOTAL_REQUESTS / $TOTAL_TIME" | bc)
    log "Requests per second: $RPS"
fi

# Wait a bit to ensure all requests are processed
log "Waiting for any remaining requests to complete..."
sleep 5

# Clean up workers
log "Cleaning up worker instances..."
WORKER_LIST=$(morphcloud instance list | grep "morphvm_" | awk '{print $1}')
for worker_id in $WORKER_LIST; do
    log "Stopping worker: $worker_id"
    morphcloud instance stop "$worker_id"
done

# Clean up load balancer instance
log "Cleaning up load balancer instance..."
morphcloud instance stop "$INSTANCE_ID"
log "Cleanup complete!" 