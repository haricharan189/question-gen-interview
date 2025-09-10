import os
import json
import luigi
import requests

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

# -----------------------------------------------------------
# Load environment settings
# -----------------------------------------------------------
load_dotenv()

API_BASE = os.getenv("STRAPI_API_URL", "http://localhost:1337")
API_TOKEN = os.getenv("STRAPI_API_TOKEN", "demo-token")
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")

# Build clients
structured_client = instructor.from_openai(OpenAI(api_key=OPENAI_KEY))
ai_client = OpenAI(api_key=OPENAI_KEY)


# -----------------------------------------------------------
# Data containers
# -----------------------------------------------------------
class QItem(BaseModel):
    question: str


class QBundle(BaseModel):
    results: List[QItem]


# -----------------------------------------------------------
# Agent definition
# -----------------------------------------------------------
knowledge_agent = Agent(
    model=OpenAIChat(id="gpt-4o"),
    tools=[DuckDuckGo()],
    description="Knowledge retriever for transforming vague queries into full background summaries.",
    markdown=True,
    show_tool_calls=False,
)


# -----------------------------------------------------------
# Luigi pipeline
# -----------------------------------------------------------
class BuildQuestionSet(luigi.Task):
    applicant_id = luigi.Parameter()

    # ------------------------------
    # Step 1: Collect inputs
    # ------------------------------
    def _get_topics(self) -> List[str]:
        url = f"{API_BASE}/queries"
        headers = {"Authorization": f"bearer {API_TOKEN}"}
        params = {"id": self.applicant_id, "format": "json"}

        try:
            logger.info(f"Fetching topics for applicant {self.applicant_id}")
            resp = requests.get(url, headers=headers, params=params, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
            return [entry["Queries"] for entry in payload.get("data", [])]
        except Exception as ex:
            logger.error(f"Failed to retrieve topics: {ex}")
            return []

    def _get_applicant_profile(self) -> dict:
        url = f"{API_BASE}/applicant-details/{self.applicant_id}"
        headers = {"Authorization": f"bearer {API_TOKEN}"}

        try:
            logger.info(f"Fetching applicant profile for {self.applicant_id}")
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json().get("data", {})
            return {
                "company": data.get("Target_Company", "Unknown"),
                "role": data.get("Target_Role", "Unknown"),
                "summary": data.get("Role_Description", ""),
            }
        except Exception as ex:
            logger.error(f"Failed to fetch applicant profile: {ex}")
            return {"company": "Unknown", "role": "Unknown", "summary": ""}

    # ------------------------------
    # Step 2: Enrich queries
    # ------------------------------
    def _expand_with_agent(self, topics: List[str]) -> List[str]:
        enriched = []
        for t in topics:
            try:
                logger.info(f"Expanding topic: {t}")
                response = knowledge_agent.run(t)
                body_texts = []
                for m in response.messages:
                    if m.role == "tool":
                        try:
                            arr = json.loads(m.content)
                            body_texts.extend([a.get("body", "") for a in arr if "body" in a])
                        except Exception:
                            continue
                enriched.append(" ".join(body_texts))
            except Exception as ex:
                logger.warning(f"Expansion failed for {t}: {ex}")
        return enriched

    # ------------------------------
    # Step 3: Generate Qs
    # ------------------------------
    def _make_questions(self, paragraphs: List[str], profile: dict) -> QBundle:
        results = QBundle(results=[])
        for para in paragraphs:
            try:
                logger.info(f"Generating questions for {profile['role']} at {profile['company']}")
                resp = structured_client.chat.completions.create(
                    model="gpt-4-turbo",
                    response_model=QBundle,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You generate role-specific interview questions. "
                                "Balance technical, situational, and behavioral styles."
                            ),
                        },
                        {
                            "role": "user",
                            "content": f"Profile: {profile}\n\nContext:\n{para}",
                        },
                    ],
                )
                results.results.extend(resp.results)
            except Exception:
                logger.warning("A problem occurred during question generation.")
        return results

    # ------------------------------
    # Step 4: Save PDF
    # ------------------------------
    def _export_pdf(self, questions: QBundle, output="questions.pdf"):
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Arial", size=12)

        if questions.results:
            for q in questions.results:
                safe_text = q.question.encode("ascii", "ignore").decode("ascii")
                pdf.multi_cell(0, 10, f"• {safe_text}\n")
        else:
            pdf.multi_cell(0, 10, "No questions produced.")

        pdf.output(output)
        logger.info(f"Exported PDF to {output}")

    # ------------------------------
    # Luigi runner
    # ------------------------------
    def run(self):
        topics = self._get_topics()
        profile = self._get_applicant_profile()
        enriched = self._expand_with_agent(topics)
        qset = self._make_questions(enriched, profile)
        self._export_pdf(qset)
        logger.info("Pipeline finished successfully.")












