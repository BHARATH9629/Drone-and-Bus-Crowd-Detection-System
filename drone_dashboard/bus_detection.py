"""
Bus phone IP-camera crowd and object detection.

Counts people for the bus dashboard and draws YOLO object labels directly on
the camera window.
"""

import os
from pathlib import Path
import sys
import time
import urllib.request
import cv2
import numpy as np
import requests
from ultralytics import YOLO

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent

# Load environment variables (.env in script dir or project root)
try:
    from dotenv import load_dotenv
    if (SCRIPT_DIR / ".env").exists():
        load_dotenv(dotenv_path=SCRIPT_DIR / ".env")
    elif (ROOT_DIR / ".env").exists():
        load_dotenv(dotenv_path=ROOT_DIR / ".env")
except ImportError:
    pass

# PHONE CAMERA CONFIG
PHONE_CAM_IP = os.getenv("PHONE_CAM_IP", "172.17.8.151")
PHONE_CAM_PORT = os.getenv("PHONE_CAM_PORT", "8080")
PHONE_STREAM_PATH = os.getenv("PHONE_STREAM_PATH", "/video")
PHONE_STREAM_URL = f"http://{PHONE_CAM_IP}:{PHONE_CAM_PORT}{PHONE_STREAM_PATH}"

# ESP32-CAM CONFIG
ESP32_IP = os.getenv("ESP32_IP", "192.168.1.50")
ESP32_STREAM_URL = f"http://{ESP32_IP}:81/stream"

DASHBOARD = os.getenv("DASHBOARD_URL", "http://localhost:3000/api/detection")

# Dynamically locate YOLO model (prefers yolov8s.pt, falls back to yolov8n.pt or auto-download)
def get_model_path():
    candidates = [
        SCRIPT_DIR / "yolov8s.pt",
        ROOT_DIR / "yolov8s.pt",
        SCRIPT_DIR / "yolov8n.pt",
        ROOT_DIR / "yolov8n.pt",
        Path("yolov8s.pt"),
        Path("yolov8n.pt"),
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return "yolov8s.pt"

MODEL_PATH = get_model_path()
PERSON_CONFIDENCE_LIMIT = 0.25
OBJECT_CONFIDENCE_LIMIT = 0.30
POST_INTERVAL_SECONDS = 1
PROCESS_WIDTH = 640
CAMERA_WINDOW_WIDTH = 600
CAMERA_WINDOW_HEIGHT = 380
CAMERA_WINDOW_NAME = "Bus Crowd & Object Detection (YOLOv8)"

CROWD_WARN = 5
CROWD_DANGER = 10

WATCH_CLASSES = {
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train",
    "truck", "boat", "traffic light", "fire hydrant", "stop sign",
    "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow",
    "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard",
    "sports ball", "kite", "baseball bat", "baseball glove", "skateboard",
    "surfboard", "tennis racket", "bottle", "wine glass", "cup", "fork",
    "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
    "couch", "potted plant", "bed", "dining table", "toilet", "tv",
    "laptop", "mouse", "remote", "keyboard", "cell phone", "microwave",
    "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase",
    "scissors", "teddy bear", "hair drier", "toothbrush",
}

print(f"[INFO] Initializing YOLO with model: {MODEL_PATH}")
model = YOLO(MODEL_PATH)


class CameraStream:
    def __init__(self, url):
        self.url = url
        self.stream = None
        self.bytes = b""
        self._connect()

    def _connect(self):
        print(f"[INFO] Connecting to phone camera stream: {self.url}")
        try:
            self.stream = urllib.request.urlopen(self.url, timeout=5)
            print("[INFO] Phone camera connected.")
        except Exception as e:
            print(f"[ERROR] {e}")
            self.stream = None

    def read(self):
        if self.stream is None:
            return False, None
        try:
            self.bytes += self.stream.read(32768)
            b = self.bytes.rfind(b"\xff\xd9")
            a = self.bytes.rfind(b"\xff\xd8", 0, b)
            if a != -1 and b != -1 and b > a:
                jpg = self.bytes[a:b + 2]
                self.bytes = self.bytes[b + 2:]
                frame = cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)
                if frame is not None:
                    return True, frame
            if len(self.bytes) > 2_000_000:
                self.bytes = self.bytes[-500_000:]
        except Exception as e:
            print(f"[WARN] {e} - retrying phone camera stream...")
            self._connect()
        return False, None

    def release(self):
        if self.stream:
            self.stream.close()


def crowd_status(count):
    if count == 0:
        return "CLEAR", (0, 255, 0)
    if count < CROWD_WARN:
        return "LOW", (0, 220, 255)
    if count < CROWD_DANGER:
        return "WARNING", (0, 165, 255)
    return "DANGER", (0, 0, 255)


def summarize_objects(detections):
    counts = {}
    for item in detections:
        label = item["label"]
        counts[label] = counts.get(label, 0) + 1
    return ", ".join(f"{name}:{count}" for name, count in sorted(counts.items())) or "none"


def resize_for_processing(frame):
    h, w = frame.shape[:2]
    if w <= PROCESS_WIDTH:
        return frame
    scale = PROCESS_WIDTH / w
    return cv2.resize(frame, (PROCESS_WIDTH, int(h * scale)), interpolation=cv2.INTER_AREA)


def post_to_dashboard(crowd_count, crowd_status_str, detected_objects):
    payload = {
        "crowd_count": crowd_count,
        "crowd_status": crowd_status_str,
        "detected_objects": summarize_objects(detected_objects),
        "sample_interval_seconds": POST_INTERVAL_SECONDS,
    }
    try:
        requests.post(DASHBOARD, json=payload, timeout=0.8)
    except requests.RequestException:
        pass


