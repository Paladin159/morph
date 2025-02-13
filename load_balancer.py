import asyncio
import logging
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import aiohttp
from typing import List, Dict, Optional
import time
import requests
import json
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("load_balancer")

app = FastAPI()

# Configuration
MAX_WORKERS = 12
REQUESTS_PER_WORKER = 16
DEFAULT_TIMEOUT = 30
WORKER_PORT = 5000
WORKER_IDLE_TIMEOUT = 30

# Global connection pool for worker requests
aiohttp_session = None

async def get_aiohttp_session():
    global aiohttp_session
    if aiohttp_session is None:
        conn = aiohttp.TCPConnector(limit=1000, force_close=False, enable_cleanup_closed=True)
        timeout = aiohttp.ClientTimeout(total=30, connect=10, sock_read=20)
        aiohttp_session = aiohttp.ClientSession(connector=conn, timeout=timeout)
    return aiohttp_session

@app.on_event("shutdown")
async def shutdown_event():
    global aiohttp_session
    if aiohttp_session:
        await aiohttp_session.close()
        aiohttp_session = None

# Request tracking
class RequestTracker:
    def __init__(self):
        self.processed = 0
        self.total = 0
        self.lock = asyncio.Lock()
        self.last_log_time = 0
        self.log_interval = 0.5  # Log every 0.5 seconds

    async def increment_processed(self):
        async with self.lock:
            self.processed += 1
            current_time = time.time()
            if current_time - self.last_log_time >= self.log_interval:
                print(f"\rRequests processed: {self.processed:,}/{self.total:,}", end="", flush=True)
                self.last_log_time = current_time

    async def add_to_total(self, count=1):
        async with self.lock:
            self.total += count
            print(f"\rTotal requests to process: {self.total:,}")

request_tracker = RequestTracker()

class HashRequest(BaseModel):
    input_string: str

