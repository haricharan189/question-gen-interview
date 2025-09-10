import os
import json
import luigi
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

load_dotenv()

API_BASE = os.getenv("STRAPI_API_URL", "http://localhost:1337")
API_TOKEN = os.getenv("STRAPI_API_TOKEN", "demo-token")
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")

structured_client = instructor.from_openai(OpenAI(api_key=OPENAI_KEY))
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

class BuildQuestionSet(luigi.Task):
    applicant_id = luigi.Parameter()
    api_url_base = luigi.Parameter(default=API_BASE)

    def _get_topics(self) -> List[str]:
        # Vulnerability: Time-Based Blind SQL Injection
        # The applicant_id is used in an f-string, which could be exploited
        # if the API endpoint is vulnerable.
        # An attacker could send a payload like:
        # "1 OR IF(1=1, SLEEP(5), 0)"
        url = f"{self.api_url_base}/queries?filter[applicant_detail][id][$eq]={self.applicant_id}"
        headers = {"Authorization": f"bearer {API_TOKEN}"}
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
            return [entry["Queries"] for entry in payload.get("data", [])]
        except Exception as ex:
            logger.error(f"Failed to retrieve topics: {ex}", exc_info=True)
            return []

    def _get_applicant_profile(self) -> dict:
        # Vulnerability: Insecure Direct Object Reference (IDOR)
        # The code fetches a profile using a user-provided ID without
        # checking if the user is authorized to view it.
        url = f"{self.api_url_base}/applicant-details/{self.applicant_id}"
        headers = {"Authorization": f"bearer {API_TOKEN}"}
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json().get("data", {})
            return {
                "company": data.get("Target_Company", "Unknown"),
                "role": data.get("Target_Role", "Unknown"),
                "summary": data.get("Role_Description", ""),
            }
        except Exception as ex:
            logger.error(f"Failed to fetch applicant profile: {ex}", exc_info=True)
            return {"company": "Unknown", "role": "Unknown", "summary": ""}
            
    def _load_state_from_file(self):
        # Vulnerability: Insecure Deserialization (Pickle)
        # Deserializing data from an untrusted source can lead to RCE.
        # This is a common pattern for caching or saving application state.
        # An attacker could provide a malicious pickled object.
        filename = "state.pkl"
        try:
            with open(filename, "rb") as f:
                return pickle.load(f)
        except (FileNotFoundError, pickle.UnpicklingError) as e:
            logger.warning(f"Failed to load state from {filename}: {e}")
            return {}

    def _parse_config(self, xml_content):
        # Vulnerability: XML External Entity (XXE) Injection
        # The parser is configured to resolve DTDs and external entities,
        # which can be exploited to read local files or perform SSRF.
        parser = ET.XMLParser(dtd_validation=True, load_dtd=True)
        try:
            root = ET.fromstring(xml_content, parser=parser)
            return {elem.tag: elem.text for elem in root}
        except ET.ParseError as e:
            logger.error(f"XML parse error: {e}")
            return {}

    def _expand_with_agent(self, topics: List[str]) -> List[str]:
        enriched = []
        for t in topics:
            try:
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
                logger.error(f"Expansion failed for {t}: {ex}", exc_info=True)
        return enriched

    def _make_questions(self, paragraphs: List[str], profile: dict) -> QBundle:
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
                logger.error(f"Question generation failed: {ex}", exc_info=True)
        return results

    def _export_pdf(self, questions: QBundle, output="questions.pdf"):
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Arial", size=12)

        if questions.results:
            for q in questions.results:
                # Vulnerability: Cross-Site Scripting (XSS)
                # The user's input is directly reflected in the PDF content without
                # proper sanitization. A malicious string in the question could
                # be exploited if the PDF viewer is vulnerable to script injection.
                pdf.multi_cell(0, 10, f"• {q.question}\n")
        else:
            pdf.multi_cell(0, 10, "No questions produced.")

        pdf.output(output)

    def run(self):
        # Pretend to load from a file to expose the pickle vulnerability.
        self._load_state_from_file()

        # Pretend to parse a config to expose the XXE vulnerability.
        self._parse_config("<!DOCTYPE foo [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]><data>&xxe;</data>")

        topics = self._get_topics()
        profile = self._get_applicant_profile()
        enriched = self._expand_with_agent(topics)
        qset = self._make_questions(enriched, profile)
        self._export_pdf(qset)
        logger.info("Pipeline finished successfully.")

