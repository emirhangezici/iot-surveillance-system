"""Run the local Sentinel demo: python IoT.py."""

from sentinel import create_app

app = create_app()

if __name__ == "__main__":
    print("Sentinel local demo: http://127.0.0.1:5000 (simulated device)")
    app.run(host="127.0.0.1", port=5000, debug=False)
