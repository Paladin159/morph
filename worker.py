import hashlib
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from concurrent.futures import ThreadPoolExecutor
import asyncio
from typing import Optional

app = FastAPI()

# Configure max concurrent processes per worker
MAX_CONCURRENT_PROCESSES = 16
semaphore = asyncio.Semaphore(MAX_CONCURRENT_PROCESSES)
thread_pool = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_PROCESSES)

class HashRequest(BaseModel):
    input_string: str

def calculate_hash(input_string: str) -> str:
    return hashlib.sha256(input_string.encode()).hexdigest()

@app.post("/hash")
async def hash_string(request: HashRequest):
    async with semaphore:
        # Use thread pool to handle CPU-bound hash calculation
        result = await asyncio.get_event_loop().run_in_executor(
            thread_pool, 
            calculate_hash, 
            request.input_string
        )
        return {"hash": result}

@app.get("/health")
async def health_check():
    return {"status": "healthy"}