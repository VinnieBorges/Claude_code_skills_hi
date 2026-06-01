import os
import json
import sqlite3
import shutil
import threading
from fastapi import FastAPI, UploadFile, File, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from typing import List, Optional

import database
import processor
import render_engine
import ai_editor

# Initialize database tables
database.init_db()

app = FastAPI(title="ViniCut-AI // Premiere Backend API")

# Enable CORS for frontend development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# FIFO Processing Queue for safe VRAM utilization
queue_lock = threading.Lock()
project_queue = []
queue_worker_thread = None

# Request Models
class SubtitleSegmentModel(BaseModel):
    start: float
    end: float
    text: str
    words: Optional[List[dict]] = None

class RenderRequest(BaseModel):
    transcript: List[SubtitleSegmentModel]
    style_preset: str
    order: List[str]
    font_family: Optional[str] = "Montserrat"
    zoom_effect: Optional[int] = 1

def get_db_connection():
    # Delegate to the shared helper so WAL + busy_timeout apply everywhere and
    # concurrent workers don't hit "database is locked".
    return database.get_db_connection()


def safe_media_filename(filename, allowed_exts=None):
    """
    Reduces an uploaded filename to a safe basename and (optionally) validates
    its extension, preventing path traversal (e.g. '..\\..\\evil') from escaping
    the intended upload directory.
    """
    base = os.path.basename(filename or "")
    base = base.replace("\\", "").replace("/", "").strip()
    if not base or base in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid filename.")
    if allowed_exts is not None:
        ext = os.path.splitext(base)[1].lower()
        if ext not in allowed_exts:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(allowed_exts))}."
            )
    return base

