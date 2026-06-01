import os
import sys
import database
import render_engine
import processor

def run_tests():
    print("=== ViniCut-AI Verification Script ===")
    
    # Test 1: Directory Setup
    print("\n[Test 1] Checking directory creation...")
    for d in [database.RAW_DIR, database.CUTS_DIR, database.DB_DIR]:
        exists = os.path.exists(d)
        print(f"Directory {os.path.basename(d)} exists: {exists}")
        if not exists:
            sys.exit("Error: Required directory is missing.")
            
    # Test 2: Database and Default Prompts
    print("\n[Test 2] Querying default prompts and styling preferences...")
    whisper_prompt = database.get_system_prompt("whisper")
    vision_prompt = database.get_system_prompt("vision")
    print(f"Whisper Prompt: {whisper_prompt[:60]}...")
    print(f"Vision Prompt: {vision_prompt[:60]}...")
    if not whisper_prompt or not vision_prompt:
        sys.exit("Error: Could not retrieve default system prompts.")
        
    # Test 3: Filename keyword styling defaults
    print("\n[Test 3] Testing style preset matching based on file names...")
    presets_to_test = {
        "luxury_car_review.mp4": "Minimalist",
        "skin_care_routine.mov": "Clean White",
        "random_ugc_product.mp4": "Bold Yellow"
    }
    for filename, expected in presets_to_test.items():
        actual = database.get_style_preset_for_file(filename)
        print(f"Filename: '{filename}' -> Detected: '{actual}' (Expected: '{expected}')")
        if actual != expected:
            sys.exit(f"Error: Style matching failed for {filename}")
            
    # Test 4: Generating ASS Subtitles
    print("\n[Test 4] Generating mock ASS subtitle file...")
    mock_segments = [
        {"start": 0.5, "end": 2.5, "text": "This is a high-energy hook!"},
        {"start": 2.6, "end": 5.0, "text": "Testing the new wellness cream."},
        {"start": 5.1, "end": 7.5, "text": "Get yours at our link below!"}
    ]
    ass_path = os.path.join(database.CUTS_DIR, "test_verification.ass")
    render_engine.generate_ass_file(mock_segments, "Bold Yellow", ass_path)
    if os.path.exists(ass_path):
        print(f"ASS subtitle generated successfully at: {ass_path}")
        with open(ass_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            print(f"Sample Line from ASS file: {lines[-1].strip()}")
    else:
        sys.exit("Error: Failed to generate ASS file.")
        
    # Test 5: Check FFmpeg NVENC acceleration capability
    print("\n[Test 5] Checking FFmpeg NVENC encoder availability...")
    import subprocess
    env = render_engine.get_ffmpeg_env()
    result = subprocess.run(
        "ffmpeg -encoders", 
        shell=True,
        capture_output=True, 
        text=True, 
        env=env
    )
    if "h264_nvenc" in result.stdout:
        print("NVENC hardware-accelerated H.264 encoder is AVAILABLE.")
    else:
        print("WARNING: NVENC H.264 encoder is NOT listed in ffmpeg -encoders. FFMPEG might fallback or fail.")
        
    print("\n=== All basic unit tests PASSED successfully! ===")

if __name__ == "__main__":
    run_tests()
