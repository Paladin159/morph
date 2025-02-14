import asyncio
import logging
import sys
from fastapi import FastAPI, HTTPException, BackgroundTasks
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

# Startup verification
logger.info("Starting load balancer...")
logger.info(f"Python executable: {sys.executable}")
logger.info(f"Python version: {sys.version}")
logger.info(f"WORKER_SNAPSHOT_ID: {os.environ.get('WORKER_SNAPSHOT_ID')}")
logger.info(f"MORPH_API_KEY present: {'Yes' if os.environ.get('MORPH_API_KEY') else 'No'}")

app = FastAPI()

# Configuration
MAX_WORKERS = 12
REQUESTS_PER_WORKER = 16
DEFAULT_TIMEOUT = 60
WORKER_PORT = 5000
WORKER_IDLE_TIMEOUT = 30
WORKER_SNAPSHOT_ID = os.environ.get('WORKER_SNAPSHOT_ID')
if not WORKER_SNAPSHOT_ID:
    logger.error("WORKER_SNAPSHOT_ID environment variable is not set!")
    raise ValueError("WORKER_SNAPSHOT_ID environment variable must be set")
logger.info(f"Using worker snapshot ID: {WORKER_SNAPSHOT_ID}")

# Global connection pool for worker requests
aiohttp_session = None

# Request queue
request_queue = asyncio.Queue()
processing_task = None

async def get_aiohttp_session():
    global aiohttp_session
    if aiohttp_session is None:
        conn = aiohttp.TCPConnector(limit=None, force_close=False, enable_cleanup_closed=True)
        timeout = aiohttp.ClientTimeout(total=60, connect=10, sock_read=50)
        aiohttp_session = aiohttp.ClientSession(connector=conn, timeout=timeout)
    return aiohttp_session

@app.on_event("shutdown")
async def shutdown_event():
    global aiohttp_session
    if aiohttp_session:
        await aiohttp_session.close()
        aiohttp_session = None
    
    # Clean up all worker instances on shutdown
    logger.info("Cleaning up worker instances on shutdown")
    try:
        async with worker_manager.lock:
            for worker_id in list(worker_manager.workers.keys()):
                try:
                    logger.info(f"Deleting worker instance {worker_id}")
                    response = requests.delete(
                        f"{worker_manager.api_base}/instance/{worker_id}",
                        headers=worker_manager._headers()
                    )
                    if response.status_code not in (200, 204):
                        logger.warning(f"Unexpected status code when deleting worker: {response.status_code}")
                    del worker_manager.workers[worker_id]
                    del worker_manager.request_counts[worker_id]
                    del worker_manager.last_request_time[worker_id]
                    logger.info(f"Successfully deleted worker {worker_id}")
                except Exception as e:
                    logger.error(f"Error cleaning up worker {worker_id}: {str(e)}")
    except Exception as e:
        logger.error(f"Error during shutdown cleanup: {str(e)}")

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
        self.api_key = os.environ.get('MORPH_API_KEY')
        if not self.api_key:
            raise ValueError("MORPH_API_KEY environment variable must be set")
        self.api_base = "https://cloud.morph.so/api"
        self.workers: Dict[str, Dict] = {}
        self.request_counts: Dict[str, int] = {}
        self.last_request_time: Dict[str, float] = {}
        self.lock = asyncio.Lock()
        self.worker_creation_lock = asyncio.Lock()

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
                return None

    async def create_worker(self):
        try:
            logger.info(f"Starting new worker from snapshot {WORKER_SNAPSHOT_ID}")
            
            response = requests.post(
                f"{self.api_base}/instance?snapshot_id={WORKER_SNAPSHOT_ID}",
                headers=self._headers()
            )
            data = response.json()
            instance_id = data.get('id')
            if not instance_id:
                raise Exception("Failed to get instance ID from start response")
            
            logger.info(f"Started worker instance {instance_id}")
            
            max_attempts = 30
            for attempt in range(max_attempts):
                try:
                    response = requests.get(
                        f"{self.api_base}/instance/{instance_id}",
                        headers=self._headers()
                    )
                    logger.info(f"GET instance info response status: {response.status_code}")
                    logger.info(f"GET instance info response body: {response.text}")
                    
                    instance_info = response.json()
                    
                    # Get internal IP
                    internal_ip = instance_info.get('networking', {}).get('internal_ip')
                    if not internal_ip:
                        logger.error(f"Worker internal IP not found. Instance info: {instance_info}")
                        raise Exception("Worker internal IP not found")
                    
                    logger.info(f"Worker {instance_id} assigned internal IP: {internal_ip}")
                    logger.info(f"Attempting to connect to worker at http://{internal_ip}:{WORKER_PORT}/health")
                    
                    async with aiohttp.ClientSession() as session:
                        try:
                            async with session.get(
                                f"http://{internal_ip}:{WORKER_PORT}/health",
                                timeout=aiohttp.ClientTimeout(total=5)
                            ) as response:
                                response_text = await response.text()
                                logger.info(f"Health check response status: {response.status}, body: {response_text}")
                                if response.status == 200:
                                    logger.info(f"Worker {instance_id} is ready and healthy at {internal_ip}:{WORKER_PORT}")
                                    async with self.lock:
                                        self.workers[instance_id] = {
                                            'id': instance_id,
                                            'internal_ip': internal_ip,
                                            'port': WORKER_PORT
                                        }
                                        self.request_counts[instance_id] = 0
                                        self.last_request_time[instance_id] = time.time()
                                        logger.info(f"Active workers: {', '.join(self.workers.keys())}")
                                    return
                        except Exception as e:
                            logger.error(f"Error checking worker health: {str(e)}")
                except Exception as e:
                    logger.debug(f"Worker {instance_id} not ready yet (attempt {attempt + 1}/{max_attempts}): {str(e)}")
                    await asyncio.sleep(2)
            
            logger.error(f"Worker {instance_id} failed to become ready after {max_attempts} attempts")
            try:
                logger.info(f"Cleaning up failed worker instance {instance_id}")
                requests.delete(
                    f"{self.api_base}/instance/{instance_id}",
                    headers=self._headers()
                )
            except:
                pass
            
        except Exception as e:
            logger.error(f"Failed to create worker: {str(e)}")

    async def release_worker(self, worker_id: str):
        async with self.lock:
            if worker_id in self.request_counts:
                self.request_counts[worker_id] -= 1
                self.last_request_time[worker_id] = time.time()
                logger.debug(f"Released worker {worker_id}, load: {self.request_counts[worker_id]}")

    async def cleanup_workers(self):
        """Clean up all worker instances"""
        try:
            # Wait a bit to ensure all requests are truly done
            await asyncio.sleep(5)
            
            print("\nAll requests processed!")
            print(f"Total workers created: {len(self.workers)}")
            print(f"Active workers: {', '.join(self.workers.keys())}")
            
            print("\nStarting cleanup process...")
            print("Cleaning up worker instances...")
            async with self.lock:
                for worker_id in list(self.workers.keys()):
                    try:
                        print(f"Deleting worker instance {worker_id}")
                        response = requests.delete(
                            f"{self.api_base}/instance/{worker_id}",
                            headers=self._headers()
                        )
                        if response.status_code not in (200, 204):
                            logger.warning(f"Unexpected status code when deleting worker: {response.status_code}")
                        del self.workers[worker_id]
                        del self.request_counts[worker_id]
                        del self.last_request_time[worker_id]
                        print(f"Successfully deleted worker {worker_id}")
                    except Exception as e:
                        logger.error(f"Error cleaning up worker {worker_id}: {str(e)}")
            
            print("Cleanup complete!")
            
        except Exception as e:
            logger.error(f"Error during cleanup: {str(e)}")

