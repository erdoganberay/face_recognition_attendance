"""
Locust stress test for the recognize_face endpoint.

Setup before running:
  1. Start the server:           docker-compose up
  2. Log in as a teacher and start a session — note the session ID from the URL.
  3. Run Locust:
       docker-compose exec web locust --host=http://localhost:8000

     Or from your machine (if locust is installed locally):
       SESSION_ID=<id> locust --host=http://localhost:8000

  4. Open http://localhost:8089 in your browser.
  5. Set number of users (try 10, 50, 100) and spawn rate (e.g. 5 users/sec).
  6. Click Start and watch the charts.

What to look for:
  - RPS (requests per second): how much throughput the server handles
  - p95 response time: 95% of requests finish within this time
  - Failures: should stay at 0% under normal load
  - When response times spike or failures appear, that is the breaking point
"""

import base64
import os
from locust import HttpUser, task, between

# A minimal valid base64 image.
# face_recognition is NOT called during load testing because we want to measure
# server throughput, not the library's speed.  The endpoint will decode the
# image and call face_recognition — that is intentional, it is the real workload.
# To test pure HTTP overhead without face processing, swap this for a real
# face image encoded in base64.
# Generate a real 100x100 grey JPEG using Pillow (already installed as a
# face_recognition dependency).  face_locations will return [] — no face
# detected — which is the expected result for a blank image.
import io
from PIL import Image

_face_path = os.path.join(os.path.dirname(__file__), 'face_b64.txt')
if os.path.exists(_face_path):
    with open(_face_path, 'r') as f:
        FAKE_IMAGE = f.read().strip()
    print("Using real face image from face_b64.txt")
else:
    _buf = io.BytesIO()
    Image.new('RGB', (100, 100), color=(128, 128, 128)).save(_buf, format='JPEG')
    FAKE_IMAGE = 'data:image/jpeg;base64,' + base64.b64encode(_buf.getvalue()).decode()
    print("WARNING: face_b64.txt not found — using blank image, face recognition will not run.")

# Set SESSION_ID env var to an active session before running.
# Example:  SESSION_ID=3 locust --host=http://localhost:8000
SESSION_ID = int(os.environ.get('SESSION_ID', 1))


class RecognizeFaceUser(HttpUser):
    """
    Simulates a camera client repeatedly sending frames to the server.
    wait_time = between(2, 4) means each simulated user waits 2–4 seconds
    between requests, matching the real 3-second interval in attendance.html.
    """
    wait_time = between(2, 4)

    @task
    def recognize_face(self):
        with self.client.post(
            '/accounts/recognize/',
            json={
                'image': FAKE_IMAGE,
                'session_id': SESSION_ID,
            },
            # Locust marks requests as failed only on network errors by default.
            # catch_response=True lets us also mark application-level failures.
            catch_response=True,
            name='POST /accounts/recognize/',
        ) as response:
            if response.status_code == 500:
                response.failure(f'Server error: {response.text[:200]}')
            elif response.status_code == 200:
                data = response.json()
                # 'no_face' and 'unknown' are expected with a fake image — not failures.
                # Only mark as failure if the server itself errored.
                if data.get('status') == 'error':
                    response.failure(f"App error: {data.get('message')}")
                else:
                    response.success()
