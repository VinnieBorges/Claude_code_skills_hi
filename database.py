import os
import sqlite3
import json
from datetime import datetime

# Define base paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(BASE_DIR, "raw")
CUTS_DIR = os.path.join(BASE_DIR, "cuts")
DB_DIR = os.path.join(BASE_DIR, "database")
DB_PATH = os.path.join(DB_DIR, "vinicut.db")
WATCH_DIR = os.path.join(BASE_DIR, "watch")
AUTO_CUTS_DIR = os.path.join(BASE_DIR, "auto_cuts")

# Default self-learning transcription prompt (seed + "reset to default" target).
DEFAULT_WHISPER_PROMPT = (
    "Transcribe the audio accurately. Focus on punctuation, capitalization, and "
    "correct spelling of technical terms or brand names like Hidratei."
)

def init_db():
    """Initializes the required directories and SQLite database tables."""
    # Task 1: Create local directory structure
    for directory in [RAW_DIR, CUTS_DIR, DB_DIR, WATCH_DIR, AUTO_CUTS_DIR]:
        os.makedirs(directory, exist_ok=True)
        
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Create tables
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status TEXT DEFAULT 'pending',
        segments_map_json TEXT,
        transcript_json TEXT,
        style_preset TEXT DEFAULT 'Bold Yellow'
    )
    """)
    
    # Run dynamic alters for backward compatibility
    try:
        cursor.execute("ALTER TABLE projects ADD COLUMN segments_map_json TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE projects ADD COLUMN transcript_json TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE projects ADD COLUMN style_preset TEXT DEFAULT 'Bold Yellow'")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE projects ADD COLUMN bg_music_path TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE projects ADD COLUMN font_family TEXT DEFAULT 'Montserrat'")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE projects ADD COLUMN zoom_effect INTEGER DEFAULT 1")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE projects ADD COLUMN custom_preset_json TEXT")
    except sqlite3.OperationalError:
        pass
        
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS cuts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER,
        cut_type TEXT NOT NULL,
        start_time REAL,
        end_time REAL,
        filepath TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(id)
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS custom_fonts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT NOT NULL,
        family_name TEXT UNIQUE NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS system_settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)
    
    # Prepopulate default settings
    default_settings = [
        ("vision_model", "llama3.2-vision:latest"),
        ("edit_model", "gemma4:26b"),
        ("whisper_model", "large-v3")
    ]
    for key, val in default_settings:
        cursor.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES (?, ?)", (key, val))
        
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS subtitle_corrections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER,
        segment_index INTEGER,
        start_time REAL,
        end_time REAL,
        original_text TEXT,
        corrected_text TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(id)
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS system_prompts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        component TEXT UNIQUE NOT NULL,
        prompt_text TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS style_preferences (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        keyword TEXT UNIQUE NOT NULL,
        preset_name TEXT NOT NULL
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ai_montages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER,
        name TEXT NOT NULL,
        description TEXT,
        order_json TEXT NOT NULL,
        filepath_subbed TEXT,
        filepath_raw TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(id)
    )
    """)
    
    # Prepopulate default system prompts
    cursor.execute("INSERT OR IGNORE INTO system_prompts (component, prompt_text) VALUES (?, ?)", (
        "whisper",
        DEFAULT_WHISPER_PROMPT
    ))
    
    cursor.execute("INSERT OR IGNORE INTO system_prompts (component, prompt_text) VALUES (?, ?)", (
        "vision",
        "Analyze this video to identify key structural parts: the Hook (first 1-5 seconds that grab attention), the Product Demonstration (showing the product features or in action), and the CTA (Call to Action at the end). Return a valid JSON with keys 'hook', 'demo', and 'cta', each having 'start' and 'end' numeric timestamp values in seconds. Example: {'hook': {'start': 0, 'end': 3}, 'demo': {'start': 3, 'end': 25}, 'cta': {'start': 25, 'end': 30}}"
    ))
    
    # Prepopulate default style keyword mappings
    default_styles = [
        ("car", "Minimalist"),
        ("auto", "Minimalist"),
        ("luxury", "Minimalist"),
        ("skin", "Clean White"),
        ("cream", "Clean White"),
        ("serum", "Clean White"),
        ("wellness", "Clean White"),
        ("cosmetics", "Clean White")
    ]
    for keyword, style in default_styles:
        cursor.execute("INSERT OR IGNORE INTO style_preferences (keyword, preset_name) VALUES (?, ?)", (keyword, style))
        
    conn.commit()
    conn.close()

