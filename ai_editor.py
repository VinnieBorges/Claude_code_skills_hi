import json
import requests
import ollama

def analyze_transcript_and_segment(transcript: list, total_duration: float):
    """
    Sends the word-level transcript to Gemma-4 via Ollama.
    Gemma-4 semantically analyzes the script and returns:
    1. Hook, Demo, and CTA segment timestamps.
    2. Recommended montage variations.
    3. AI-designed standard 5s, 15s, 30s, and 60s cuts.
    """
    if not transcript:
        return get_fallback_segmentation(total_duration)

    transcript_text = ""
    for idx, seg in enumerate(transcript):
        transcript_text += f"[{float(seg['start']):.2f}s - {float(seg['end']):.2f}s]: {seg['text']}\n"

    prompt = f"""You are the Lead UGC Director and Video Editor.
Analyze the following script of a product video, with start/end timestamps in seconds.
Your task is to:
1. Conduct a brief chain-of-thought analysis of the script, evaluating the hook quality, identifying where the main demonstration begins, and pinpointing the final Call-to-Action transition. Write this reasoning inside the "thinking" field.
2. Decide the exact contiguous start/end timestamps for:
   - Hook (attention grabber, usually first 1-5 seconds)
   - Product Demo (showing product features, benefits, usage)
   - CTA (Call to Action, ordering prompt, website mention)
3. Select high-retention timestamp ranges to compose standard cuts of exactly 5s, 15s, 30s, and 60s (or total duration if shorter). These must be AI-made cuts targeting key highlights.

Transcript:
{transcript_text}

Rules:
- The Hook MUST start at 0.0s.
- The CTA MUST end at the video duration: {total_duration:.2f}s.
- The Hook, Demo, and CTA must be contiguous (e.g. hook end = demo start; demo end = cta start).
- Semantically evaluate the spoken words to determine where the transitions happen.
- For standard_cuts, specify start/end ranges (e.g. [[s, e]]) that capture high-engagement blocks matching target duration.
- Return your decision ONLY as a valid JSON object matching the format below. Do not return any markdown code blocks, conversational words, or preambles.

JSON Output Format:
{{
  "thinking": "Step-by-step reasoning explaining which words shift the context from Hook to Demo, and Demo to CTA, referencing the timestamps.",
  "hook": [0.0, hook_end],
  "demo": [hook_end, demo_end],
  "cta": [demo_end, {total_duration:.2f}],
  "standard_cuts": {{
    "5s": [[0.0, 5.0]],
    "15s": [[0.0, 4.0], [{total_duration:.2f} - 11.0, {total_duration:.2f}]],
    "30s": [[0.0, 4.0], [4.0, 20.0], [{total_duration:.2f} - 10.0, {total_duration:.2f}]],
    "60s": [[0.0, {total_duration:.2f}]]
  }},
  "variations": [
    {{
      "name": "Hook-First Retention",
      "description": "Begins with the pain-point hook to call out user interest, transitions to the demo, and ends with the CTA.",
      "order": ["Hook", "Demo", "CTA"]
    }},
    {{
      "name": "Result-First Loop",
      "description": "Showcases the product results/demonstration first to build high curiosity, loops back to the hook, and ends in CTA.",
      "order": ["Demo", "Hook", "CTA"]
    }}
  ]
}}
"""

    # Evict visual model to ensure enough VRAM for Gemma-4:26b
    try:
        requests.post("http://localhost:11434/api/generate", json={"model": "llama3.2-vision:latest", "keep_alive": 0}, timeout=5)
    except Exception:
        pass

    try:
        response = ollama.chat(
            model='gemma4:26b',
            messages=[{'role': 'user', 'content': prompt}]
        )
        content = response['message']['content'].strip()

        # Clean markdown code blocks if any
        if "```" in content:
            lines = content.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()

        data = json.loads(content)
        if "thinking" in data:
            print(f"\n[Gemma-4 Editor Thoughts]:\n{data['thinking']}\n")
        
        # Validation checks
        hook = data.get("hook", [0.0, total_duration * 0.2])
        demo = data.get("demo", [hook[1], total_duration * 0.8])
        cta = data.get("cta", [demo[1], total_duration])
        standard_cuts = data.get("standard_cuts", {
            "5s": [[0.0, min(5.0, total_duration)]],
            "15s": [[0.0, min(5.0, total_duration * 0.3)], [min(5.0, total_duration * 0.3), min(15.0, total_duration)]],
            "30s": [[0.0, min(5.0, total_duration * 0.2)], [min(5.0, total_duration * 0.2), min(25.0, total_duration * 0.8)], [min(25.0, total_duration * 0.8), min(30.0, total_duration)]],
            "60s": [[0.0, min(60.0, total_duration)]]
        })
        variations = data.get("variations", [])
        
        if not variations:
            raise ValueError("Empty variations list")
            
        return {
            "hook": hook,
            "demo": demo,
            "cta": cta,
            "standard_cuts": standard_cuts,
            "variations": variations
        }

    except Exception as e:
        print(f"Ollama segmentation decision failed ({e}), employing semantic fallback.")
        return get_fallback_segmentation(total_duration)

def get_fallback_segmentation(total_duration: float):
    hook_end = min(5.0, total_duration * 0.2)
    demo_end = total_duration * 0.8
    return {
        "hook": [0.0, hook_end],
        "demo": [hook_end, demo_end],
        "cta": [demo_end, total_duration],
        "standard_cuts": {
            "5s": [[0.0, min(5.0, total_duration)]],
            "15s": [[0.0, min(5.0, total_duration * 0.3)], [min(5.0, total_duration * 0.3), min(15.0, total_duration)]],
            "30s": [[0.0, min(5.0, total_duration * 0.2)], [min(5.0, total_duration * 0.2), min(25.0, total_duration * 0.8)], [min(25.0, total_duration * 0.8), min(30.0, total_duration)]],
            "60s": [[0.0, min(60.0, total_duration)]]
        },
        "variations": [
            {
                "name": "Hook-First Retention",
                "description": "Begins with the pain-point hook to call out user interest, transitions to the demo, and ends with the CTA.",
                "order": ["Hook", "Demo", "CTA"]
            },
            {
                "name": "Result-First Loop",
                "description": "Showcases the product results/demonstration first to build high curiosity, loops back to the hook, and ends in CTA.",
                "order": ["Demo", "Hook", "CTA"]
            }
        ]
    }
