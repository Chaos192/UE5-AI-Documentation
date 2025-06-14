# train_model.py
import torch
import sqlite3
import pandas as pd
from transformers import (
    AutoTokenizer,
    AutoModelForCausalLM,
    TrainingArguments,
    Trainer,
    BitsAndBytesConfig
)
from datasets import Dataset
import os

# --- Configuration ---
DB_FILE = "crawler_state.db"
BASE_MODEL = "gpt2"  # A solid, small starting model. You can swap this for others like "EleutherAI/pythia-1b-deduped".
NEW_MODEL_NAME = "unreal-engine-gpt2" # The name of the folder for your new model
TRAINING_EPOCHS = 1 # 1-3 epochs is usually sufficient for domain adaptation.
LEARNING_RATE = 2e-5
BATCH_SIZE = 1 # Keep this at 1 for low VRAM. We use gradient accumulation to compensate.
GRADIENT_ACCUMULATION_STEPS = 8 # Effective batch size will be BATCH_SIZE * GRADIENT_ACCUMULATION_STEPS (1*8=8)

# --- Main Script ---
def create_training_dataset(db_path: str) -> Dataset:
    """Loads the scraped content from the SQLite database."""
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database file not found at {db_path}. Please run the scraper first.")
    
    with sqlite3.connect(db_path) as conn:
        # Load the raw content into a pandas DataFrame
        df = pd.read_sql_query("SELECT content_raw FROM analyzed_content", conn)
        # Rename column for clarity
        df.rename(columns={"content_raw": "text"}, inplace=True)

    print(f"Loaded {len(df)} documents from the database.")
    # Convert the DataFrame to a Hugging Face Dataset object
    return Dataset.from_pandas(df)

def main():
    print("--- Domain-Adaptive Pre-training Script ---")

    # --- 1. Load the Dataset ---
    print("\n[Step 1/5] Loading scraped data from database...")
    try:
        dataset = create_training_dataset(DB_FILE)
    except Exception as e:
        print(f"Error loading dataset: {e}")
        return

    # --- 2. Load Tokenizer and Model ---
    print(f"\n[Step 2/5] Loading base model and tokenizer: '{BASE_MODEL}'...")

    # Configure quantization to load the model in 4-bit, saving a lot of memory.
    quantization_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16
    )

    # Load the tokenizer
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
    tokenizer.pad_token = tokenizer.eos_token # Set padding token for consistency

    # Load the model with quantization
    model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL,
        quantization_config=quantization_config,
        device_map="auto", # Automatically use the GPU if available
    )
    
    print("Model and tokenizer loaded successfully.")

    # --- 3. Prepare and Tokenize the Dataset ---
    print("\n[Step 3/5] Tokenizing and formatting the dataset...")
    
    def tokenize_function(examples):
        # Tokenize the text. The tokenizer converts text into numbers (token IDs) the model understands.
        return tokenizer(examples["text"], truncation=True, max_length=1024)

    tokenized_dataset = dataset.map(tokenize_function, batched=True, remove_columns=["text"])
    print("Dataset prepared.")

    # --- 4. Configure Training ---
    print("\n[Step 4/5] Configuring training arguments...")
    training_args = TrainingArguments(
        output_dir=NEW_MODEL_NAME,
        num_train_epochs=TRAINING_EPOCHS,
        per_device_train_batch_size=BATCH_SIZE,
        gradient_accumulation_steps=GRADIENT_ACCUMULATION_STEPS,
        learning_rate=LEARNING_RATE,
        logging_steps=10,        # Log progress every 10 steps
        save_steps=100,          # Save a checkpoint every 100 steps
        fp16=torch.cuda.is_available(), # Use mixed-precision if a GPU is available (faster)
        push_to_hub=False,       # Do not upload to Hugging Face Hub
    )

    # --- 5. Start Training ---
    print("\n[Step 5/5] Starting training... (This may take several hours)")
    print("You can monitor progress below. Look for the 'loss' value to decrease over time.")
    
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=tokenized_dataset,
    )

    trainer.train()

    # --- Save the Final Model ---
    print("\nTraining complete! Saving final model...")
    trainer.save_model(NEW_MODEL_NAME)
    tokenizer.save_pretrained(NEW_MODEL_NAME)
    print(f"✅ Model saved to folder: './{NEW_MODEL_NAME}'")


if __name__ == "__main__":
    main()