def get_db_connection():
    """
    Opens a SQLite connection configured for safe concurrent access.

    The watch-folder thread, the queue-worker thread and FastAPI BackgroundTasks
    all write to the same DB. WAL lets readers and a writer coexist, and the busy
    timeout makes writers wait for a lock instead of immediately raising
    "database is locked".
    """
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def get_system_prompt(component):
    """Retrieves the system prompt for a specific component."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT prompt_text FROM system_prompts WHERE component = ?", (component,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return row[0]
    return ""

def update_system_prompt(component, prompt_text):
    """Updates the system prompt for a component."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO system_prompts (component, prompt_text, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(component) DO UPDATE SET prompt_text=excluded.prompt_text, updated_at=excluded.updated_at
    """, (component, prompt_text, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_style_preset_for_file(filename):
    """Determines the style preset based on filename keywords."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT keyword, preset_name FROM style_preferences")
    rules = cursor.fetchall()
    conn.close()
    
    filename_lower = filename.lower()
    for keyword, preset in rules:
        kw = keyword.lower()
        if kw in filename_lower:
            # Avoid matching "car" when it is only part of "care"
            if kw == "car" and "care" in filename_lower:
                if filename_lower.count("car") == filename_lower.count("care"):
                    continue
            return preset
    return "Bold Yellow"  # Default preset

def log_subtitle_correction(project_id, segment_index, start_time, end_time, original_text, corrected_text):
    """Logs a subtitle correction to the database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO subtitle_corrections (project_id, segment_index, start_time, end_time, original_text, corrected_text)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (project_id, segment_index, start_time, end_time, original_text, corrected_text))
    conn.commit()
    conn.close()

def get_subtitle_corrections(limit=100):
    """Returns recent subtitle corrections (newest first) for the telemetry UI."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, project_id, segment_index, original_text, corrected_text, created_at
        FROM subtitle_corrections
        ORDER BY id DESC
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [
        {
            "id": r[0], "project_id": r[1], "segment_index": r[2],
            "original_text": r[3], "corrected_text": r[4], "created_at": r[5],
        }
        for r in rows
    ]

def run_self_improvement_loop(project_id):
    """Runs the self-improvement loop using Ollama Gemma-4 to update system prompts based on subtitle corrections."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get corrections for this project
    cursor.execute("""
        SELECT original_text, corrected_text FROM subtitle_corrections 
        WHERE project_id = ?
    """, (project_id,))
    corrections = cursor.fetchall()
    conn.close()
    
    if not corrections:
        return "No corrections logged. System prompt unchanged."
        
    current_prompt = get_system_prompt("whisper")
    
    corrections_str = "\n".join([f"- Original: '{orig}' -> Corrected: '{corr}'" for orig, corr in corrections])
    
    prompt = f"""You are the Self-Improvement Optimizer for ViniCut-AI, a professional video editing system.
Your job is to analyze the spelling and grammar corrections made by the editor, and update the transcription system prompt for Whisper to avoid these errors next time.

Here are the manual corrections the editor just made:
{corrections_str}

Here is the current system prompt for Whisper:
"{current_prompt}"

Write a revised system prompt for Whisper that incorporates instructions on how to handle these specific cases (e.g. adding specific brand name spellings, correcting common acronyms, formatting styles).
The revised prompt must still be a concise set of transcription instructions.
Return ONLY the new system prompt text. Do not include any introductory text, markdown code blocks, or explanations. Only the text of the prompt.
"""
    # Unload Llama 3.2 Vision to free up VRAM before loading Gemma-4
    try:
        import requests
        requests.post("http://localhost:11434/api/generate", json={"model": "llama3.2-vision:latest", "keep_alive": 0}, timeout=5)
    except Exception:
        pass

    try:
        import ollama
        response = ollama.chat(
            model='gemma4:26b',
            messages=[
                {'role': 'user', 'content': prompt}
            ]
        )
        new_prompt = response['message']['content'].strip()
        # Strip code block markers if any (Gemma might return them anyway)
        if new_prompt.startswith("```"):
            lines = new_prompt.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            new_prompt = "\n".join(lines).strip()
        if new_prompt:
            update_system_prompt("whisper", new_prompt)
            return f"System prompt successfully updated. New prompt:\n{new_prompt}"
        else:
            return "Ollama returned an empty response. System prompt unchanged."
    except Exception as e:
        return f"Failed to run self-improvement loop via Gemma-4: {str(e)}"

def get_setting(key, default_value=None):
    """Retrieves the value of a system setting."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM system_settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return row[0]
    return default_value

def update_setting(key, value):
    """Updates or inserts a system setting."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO system_settings (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
    """, (key, str(value)))
    conn.commit()
    conn.close()

def register_custom_font(filename, family_name):
    """Registers a custom uploaded font in the DB."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO custom_fonts (filename, family_name)
            VALUES (?, ?)
        """, (filename, family_name))
        conn.commit()
    except sqlite3.IntegrityError:
        # Ignore duplicate registers
        pass
    conn.close()

def get_custom_fonts():
    """Returns a list of all registered custom fonts."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT filename, family_name FROM custom_fonts ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()
    return [{"filename": r[0], "family_name": r[1]} for r in rows]

# Automatically run initialization on import
init_db()

