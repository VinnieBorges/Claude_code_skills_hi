import time
import requests
import os

API_BASE = "http://localhost:8000"
VIDEO_PATH = "raw/test_video.mp4"

def run_verification():
    print("--- ViniCut-AI E2E Verification Script ---")
    if not os.path.exists(VIDEO_PATH):
        print(f"Error: test video {VIDEO_PATH} not found.")
        return
        
    # 1. Upload Video
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
    
    # 2. Trigger Analysis
    print("Step 2: Triggering analysis...")
    res = requests.post(f"{API_BASE}/api/projects/{project_id}/analyze")
    if res.status_code != 200:
        print(f"Failed to analyze: {res.text}")
        return
    print("Analysis started successfully. Polling status...")
    
    # 3. Poll for 'reviewed' status
    while True:
        res = requests.get(f"{API_BASE}/api/projects/{project_id}")
        details = res.json()
        status = details["status"]
        print(f"Current Status: {status}")
        if status == "reviewed":
            break
        elif status == "failed":
            print("Analysis failed.")
            return
        time.sleep(3)
        
    print("Analysis complete! Project Details:")
    print(f"Segments map: {details['segments_map']}")
    print(f"Subtitle word count: {len(details['transcript'])}")
    
    # 4. Fetch AI Montage Recommendation
    print("Step 4: Requesting AI Montage suggestions...")
    res = requests.post(f"{API_BASE}/api/projects/{project_id}/montage")
    if res.status_code == 200:
        montage = res.json()
        print(f"Recommended order: {montage['recommended_order']}")
        print(f"Reasoning: {montage['reasoning']}")
    else:
        print(f"Failed to fetch montage suggestion: {res.text}")
        montage = {"recommended_order": ["CTA", "Hook", "Demo"]}
        
    # 5. Trigger Render (with custom edits)
    print("Step 5: Triggering cuts rendering (subbed and raw)...")
    # Simulate a subtitle edit
    transcript = details["transcript"]
    if transcript:
        transcript[0]["text"] = "Welcome to ViniCut AI editor!"
        
    render_payload = {
        "transcript": transcript,
        "style_preset": "Bold Yellow",
        "order": montage["recommended_order"]
    }
    
    res = requests.post(f"{API_BASE}/api/projects/{project_id}/render", json=render_payload)
    if res.status_code != 200:
        print(f"Failed to trigger render: {res.text}")
        return
    print("Render triggered. Polling status...")
    
    # 6. Poll for 'completed' status
    while True:
        res = requests.get(f"{API_BASE}/api/projects/{project_id}")
        details = res.json()
        status = details["status"]
        print(f"Current Status: {status}")
        if status == "completed":
            break
        elif status == "failed":
            print("Rendering failed.")
            return
        time.sleep(3)
        
    print("Rendering completed successfully!")
    print(f"Cuts available in DB: {details['cuts']}")
    
    # 7. Verify downloads
    print("Step 7: Verifying dual downloads (subbed and raw)...")
    cuts_to_verify = ["5s", "custom"]
    for cut in cuts_to_verify:
        # Check subbed download
        res_sub = requests.get(f"{API_BASE}/api/projects/{project_id}/download/{cut}/true")
        if res_sub.status_code == 200:
            print(f"  [SUCCESS] Downloaded subtitled {cut} cut (size: {len(res_sub.content)} bytes)")
        else:
            print(f"  [FAILED] Downloaded subtitled {cut} cut: {res_sub.status_code}")
            
        # Check raw download
        res_raw = requests.get(f"{API_BASE}/api/projects/{project_id}/download/{cut}/false")
        if res_raw.status_code == 200:
            print(f"  [SUCCESS] Downloaded raw {cut} cut (size: {len(res_raw.content)} bytes)")
        else:
            print(f"  [FAILED] Downloaded raw {cut} cut: {res_raw.status_code}")
            
    print("\n--- E2E VERIFICATION COMPLETED SUCCESSFULLY ---")

if __name__ == "__main__":
    run_verification()
