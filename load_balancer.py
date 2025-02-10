from flask import Flask, request, jsonify
import requests
import threading
import queue
import time
import os
import json

app = Flask(__name__)

# Configuration
MAX_WORKERS = 16
REQUESTS_PER_WORKER = 16
WORKER_SNAPSHOT_ID = "snapshot_xj3em2jb"
WORKER_INSTANCES = {}
REQUEST_QUEUE = queue.Queue()
PROCESSING_SEMAPHORE = threading.Semaphore(MAX_WORKERS * REQUESTS_PER_WORKER)

API_KEY = os.environ.get("MORPH_API_KEY")
API_BASE = "https://cloud.morph.so/api"
HEADERS = {
    'Content-Type': 'application/json',
    'Accept': 'application/json',
    'Authorization': f'Bearer {API_KEY}'
}

def spawn_worker():
    """Branch the latest available worker and ensure it starts services on the correct ports."""
    try:
        parent_instance_id = "morphvm_v2j2u7ly" if not WORKER_INSTANCES else list(WORKER_INSTANCES.keys())[-1]
        print(f"Branching from: {parent_instance_id}")

        branch_response = requests.post(
            f"{API_BASE}/instance/{parent_instance_id}/branch",
            headers=HEADERS,
            data=json.dumps({"snapshot_metadata": {}, "instance_metadata": {}})
        )
        branch_response.raise_for_status()
        branch_data = branch_response.json()

        if not branch_data.get("instances"):
            print("Error: No instances returned from branch API.")
            return None

        new_instance = branch_data["instances"][0]
        new_instance_id = new_instance["id"]
        print(f"Created branched instance with ID: {new_instance_id}")

        time.sleep(5)

        exec_response = requests.post(
            f"{API_BASE}/instance/{new_instance_id}/exec",
            headers=HEADERS,
            data=json.dumps({
                "command": [
                    "bash", "-c",
                    "systemctl daemon-reload && systemctl restart worker.service"
                ]
            })
        )

        print(f"Service start response: {exec_response.status_code}")

        expose_response = requests.post(
            f"{API_BASE}/instance/{new_instance_id}/http",
            headers=HEADERS,
            data=json.dumps({"name": "worker", "port": 5000})
        )
        expose_response.raise_for_status()
        instance_info = expose_response.json()

        worker_url = None
        for service in instance_info["networking"]["http_services"]:
            if service["port"] == 5000:
                worker_url = service["url"]
                break

        if not worker_url:
            print(f"Error: No valid worker URL found for instance {new_instance_id}")
            return None

        print(f"New worker URL: {worker_url}")

        WORKER_INSTANCES[new_instance_id] = {
            "instance": new_instance,
            "requests": 0,
            "last_used": time.time(),
            "url": worker_url
        }

        return new_instance_id

    except Exception as e:
        print(f"Error branching worker: {e}")
        return None


def wait_for_worker(worker_url, timeout=40):
    """Wait until the new worker is reachable"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            response = requests.get(worker_url, timeout=4)
            if response.status_code == 200:
                print(f"Worker {worker_url} is ready!")
                return True
            print(response)
        except requests.exceptions.RequestException:
            print(f"Worker {worker_url} is not ready yet, retrying...")
            time.sleep(3)

    print(f"Worker {worker_url} did not start in time.")
    return False


def process_request(string_to_hash, worker_id):
    """Forward request to the correct worker"""
    try:
        worker_url = WORKER_INSTANCES[worker_id]["url"]
        print(f"Forwarding request to worker: {worker_url}")

        if not wait_for_worker(worker_url):
            return {"error": "Worker not available"}, 503

        response = requests.post(
            f"{worker_url}/hash",
            json={"string": string_to_hash},
            timeout=10  # Increased timeout
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error processing request: {e}")
        return {"error": str(e)}, 500
    finally:
        WORKER_INSTANCES[worker_id]["requests"] -= 1
        WORKER_INSTANCES[worker_id]["last_used"] = time.time()

def get_available_worker():
    """Get or create an available worker"""
    try:
        print(f"Current workers: {list(WORKER_INSTANCES.keys())}")
        print(f"WORKER_INSTANCES state: {json.dumps(WORKER_INSTANCES, default=str)}")
        
        # Find the worker with the fewest requests
        available_worker = None
        min_requests = REQUESTS_PER_WORKER

        for worker_id, worker_data in WORKER_INSTANCES.items():
            if worker_data["requests"] < min_requests:
                available_worker = worker_id
                min_requests = worker_data["requests"]
                print(f"Found available worker: {worker_id} with {worker_data['requests']} requests")

        # Spawn new worker if needed
        if not available_worker and len(WORKER_INSTANCES) < MAX_WORKERS:
            print("No available workers, spawning new one")
            available_worker = spawn_worker()
            print(f"Spawn worker returned: {available_worker}")

        return available_worker
    except Exception as e:
        print(f"Error in get_available_worker: {e}")
        return None


@app.route("/hash", methods=["POST"])
def hash_endpoint():
    data = request.get_json()
    if not data or "string" not in data:
        return {"error": "Missing string in request"}, 400

    print(f"Received request for string: {data['string']}")
    with PROCESSING_SEMAPHORE:
        worker_id = get_available_worker()
        if not worker_id:
            print("No workers available")
            REQUEST_QUEUE.put(data["string"])
            return {"error": "Service at capacity"}, 503

        print(f"Using worker: {worker_id}")
        WORKER_INSTANCES[worker_id]["requests"] += 1
        return process_request(data["string"], worker_id)


if __name__ == "__main__":
    print(f"Starting load balancer with API key present: {bool(API_KEY)}")
    app.debug = True
    app.run(host="0.0.0.0", port=8000)