def choose_camera():
    """
    Lets the user pick a camera source, with automatic fallback:
      1 = ESP32-CAM
      2 = Phone Camera
      3 = Laptop / USB Webcam
      4 = Video File (e.g. bus_stop.mp4)
    """
    print("\nSelect video/camera source:")
    print("  1. ESP32-CAM")
    print("  2. Phone Camera")
    print("  3. Laptop / USB Webcam")
    print("  4. Video File (e.g. bus_stop.mp4)")
    choice = input("Enter choice [1/2/3/4]: ").strip()

    if choice == "1":
        cap = CameraStream(ESP32_STREAM_URL)
        if cap.stream is not None:
            return cap, False
        cap.release()
        ans = input("[?] ESP32-CAM not reachable. Try Phone Camera instead? [y/N] ").strip().lower()
        if ans == "y":
            cap = CameraStream(PHONE_STREAM_URL)
            if cap.stream is not None:
                return cap, False
            cap.release()
            ans2 = input("[?] Phone Camera not reachable. Use Laptop Camera instead? [y/N] ").strip().lower()
            if ans2 == "y":
                return cv2.VideoCapture(0), True
            sys.exit(1)
        else:
            ans2 = input("[?] Use Laptop Camera instead? [y/N] ").strip().lower()
            if ans2 == "y":
                return cv2.VideoCapture(0), True
            sys.exit(1)

    elif choice == "2":
        cap = CameraStream(PHONE_STREAM_URL)
        if cap.stream is not None:
            return cap, False
        cap.release()
        ans = input("[?] Phone Camera not reachable. Try ESP32-CAM instead? [y/N] ").strip().lower()
        if ans == "y":
            cap = CameraStream(ESP32_STREAM_URL)
            if cap.stream is not None:
                return cap, False
            cap.release()
            ans2 = input("[?] ESP32-CAM not reachable. Use Laptop Camera instead? [y/N] ").strip().lower()
            if ans2 == "y":
                return cv2.VideoCapture(0), True
            sys.exit(1)
        else:
            ans2 = input("[?] Use Laptop Camera instead? [y/N] ").strip().lower()
            if ans2 == "y":
                return cv2.VideoCapture(0), True
            sys.exit(1)

    elif choice == "3":
        return cv2.VideoCapture(0), True

    elif choice == "4":
        video_name = input("Enter video filename or path (default: bus_stop.mp4): ").strip() or "bus_stop.mp4"
        candidate_paths = [
            Path(video_name),
            ROOT_DIR / video_name,
            SCRIPT_DIR / video_name,
            ROOT_DIR / "bus_stop.mp4",
        ]
        chosen = None
        for p in candidate_paths:
            if p.is_file():
                chosen = str(p)
                break
        if not chosen:
            print(f"[ERROR] Video file '{video_name}' not found.")
            sys.exit(1)
        print(f"[INFO] Playing video file: {chosen}")
        return cv2.VideoCapture(chosen), True

    else:
        print("[ERROR] Invalid choice.")
        sys.exit(1)


def main():
    cap, cap_is_cv2 = choose_camera()

    print("[INFO] Crowd detection running. Press ESC to quit.")
    cv2.namedWindow(CAMERA_WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(CAMERA_WINDOW_NAME, CAMERA_WINDOW_WIDTH, CAMERA_WINDOW_HEIGHT)
    last_post = 0

    while True:
        ret, frame = cap.read()
        if not ret or frame is None:
            if cap_is_cv2 and isinstance(cap, cv2.VideoCapture):
                # Rewind video file to beginning if it reached EOF
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                time.sleep(0.03)
                continue
            time.sleep(0.05)
            continue

        frame = resize_for_processing(frame)
        h, w = frame.shape[:2]

        crowd_count = 0
        detections = []

        results = model(frame, stream=True, verbose=False, conf=PERSON_CONFIDENCE_LIMIT, iou=0.45)
        for result in results:
            for box in result.boxes:
                cls_id = int(box.cls[0])
                label = model.names[cls_id]
                conf = float(box.conf[0])
                if label not in WATCH_CLASSES:
                    continue
                confidence_limit = PERSON_CONFIDENCE_LIMIT if label == "person" else OBJECT_CONFIDENCE_LIMIT
                if conf < confidence_limit:
                    continue

                x1, y1, x2, y2 = map(int, box.xyxy[0])
                item = {"label": label, "confidence": conf}
                detections.append(item)

                if label == "person":
                    crowd_count += 1

                color = (0, 255, 0) if label == "person" else (255, 180, 0)
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                cv2.putText(frame, f"{label} {conf:.0%}", (x1, max(20, y1 - 6)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)

        status_str, status_col = crowd_status(crowd_count)

        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (w, 92), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)
        cv2.putText(frame, f"Bus crowd: {crowd_count} [{status_str}]",
                    (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, status_col, 2)
        cv2.putText(frame, f"Objects: {summarize_objects(detections)}",
                    (10, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (220, 220, 220), 2)

        now = time.time()
        if now - last_post >= POST_INTERVAL_SECONDS:
            post_to_dashboard(crowd_count, status_str, detections)
            print(f"[{time.strftime('%H:%M:%S')}] People={crowd_count} ({status_str}) | "
                  f"Objects={summarize_objects(detections)}")
            last_post = now

        cv2.imshow(CAMERA_WINDOW_NAME, frame)
        if cv2.waitKey(1) & 0xFF == 27:
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()