# Sequential Background Queue Processor
def sequential_queue_worker():
    while True:
        project_id = None
        with queue_lock:
            if len(project_queue) > 0:
                project_id = project_queue.pop(0)
            else:
                break
        
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT filename FROM projects WHERE id = ?", (project_id,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                continue
            filename = row[0]
            video_path = os.path.join(database.RAW_DIR, filename)
            
            # Step 1: Update status to analyzing
            cursor.execute("UPDATE projects SET status = 'analyzing' WHERE id = ?", (project_id,))
            conn.commit()
            conn.close()
            
            # Step 2: Run Whisper transcription
            try:
                trans_segs = processor.run_audio_transcription(video_path)
                transcription = [dict(s) for s in trans_segs]
            except Exception as e:
                print(f"Transcription failed for project {project_id}: {e}")
                transcription = [{"start": 0.0, "end": 5.0, "text": "Failed to transcribe."}]
            
            # Step 3: Run AI Slicing & Scriptor decisions (Gemma-4)
            total_dur = processor.get_video_duration(video_path)
            try:
                ai_data = ai_editor.analyze_transcript_and_segment(transcription, total_dur)
            except Exception as e:
                print(f"AI Auto-Cuts Decision failed: {e}")
                ai_data = ai_editor.get_fallback_segmentation(total_dur)
                
            segments_map = {
                "hook": ai_data["hook"],
                "demo": ai_data["demo"],
                "cta": ai_data["cta"],
                "standard_cuts": ai_data.get("standard_cuts", {})
            }
            
            # Update DB with segment maps and transcripts
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE projects 
                SET segments_map_json = ?, transcript_json = ?, status = 'rendering'
                WHERE id = ?
            """, (json.dumps(segments_map), json.dumps(transcription), project_id))
            conn.commit()
            conn.close()
            
            # Step 4: Render standard cuts (5s, 15s, 30s, 60s)
            processor.render_final_cuts(
                project_id=project_id,
                filename=filename,
                original_video_path=video_path,
                segments=transcription,
                style_preset="Bold Yellow",
                segments_map=segments_map,
                order=["Hook", "Demo", "CTA"]
            )
            
            # Fetch styling info
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT style_preset, custom_preset_json FROM projects WHERE id = ?", (project_id,))
            p_row = cursor.fetchone()
            conn.close()
            
            db_preset = p_row[0] if (p_row and p_row[0]) else "Bold Yellow"
            custom_preset_json = p_row[1] if (p_row and p_row[1]) else None
            
            resolved_preset = db_preset
            if custom_preset_json:
                try:
                    resolved_preset = json.loads(custom_preset_json)
                except Exception:
                    pass

            # Step 5: Render AI custom variations (subbed & raw) for 5s, 15s, 30s, and 60s targets
            import shutil
            for var in ai_data["variations"]:
                var_name = var["name"]
                var_desc = var["description"]
                var_order = var["order"]
                
                for dur in [5, 15, 30, 60]:
                    dur_name = f"{var_name} ({dur}s)"
                    dur_desc = f"{var_desc} ({dur}s variation)"
                    
                    # Output filenames
                    safe_name = var_name.replace(" ", "_").replace("/", "_")
                    sub_filename = f"project_{project_id}_ai_montage_{safe_name}_{dur}s_subbed.mp4"
                    sub_path = os.path.join(database.CUTS_DIR, sub_filename)
                    
                    raw_filename = f"project_{project_id}_ai_montage_{safe_name}_{dur}s_raw.mp4"
                    raw_path = os.path.join(database.CUTS_DIR, raw_filename)
                    
                    # Render subbed version
                    try:
                        render_engine.render_custom_reordered_cut(
                            video_path,
                            segments_map,
                            var_order,
                            transcription,
                            resolved_preset,
                            sub_path,
                            target_duration=dur,
                            total_dur=total_dur
                        )
                    except Exception as re_sub:
                        print(f"Error rendering AI subbed variation {dur_name}: {re_sub}")
                        sub_path = ""
                        
                    # Render raw version
                    try:
                        render_engine.render_custom_reordered_cut(
                            video_path,
                            segments_map,
                            var_order,
                            None,
                            resolved_preset,
                            raw_path,
                            target_duration=dur,
                            total_dur=total_dur
                        )
                    except Exception as re_raw:
                        print(f"Error rendering AI raw variation {dur_name}: {re_raw}")
                        raw_path = ""
                    
                    # Insert into ai_montages table
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT INTO ai_montages (project_id, name, description, order_json, filepath_subbed, filepath_raw)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (project_id, dur_name, dur_desc, json.dumps(var_order), sub_path, raw_path))
                    conn.commit()
                    conn.close()
                    
                    # Auto-copy raw variation cut to daily edits directory
                    if raw_path and os.path.exists(raw_path):
                        from datetime import datetime
                        date_str = datetime.now().strftime("%Y-%m-%d")
                        auto_cuts_dir = os.path.join(database.BASE_DIR, "auto_cuts", f"edits_{date_str}")
                        os.makedirs(auto_cuts_dir, exist_ok=True)
                        base_name = os.path.splitext(filename)[0]
                        dest_name = f"{base_name}_{safe_name}_{dur}s_raw.mp4"
                        dest_path = os.path.join(auto_cuts_dir, dest_name)
                        try:
                            shutil.copy2(raw_path, dest_path)
                            print(f"[Queue Worker] Copied raw variation to {dest_path}")
                        except Exception as cp_err:
                            print(f"Error copying raw variation to auto_cuts: {cp_err}")
                
            # Set status to completed
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE projects SET status = 'completed' WHERE id = ?", (project_id,))
            conn.commit()
            conn.close()
            
            # Start self-improvement logging
            try:
                database.run_self_improvement_loop(project_id)
            except Exception as se:
                print(f"Self-improvement loop failed: {se}")
                
        except Exception as ex:
            print(f"Queue worker error on project {project_id}: {ex}")
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE projects SET status = 'failed' WHERE id = ?", (project_id,))
            conn.commit()
            conn.close()

def start_queue_worker():
    global queue_worker_thread
    with queue_lock:
        if queue_worker_thread is None or not queue_worker_thread.is_alive():
            queue_worker_thread = threading.Thread(target=sequential_queue_worker, daemon=True)
            queue_worker_thread.start()

# Watch Folder Thread
watch_worker_thread = None
watch_lock = threading.Lock()

def watch_folder_worker():
    import time
    watch_dir = os.path.join(database.BASE_DIR, "watch")
    file_sizes = {}
    
    while True:
        try:
            if not os.path.exists(watch_dir):
                time.sleep(3.0)
                continue
                
            # Recursively find all files in watch_dir
            video_files = []
            for root, dirs, files_in_dir in os.walk(watch_dir):
                for f in files_in_dir:
                    full_path = os.path.join(root, f)
                    video_files.append(full_path)
            
            # Clean up untracked keys in memory
            for path in list(file_sizes.keys()):
                if not os.path.exists(path):
                    del file_sizes[path]
            
            valid_extensions = [".mp4", ".mov", ".avi", ".mkv"]
            
            for file_path in video_files:
                filename = os.path.basename(file_path)
                ext = os.path.splitext(filename)[1].lower()
                if ext not in valid_extensions:
                    continue
                    
                try:
                    current_size = os.path.getsize(file_path)
                except OSError:
                    continue
                    
                if file_path not in file_sizes:
                    file_sizes[file_path] = (current_size, 0)
                    continue
                    
                prev_size, checks_stable = file_sizes[file_path]
                if current_size == prev_size:
                    checks_stable += 1
                    file_sizes[file_path] = (current_size, checks_stable)
                else:
                    file_sizes[file_path] = (current_size, 0)
                    continue
                    
                if checks_stable >= 2:
                    del file_sizes[file_path]
                    raw_path = os.path.join(database.RAW_DIR, filename)
                    base_name, file_ext = os.path.splitext(filename)
                    counter = 1
                    while os.path.exists(raw_path):
                        new_filename = f"{base_name}_{counter}{file_ext}"
                        raw_path = os.path.join(database.RAW_DIR, new_filename)
                        counter += 1
                        filename = new_filename
                        
                    try:
                        shutil.move(file_path, raw_path)
                        # Clean up empty parent directories inside watch folder
                        parent_dir = os.path.dirname(file_path)
                        if parent_dir != watch_dir:
                            try:
                                if not os.listdir(parent_dir):
                                    os.rmdir(parent_dir)
                                    print(f"[Watch Folder] Removed empty subdirectory: {parent_dir}")
                            except Exception:
                                pass
                    except Exception as mv_err:
                        print(f"Error moving file from watch folder: {mv_err}")
                        continue
                        
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute("INSERT INTO projects (filename, status) VALUES (?, 'pending')", (filename,))
                    project_id = cursor.lastrowid
                    conn.commit()
                    conn.close()
                    
                    with queue_lock:
                        project_queue.append(project_id)
                    start_queue_worker()
                    print(f"[Watch Folder] Ingested {filename} as Project #{project_id}")
                    
        except Exception as e:
            print(f"Watch worker error: {e}")
            
        time.sleep(1.5)

def start_watch_worker():
    global watch_worker_thread
    with watch_lock:
        if watch_worker_thread is None or not watch_worker_thread.is_alive():
            watch_worker_thread = threading.Thread(target=watch_folder_worker, daemon=True)
            watch_worker_thread.start()
            print("[Watch Folder] Background monitor active.")

# Start watch worker on module import
start_watch_worker()

# Manual rendering callback fallback pipeline
def bg_run_manual_render(project_id: int, filename: str, video_path: str, segments: list, style_preset: str, segments_map: dict, order: list):
    try:
        # Compare corrections and log to DB
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT transcript_json FROM projects WHERE id = ?", (project_id,))
        row = cursor.fetchone()
        
        if row and row[0]:
            original_segments = json.loads(row[0])
            for idx, (orig, curr) in enumerate(zip(original_segments, segments)):
                if orig["text"].strip() != curr["text"].strip() or abs(orig["start"] - curr["start"]) > 0.05 or abs(orig["end"] - curr["end"]) > 0.05:
                    database.log_subtitle_correction(
                        project_id=project_id,
                        segment_index=idx,
                        start_time=curr["start"],
                        end_time=curr["end"],
                        original_text=orig["text"],
                        corrected_text=curr["text"]
                    )
                    
        # Update status to rendering
        cursor.execute("UPDATE projects SET status = 'rendering' WHERE id = ?", (project_id,))
        conn.commit()
        conn.close()
        
        # Resolve custom preset from database if exists
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT style_preset, custom_preset_json, font_family, zoom_effect, bg_music_path FROM projects WHERE id = ?", (project_id,))
        p_row = cursor.fetchone()
        conn.close()
        
        db_preset = p_row[0] if (p_row and p_row[0]) else "Bold Yellow"
        custom_preset_json = p_row[1] if (p_row and p_row[1]) else None
        font_family = p_row[2] if (p_row and p_row[2]) else "Montserrat"
        zoom_effect = int(p_row[3]) if (p_row and p_row[3] is not None) else 1
        bg_music_path = p_row[4] if (p_row and p_row[4]) else None
        
        resolved_preset = db_preset
        if custom_preset_json:
            try:
                resolved_preset = json.loads(custom_preset_json)
            except Exception:
                pass
                
        # Render cuts
        processor.render_final_cuts(
            project_id=project_id,
            filename=filename,
            original_video_path=video_path,
            segments=segments,
            style_preset=resolved_preset,
            segments_map=segments_map,
            order=order
        )
        
        # Re-render AI variations using the custom preset
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name, order_json, filepath_subbed, filepath_raw FROM ai_montages WHERE project_id = ?", (project_id,))
        montages_rows = cursor.fetchall()
        conn.close()
        
        for m_name, m_order_json, m_filepath_sub, m_filepath_raw in montages_rows:
            m_order = json.loads(m_order_json)
            try:
                m_dur = int(m_name.split(" (")[1].replace("s)", ""))
            except Exception:
                m_dur = None
                
            if m_filepath_sub:
                try:
                    render_engine.render_custom_reordered_cut(
                        video_path,
                        segments_map,
                        m_order,
                        segments,
                        resolved_preset,
                        m_filepath_sub,
                        font_family=font_family,
                        zoom_effect=zoom_effect,
                        bg_music_path=bg_music_path,
                        target_duration=m_dur
                    )
                except Exception as ex_sub:
                    print(f"Error re-rendering AI variation subbed: {ex_sub}")
                    
            if m_filepath_raw:
                try:
                    render_engine.render_custom_reordered_cut(
                        video_path,
                        segments_map,
                        m_order,
                        None,
                        resolved_preset,
                        m_filepath_raw,
                        font_family=font_family,
                        zoom_effect=zoom_effect,
                        bg_music_path=bg_music_path,
                        target_duration=m_dur
                    )
                except Exception as ex_raw:
                    print(f"Error re-rendering AI variation raw: {ex_raw}")
        
        # Set to completed
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE projects SET status = 'completed' WHERE id = ?", (project_id,))
        conn.commit()
        conn.close()
        
        # Start Self-Improvement Loop
        try:
            database.run_self_improvement_loop(project_id)
        except Exception as se:
            print(f"Self-improvement loop failed: {se}")
            
    except Exception as e:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE projects SET status = 'failed' WHERE id = ?", (project_id,))
        conn.commit()
        conn.close()
        print(f"Error in background manual render: {e}")

@app.post("/api/upload")
async def upload_video(file: UploadFile = File(...)):
    filename = safe_media_filename(file.filename, {".mp4", ".mov", ".avi", ".mkv"})
    raw_path = os.path.join(database.RAW_DIR, filename)
    
    with open(raw_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO projects (filename, status) VALUES (?, 'pending')", (filename,))
    project_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    with queue_lock:
        project_queue.append(project_id)
        
    start_queue_worker()
    return {"project_id": project_id, "filename": filename, "status": "pending"}

@app.post("/api/upload-bulk")
async def upload_bulk(files: List[UploadFile] = File(...)):
    uploaded_projects = []
    conn = get_db_connection()
    cursor = conn.cursor()
    
    for file in files:
        filename = safe_media_filename(file.filename, {".mp4", ".mov", ".avi", ".mkv"})
        raw_path = os.path.join(database.RAW_DIR, filename)

        with open(raw_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        cursor.execute("INSERT INTO projects (filename, status) VALUES (?, 'pending')", (filename,))
        project_id = cursor.lastrowid
        
        uploaded_projects.append({
            "id": project_id,
            "filename": filename,
            "status": "pending"
        })
        
        with queue_lock:
            project_queue.append(project_id)
            
    conn.commit()
    conn.close()
    
    start_queue_worker()
    return {"message": f"Enqueued {len(files)} videos successfully.", "projects": uploaded_projects}

@app.get("/api/projects")
def list_projects():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, filename, created_at, status FROM projects ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    
    # Read current queue to mark waiting items
    with queue_lock:
        in_queue = list(project_queue)
        
    projects = []
    for r in rows:
        proj_status = r[3]
        if r[0] in in_queue and proj_status == "pending":
            proj_status = "queued"
            
        projects.append({
            "id": r[0],
            "filename": r[1],
            "created_at": r[2],
            "status": proj_status
        })
    return projects

@app.get("/api/projects/{project_id}")
def get_project_details(project_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, filename, status, segments_map_json, transcript_json, style_preset, font_family, zoom_effect, bg_music_path, custom_preset_json 
        FROM projects WHERE id = ?
    """, (project_id,))
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Project not found")
        
    cursor.execute("SELECT cut_type, filepath FROM cuts WHERE project_id = ?", (project_id,))
    cuts_rows = cursor.fetchall()
    
    # Load AI variations montages
    cursor.execute("""
        SELECT name, description, order_json, filepath_subbed, filepath_raw 
        FROM ai_montages WHERE project_id = ?
    """, (project_id,))
    ai_rows = cursor.fetchall()
    conn.close()
    
    cuts = {}
    for cut_type, path in cuts_rows:
        cuts[cut_type] = os.path.basename(path) if path else None
        
    ai_montages = []
    for name, desc, order_json, filepath_sub, filepath_raw in ai_rows:
        ai_montages.append({
            "name": name,
            "description": desc,
            "order": json.loads(order_json),
            "filename_subbed": os.path.basename(filepath_sub) if filepath_sub else None,
            "filename_raw": os.path.basename(filepath_raw) if filepath_raw else None
        })
        
    return {
        "id": row[0],
        "filename": row[1],
        "status": row[2],
        "segments_map": json.loads(row[3]) if row[3] else None,
        "transcript": json.loads(row[4]) if row[4] else None,
        "style_preset": row[5] or "Bold Yellow",
        "font_family": row[6] or "Montserrat",
        "zoom_effect": row[7] if row[7] is not None else 1,
        "bg_music_path": os.path.basename(row[8]) if row[8] else None,
        "custom_preset": json.loads(row[9]) if row[9] else None,
        "cuts": cuts,
        "ai_montages": ai_montages
    }

