import time
import requests
import os
import shutil

API_BASE = "http://localhost:8000"
SOURCE_VIDEO = "raw/test_video.mp4"
MOV_TEST_FILE = "raw/test_video.mov"

def test_mov_flow():
    print("--- ViniCut-AI MOV Extension Support Verification ---")
    if not os.path.exists(SOURCE_VIDEO):
        print(f"Error: source video {SOURCE_VIDEO} not found.")
        return
        
    # 1. Create a MOV copy of the test video
    print(f"[*] Creating test MOV container by copying {SOURCE_VIDEO}...")
    shutil.copyfile(SOURCE_VIDEO, MOV_TEST_FILE)
    
    try:
        # 2. Upload the MOV file
        print("[*] Uploading test_video.mov via single upload API...")
        with open(MOV_TEST_FILE, "rb") as f:
            files = {"file": ("test_video.mov", f, "video/quicktime")}
            res = requests.post(f"{API_BASE}/api/upload", files=files)
            
        if res.status_code != 200:
            print(f"[ERROR] Single upload failed: {res.text}")
            return
            
        data = res.json()
        project_id = data["project_id"]
        print(f"[OK] Successfully enqueued MOV project. Project ID: {project_id}")
        
        # 3. Poll for status change to completed
        print("[*] Monitoring project processing status...")
        start_time = time.time()
        success = False
        while time.time() - start_time < 300: # 5 min timeout
            status_res = requests.get(f"{API_BASE}/api/projects/{project_id}")
            if status_res.status_code != 200:
                print(f"[ERROR] Failed to query project details: {status_res.text}")
                break
                
            project = status_res.json()
            status = project["status"]
            print(f"Time elapsed: {int(time.time() - start_time)}s | Status: {status}")
            
            if status == "completed":
                success = True
                break
            elif status == "failed":
                print("[ERROR] Project status set to failed.")
                break
                
            time.sleep(5)
            
        if not success:
            print("[ERROR] Verification timed out or failed.")
            return
            
        # 4. Verify downloads
        print("\n[OK] Project processed successfully. Verifying downloads...")
        
        # Download standard cuts
        for cut_tab in ["5s", "15s", "30s", "60s"]:
            # Subtitled
            sub_res = requests.get(f"{API_BASE}/api/projects/{project_id}/download/{cut_tab}/true")
            # Raw
            raw_res = requests.get(f"{API_BASE}/api/projects/{project_id}/download/{cut_tab}/false")
            print(f"  {cut_tab} Cut Subbed size: {len(sub_res.content)} bytes (status: {sub_res.status_code})")
            print(f"  {cut_tab} Cut Raw size: {len(raw_res.content)} bytes (status: {raw_res.status_code})")
            
        # Download ZIP
        zip_res = requests.get(f"{API_BASE}/api/projects/{project_id}/download-zip")
        print(f"  ZIP package size: {len(zip_res.content)} bytes (status: {zip_res.status_code})")
        
        if zip_res.status_code == 200:
            print("\n[OK] E2E MOV video pipeline verification succeeded!")
        else:
            print("\n[ERROR] E2E MOV video pipeline verification failed during download checks.")
            
    finally:
        # Clean up temporary MOV file
        if os.path.exists(MOV_TEST_FILE):
            os.remove(MOV_TEST_FILE)
            print("[*] Cleaned up temporary MOV file.")

if __name__ == "__main__":
    test_mov_flow()
