# generate_qa_dataset_gemini.py
import sqlite3
import pandas as pd
import google.generativeai as genai
import json
import os
import time
from tqdm import tqdm
from colorama import Fore, Style, init

init(autoreset=True)

# --- Configuration (Gemini Version) ---
DB_FILE = "crawler_state.db"
OUTPUT_TRAINING_FILE = "ue5_qa_training_dataset.jsonl" 
# Get your API key from Google AI Studio (https://aistudio.google.com/app/apikey)
GEMINI_API_KEY = "YOUR_API_KEY"  # IMPORTANT: REPLACE WITH YOUR GOOGLE GEMINI API KEY
GEMINI_MODEL = "gemini-1.5-flash-latest" # Fast, capable, and cost-effective

# This prompt instructs Gemini how to create the Q&A pairs.
# Gemini responds well to clear, structured instructions and examples.
SYSTEM_PROMPT = """
You are an expert AI data specialist creating a training dataset about the Unreal Engine.
Your task is to generate 5 high-quality, distinct question-and-answer pairs based ONLY on the following text from the official documentation.

RULES:
1. The user is a game developer. The questions must be practical and specific.
2. The answers must be derived exclusively from the provided text. Do not invent information or use outside knowledge.
3. Your output MUST be a valid JSON object containing a single key "qa_pairs" which holds a list of objects. Each object in the list must have a "question" key and an "answer" key.
4. Do not output any other text, explanation, or formatting like markdown backticks.

EXAMPLE TEXT:
'A Material Instance is an asset that allows you to create variations of a master Material by changing its parameters without needing to recompile the shader, making it very efficient for projects with many similar-looking objects.'

EXAMPLE JSON OUTPUT:
{
  "qa_pairs": [
    {
      "question": "What is the primary function of a Material Instance in Unreal Engine?",
      "answer": "A Material Instance is an asset that allows you to create variations of a master Material by changing its parameters without needing to recompile the shader, making it very efficient for projects with many similar-looking objects."
    }
  ]
}
"""

# --- Main Script ---
def main():
    if GEMINI_API_KEY == "...":
        print(f"{Fore.RED}Error: Please replace '...' with your actual Google Gemini API key in the script.")
        return

    # Configure the Gemini client
    try:
        genai.configure(api_key=GEMINI_API_KEY)
    except Exception as e:
        print(f"{Fore.RED}Failed to configure Gemini client: {e}")
        return

    # Configure the model for JSON output
    generation_config = {
      "temperature": 0.4,
      "response_mime_type": "application/json",
    }
    model = genai.GenerativeModel(GEMINI_MODEL, generation_config=generation_config)

    print(f"Loading content from '{DB_FILE}'...")
    with sqlite3.connect(DB_FILE) as conn:
        df = pd.read_sql_query("SELECT url, content_raw FROM analyzed_content", conn)
    
    print(f"Found {len(df)} documents to process.")
    
    processed_urls = set()
    if os.path.exists(OUTPUT_TRAINING_FILE):
        with open(OUTPUT_TRAINING_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    processed_urls.add(json.loads(line)['metadata']['source_url'])
                except (json.JSONDecodeError, KeyError): continue
        print(f"Resuming. Found {len(processed_urls)} URLs already processed.")

    with open(OUTPUT_TRAINING_FILE, 'a', encoding='utf-8') as f:
        pbar = tqdm(total=len(df), desc="Generating Q&A Pairs")
        
        for index, row in df.iterrows():
            if row['url'] in processed_urls:
                pbar.update(1)
                continue
            
            text = row['content_raw']
            chunk_size = 8000 # Gemini 1.5 Flash has a large context window, we can use bigger chunks
            chunks = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]

            for chunk in chunks:
                if not chunk.strip(): continue
                
                # Add a small delay to respect API rate limits
                time.sleep(1.5) 
                
                try:
                    # --- Call the Gemini API ---
                    full_prompt = [SYSTEM_PROMPT, "Here is the documentation text:", chunk]
                    response = model.generate_content(full_prompt)
                    
                    # --- Extract and validate the JSON content from Gemini's response ---
                    response_json = json.loads(response.text)
                    qa_pairs = response_json.get("qa_pairs", [])
                    
                    for pair in qa_pairs:
                        if "question" in pair and "answer" in pair:
                            formatted_entry = {
                                "instruction": pair["question"],
                                "input": "",
                                "output": pair["answer"],
                                "metadata": {"source_url": row['url']}
                            }
                            f.write(json.dumps(formatted_entry) + '\n')
                            
                except Exception as e:
                    print(f"\n{Fore.RED}An error occurred while processing URL {row['url']}: {e}")
                    if hasattr(e, 'response') and 'rate limit' in str(e.response.text).lower():
                        print(f"{Fore.YELLOW}Rate limit likely exceeded. Waiting for 60 seconds...")
                        time.sleep(60)

                    with open("qa_generation_errors.log", 'a', encoding='utf-8') as error_log:
                        error_log.write(f"URL: {row['url']}\nError: {e}\n\n")
                    break
            
            pbar.update(1)
            
    pbar.close()
    print(f"\n{Style.BRIGHT}Dataset generation complete!{Style.RESET_ALL}")
    print(f"Your training data is ready in '{OUTPUT_TRAINING_FILE}'.")


if __name__ == "__main__":
    main()