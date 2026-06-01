import os
import streamlit as st
import shutil
import database
import processor
import render_engine

# Initialize database
database.init_db()

# Set page config
st.set_page_config(
    page_title="ViniCut-AI // Premiere Studio",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Styling (Premiere Pro & After Effects Charcoal/Slate Theme)
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;500;600;700&display=swap');

    /* Main body styles */
    .stApp {
        background-color: #0e0e14 !important;
        color: #d1d1d8 !important;
        font-family: 'Inter', sans-serif !important;
    }
    
    /* Custom panel container */
    .premiere-panel {
        background-color: #171722;
        border: 1px solid #2a2a3e;
        border-radius: 8px;
        padding: 20px;
        margin-bottom: 20px;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
    }
    
    /* Headers styling */
    h1, h2, h3, h4, h5, h6 {
        font-family: 'Outfit', sans-serif !important;
        color: #00e5ff !important;
        font-weight: 600 !important;
        letter-spacing: 0.5px;
        margin-top: 0px !important;
    }
    
    /* Buttons */
    .stButton > button {
        background: linear-gradient(135deg, #00b4db 0%, #0083b0 100%) !important;
        color: #ffffff !important;
        border: 1px solid #00e5ff !important;
        border-radius: 6px !important;
        padding: 8px 24px !important;
        font-family: 'Outfit', sans-serif !important;
        font-weight: 600 !important;
        transition: all 0.25s ease !important;
        box-shadow: 0 2px 10px rgba(0, 229, 255, 0.2) !important;
    }
    .stButton > button:hover {
        background: linear-gradient(135deg, #0083b0 0%, #00b4db 100%) !important;
        transform: translateY(-1px) !important;
        box-shadow: 0 4px 15px rgba(0, 229, 255, 0.4) !important;
    }
    
    /* Sidebar styling */
    section[data-testid="stSidebar"] {
        background-color: #12121a !important;
        border-right: 1px solid #232332 !important;
    }
    
    /* Adobe Program monitor screen border */
    .monitor-screen {
        border: 4px solid #1f1f2e;
        border-radius: 8px;
        overflow: hidden;
        background-color: #000000;
        margin-bottom: 15px;
    }
    
    /* Input elements customization */
    div[data-baseweb="select"] > div, input, textarea {
        background-color: #1a1a26 !important;
        border: 1px solid #2e2e42 !important;
        color: #e2e2e9 !important;
        border-radius: 6px !important;
    }
    
    /* Tab controls customization */
    div[data-baseweb="tab-list"] {
        background-color: #12121a !important;
        border-radius: 6px 6px 0 0;
        padding: 5px 10px 0 10px;
        border-bottom: 1px solid #232332;
    }
    div[data-baseweb="tab-list"] button {
        background-color: transparent !important;
        color: #8f90a6 !important;
        font-family: 'Outfit', sans-serif !important;
        font-weight: 500 !important;
        border: none !important;
        padding: 10px 20px !important;
        transition: all 0.2s ease !important;
    }
    div[data-baseweb="tab-list"] button[aria-selected="true"] {
        background-color: #171722 !important;
        color: #00e5ff !important;
        border-bottom: 2px solid #00e5ff !important;
        border-radius: 6px 6px 0 0;
    }
    
    /* Visual Timeline container */
    .timeline-track {
        background-color: #13131c;
        border: 1px solid #28283a;
        border-radius: 8px;
        padding: 16px;
        margin-top: 15px;
        margin-bottom: 15px;
    }
    
    /* Download Buttons */
    div[data-testid="stDownloadButton"] > button {
        background: linear-gradient(135deg, #a29bfe 0%, #6c5ce7 100%) !important;
        color: #ffffff !important;
        border: 1px solid #a29bfe !important;
        border-radius: 6px !important;
        font-weight: 600 !important;
        font-family: 'Outfit', sans-serif !important;
        box-shadow: 0 2px 10px rgba(108, 92, 231, 0.2) !important;
    }
    div[data-testid="stDownloadButton"] > button:hover {
        background: linear-gradient(135deg, #6c5ce7 0%, #a29bfe 100%) !important;
        box-shadow: 0 4px 15px rgba(108, 92, 231, 0.4) !important;
    }
    
    .status-terminal {
        background-color: #09090d;
        border: 1px solid #1f1f2e;
        border-radius: 6px;
        padding: 12px;
        font-family: 'Courier New', Courier, monospace;
        font-size: 12px;
        color: #00ffcc;
        max-height: 180px;
        overflow-y: auto;
    }
</style>
""", unsafe_allow_html=True)

# Initialize Session State
if "project_id" not in st.session_state:
    st.session_state.project_id = None
if "filename" not in st.session_state:
    st.session_state.filename = ""
if "video_path" not in st.session_state:
    st.session_state.video_path = ""
if "status" not in st.session_state:
    st.session_state.status = "idle"  # idle, analyzing, reviewed, rendering, completed
if "segments_map" not in st.session_state:
    st.session_state.segments_map = {}
if "original_segments" not in st.session_state:
    st.session_state.original_segments = []
if "current_segments" not in st.session_state:
    st.session_state.current_segments = []
if "style_preset" not in st.session_state:
    st.session_state.style_preset = "Bold Yellow"
if "cuts_paths" not in st.session_state:
    st.session_state.cuts_paths = {}
if "logs" not in st.session_state:
    st.session_state.logs = []
if "improvement_logs" not in st.session_state:
    st.session_state.improvement_logs = ""
if "custom_order" not in st.session_state:
    st.session_state.custom_order = ["Hook", "Demo", "CTA"]

def log(message):
    st.session_state.logs.append(message)
    print(message)

# Logo & Header Banner
st.markdown("""
<div style="display: flex; align-items: center; border-bottom: 1px solid #2e2e42; padding-bottom: 15px; margin-bottom: 25px;">
    <span style="font-size: 34px; margin-right: 15px;">🎬</span>
    <div>
        <h1 style="margin: 0; color: #00e5ff; font-size: 26px; font-weight: 700; font-family: 'Outfit';">ViniCut-AI // Premiere Studio</h1>
        <p style="margin: 0; color: #8f90a6; font-size: 11px; text-transform: uppercase; letter-spacing: 1.5px; font-weight: 600;">Autonomous UGC Video Slicing & Subtitle System</p>
    </div>
</div>
""", unsafe_allow_html=True)

# Layout Setup
col_left, col_right = st.columns([1, 2])

# Left Column: Media Import & Configuration Panel
with col_left:
    st.markdown('<div class="premiere-panel">', unsafe_allow_html=True)
    st.subheader("📥 Media Bin / Import")
    uploaded_file = st.file_uploader("Drag & Drop UGC Video File", type=["mp4", "mov", "avi"])
    
    if uploaded_file:
        filename = uploaded_file.name
        raw_path = os.path.join(database.RAW_DIR, filename)
        
        # New upload check
        if st.session_state.filename != filename:
            with open(raw_path, "wb") as f:
                f.write(uploaded_file.getbuffer())
                
            st.session_state.filename = filename
            st.session_state.video_path = raw_path
            
            # Detect branding template based on file keywords
            detected_preset = database.get_style_preset_for_file(filename)
            st.session_state.style_preset = detected_preset
            
            # Add database entry
            conn = database.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("INSERT INTO projects (filename, status) VALUES (?, 'pending')", (filename,))
            st.session_state.project_id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            # Reset workflow session parameters
            st.session_state.status = "idle"
            st.session_state.segments_map = {}
            st.session_state.original_segments = []
            st.session_state.current_segments = []
            st.session_state.cuts_paths = {}
            st.session_state.logs = [f"Imported project {st.session_state.project_id} from {filename}"]
            st.session_state.improvement_logs = ""
            
        st.success(f"Loaded: **{filename}**")
        
        st.subheader("🎨 Brand Styling Preset")
        st.session_state.style_preset = st.selectbox(
            "Select Branding Style Template",
            ["Bold Yellow", "Clean White", "Minimalist"],
            index=["Bold Yellow", "Clean White", "Minimalist"].index(st.session_state.style_preset)
        )
        
        st.subheader("⚙️ AI Model Engines")
        vision_model = st.text_input("Vision Model (Ollama)", value="llama3.2-vision:latest")

        st.subheader("🎞️ Boundary Transitions")
        _trans_options = ["none", "fade", "dissolve", "fade_black", "fade_white",
                          "zoom_blur", "slideleft", "wipeleft", "circleopen", "radial", "pixelize"]
        _cur_trans = database.get_setting("transition_style", "fade")
        if _cur_trans not in _trans_options:
            _trans_options.insert(0, _cur_trans)
        transition_style = st.selectbox(
            "Transition between Hook / Demo / CTA",
            _trans_options,
            index=_trans_options.index(_cur_trans),
            help="Cross-fade applied where segments join in the cuts. 'none' = hard cut."
        )
        try:
            _cur_dur = float(database.get_setting("transition_duration", "0.4"))
        except (TypeError, ValueError):
            _cur_dur = 0.4
        transition_duration = st.slider(
            "Transition duration (seconds)",
            min_value=0.1, max_value=1.0, value=min(max(_cur_dur, 0.1), 1.0), step=0.05,
            disabled=(transition_style == "none")
        )
        # Persist so render_final_cuts (via get_transition_settings) picks these up.
        database.update_setting("transition_style", transition_style)
        database.update_setting("transition_duration", str(transition_duration))

        st.markdown("<br>", unsafe_allow_html=True)
        if st.session_state.status == "idle":
            if st.button("🚀 Run AI Slicing & Transcription"):
                st.session_state.status = "analyzing"
                st.rerun()
                
    st.markdown('</div>', unsafe_allow_html=True)
    
    # Logs Panel
    if st.session_state.status != "idle" and st.session_state.logs:
        st.markdown('<div class="premiere-panel">', unsafe_allow_html=True)
        st.subheader("📋 Output Terminal Log")
        log_text = "\n".join(st.session_state.logs)
        st.markdown(f'<div class="status-terminal">{log_text}</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

# Run Slicing Pipeline
if st.session_state.status == "analyzing":
    log("Step 1: Analyzing keyframes sequentially with Ollama Vision...")
    with st.spinner("Extracting timeline chapters..."):
        try:
            segments_map = processor.run_vision_analysis(
                st.session_state.video_path,
                model_name=vision_model
            )
            st.session_state.segments_map = segments_map
            log(f"Semantic segments detected: Hook: {segments_map['hook']}, Demo: {segments_map['demo']}, CTA: {segments_map['cta']}")
        except Exception as e:
            log(f"Vision analysis failed, using fallback: {e}")
            st.session_state.segments_map = {
                "hook": [0.0, 5.0],
                "demo": [5.0, 20.0],
                "cta": [20.0, 25.0]
            }
            
    log("Step 2: Performing Whisper word-level transcription on GPU...")
    with st.spinner("Transcribing audio segments (CUDA accelerated)..."):
        try:
            trans_segs = processor.run_audio_transcription(st.session_state.video_path)
            st.session_state.original_segments = trans_segs
            st.session_state.current_segments = [dict(s) for s in trans_segs]
            log(f"Whisper transcription completed. Transcribed {len(trans_segs)} words.")
        except Exception as e:
            log(f"Transcription failed: {e}")
            st.session_state.original_segments = [{"start": 0.0, "end": 5.0, "text": "Audio transcription failed."}]
            st.session_state.current_segments = [{"start": 0.0, "end": 5.0, "text": "Audio transcription failed."}]
            
    st.session_state.status = "reviewed"
    st.rerun()

# Right Column: Program Monitor / Timeline / Previews
with col_right:
    st.markdown('<div class="premiere-panel">', unsafe_allow_html=True)
    
    if st.session_state.status in ["idle", "analyzing"]:
        st.subheader("📺 Program Monitor: Source Preview")
        if st.session_state.video_path:
            st.markdown('<div class="monitor-screen">', unsafe_allow_html=True)
            st.video(st.session_state.video_path)
            st.markdown('</div>', unsafe_allow_html=True)
        else:
            st.info("Import a UGC video clip to view source monitor.")
            
    elif st.session_state.status in ["reviewed", "rendering", "completed"]:
        st.subheader("📺 Program Monitor")
        
        # Display Dynamic Visual Timeline
        hook_dur = st.session_state.segments_map['hook'][1] - st.session_state.segments_map['hook'][0]
        demo_dur = st.session_state.segments_map['demo'][1] - st.session_state.segments_map['demo'][0]
        cta_dur = st.session_state.segments_map['cta'][1] - st.session_state.segments_map['cta'][0]
        total_dur = hook_dur + demo_dur + cta_dur
        
        hook_pct = (hook_dur / total_dur) * 100 if total_dur > 0 else 33
        demo_pct = (demo_dur / total_dur) * 100 if total_dur > 0 else 33
        cta_pct = (cta_dur / total_dur) * 100 if total_dur > 0 else 33
        
        st.markdown(f"""
        <div class="timeline-track">
            <div style="color: #00e5ff; font-size: 11px; text-transform: uppercase; letter-spacing: 1.5px; font-weight: 700; margin-bottom: 8px; font-family: 'Outfit';">
                🎞️ Active Timeline Track (Total Duration: {total_dur:.2f}s)
            </div>
            <div style="display: flex; height: 35px; border-radius: 6px; overflow: hidden; border: 1px solid #2e2e42;">
                <div style="width: {hook_pct}%; background: linear-gradient(90deg, #00b4db, #0083b0); display: flex; align-items: center; justify-content: center; color: white; font-weight: bold; font-size: 12px; text-shadow: 1px 1px 2px rgba(0,0,0,0.5);">
                    Hook ({hook_dur:.1f}s)
                </div>
                <div style="width: {demo_pct}%; background: linear-gradient(90deg, #6c5ce7, #a29bfe); display: flex; align-items: center; justify-content: center; color: white; font-weight: bold; font-size: 12px; text-shadow: 1px 1px 2px rgba(0,0,0,0.5);">
                    Demo ({demo_dur:.1f}s)
                </div>
                <div style="width: {cta_pct}%; background: linear-gradient(90deg, #ff007f, #ff00ab); display: flex; align-items: center; justify-content: center; color: white; font-weight: bold; font-size: 12px; text-shadow: 1px 1px 2px rgba(0,0,0,0.5);">
                    CTA ({cta_dur:.1f}s)
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        # Custom Sequence Reordering Panel
        st.subheader("🔄 AI Video Sequence Reorder")
        st.markdown("Rearrange the segment layout of your output video below:")
        
        c_order1, c_order2, c_order3 = st.columns(3)
        with c_order1:
            seg1 = st.selectbox("1st Clip Part", ["Hook", "Demo", "CTA"], index=0)
        with c_order2:
            seg2 = st.selectbox("2nd Clip Part", ["Hook", "Demo", "CTA"], index=1)
        with c_order3:
            seg3 = st.selectbox("3rd Clip Part", ["Hook", "Demo", "CTA"], index=2)
            
        st.session_state.custom_order = [seg1, seg2, seg3]
        
        # Validation warning for duplicate orders
        if len(set(st.session_state.custom_order)) < 3:
            st.error("⚠️ Duplicate clip selections! All three parts (Hook, Demo, CTA) must be assigned exactly once.")
            order_valid = False
        else:
            st.success(f"Current reorder path: **{' ➜ '.join(st.session_state.custom_order)}**")
            order_valid = True

        # Previews and Outputs Display
        if st.session_state.status == "completed":
            st.markdown("---")
            st.subheader("🎬 Output Export Bin")
            
            tab_5, tab_15, tab_30, tab_60, tab_custom = st.tabs([
                "5s Hook Cut", "15s Pitch Cut", "30s UGC Ad Cut", "60s Standard Cut", "Custom Reordered Cut"
            ])
            
            # Helper download utility
            def get_download_btn(filepath, label):
                if os.path.exists(filepath):
                    with open(filepath, "rb") as f:
                        st.download_button(
                            label=f"💾 Download {label}",
                            data=f,
                            file_name=os.path.basename(filepath),
                            mime="video/mp4"
                        )
                else:
                    st.error("Target video file not found.")

            with tab_5:
                st.markdown("#### Hook Only Cut (5s)")
                if 5 in st.session_state.cuts_paths:
                    filepath = st.session_state.cuts_paths[5]
                    st.video(filepath)
                    get_download_btn(filepath, "5s Cut")
                    
            with tab_15:
                st.markdown("#### Quick Pitch Cut (15s)")
                if 15 in st.session_state.cuts_paths:
                    filepath = st.session_state.cuts_paths[15]
                    st.video(filepath)
                    get_download_btn(filepath, "15s Cut")
                    
            with tab_30:
                st.markdown("#### Standard Ad Cut (30s)")
                if 30 in st.session_state.cuts_paths:
                    filepath = st.session_state.cuts_paths[30]
                    st.video(filepath)
                    get_download_btn(filepath, "30s Cut")
                    
            with tab_60:
                st.markdown("#### Full Length Cut (60s)")
                if 60 in st.session_state.cuts_paths:
                    filepath = st.session_state.cuts_paths[60]
                    st.video(filepath)
                    get_download_btn(filepath, "60s Cut")
                    
            with tab_custom:
                st.markdown("#### Custom Sequence Cut")
                if "custom" in st.session_state.cuts_paths:
                    filepath = st.session_state.cuts_paths["custom"]
                    st.video(filepath)
                    get_download_btn(filepath, "Reordered Custom Cut")
                else:
                    st.warning("Reordered cut was not created. Confirm sequence configuration.")
                    
            if st.session_state.improvement_logs:
                st.subheader("🧠 Self-Improvement Prompt Updates")
                st.info(st.session_state.improvement_logs)
                
            if st.button("🔄 Edit and Process Another Video"):
                st.session_state.project_id = None
                st.session_state.filename = ""
                st.session_state.video_path = ""
                st.session_state.status = "idle"
                st.session_state.segments_map = {}
                st.session_state.original_segments = []
                st.session_state.current_segments = []
                st.session_state.cuts_paths = {}
                st.session_state.logs = []
                st.session_state.improvement_logs = ""
                st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)

# Sidebar: Subtitle Review & Text Edit Panel
if st.session_state.status in ["reviewed", "rendering", "completed"]:
    with st.sidebar:
        st.header("✏️ Subtitle Text review")
        st.markdown("Edit transcription texts and adjust timings before burning them:")
        st.markdown("---")
        
        edited_segs = []
        for idx, seg in enumerate(st.session_state.current_segments):
            st.markdown(f"**Segment {idx+1}**")
            col_start, col_end = st.columns(2)
            with col_start:
                s_val = st.number_input(f"Start (s)##{idx}", value=float(seg["start"]), step=0.1, format="%.2f")
            with col_end:
                e_val = st.number_input(f"End (s)##{idx}", value=float(seg["end"]), step=0.1, format="%.2f")
            
            t_val = st.text_area(f"Text##{idx}", value=seg["text"], height=70)
            
            edited_segs.append({
                "start": s_val,
                "end": e_val,
                "text": t_val
            })
            st.markdown("---")
            
        st.session_state.current_segments = edited_segs
        
        # Action controls
        if st.session_state.status == "reviewed":
            # Disabled button if orders are not valid
            if order_valid:
                if st.button("🔥 Burn Subtitles & Cut Video", key="burn_btn"):
                    st.session_state.status = "rendering"
                    st.rerun()
            else:
                st.error("Sequence configuration is invalid. Fix duplicates first.")

# Execute Subtitle Burn, Cuts, Reordering and Prompt Self-Improvement Loop
if st.session_state.status == "rendering":
    log("Step 3: Comparing corrections and logging changes to DB...")
    for idx, (orig, curr) in enumerate(zip(st.session_state.original_segments, st.session_state.current_segments)):
        if orig["text"].strip() != curr["text"].strip() or abs(orig["start"] - curr["start"]) > 0.05 or abs(orig["end"] - curr["end"]) > 0.05:
            database.log_subtitle_correction(
                project_id=st.session_state.project_id,
                segment_index=idx,
                start_time=curr["start"],
                end_time=curr["end"],
                original_text=orig["text"],
                corrected_text=curr["text"]
            )
            log(f"Logged corrections on Segment {idx+1}: '{orig['text']}' -> '{curr['text']}'")
            
    log("Step 4: Compiling ASS and rendering final cuts with FFMPEG (NVENC)...")
    with st.spinner("Processing video renders using GPU encoder..."):
        try:
            cut_paths = processor.render_final_cuts(
                project_id=st.session_state.project_id,
                filename=st.session_state.filename,
                original_video_path=st.session_state.video_path,
                segments=st.session_state.current_segments,
                style_preset=st.session_state.style_preset,
                segments_map=st.session_state.segments_map,
                order=st.session_state.custom_order
            )
            st.session_state.cuts_paths = cut_paths
            log("Renders generated successfully.")
        except Exception as e:
            log(f"Renders execution failed: {e}")
            
    log("Step 5: Starting Ollama Gemma-4 Self-Improvement Loop...")
    with st.spinner("Optimizing prompts with Gemma-4..."):
        improvement_msg = database.run_self_improvement_loop(st.session_state.project_id)
        st.session_state.improvement_logs = improvement_msg
        log("Auto-Improvement completed.")
        
    st.session_state.status = "completed"
    st.rerun()