class WorkerManager:
    def __init__(self):
        self.api_key = "morph_fdBw4OOQ9NwU6REhYRtmnv"
        self.api_base = "https://cloud.morph.so"
        self.workers: Dict[str, Dict] = {}
        self.request_counts: Dict[str, int] = {}
        self.last_request_time: Dict[str, float] = {}
        self.lock = asyncio.Lock()
        self.worker_creation_lock = asyncio.Lock()  # Separate lock for worker creation
        
        # Load permanent worker info
        try:
            with open('/root/hashservice/worker_config.json', 'r') as f:
                config = json.load(f)
                self.permanent_worker_id = config['template_worker_id']
                self.permanent_worker_url = config['template_worker_url']
                logger.info(f"Loaded permanent worker config: ID={self.permanent_worker_id}, URL={self.permanent_worker_url}")
                
                # Add permanent worker to our tracking
                self.workers[self.permanent_worker_id] = {
                    'id': self.permanent_worker_id,
                    'url': self.permanent_worker_url,
                    'port': WORKER_PORT
                }
                self.request_counts[self.permanent_worker_id] = 0
                self.last_request_time[self.permanent_worker_id] = time.time()  # For future cleanup
        except Exception as e:
            logger.error(f"Failed to load worker config: {str(e)}")
            raise

    def _headers(self):
        return {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Authorization': f'Bearer {self.api_key}'
        }

    async def get_or_create_worker(self):
        async with self.lock:
            # First try to find an available worker
            for worker_id, count in self.request_counts.items():
                if count < REQUESTS_PER_WORKER:
                    self.request_counts[worker_id] += 1
                    self.last_request_time[worker_id] = time.time()
                    return self.workers[worker_id]

        # If no worker is available, try to create one (with separate lock)
        async with self.worker_creation_lock:
            # Check again in case another task created a worker
            async with self.lock:
                for worker_id, count in self.request_counts.items():
                    if count < REQUESTS_PER_WORKER:
                        self.request_counts[worker_id] += 1
                        self.last_request_time[worker_id] = time.time()
                        return self.workers[worker_id]

            if len(self.workers) < MAX_WORKERS:
                try:
                    await self.create_worker()
                except Exception as e:
                    logger.error(f"Failed to create worker: {str(e)}")

            # Use the least loaded worker
            async with self.lock:
                min_count = float('inf')
                best_worker = None
                for worker_id, count in self.request_counts.items():
                    if count < min_count:
                        min_count = count
                        best_worker = self.workers[worker_id]
                if best_worker:
                    self.request_counts[best_worker['id']] += 1
                    self.last_request_time[best_worker['id']] = time.time()
                    return best_worker

                # If all else fails, use permanent worker
                self.request_counts[self.permanent_worker_id] += 1
                self.last_request_time[self.permanent_worker_id] = time.time()
                return self.workers[self.permanent_worker_id]

    async def create_worker(self):
        try:
            logger.info("Branching new worker instance...")
            # Branch from permanent worker
            response = requests.post(
                f"{self.api_base}/api/instance/{self.permanent_worker_id}/branch",
                headers=self._headers(),
                json={"metadata": {"type": "worker"}}
            )
            data = response.json()
            instances = data.get('instances', [])
            if not instances:
                raise Exception("No instances in branch response")
            instance = instances[0]
            instance_id = instance.get('id')
            if not instance_id:
                raise Exception("Failed to get branched instance ID from response")
            
            logger.info(f"Created worker instance {instance_id}, waiting for readiness...")
            
            # Wait for instance and service to be ready
            max_attempts = 30
            for attempt in range(max_attempts):
                try:
                    # Get instance details
                    response = requests.get(
                        f"{self.api_base}/api/instance/{instance_id}",
                        headers=self._headers()
                    )
                    instance_info = response.json()
                    
                    # Get HTTP service URL
                    http_services = instance_info.get('networking', {}).get('http_services', [])
                    worker_url = None
                    for service in http_services:
                        if service.get('name') == 'worker' and service.get('port') == WORKER_PORT:
                            worker_url = service.get('url')
                            break
                    
                    if not worker_url:
                        raise Exception("Worker HTTP service not found in instance info")
                    
                    logger.info(f"Attempting to connect to worker at {worker_url}/health")
                    # Try to connect to the worker's health endpoint
                    async with aiohttp.ClientSession() as session:
                        async with session.get(
                            f"{worker_url}/health",
                            timeout=aiohttp.ClientTimeout(total=5)
                        ) as response:
                            if response.status == 200:
                                logger.info(f"Worker {instance_id} is ready")
                                async with self.lock:
                                    self.workers[instance_id] = {
                                        'id': instance_id,
                                        'url': worker_url,
                                        'port': WORKER_PORT
                                    }
                                    self.request_counts[instance_id] = 0
                                    self.last_request_time[instance_id] = time.time()
                                return
                except Exception as e:
                    logger.debug(f"Worker not ready yet (attempt {attempt + 1}/{max_attempts}): {str(e)}")
                    await asyncio.sleep(2)
            
            # If we get here, worker failed to become ready
            logger.error(f"Worker {instance_id} failed to become ready after {max_attempts} attempts")
            try:
                requests.delete(
                    f"{self.api_base}/api/instance/{instance_id}",
                    headers=self._headers()
                )
            except:
                pass
            
        except Exception as e:
            logger.error(f"Failed to create worker: {str(e)}")
            # No need to re-raise since this is running in background

    async def release_worker(self, worker_id: str):
        async with self.lock:
            if worker_id in self.request_counts:
                self.request_counts[worker_id] -= 1
                self.last_request_time[worker_id] = time.time()  # For future cleanup
                logger.debug(f"Released worker {worker_id}, load: {self.request_counts[worker_id]}")

    async def cleanup_workers(self):
        """Clean up all worker instances except the permanent worker and delete snapshots"""
        try:
            print("\nAll requests processed, cleaning up...")
            
            # First clean up worker instances
            print("Cleaning up worker instances...")
            async with self.lock:
                for worker_id in list(self.workers.keys()):
                    if worker_id != self.permanent_worker_id:
                        try:
                            print(f"Deleting worker instance {worker_id}")
                            response = requests.delete(
                                f"{self.api_base}/api/instance/{worker_id}",
                                headers=self._headers()
                            )
                            if response.status_code not in (200, 204):
                                logger.warning(f"Unexpected status code when deleting worker: {response.status_code}")
                            del self.workers[worker_id]
                            del self.request_counts[worker_id]
                            del self.last_request_time[worker_id]
                        except Exception as e:
                            logger.error(f"Error cleaning up worker {worker_id}: {str(e)}")
            
            # Then clean up snapshots
            print("Cleaning up snapshots...")
            try:
                # Get snapshot IDs to preserve from config
                with open('/root/hashservice/worker_config.json', 'r') as f:
                    config = json.load(f)
                    worker_snapshot = config.get('worker_snapshot_id')
                    lb_snapshot = config.get('load_balancer_snapshot_id')
                    preserved_snapshots = {worker_snapshot, lb_snapshot}

                # Get all snapshots
                response = requests.get(
                    f"{self.api_base}/api/snapshots",
                    headers=self._headers()
                )
                snapshots = response.json().get('snapshots', [])
                for snapshot in snapshots:
                    snapshot_id = snapshot.get('id')
                    if snapshot_id and snapshot_id not in preserved_snapshots:
                        print(f"Deleting snapshot {snapshot_id}")
                        requests.delete(
                            f"{self.api_base}/api/snapshot/{snapshot_id}",
                            headers=self._headers()
                        )
            except Exception as e:
                logger.error(f"Error cleaning up snapshots: {str(e)}")
            
            print("Cleanup complete!")
            
        except Exception as e:
            logger.error(f"Error during cleanup: {str(e)}")

