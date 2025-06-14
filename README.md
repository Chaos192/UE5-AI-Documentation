
# UE5 LLaMA LoRA Data Pipeline

## 📑 Table of Contents
- [Project Description](#project-description)
- [How to Replicate](#how-to-replicate)
  - [Initial Setup](#initial-setup)
  - [Stage 1: Crawling the Documentation](#stage-1-crawling-the-documentation)
  - [Stage 2: Generating the Q&A Dataset](#stage-2-generating-the-qa-dataset)
  - [Stage 3: Fine-Tuning with LoRA](#stage-3-fine-tuning-with-lora)
- [Expected Results](#expected-results)
- [Limitations and Future Improvements](#limitations-and-future-improvements)
- [License](#license)
- [Acknowledgements](#acknowledgements)

---

## 📘 Project Description

This is a **proof-of-concept** project providing a complete data pipeline to create a specialized documentation assistant for **Unreal Engine 5**. Inspired by pioneering projects like [bublint/ue5-llama-lora](https://github.com/bublint/ue5-llama-lora), this repository explores the use of locally trainable LLMs to build powerful, context-aware development tools.

The core idea is that by **fine-tuning** a general-purpose LLM on a curated source of domain-specific knowledge, we can build an assistant that **outperforms even large general models** like ChatGPT on niche subjects.

### 🧩 Pipeline Overview
1. A **robust, multi-threaded web crawler** to scrape Unreal Engine documentation, bypassing Cloudflare protections.
2. An **AI-powered dataset generator** using the Gemini API to convert scraped text into a structured Question-Answer format.

The final output is a high-quality dataset ready for **LoRA fine-tuning** on models like LLaMA or Mistral, turning them into Unreal Engine experts.

---

## 🔧 How to Replicate

You will need a machine with:
- A modern NVIDIA GPU
- Python 3.x installed

### ✅ Initial Setup

**Clone the repository:**
```bash
git clone <your-repository-url>
cd <your-repository-folder>
```

**Create a virtual environment (recommended):**
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

**Install all dependencies:**
```bash
pip install undetected-chromedriver webdriver-manager google-generativeai spacy pandas beautifulsoup4 tqdm colorama
```

**Download the NLP model:**
```bash
python -m spacy download en_core_web_sm
```

**Get your API key:**
- Visit [Google AI Studio](https://aistudio.google.com/) to obtain a free Gemini API key.
- Paste the key into `2_generate_qa_dataset.py` under the `GEMINI_API_KEY` variable.

---

### 📄 Stage 1: Crawling the Documentation

Run the crawler script to scrape Unreal Engine documentation:
```bash
python 1_crawler.py
```

- Output is saved in `crawler_state.db`.
- Resumable if interrupted.
- To watch it work or debug, set `DEBUG_MODE = True` in the script.

---

### 🧠 Stage 2: Generating the Q&A Dataset

After crawling:

1. **Insert your Gemini API key** in `2_generate_qa_dataset.py`:
    ```python
    GEMINI_API_KEY = "YOUR_API_KEY_HERE"
    ```

2. **Run the dataset generator:**
    ```bash
    python 2_generate_qa_dataset.py
    ```

- This produces `ue5_qa_training_dataset.jsonl`
- **Note**: May incur API costs.

---

### 🛠️ Stage 3: Fine-Tuning with LoRA

Use [Oobabooga's Text Generation WebUI](https://github.com/oobabooga/text-generation-webui):

1. **Set up the WebUI** following official instructions.
2. **Load a base model**, such as:
   - Mistral-7B
   - LLaMA 3 8B
3. **Train using LoRA**:
   - Go to the **Training** tab.
   - Choose **LoRA** method.
   - Use your `ue5_qa_training_dataset.jsonl` as input.
   - Configure training settings and start.

---

## 🎯 Expected Results

After fine-tuning, your model should:

- Accurately answer technical UE5 questions (Nanite, Lumen, Mass Avoidance, etc.)
- Provide relevant code/blueprint examples from documentation.
- Minimize hallucinations by grounding output in source material.

---

## 🚧 Limitations and Future Improvements

- **Hallucinations**: Still possible without high-quality Q&A.
- **Prompting**: Custom prompt formats can significantly improve performance.
- **Dataset format**: Alternative structures (e.g., Alpaca-style) might yield better fine-tuning.
- **Crawler speed**: Simpler methods may be faster but less resilient.

---

## 📄 License

This project is licensed under the **MIT License**. See `LICENSE` file for details.

---

## 🙏 Acknowledgements

- Inspired by [bublint/ue5-llama-lora](https://github.com/bublint/ue5-llama-lora)
- Oobabooga's amazing **Text Generation WebUI**
- The open-source contributors behind:
  - `undetected-chromedriver`
  - `beautifulsoup4`
  - `spacy`
  - `google-generativeai`
  - and more

---

> _Built to empower game developers and AI tinkerers alike._ 🎮🤖
