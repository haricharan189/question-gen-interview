import asyncio
import aiohttp
import os
import json
from dotenv import load_dotenv
from loguru import logger
from pydantic import BaseModel
from typing import List, Dict
from openai import OpenAI
import instructor
from fpdf import FPDF

load_dotenv()

# --- ENVIRONMENT ---
STRAPI_BASE_URL = os.getenv("STRAPI_API_URL")
STRAPI_TOKEN = f"bearer {os.getenv('STRAPI_API_TOKEN')}"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

structured_client = instructor.from_openai(OpenAI(api_key=OPENAI_API_KEY))
client = OpenAI(api_key=OPENAI_API_KEY)

# --- Models ---
class FinalQuestion(BaseModel):
    Question: str

class FinalQuestions(BaseModel):
    Questions: List[FinalQuestion]

# --- Simple Cache ---
CACHE: Dict[str, Dict] = {}

# --- Fetch Data ---
async def fetch_queries(docid: str) -> List[str]:
    if docid in CACHE and "queries" in CACHE[docid]:
        return CACHE[docid]["queries"]

    url = f"{STRAPI_BASE_URL}/queries"
    headers = {"Authorization": STRAPI_TOKEN}
    params = {"filter[applicant_detail][$eq]": docid}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()
            queries = [item["Queries"] for item in data["data"]]

    CACHE.setdefault(docid, {})["queries"] = queries
    return queries

async def fetch_role(docid: str) -> Dict:
    if docid in CACHE and "role" in CACHE[docid]:
        return CACHE[docid]["role"]

    url = f"{STRAPI_BASE_URL}/applicant-details/{docid}"
    headers = {"Authorization": STRAPI_TOKEN}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            resp.raise_for_status()
            data = await resp.json()

    role_info = {
        "company": data["data"].get("Target_Company"),
        "role": data["data"].get("Target_Role"),
        "description": data["data"].get("Role_Description"),
    }
    CACHE.setdefault(docid, {})["role"] = role_info
    return role_info

# --- Paragraph Generation ---
async def generate_paragraph(query: str) -> str:
    """
    Instead of DuckDuckGo, ask OpenAI to simulate retrieval.
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a research assistant summarizing answers from the web."},
                {"role": "user", "content": f"Search the web and summarize this query in one detailed paragraph:\n{query}"}
            ],
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Error generating paragraph: {e}")
        return ""

# --- Question Generation ---
async def generate_questions(role_info: Dict, paragraph: str) -> List[FinalQuestion]:
    try:
        response = structured_client.chat.completions.create(
            model="gpt-4o",
            response_model=FinalQuestions,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an AI interviewer creating role-specific, skill-based, and company-focused questions."
                        " Generate 5 unique, relevant questions per context."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Role: {role_info['role']} at {role_info['company']}\n\n"
                        f"Context paragraph:\n{paragraph}"
                    ),
                },
            ],
        )
        return response.Questions
    except Exception as e:
        logger.error(f"Error generating questions: {e}")
        return []

# --- Save Outputs ---
def save_json(docid: str, questions: List[FinalQuestion]):
    path = f"output/questions_{docid}.json"
    with open(path, "w") as f:
        json.dump({"Questions": [q.dict() for q in questions]}, f, indent=2)
    logger.info(f"Saved JSON to {path}")

def save_pdf(docid: str, questions: List[FinalQuestion]):
    path = f"output/questions_{docid}.pdf"
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Arial", size=12)

    if questions:
        for q in questions:
            text = q.Question.encode("latin-1", "replace").decode("latin-1")
            pdf.multi_cell(0, 10, f"• {text}\n")
    else:
        pdf.multi_cell(0, 10, "No questions generated.\n")

    pdf.output(path)
    logger.info(f"Saved PDF to {path}")

# --- Main Runner ---
async def pipeline(docid: str):
    queries = await fetch_queries(docid)
    role_info = await fetch_role(docid)

    logger.info(f"Fetched {len(queries)} queries for docid={docid}")
    logger.info(f"Role info: {role_info}")

    paragraphs = await asyncio.gather(*(generate_paragraph(q) for q in queries))
    logger.info(f"Generated {len(paragraphs)} paragraphs")

    all_questions: List[FinalQuestion] = []
    for para in paragraphs:
        qs = await generate_questions(role_info, para)
        all_questions.extend(qs)

    save_json(docid, all_questions)
    save_pdf(docid, all_questions)

# --- Entry ---
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python pipeline.py <docid>")
    else:
        asyncio.run(pipeline(sys.argv[1]))