@app.post("/api/projects/{project_id}/montage")
def get_ai_montage_recommendation(project_id: int):
    # Backward compatibility endpoint for custom timeline suggestions
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT transcript_json, segments_map_json FROM projects WHERE id = ?", (project_id,))
    row = cursor.fetchone()
    conn.close()
    
    if not row or not row[0] or not row[1]:
        raise HTTPException(status_code=400, detail="Project transcripts are not analyzed yet.")
        
    transcript = json.loads(row[0])
    segments_map = json.loads(row[1])
    
    hook_text = " ".join([s["text"] for s in transcript if float(s["start"]) < segments_map["hook"][1]])
    demo_text = " ".join([s["text"] for s in transcript if segments_map["demo"][0] <= float(s["start"]) < segments_map["demo"][1]])
    cta_text = " ".join([s["text"] for s in transcript if float(s["start"]) >= segments_map["cta"][0]])
    
    prompt = f"""You are the Creative UGC Montage Editor.
Analyze this UGC script and recommend the sequence:
Hook: "{hook_text.strip()}"
Demo: "{demo_text.strip()}"
CTA: "{cta_text.strip()}"

Return JSON:
{{
  "recommended_order": ["Segment1", "Segment2", "Segment3"],
  "reasoning": "Brief explanation."
}}
"""
    try:
        import ollama
        response = ollama.chat(model='gemma4:26b', messages=[{'role': 'user', 'content': prompt}])
        content = response['message']['content'].strip()
        if "```" in content:
            lines = content.splitlines()
            if lines[0].startswith("```"): lines = lines[1:]
            if lines and lines[-1].startswith("```"): lines = lines[:-1]
            content = "\n".join(lines).strip()
        return json.loads(content)
    except Exception:
        return {
            "recommended_order": ["CTA", "Hook", "Demo"],
            "reasoning": "Curiosity pattern sequence loop."
        }