worker_manager = WorkerManager()

async def process_request_queue():
    """Background task to process queued requests"""
    while True:
        try:
            # Process requests as they come
            request_data = await request_queue.get()
            
            # Create task for the request
            asyncio.create_task(process_single_request(request_data))
                
        except Exception as e:
            logger.error(f"Error in queue processor: {str(e)}")

async def process_single_request(request_data):
    """Process a single request from the queue"""
    input_string = request_data["input_string"]
    future = request_data["future"]
    worker = None
    
    try:
        session = await get_aiohttp_session()
        retry_count = 0
        max_retries = 3
        
        while retry_count < max_retries:
            try:
                worker = await worker_manager.get_or_create_worker()
                if not worker:
                    retry_count += 1
                    continue

                # Use internal IP for worker communication
                worker_url = f"http://{worker['internal_ip']}:{worker['port']}/hash"
                async with session.post(
                    worker_url,
                    json={"input_string": input_string},
                    timeout=aiohttp.ClientTimeout(total=20)
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Worker returned error {response.status}: {error_text}")
                        if worker:
                            await worker_manager.release_worker(worker['id'])
                        retry_count += 1
                        continue
                    
                    result = await response.json()
                    await worker_manager.release_worker(worker['id'])
                    await request_tracker.increment_processed()
                    future.set_result(result)
                    break

            except aiohttp.ClientError as e:
                logger.error(f"Network error with worker {worker['id'] if worker else 'unknown'}: {str(e)}")
                if worker:
                    await worker_manager.release_worker(worker['id'])
                retry_count += 1
                continue

        if retry_count >= max_retries:
            future.set_exception(HTTPException(status_code=503, detail="Service unavailable after retries"))

    except Exception as e:
        logger.error(f"Unexpected error processing request: {str(e)}")
        if worker:
            await worker_manager.release_worker(worker['id'])
        future.set_exception(HTTPException(status_code=500, detail=str(e)))

    request_queue.task_done()

@app.post("/hash")
async def hash_string(request: HashRequest, background_tasks: BackgroundTasks):
    await request_tracker.add_to_total()
    
    # Start the queue processor if it's not running
    global processing_task
    if processing_task is None or processing_task.done():
        processing_task = asyncio.create_task(process_request_queue())
    
    # Create a future to get the result
    future = asyncio.Future()
    
    # Queue the request
    await request_queue.put({
        "input_string": request.input_string,
        "future": future
    })
    
    try:
        # Wait for the result with timeout
        return await asyncio.wait_for(future, timeout=DEFAULT_TIMEOUT)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Request timed out")
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    return {"status": "healthy"}