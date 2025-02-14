from locust import HttpUser, task, between, events, constant
import random
import string
import time

# Track global statistics
test_start_time = None
total_requests = 0
successful_requests = 0
failed_requests = 0

@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    global test_start_time
    test_start_time = time.time()

@events.request.add_listener
def on_request(request_type, name, response_time, response_length, response, context, exception, **kwargs):
    global total_requests, successful_requests, failed_requests
    total_requests += 1
    if exception is None and response.status_code == 200:
        successful_requests += 1
    else:
        failed_requests += 1

@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    if test_start_time is not None:
        total_time = time.time() - test_start_time
        print("\nTest Summary:")
        print(f"Total Requests: {total_requests}")
        print(f"Successful Requests: {successful_requests}")
        print(f"Failed Requests: {failed_requests}")
        print(f"Total Time: {total_time:.2f} seconds")
        print(f"Average RPS: {total_requests/total_time:.2f}")

class HashUser(HttpUser):
    # No wait time between tasks to maximize load
    wait_time = constant(0)
    
    def random_string(self, length=10):
        return ''.join(random.choices(string.ascii_letters + string.digits, k=length))
    
    @task(1)
    def hash_string(self):
        payload = {"input_string": self.random_string()}
        with self.client.post("/hash", json=payload, catch_response=True) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Got status code {response.status_code}: {response.text}")
    
    @task(3)  # Run health check more frequently
    def health_check(self):
        with self.client.get("/health", catch_response=True) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Got status code {response.status_code}: {response.text}") 