class SubtitlePromptRequest(BaseModel):
    prompt: str

@app.post("/api/projects/{project_id}/generate-subtitle-preset")
def generate_subtitle_preset(project_id: int, req: SubtitlePromptRequest):
    import ollama
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT filename FROM projects WHERE id = ?", (project_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Project not found")
    conn.close()
    
    prompt = f"""You are an expert ASS subtitle stylist.
The user wants custom subtitle styling described as: "{req.prompt}"

Generate a styling config JSON object for ASS subtitles.
Colors in ASS use the BGR format: "&H00BBGGRR" (Blue, Green, Red).
For example:
- Pure Red is "&H000000FF"
- Pure Green is "&H0000FF00"
- Pure Blue is "&H00FF0000"
- Pure Yellow is "&H0000FFFF"
- Pure White is "&H00FFFFFF"
- Pure Black is "&H00000000"
- Pure Neon Cyan is "&H00FFFF00"
- Pure Neon Pink/Magenta is "&H00FF00FF"

Format:
- fontsize: Integer (usually between 40 and 90, e.g. 70)
- bold: 0 (normal) or -1 (bold)
- border_style: 1 (outline) or 3 (solid background box)
- outline: float border width (e.g. 3.0)
- shadow: float shadow depth (e.g. 2.0)
- alignment: 2 (bottom center), 8 (top center), 5 (middle center)
- margin_v: vertical margin from edge (e.g. 65)
- primary: hex BGR string for main text (e.g. "&H00FFFFFF")
- highlight: hex BGR string for active/highlighted word (e.g. "&H0000FFFF")
- outline_col: hex BGR string for outline (e.g. "&H00000000")
- shadow_col: hex BGR string for shadow/box (e.g. "&H00000000")
- active_word_tags: string containing ASS formatting tags applied to the active/highlighted word. Must start with a backslash and include formatting or animation tags. Do NOT wrap in double braces.
  Examples:
  - Bouncing/Zoom active word: "\\\\fscx120\\\\fscy120\\\\c[highlight_color]" (replace [highlight_color] with your selected highlight color, e.g., "\\\\fscx120\\\\fscy120\\\\c&H0000FFFF&")
  - High Bounce and Red Neon Glow active word: "\\\\fscx130\\\\fscy130\\\\c&H000000FF&\\\\xbord5\\\\ybord5"
  - Standard highlight color only: "\\\\c[highlight_color]"
- inactive_word_tags: string containing ASS formatting tags applied to the inactive words. Should restore scale and color to default.
  Examples:
  - Restore scale and primary color: "\\\\fscx100\\\\fscy100\\\\c[primary_color]" (replace [primary_color] with your selected primary color, e.g., "\\\\fscx100\\\\fscy100\\\\c&H00FFFFFF&")
  - Standard primary color only: "\\\\c[primary_color]"

Return ONLY a valid JSON object. Do not include markdown code blocks, explanations, or any extra text.

JSON Output Format:
{{
  "fontsize": 75,
  "bold": -1,
  "border_style": 1,
  "outline": 4.0,
  "shadow": 0.0,
  "alignment": 2,
  "margin_v": 70,
  "primary": "&H00FFFFFF",
  "highlight": "&H0000FF00",
  "outline_col": "&H00000000",
  "shadow_col": "&H00000000",
  "active_word_tags": "\\\\fscx120\\\\fscy120\\\\c&H0000FF00&",
  "inactive_word_tags": "\\\\fscx100\\\\fscy100\\\\c&H00FFFFFF&"
}}
"""

    try:
        response = ollama.chat(
            model='gemma4:26b',
            messages=[{'role': 'user', 'content': prompt}]
        )
        content = response['message']['content'].strip()
        if "```" in content:
            lines = content.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()
            
        data = json.loads(content)
        
        # Save to DB
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE projects SET custom_preset_json = ? WHERE id = ?", (json.dumps(data), project_id))
        conn.commit()
        conn.close()
        
        # Log to telemetry
        print(f"[AI Presets] Configured custom styling preset: {data}")
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ollama preset generation failed: {str(e)}")

