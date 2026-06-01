import time
import requests
import os

API_BASE = "http://localhost:8000"
VIDEO_PATH = "raw/test_video.mp4"

# How long to wait for the automated queue pipeline (analysis + all renders).
POLL_TIMEOUT = 600  # seconds
POLL_INTERVAL = 4    # seconds


def _poll_until(project_id, target_status):
    """Polls a project until it reaches target_status or 'failed' / timeout."""
    start = time.time()
    while time.time() - start < POLL_TIMEOUT:
        res = requests.get(f"{API_BASE}/api/projects/{project_id}")
        if res.status_code != 200:
            print(f"  [WARN] status query returned {res.status_code}")
            time.sleep(POLL_INTERVAL)
            continue
        details = res.json()
        status = details["status"]
        print(f"  Status: {status} ({int(time.time() - start)}s)")
        if status == target_status:
            return details
        if status == "failed":
            print("  [ERROR] Project entered 'failed' state.")
            return None
        time.sleep(POLL_INTERVAL)
    print("  [ERROR] Timed out waiting for status.")
    return None


def run_verification():
    print("--- ViniCut-AI E2E Verification Script ---")
    if not os.path.exists(VIDEO_PATH):
        print(f"Error: test video {VIDEO_PATH} not found.")
        return

    # 1. Upload Video. NOTE: the backend auto-enqueues the project; the queue
    #    worker runs Whisper + AI segmentation + all renders with no separate
    #    /analyze call. (The old version of this script POSTed to a non-existent
    #    /analyze endpoint and waited for a 'reviewed' status the API never sets.)
    print("Step 1: Uploading video...")
    with open(VIDEO_PATH, "rb") as f:
        res = requests.post(f"{API_BASE}/api/upload", files={"file": f})
    if res.status_code != 200:
        print(f"Failed to upload: {res.text}")
        return
    upload_data = res.json()
    project_id = upload_data["project_id"]
    filename = upload_data["filename"]
    print(f"Uploaded successfully. Project ID: {project_id}, Filename: {filename}")

    # 2. Wait for the automated pipeline to finish (analysis + standard cuts +
    #    AI montage variations all happen in the background queue worker).
    print("Step 2: Waiting for automated analysis + render to complete...")
    details = _poll_until(project_id, "completed")
    if details is None:
        return

    print("Analysis + render complete! Project Details:")
    seg_map = details.get("segments_map")
    print(f"  Segments map: {seg_map}")
    transcript = details.get("transcript") or []
    print(f"  Subtitle segment count: {len(transcript)}")
    print(f"  Standard cuts in DB: {list((details.get('cuts') or {}).keys())}")
    print(f"  AI montages: {[m['name'] for m in details.get('ai_montages', [])]}")

    # 3. Fetch an AI montage recommendation (optional, best-effort).
    print("Step 3: Requesting AI montage suggestion...")
    res = requests.post(f"{API_BASE}/api/projects/{project_id}/montage")
    if res.status_code == 200:
        montage = res.json()
        print(f"  Recommended order: {montage.get('recommended_order')}")
        print(f"  Reasoning: {montage.get('reasoning')}")
        order = montage.get("recommended_order", ["Hook", "Demo", "CTA"])
    else:
        print(f"  Montage suggestion unavailable: {res.text}")
        order = ["Hook", "Demo", "CTA"]

    # 4. Trigger a manual re-render with an edited transcript (exercises the
    #    correction-logging + custom-order render path).
    print("Step 4: Triggering manual re-render with an edited subtitle...")
    if transcript:
        transcript[0]["text"] = "Welcome to ViniCut AI editor!"

    render_payload = {
        "transcript": transcript,
        "style_preset": "Bold Yellow",
        "order": order,
    }
    res = requests.post(f"{API_BASE}/api/projects/{project_id}/render", json=render_payload)
    if res.status_code != 200:
        print(f"Failed to trigger render: {res.text}")
        return
    print("  Re-render triggered. Waiting for completion...")
    if _poll_until(project_id, "completed") is None:
        return
    print("Re-render completed successfully!")

    # 5. Verify dual downloads (subtitled + raw) for a couple of cut types.
    print("Step 5: Verifying dual downloads (subbed and raw)...")
    cuts_to_verify = ["5s", "custom"]
    for cut in cuts_to_verify:
        res_sub = requests.get(f"{API_BASE}/api/projects/{project_id}/download/{cut}/true")
        if res_sub.status_code == 200:
            print(f"  [SUCCESS] subtitled {cut} cut ({len(res_sub.content)} bytes)")
        else:
            print(f"  [FAILED] subtitled {cut} cut: {res_sub.status_code}")

        res_raw = requests.get(f"{API_BASE}/api/projects/{project_id}/download/{cut}/false")
        if res_raw.status_code == 200:
            print(f"  [SUCCESS] raw {cut} cut ({len(res_raw.content)} bytes)")
        else:
            print(f"  [FAILED] raw {cut} cut: {res_raw.status_code}")

    print("\n--- E2E VERIFICATION COMPLETED ---")


if __name__ == "__main__":
    run_verification()
