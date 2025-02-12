import requests

url = "/api/snapshot/:snapshot_id"

payload = {}
headers = {
  'Authorization': 'Bearer <TOKEN>'
}

response = requests.request("DELETE", url, headers=headers, data=payload)

print(response.text)