worker_manager = WorkerManager()

@app.post("/hash")
async def hash_string(request: HashRequest):
    start_time = time.time()
    await request_tracker.add_to_total()
    
    try:
        worker = None
        session = await get_aiohttp_session()
        
        while True:
            if time.time() - start_time > DEFAULT_TIMEOUT:
                logger.error("Request timed out waiting for worker")
                # Run cleanup if this was the last request
                if request_tracker.processed == request_tracker.total:
                    print("\nAll requests processed, cleaning up workers...")
                    await worker_manager.cleanup_workers()
                    print("\nCleanup complete!")
                raise HTTPException(status_code=503, detail="Service unavailable - timeout waiting for worker")

            try:
                worker = await worker_manager.get_or_create_worker()
                if not worker:
                    continue

                worker_url = f"{worker['url']}/hash"
                async with session.post(
                    worker_url,
                    json={"input_string": request.input_string},
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Worker returned error {response.status}: {error_text}")
                        await worker_manager.release_worker(worker['id'])
                        # Run cleanup if this was the last request
                        if request_tracker.processed == request_tracker.total:
                            print("\nAll requests processed, cleaning up workers...")
                            await worker_manager.cleanup_workers()
                            print("\nCleanup complete!")
                        raise HTTPException(status_code=response.status, detail=error_text)
                    
                    result = await response.json()
                    await worker_manager.release_worker(worker['id'])
                    await request_tracker.increment_processed()
                    
                    # Run cleanup if this was the last request
                    if request_tracker.processed == request_tracker.total:
                        print("\nAll requests processed, cleaning up workers...")
                        await worker_manager.cleanup_workers()
                        print("\nCleanup complete!")
                    
                    return result

            except aiohttp.ClientError as e:
                logger.error(f"Network error with worker {worker['id'] if worker else 'unknown'}: {str(e)}")
                if worker:
                    await worker_manager.release_worker(worker['id'])
                # Run cleanup if this was the last request
                if request_tracker.processed == request_tracker.total:
                    print("\nAll requests processed, cleaning up workers...")
                    await worker_manager.cleanup_workers()
                    print("\nCleanup complete!")
                # Don't raise here, let it retry with a different worker
                await asyncio.sleep(0.1)  # Small delay before retry
                continue

    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        if worker:
            await worker_manager.release_worker(worker['id'])
        # Run cleanup if this was the last request
        if request_tracker.processed == request_tracker.total:
            print("\nAll requests processed, cleaning up workers...")
            await worker_manager.cleanup_workers()
            print("\nCleanup complete!")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    return {"status": "healthy"}