import hashlib
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from concurrent.futures import ThreadPoolExecutor
import asyncio
import logging
import psutil
import time
from typing import Optional

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("worker")

app = FastAPI()

# Configure max concurrent processes per worker
MAX_CONCURRENT_PROCESSES = 16
THREAD_MULTIPLIER = 4  # Use more threads than processes for better throughput
thread_pool = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_PROCESSES * THREAD_MULTIPLIER)

# Track CPU usage
last_cpu_check = 0
cpu_check_interval = 1.0  # Check CPU every second
current_cpu_usage = 0.0

def get_cpu_usage():
    global last_cpu_check, current_cpu_usage
    current_time = time.time()
    if current_time - last_cpu_check >= cpu_check_interval:
        current_cpu_usage = psutil.cpu_percent(interval=None)
        last_cpu_check = current_time
    return current_cpu_usage

class HashRequest(BaseModel):
    input_string: str

def calculate_hash(input_string: str) -> str:
    return hashlib.sha256(input_string.encode()).hexdigest()

@app.post("/hash")
async def hash_string(request: HashRequest):
    try:
        # Use thread pool to handle CPU-bound hash calculation
        result = await asyncio.get_event_loop().run_in_executor(
            thread_pool, 
            calculate_hash, 
            request.input_string
        )
        return {"hash": result}
    except Exception as e:
        logger.error(f"Error processing hash request: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "max_processes": MAX_CONCURRENT_PROCESSES,
        "thread_pool_size": MAX_CONCURRENT_PROCESSES * THREAD_MULTIPLIER,
        "cpu_usage": get_cpu_usage(),
        "active_threads": len([t for t in thread_pool._threads if t.is_alive()])
    }