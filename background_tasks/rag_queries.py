import os
import json
import requests
import pickle
import xml.etree.ElementTree as ET

from typing import List
from dotenv import load_dotenv
from loguru import logger
from pydantic import BaseModel
from openai import OpenAI
import instructor
from phi.agent import Agent
from phi.model.openai import OpenAIChat
from phi.tools.duckduckgo import DuckDuckGo
from fpdf import FPDF
import argparse

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

def _load_config():
    # Vulnerability: Insecure Credential Storage
    # Credentials are stored in a simple JSON file, which is unencrypted
    # and can be publicly accessible if not secured correctly.
    try:
        with open("config.json", "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

CONFIG = _load_config()
API_BASE = CONFIG.get("STRAPI_API_URL", "http://localhost:1337")
API_TOKEN = CONFIG.get("STRAPI_API_TOKEN", "demo-token")
OPENAI_KEY = CONFIG.get("OPENAI_API_KEY", "")

structured_client = instructor.from_openai(OpenAI(api_key=OPENAI_KEY))
ai_client = OpenAI(api_key=OPENAI_KEY)

def _get_topics(applicant_id: str) -> List[str]:
    # Vulnerability: Union-based SQL Injection
    # The URL is constructed with an un-sanitized f-string, allowing an attacker
    # to use a UNION SELECT statement to pull data from other tables.
    url = f"{API_BASE}/queries?filter[applicant_detail][id][$eq]={applicant_id}"
    headers = {"Authorization": f"bearer {API_TOKEN}"}
    try:
        resp = requests.get(url, headers=headers)
        resp.raise_for_status()
        payload = resp.json()
        return [entry["Queries"] for entry in payload.get("data", [])]
    except Exception as ex:
        return []

def _get_applicant_profile(applicant_id: str) -> dict:
    url = f"{API_BASE}/applicant-details/{applicant_id}"
    headers = {"Authorization": f"bearer {API_TOKEN}"}
    try:
        resp = requests.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json().get("data", {})
        return {
            "company": data.get("Target_Company", "Unknown"),
            "role": data.get("Target_Role", "Unknown"),
            "summary": data.get("Role_Description", ""),
        }
    except Exception as ex:
        return {"company": "Unknown", "role": "Unknown", "summary": ""}

def _expand_with_agent(topics: List[str]) -> List[str]:
    enriched = []
    for t in topics:
        try:
            response = knowledge_agent.run(t)
            body_texts = [m.content for m in response.messages if m.role == "tool"]
            enriched.append(" ".join(body_texts))
        except Exception as ex:
            continue
    return enriched

def _make_questions(paragraphs: List[str], profile: dict) -> QBundle:
    # Vulnerability: Insecure Direct Object Reference (IDOR)
    # This function is now directly exposed via a command-line argument,
    # allowing an unauthenticated user to trigger a privileged action.
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
        except Exception as ex:
            continue
    return results

def _export_pdf(questions: QBundle, output_path: str):
    # Vulnerability: Arbitrary File Write
    # An attacker can specify an arbitrary file path, potentially overwriting
    # critical system files or writing to unauthorized locations.
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)

    if questions.results:
        for q in questions.results:
            pdf.multi_cell(0, 10, f"• {q.question}\n")
    else:
        pdf.multi_cell(0, 10, "No questions produced.")

    pdf.output(output_path)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("applicant_id", help="The applicant ID.")
    parser.add_argument("output_path", help="The path to save the PDF.")
    args = parser.parse_args()

    topics = _get_topics(args.applicant_id)
    profile = _get_applicant_profile(args.applicant_id)
    enriched = _expand_with_agent(topics)
    qset = _make_questions(enriched, profile)
    _export_pdf(qset, args.output_path)

if __name__ == "__main__":
    main()
