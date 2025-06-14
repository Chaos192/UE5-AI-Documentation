UE5 LLaMA LoRA Data Pipeline
This project provides a complete, multi-stage data pipeline for scraping the official Unreal Engine documentation, processing the content with AI, and generating a high-quality, structured dataset suitable for training a Large Language Model (LLM).

This is a proof-of-concept project that showcases the potential for using small, locally trainable LLMs to create next-generation documentation tools, inspired by projects like bublint/ue5-llama-lora.

Key Features
Robust Web Crawler: Utilizes undetected-chromedriver to bypass advanced bot detection and Cloudflare challenges on the target website.

Resilient & Resumable: Uses a SQLite database to track the state of every URL (new, in-progress, success, failed), allowing the crawl to be stopped and resumed at any time without losing progress.

Intelligent Retry Logic: Automatically retries failed pages with an incremental backoff, making it resilient to temporary network or website errors.

High-Concurrency Architecture: Employs a multi-threaded approach with a dedicated, asynchronous database writer to prevent I/O bottlenecks and maximize scraping speed.

AI-Powered Dataset Generation: Uses the Google Gemini Pro API to intelligently read scraped content and generate high-quality Question-Answer pairs.

Optimized for Scale: Incorporates best practices like rotating log files, efficient batch database updates, and a configurable debug mode.

The Workflow
The project is broken down into two main stages, each with its own script:

Stage 1: Crawling & Scraping (1_crawler.py)

This script navigates the entire Unreal Engine documentation site.

It scrapes the clean text content from every page.

It stores all found URLs, content, and processing status in the crawler_state.db database.

Stage 2: Q&A Dataset Generation (2_generate_qa_dataset.py)

This script reads the raw content from the database created in Stage 1.

It uses a powerful LLM (Google Gemini) to convert the raw text into structured question-answer pairs.

The final output is a ue5_qa_training_dataset.jsonl file, perfectly formatted for LoRA fine-tuning.

Setup and Installation
Follow these steps to set up your environment.

1. Clone the Repository

git clone <your-repository-url>
cd <your-repository-folder>

2. Create a Virtual Environment (Recommended)

python -m venv venv
source venv/bin/activate  # On Windows, use: venv\Scripts\activate

3. Install Dependencies
Install all required Python libraries using pip:

pip install undetected-chromedriver webdriver-manager google-generativeai spacy pandas beautifulsoup4 tqdm colorama

4. Download the NLP Model
The scraper uses a small spaCy model for some initial analysis. Download it with the following command:

python -m spacy download en_core_web_sm

5. Get Your API Key
The dataset generator requires a Google Gemini API key.

Go to Google AI Studio to get your free key.

You will need to paste this key into the 2_generate_qa_dataset.py script.

How to Use
Run the scripts in the following order.

Step 1: Run the Crawler
This script will populate the crawler_state.db with the documentation content. It can take several hours to complete, but it is fully resumable.

python 1_crawler.py

This will run in fast, headless mode.

To watch the browser windows for debugging, open the 1_crawler.py file and set DEBUG_MODE = True at the top.

Step 2: Generate the Training Dataset
After the crawler has finished (or collected a good amount of data), run this script.

Important: Open the 2_generate_qa_dataset.py file and replace the placeholder ... with your actual Gemini API key.

# In 2_generate_qa_dataset.py
GEMINI_API_KEY = "YOUR_API_KEY_HERE"

Then, run the script from your terminal:

python 2_generate_qa_dataset.py

This will read the database, make calls to the Gemini API, and create the ue5_qa_training_dataset.jsonl file. This process may incur costs depending on your API usage.

Step 3: Train Your Model
With your ue5_qa_training_dataset.jsonl file ready, you can now use it to fine-tune a model. A popular tool for this is Oobabooga's Text Generation WebUI.

Load a base model (e.g., a Mistral 7B or Llama 3 8B variant) in the WebUI.

Go to the Training tab and select the LoRA method.

Upload or select your .jsonl dataset.

Configure the training parameters and start the LoRA fine-tuning process.

File Descriptions
1_crawler.py: The main crawler script that populates the database.

2_generate_qa_dataset.py: The script that uses an LLM to generate the final Q&A training file.

crawler_state.db: The SQLite database that stores all state, content, and results. This is the "brain" of the project.

ue5_qa_training_dataset.jsonl: The final output file containing the structured Q&A data, ready for training.

unified_scraper.log: A rotating log file that records the crawler's activity for debugging.