@app.post("/api/projects/{project_id}/render")
def trigger_render(project_id: int, req: RenderRequest, background_tasks: BackgroundTasks):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT filename, segments_map_json FROM projects WHERE id = ?", (project_id,))
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Project not found")
        
    filename = row[0]
    segments_map = json.loads(row[1]) if row[1] else {"hook": [0.0, 5.0], "demo": [5.0, 20.0], "cta": [20.0, 25.0]}
    video_path = os.path.join(database.RAW_DIR, filename)
    
    # Save the updated presets/subtitles/font/zoom to database
    segments_list = []
    for s in req.transcript:
        d = dict(s)
        if d.get("words") is not None:
            # Preserve word level timestamp list
            d["words"] = [dict(w) for w in s.words] if s.words else []
        segments_list.append(d)
        
    cursor.execute("""
        UPDATE projects 
        SET style_preset = ?, font_family = ?, zoom_effect = ?, transcript_json = ?, status = 'rendering' 
        WHERE id = ?
    """, (req.style_preset, req.font_family, req.zoom_effect, json.dumps(segments_list), project_id))
    conn.commit()
    conn.close()
    
    background_tasks.add_task(
        bg_run_manual_render,
        project_id,
        filename,
        video_path,
        segments_list,
        req.style_preset,
        segments_map,
        req.order
    )
    return {"status": "rendering"}

