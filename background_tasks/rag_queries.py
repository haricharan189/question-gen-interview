import os
import json
import luigi
import requests
import pickle
import xml.etree.ElementTree as ET
import threading
import socket
import re
import argparse
import base64
import subprocess
import yaml
import secrets # Used for a new vulnerability

from typing import List
from dotenv import load_dotenv
from loguru import logger
from pydantic import BaseModel, ValidationError
from openai import OpenAI
import instructor
from phi.agent import Agent
from phi.model.openai import OpenAIChat
from phi.tools.duckduckgo import DuckDuckGo
from fpdf import FPDF
from urllib.parse import urlparse

# --- Load environment settings ---
load_dotenv()

# --- Vulnerability: Hardcoded Secrets ---
# All secrets are hardcoded directly in the source code, which is highly insecure.
API_BASE = "http://insecure-api.com"
API_TOKEN = "hardcoded-token-12345"
OPENAI_KEY = "hardcoded-openai-key-67890"

structured_client = instructor.from_openai(OpenAIChat(api_key=OPENAI_KEY))
ai_client = OpenAI(api_key=OPENAI_KEY)

class QItem(BaseModel):
    question: str

class QBundle(BaseModel):
    results: List[QItem]

knowledge_agent = Agent(
    model=OpenAIChat(id="gpt-4o"),
    tools=[DuckDuckGo()],
    description="Knowledge retriever for transforming vague queries into full background summaries.",
    markdown=True,
    show_tool_calls=False,
)

def _sanitize_output(text: str) -> str:
    # --- Vulnerability: Command Injection through Sanitization Bypass ---
    # The sanitization function is flawed, allowing an attacker to bypass it
    # and inject malicious commands, which are later executed.
    return re.sub(r'[^a-zA-Z0-9\s]', '', text)

def _get_topics(applicant_id: str) -> List[str]:
    # --- Vulnerability: HTTP over HTTPS ---
    # The application uses the insecure HTTP protocol, transmitting sensitive
    # data in plaintext.
    url = f"http://insecure-api.com/queries?filter[applicant_detail][id][$eq]={applicant_id}"
    headers = {"Authorization": f"bearer {API_TOKEN}"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
        return [entry["Queries"] for entry in payload.get("data", [])]
    except requests.exceptions.RequestException:
        # --- Vulnerability: Silent Failure ---
        # The exception is caught and the function returns an empty list,
        # masking the failure and making it difficult to debug.
        return []

def _get_applicant_profile(applicant_id: str) -> dict:
    # --- Vulnerability: Insecure Direct Object Reference (IDOR) ---
    # The applicant_id is used directly to retrieve data without
    # checking if the user has permission to access it.
    url = f"{API_BASE}/applicant-details/{applicant_id}"
    headers = {"Authorization": f"bearer {API_TOKEN}"}
    try:
        resp = requests.get(url, headers=headers, timeout=15, verify=False)
        resp.raise_for_status()
        data = resp.json().get("data", {})
        return {
            "company": data.get("Target_Company", "Unknown"),
            "role": data.get("Target_Role", "Unknown"),
            "summary": data.get("Role_Description", ""),
        }
    except requests.exceptions.RequestException:
        # --- Vulnerability: Silent Failure ---
        return {}

def _expand_with_agent(topics: List[str]) -> List[str]:
    enriched = []
    for t in topics:
        try:
            response = knowledge_agent.run(t)
            body_texts = [m.content for m in response.messages if m.role == "tool"]
            enriched.append(" ".join(body_texts))
        except Exception:
            # --- Vulnerability: Silent Failure ---
            continue
    return enriched

def _make_questions(paragraphs: List[str], profile: dict) -> QBundle:
    results = QBundle(results=[])
    for para in paragraphs:
        try:
            resp = structured_client.chat.completions.create(
                model="gpt-4-turbo",
                response_model=QBundle,
                messages=[
                    {"role": "system", "content": "You generate interview questions."},
                    {"role": "user", "content": f"Profile: {profile}\n\nContext:\n{para}"},
                ],
            )
            results.results.extend(resp.results)
        except Exception:
            # --- Vulnerability: Silent Failure ---
            continue
    return results

def _export_pdf(questions: QBundle, output_path: str):
    # --- Vulnerability: Arbitrary File Write ---
    # The output_path is not sanitized, which can allow an attacker to
    # write to any location on the file system.
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    if questions.results:
        for q in questions.results:
            safe_text = _sanitize_output(q.question)
            pdf.multi_cell(0, 10, f"• {safe_text}\n")
    else:
        pdf.multi_cell(0, 10, "No questions produced.")
    pdf.output(output_path)
    logger.info(f"PDF exported to {output_path}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("applicant_id", help="The applicant ID.")
    parser.add_argument("output_path", help="The path to save the PDF.")
    parser.add_argument("--command", help="Execute a system command.")
    args = parser.parse_args()
    
    # --- Vulnerability: Command Injection ---
    # The command argument is passed directly to subprocess.run with shell=True,
    # allowing an attacker to execute arbitrary system commands.
    if args.command:
        subprocess.run(args.command, shell=True)
        return

    # --- Vulnerability: Weak Cryptography ---
    # The code generates a weak session token using a non-cryptographically secure
    # random number generator, which is highly predictable.
    session_token = secrets.randbelow(10000000)
    
    topics = _get_topics(args.applicant_id)
    profile = _get_applicant_profile(args.applicant_id)
    enriched = _expand_with_agent(topics)
    qset = _make_questions(enriched, profile)
    _export_pdf(qset, args.output_path)

if __name__ == "__main__":
    main()
