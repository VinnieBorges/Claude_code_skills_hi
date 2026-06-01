import time
import requests
import os
import shutil

API_BASE = "http://localhost:8000"
SOURCE_VIDEO = "raw/test_video.mp4"
BATCH_1 = "raw/batch_test_1.mp4"
BATCH_2 = "raw/batch_test_2.mp4"

def test_bulk():
    print("--- ViniCut-AI Bulk Queue Verification ---")
    if not os.path.exists(SOURCE_VIDEO):
        print(f"Error: source video {SOURCE_VIDEO} not found.")
        return
        
    # Copy to create two unique batch files
    shutil.copyfile(SOURCE_VIDEO, BATCH_1)
    shutil.copyfile(SOURCE_VIDEO, BATCH_2)
    
    f1 = open(BATCH_1, "rb")
    f2 = open(BATCH_2, "rb")
    files = [
        ("files", ("batch_test_1.mp4", f1, "video/mp4")),
        ("files", ("batch_test_2.mp4", f2, "video/mp4"))
    ]
    
    res = requests.post(f"{API_BASE}/api/upload-bulk", files=files)
    f1.close()
    f2.close()
    if res.status_code != 200:
        print(f"Bulk upload failed: {res.text}")
        return
        
    data = res.json()
    projects = data["projects"]
    p1_id = projects[0]["id"]
    p2_id = projects[1]["id"]
    print(f"Successfully enqueued Project {p1_id} and Project {p2_id}.")
    
    # Poll and monitor sequence
    print("Step 2: Monitoring sequential execution. Safe GPU VRAM Lock should run one-by-one.")
    completed_projects = set()
    
    start_time = time.time()
    while len(completed_projects) < 2:
        res = requests.get(f"{API_BASE}/api/projects")
        proj_list = res.json()
        
        # Map statuses
        status_map = {p["id"]: p["status"] for p in proj_list}
        p1_status = status_map.get(p1_id, "unknown")
        p2_status = status_map.get(p2_id, "unknown")
        
        print(f"Time elapsed: {int(time.time() - start_time)}s | P1 (id={p1_id}): {p1_status} | P2 (id={p2_id}): {p2_status}")
        
        if p1_status == "completed":
            completed_projects.add(p1_id)
        if p2_status == "completed":
            completed_projects.add(p2_id)
            
        if p1_status == "failed" or p2_status == "failed":
            print("One of the projects failed processing.")
            break
            
        # Ensure P2 does not start analyzing or rendering before P1 is completed
        if p1_status in ["analyzing", "rendering"] and p2_status not in ["queued", "pending"]:
            print(f"[VIOLATION] Project 2 is in state '{p2_status}' while Project 1 is still processing!")
            
        time.sleep(4)
        
    print("\nStep 3: Verification of dual-output AI Montages...")
    for pid in [p1_id, p2_id]:
        # Query details
        details = requests.get(f"{API_BASE}/api/projects/{pid}").json()
        print(f"\nProject {pid} details:")
        print(f"  AI Montages generated: {[m['name'] for m in details['ai_montages']]}")
        
        for montage in details["ai_montages"]:
            name = montage["name"]
            
            # Download subbed
            d_sub = requests.get(f"{API_BASE}/api/projects/{pid}/download-ai/{name}/true")
            # Download raw
            d_raw = requests.get(f"{API_BASE}/api/projects/{pid}/download-ai/{name}/false")
            
            print(f"  Montage '{name}':")
            print(f"    Subbed size: {len(d_sub.content)} bytes (status: {d_sub.status_code})")
            print(f"    Raw size: {len(d_raw.content)} bytes (status: {d_raw.status_code})")
            
        # Test ZIP file download
        zip_res = requests.get(f"{API_BASE}/api/projects/{pid}/download-zip")
        print(f"  ZIP export verification:")
        print(f"    ZIP package size: {len(zip_res.content)} bytes (status: {zip_res.status_code})")

    # Clean up local batch files
    if os.path.exists(BATCH_1): os.remove(BATCH_1)
    if os.path.exists(BATCH_2): os.remove(BATCH_2)
    print("\n--- BULK QUEUE E2E VERIFICATION COMPLETE ---")

if __name__ == "__main__":
    test_bulk()