@app.get("/api/projects/{project_id}/download/{cut_type}/{with_subs}")
def download_cut(project_id: int, cut_type: str, with_subs: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    db_cut_type = cut_type
    if with_subs.lower() != "true":
        db_cut_type = f"{cut_type}_raw"
        if not db_cut_type.endswith("s_raw") and db_cut_type != "custom_raw":
            db_cut_type = f"{cut_type}s_raw"
            
    cursor.execute("SELECT filepath FROM cuts WHERE project_id = ? AND cut_type = ?", (project_id, db_cut_type))
    row = cursor.fetchone()
    conn.close()
    
    if not row or not os.path.exists(row[0]):
        raise HTTPException(status_code=404, detail=f"Cut {db_cut_type} not found or not rendered yet.")
        
    return FileResponse(row[0], media_type="video/mp4", filename=os.path.basename(row[0]))

@app.get("/api/projects/{project_id}/download-ai/{montage_name}/{with_subs}")
def download_ai_montage(project_id: int, montage_name: str, with_subs: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT filepath_subbed, filepath_raw 
        FROM ai_montages WHERE project_id = ? AND name = ?
    """, (project_id, montage_name))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="AI montage not found")
        
    filepath = row[0] if with_subs.lower() == "true" else row[1]
    if not filepath or not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Rendered file not found")
        
    return FileResponse(filepath, media_type="video/mp4", filename=os.path.basename(filepath))
    
@app.get("/api/projects/{project_id}/download-zip")
def download_all_cuts_zip(project_id: int):
    import zipfile
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Fetch standard cuts paths
    cursor.execute("SELECT cut_type, filepath FROM cuts WHERE project_id = ?", (project_id,))
    cuts_rows = cursor.fetchall()
    
    # 2. Fetch AI montages paths
    cursor.execute("SELECT name, filepath_subbed, filepath_raw FROM ai_montages WHERE project_id = ?", (project_id,))
    ai_rows = cursor.fetchall()
    conn.close()
    
    # Gather paths to package
    paths_to_zip = []
    
    for cut_type, filepath in cuts_rows:
        if filepath and os.path.exists(filepath):
            # Clean folder separation
            folder = "standard_cuts"
            paths_to_zip.append((filepath, f"{folder}/{os.path.basename(filepath)}"))
            
    for name, filepath_sub, filepath_raw in ai_rows:
        if filepath_sub and os.path.exists(filepath_sub):
            paths_to_zip.append((filepath_sub, f"ai_montages/{os.path.basename(filepath_sub)}"))
        if filepath_raw and os.path.exists(filepath_raw):
            paths_to_zip.append((filepath_raw, f"ai_montages/{os.path.basename(filepath_raw)}"))
            
    if not paths_to_zip:
        raise HTTPException(status_code=404, detail="No rendered cuts or montages found to package.")
        
    zip_filename = f"project_{project_id}_all_cuts.zip"
    zip_filepath = os.path.join(database.CUTS_DIR, zip_filename)
    
    try:
        # Build ZIP archive
        with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for file_path, archive_name in paths_to_zip:
                zip_file.write(file_path, archive_name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create ZIP package: {str(e)}")
        
    return FileResponse(zip_filepath, media_type="application/zip", filename=zip_filename)

FONTS_DIR = os.path.join(database.BASE_DIR, "fonts")
os.makedirs(FONTS_DIR, exist_ok=True)

import font_parser

@app.post("/api/fonts")
def upload_custom_font(file: UploadFile = File(...)):
    filename = safe_media_filename(file.filename, {".ttf", ".otf"})
    font_path = os.path.join(FONTS_DIR, filename)
    with open(font_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    # Parse family name
    family_name = font_parser.parse_font_family(font_path)
    database.register_custom_font(filename, family_name)
    
    return {"filename": filename, "family_name": family_name}

@app.get("/api/fonts")
def list_custom_fonts():
    return database.get_custom_fonts()

class BulkDownloadRequest(BaseModel):
    project_ids: List[int]

@app.post("/api/projects/download-zip-bulk")
def download_bulk_zip(req: BulkDownloadRequest):
    import zipfile
    import time
    paths_to_zip = []
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    for project_id in req.project_ids:
        cursor.execute("SELECT filename FROM projects WHERE id = ?", (project_id,))
        p_row = cursor.fetchone()
        if not p_row:
            continue
        p_name = os.path.splitext(p_row[0])[0]
        
        # 1. Standard cuts
        cursor.execute("SELECT cut_type, filepath FROM cuts WHERE project_id = ?", (project_id,))
        cuts_rows = cursor.fetchall()
        for cut_type, filepath in cuts_rows:
            if filepath and os.path.exists(filepath):
                archive_name = f"project_{project_id}_{p_name}/standard_cuts/{os.path.basename(filepath)}"
                paths_to_zip.append((filepath, archive_name))
                
        # 2. AI variations
        cursor.execute("SELECT name, filepath_subbed, filepath_raw FROM ai_montages WHERE project_id = ?", (project_id,))
        ai_rows = cursor.fetchall()
        for name, filepath_sub, filepath_raw in ai_rows:
            if filepath_sub and os.path.exists(filepath_sub):
                archive_name = f"project_{project_id}_{p_name}/ai_montages/{os.path.basename(filepath_sub)}"
                paths_to_zip.append((filepath_sub, archive_name))
            if filepath_raw and os.path.exists(filepath_raw):
                archive_name = f"project_{project_id}_{p_name}/ai_montages/{os.path.basename(filepath_raw)}"
                paths_to_zip.append((filepath_raw, archive_name))
                
    conn.close()
    
    if not paths_to_zip:
        raise HTTPException(status_code=404, detail="No rendered assets found for selected projects.")
        
    zip_filename = f"bulk_export_{int(time.time())}.zip"
    zip_filepath = os.path.join(database.CUTS_DIR, zip_filename)
    
    try:
        with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for file_path, archive_name in paths_to_zip:
                zip_file.write(file_path, archive_name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create bulk ZIP package: {str(e)}")
        
    return FileResponse(zip_filepath, media_type="application/zip", filename=zip_filename)

class SettingsUpdateRequest(BaseModel):
    settings: dict

@app.get("/api/settings")
def get_system_settings():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT prompt_text FROM system_prompts WHERE component = 'whisper'")
    row = cursor.fetchone()
    whisper_prompt = row[0] if row else "Transcribe the audio accurately."
    
    cursor.execute("SELECT COUNT(*) FROM subtitle_corrections")
    corrections_count = cursor.fetchone()[0]
    conn.close()
    
    agent_notes = [
        "AI Slicing Boundary Alignment: Snapping is fully active. Snapped all video cuts to the nearest word end or silence gap.",
        "9:16 Portrait Enforcer: Locked all outputs to 1080x1920 (9:16 layout) to prevent metadata or container aspect ratio switching.",
        f"Whisper Self-Learning Loop: Loaded Whisper Large-V3 model. Tuned instructions with {corrections_count} manual editor corrections.",
        "Retention Zoom: Automatically triggered 1.15x jump cut zoom on Hook and CTA segments to maximize viewer retention.",
        "Background Ducking: Applied -15dB sidechain ducking compression on background music track during spoken audio phases.",
        "Karaoke Highlighting: Pre-configured primary text to Montserrat white, highlighting active words in Bold Yellow."
    ]
    
    return {
        "vision_model": database.get_setting("vision_model", "llama3.2-vision:latest"),
        "edit_model": database.get_setting("edit_model", "gemma4:26b"),
        "whisper_model": database.get_setting("whisper_model", "large-v3"),
        "whisper_prompt": whisper_prompt,
        "transition_style": database.get_setting("transition_style", "fade"),
        "transition_duration": database.get_setting("transition_duration", "0.4"),
        "agent_notes": agent_notes
    }

@app.post("/api/settings")
def update_system_settings(req: SettingsUpdateRequest):
    for key, val in req.settings.items():
        database.update_setting(key, val)
    return {"status": "success"}

@app.post("/api/projects/{project_id}/bg-music")
def upload_bg_music(project_id: int, file: UploadFile = File(...)):
    filename = safe_media_filename(file.filename, {".mp3", ".wav", ".m4a", ".ogg"})
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT filename FROM projects WHERE id = ?", (project_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Project not found")

    bg_music_dir = os.path.join(database.RAW_DIR, f"project_{project_id}_music")
    os.makedirs(bg_music_dir, exist_ok=True)
    music_path = os.path.join(bg_music_dir, filename)
    with open(music_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    cursor.execute("UPDATE projects SET bg_music_path = ? WHERE id = ?", (music_path, project_id))
    conn.commit()
    conn.close()
    
    return {"status": "success", "music_path": music_path}

# Mount media bins to serve previews directly in HTML5 videos
app.mount("/raw", StaticFiles(directory=database.RAW_DIR), name="raw")
app.mount("/cuts", StaticFiles(directory=database.CUTS_DIR), name="cuts")

# Serve React static assets
frontend_dist = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "dist")
if os.path.exists(frontend_dist):
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
