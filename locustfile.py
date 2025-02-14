from locust import HttpUser, task, between
import random
import string

class HashUser(HttpUser):
    wait_time = between(0.1, 0.5)  # Wait between 0.1 and 0.5 seconds between tasks
    
    def random_string(self, length=10):
        return ''.join(random.choices(string.ascii_letters + string.digits, k=length))
    
    @task
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