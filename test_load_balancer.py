import asyncio
import aiohttp
import time
import statistics
import logging
from datetime import datetime
import os

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Test configuration
TOTAL_REQUESTS = 16384
LOAD_BALANCER_URL = os.environ.get('LOAD_BALANCER_URL')
if not LOAD_BALANCER_URL:
    raise ValueError("LOAD_BALANCER_URL environment variable must be set")

async def send_request(session, request_id):
    start_time = time.time()
    try:
        async with session.post(
            f"{LOAD_BALANCER_URL}/hash",
            json={"input_string": f"test_string_{request_id}"}
        ) as response:
            response_time = time.time() - start_time
            status = response.status
            if status == 200:
                result = await response.json()
                return {
                    "success": True,
                    "time": response_time,
                    "status": status,
                    "hash": result["hash"]
                }
            else:
                return {
                    "success": False,
                    "time": response_time,
                    "status": status,
                    "error": await response.text()
                }
    except Exception as e:
        return {
            "success": False,
            "time": time.time() - start_time,
            "status": -1,
            "error": str(e)
        }

async def run_load_test():
    logger.info(f"Starting load test with {TOTAL_REQUESTS} requests")
    start_time = time.time()
    
    # Create a connection pool with a large number of connections
    conn = aiohttp.TCPConnector(limit=1000, force_close=False, enable_cleanup_closed=True)
    timeout = aiohttp.ClientTimeout(total=60, connect=30, sock_read=30)  # More forgiving timeouts
    
    async with aiohttp.ClientSession(connector=conn, timeout=timeout) as session:
        # Send requests in smaller batches to avoid overwhelming the system
        batch_size = 1000
        results = []
        
        for i in range(0, TOTAL_REQUESTS, batch_size):
            end = min(i + batch_size, TOTAL_REQUESTS)
            batch = range(i, end)
            batch_results = await asyncio.gather(*[send_request(session, j) for j in batch], return_exceptions=True)
            results.extend(batch_results)
            logger.info(f"Completed batch {i//batch_size + 1}/{(TOTAL_REQUESTS + batch_size - 1)//batch_size}")
            await asyncio.sleep(1)  # Give the system a small breather between batches
    
    total_time = time.time() - start_time
    
    # Analyze results
    successful_requests = [r for r in results if r["success"]]
    failed_requests = [r for r in results if not r["success"]]
    response_times = [r["time"] for r in successful_requests]
    
    # Calculate statistics
    stats = {
        "total_requests": TOTAL_REQUESTS,
        "successful_requests": len(successful_requests),
        "failed_requests": len(failed_requests),
        "total_time": total_time,
        "requests_per_second": TOTAL_REQUESTS / total_time,
        "min_response_time": min(response_times) if response_times else 0,
        "max_response_time": max(response_times) if response_times else 0,
        "avg_response_time": statistics.mean(response_times) if response_times else 0,
        "median_response_time": statistics.median(response_times) if response_times else 0
    }
    
    # Log results
    logger.info("Load Test Results:")
    logger.info(f"Total Requests: {stats['total_requests']}")
    logger.info(f"Successful Requests: {stats['successful_requests']}")
    logger.info(f"Failed Requests: {stats['failed_requests']}")
    logger.info(f"Total Time: {stats['total_time']:.2f} seconds")
    logger.info(f"Requests/second: {stats['requests_per_second']:.2f}")
    logger.info(f"Min Response Time: {stats['min_response_time']:.3f} seconds")
    logger.info(f"Max Response Time: {stats['max_response_time']:.3f} seconds")
    logger.info(f"Average Response Time: {stats['avg_response_time']:.3f} seconds")
    logger.info(f"Median Response Time: {stats['median_response_time']:.3f} seconds")
    
    # Log some failed request details if any
    if failed_requests:
        logger.error("Sample of failed requests:")
        for failed in failed_requests[:5]:  # Show first 5 failures
            logger.error(f"Status: {failed['status']}, Error: {failed['error']}")
    
    return stats

if __name__ == "__main__":
    asyncio.run(run_load_test()) 