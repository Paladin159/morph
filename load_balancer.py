import asyncio
import logging
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import aiohttp
from typing import List, Dict, Optional
import time
from morphcloud.api import MorphCloudClient

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("load_balancer")

app = FastAPI()

# Configuration
MAX_WORKERS = 12
REQUESTS_PER_WORKER = 16
DEFAULT_TIMEOUT = 30
WORKER_PORT = 5000

class HashRequest(BaseModel):
    input_string: str

class WorkerManager:
    def __init__(self):
        self.morph_client = MorphCloudClient()
        self.workers: Dict[str, Dict] = {}
        self.request_counts: Dict[str, int] = {}
        self.lock = asyncio.Lock()
        self.worker_snapshot_id = None
        self.template_worker_id = None

    async def initialize_worker_template(self):
        """Create initial worker snapshot and instance to branch from"""
        try:
            logger.info("Creating initial worker snapshot...")
            # Create minimal snapshot
            snapshot = self.morph_client.snapshots.create(
                vcpus=1,
                memory=512,
                disk_size=2056,
                image_id="morphvm-minimal"
            )
            logger.info(f"Created initial snapshot: {snapshot.id}")

            # Start template instance
            instance = self.morph_client.instances.start(snapshot_id=snapshot.id)
            logger.info(f"Started template instance: {instance.id}")

            # Wait for instance to be ready
            await asyncio.sleep(30)

            # Copy files using SSH
            with instance.ssh() as ssh:
                logger.info("Copying worker files...")
                ssh.put("worker.py", "/root/worker.py")
                ssh.put("worker.service", "/etc/systemd/system/worker.service")
                
                logger.info("Installing dependencies...")
                ssh.run("sudo apt-get update")
                ssh.run("sudo apt-get install -y python3-full python3-venv")
                ssh.run("python3 -m venv /venv")
                ssh.run("/venv/bin/pip install fastapi uvicorn[standard] aiohttp pydantic")
                
                logger.info("Starting worker service...")
                ssh.run("sudo systemctl daemon-reload")
                ssh.run("sudo systemctl enable worker.service")
                ssh.run("sudo systemctl start worker.service")

            # Create final snapshot from configured instance
            template_snapshot = self.morph_client.snapshots.create(
                instance_id=instance.id,
                metadata={"type": "worker_template"}
            )
            
            self.worker_snapshot_id = template_snapshot.id
            self.template_worker_id = instance.id
            
            logger.info(f"Worker template setup complete. Snapshot ID: {self.worker_snapshot_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize worker template: {str(e)}")
            raise

    async def get_or_create_worker(self):
        async with self.lock:
            if not self.worker_snapshot_id:
                logger.info("No worker snapshot exists, initializing...")
                await self.initialize_worker_template()

            # Find available worker
            for worker_id, count in self.request_counts.items():
                if count < REQUESTS_PER_WORKER:
                    self.request_counts[worker_id] += 1
                    logger.debug(f"Reusing worker {worker_id}, load: {count + 1}")
                    return self.workers[worker_id]

            # Create new worker if under limit
            if len(self.workers) < MAX_WORKERS:
                worker = await self.create_worker()
                self.workers[worker['id']] = worker
                self.request_counts[worker['id']] = 1
                logger.info(f"Created worker {worker['id']}, total: {len(self.workers)}")
                return worker

            logger.warning("All workers at capacity")
            return None

    async def create_worker(self):
        try:
            logger.info("Branching new worker instance...")
            # Branch from template instance
            instance = self.morph_client.instances.branch(
                instance_id=self.template_worker_id,
                metadata={"type": "worker"}
            )
            
            # Wait for instance to be ready
            await asyncio.sleep(10)
            
            # Get instance details
            instance_info = self.morph_client.instances.get(instance.id)
            
            return {
                'id': instance.id,
                'ip': instance_info.ip,
                'port': WORKER_PORT
            }
        except Exception as e:
            logger.error(f"Failed to create worker: {str(e)}")
            raise

    async def release_worker(self, worker_id: str):
        async with self.lock:
            if worker_id in self.request_counts:
                self.request_counts[worker_id] -= 1
                logger.debug(f"Released worker {worker_id}, load: {self.request_counts[worker_id]}")
                
                # Don't destroy template worker
                if (worker_id != self.template_worker_id and 
                    self.request_counts[worker_id] <= 0):
                    await self.destroy_worker(worker_id)

    async def destroy_worker(self, worker_id: str):
        try:
            logger.info(f"Destroying worker {worker_id}")
            self.morph_client.instances.delete(worker_id)
            del self.workers[worker_id]
            del self.request_counts[worker_id]
        except Exception as e:
            logger.error(f"Failed to destroy worker: {str(e)}")

worker_manager = WorkerManager()

@app.post("/hash")
async def hash_string(request: HashRequest):
    start_time = time.time()
    
    while True:
        if time.time() - start_time > DEFAULT_TIMEOUT:
            raise HTTPException(status_code=503, detail="Service unavailable")

        worker = await worker_manager.get_or_create_worker()
        if worker:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"http://{worker['ip']}:{worker['port']}/hash",
                        json={"input_string": request.input_string}
                    ) as response:
                        result = await response.json()
                        await worker_manager.release_worker(worker['id'])
                        return result
            except Exception as e:
                logger.error(f"Request failed: {str(e)}")
                await worker_manager.release_worker(worker['id'])
                raise HTTPException(status_code=500, detail=str(e))
        
        await asyncio.sleep(0.1)

@app.get("/health")
async def health_check():
    return {"status": "healthy"}