from flask import Flask, request
import hashlib

app = Flask(__name__)

@app.route("/hash", methods=["POST"])
def calculate_hash():
    data = request.get_json()
    if not data or "string" not in data:
        return {"error": "Missing string in request"}, 400
    
    result = hashlib.sha256(data["string"].encode()).hexdigest()
    return {"hash": result}

if __name__ == "__main__":
    print("Worker started and listening on port 5000...")
    app.run(host="0.0.0.0", port=5000)
