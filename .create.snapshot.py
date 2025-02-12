import requests
import json

url = "/api/snapshot"

payload = json.dumps({
  "image_id": "string",
  "readiness_check": {
    "type": "timeout",
    "timeout": 10
  },
  "vcpus": 1,
  "memory": 128,
  "disk_size": 700,
  "digest": "string",
  "metadata": {}
})
headers = {
  'Content-Type': 'application/json',
  'Accept': 'application/json',
  'Authorization': 'Bearer <TOKEN>'
}

response = requests.request("POST", url, headers=headers, data=payload)

print(response.text)