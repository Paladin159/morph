import requests
import json

url = "/api/instance/:instance_id/snapshot"

payload = json.dumps({
  "metadata": {}
})
headers = {
  'Content-Type': 'application/json',
  'Accept': 'application/json',
  'Authorization': 'Bearer <TOKEN>'
}

response = requests.request("POST", url, headers=headers, data=payload)

print(response.text)