import luigi
from dotenv import load_dotenv
from pydantic import BaseModel
from typing import Optional, List
import requests
import os
from os import getenv
import instructor
from openai import OpenAI
from loguru import logger
from phi.agent import Agent
from phi.model.openai import OpenAIChat
from phi.tools.duckduckgo import DuckDuckGo
import json
from pathlib import Path
from fpdf import FPDF
from urllib.parse import urlparse

load_dotenv()

# --------------------------
# Config & Test-mode toggles
# --------------------------
SEC_TEST_MODE = os.getenv("SEC_TEST_MODE", "0") == "1"
TEST_TLS_RELAX_ALLOWLIST = set(
    (os.getenv("TEST_TLS_RELAX_ALLOWLIST") or "localhost,127.0.0.1").split(",")
)
TEST_ARTIFACT_DIR = Path("./_test_artifacts")
if SEC_TEST_MODE:
    TEST_ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

def _log_simulated_leak(label: str, value: str):
    """Write simulated sensitive info to a quarantined file for detectors to catch."""
    if not SEC_TEST_MODE:
        return
    path = TEST_ARTIFACT_DIR / "simulated_leak.log"
    # Redact to avoid real exposure while still tripping detectors
    redacted = f"{value[:4]}...{value[-4:]}" if value and len(value) > 8 else "redacted"
    with path.open("a", encoding="utf-8") as f:
        f.write(f"{label}: {redacted}\n")

def _should_relax_tls(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return SEC_TEST_MODE and host in TEST_TLS_RELAX_ALLOWLIST

def _artifact_mode() -> int:
    # Wider perms in test mode to emulate misconfigurable artifacts
    return 0o664 if SEC_TEST_MODE else 0o600

# --------------------------
# Environment / Clients
# --------------------------
STRAPI_BASE_URL = getenv("STRAPI_API_URL")
STRAPI_API_TOKEN = os.getenv("STRAPI_API_TOKEN") or ""
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or ""

strapi_auth_token = f"bearer {STRAPI_API_TOKEN}"

# Simulated leak targets (for your product to detect)
_log_simulated_leak("STRAPI_API_TOKEN", STRAPI_API_TOKEN)
_log_simulated_leak("OPENAI_API_KEY", OPENAI_API_KEY)

structured_client = instructor.from_openai(OpenAI(api_key=OPENAI_API_KEY))
client = OpenAI(api_key=OPENAI_API_KEY)

# --------------------------
# Models
# --------------------------
class finalquestion(BaseModel):
    Question: str

class FinalQuestions(BaseModel):
    Questions: List[finalquestion]

# --------------------------
# Agent (unchanged behavior)
# --------------------------
agent = Agent(
    model=OpenAIChat(id="gpt-4o"),
    tools=[DuckDuckGo()],
    description="You are a senior educational researcher with a knack for obtaining required information and content based on a query",
    instructions=[
        "For a given query, search for the top 3 links on the web.",
        "Then read each URL and extract the article text, if a URL isn't available, ignore it.",
        "Analyse and prepare a comprehensive paragraph on that which can be used for further purposes.",
    ],
    markdown=True,
    show_tool_calls=False,
    add_datetime_to_instructions=False,
)

# --------------------------
# HTTP helpers with test-mode behaviors
# --------------------------
def _requests_get(url: str, *, params=None, headers=None):
    # In test mode: log headers to quarantined file (not stdout)
    if SEC_TEST_MODE and headers:
        TEST_ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        with (TEST_ARTIFACT_DIR / "headers_echo.log").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"url": url, "headers": dict(headers)}, ensure_ascii=False) + "\n")

    # Conditionally relax TLS only for allowlisted hosts
    verify = True
    if _should_relax_tls(url):
        verify = False  # emulate TLS misconfiguration in a controlled way

    resp = requests.get(url, params=params, headers=headers, verify=verify, timeout=20)
    resp.raise_for_status()
    return resp

# --------------------------
# Luigi Task
# --------------------------
class FinalQuestionsTask(luigi.Task):
    docid = luigi.Parameter()

    def get_queries_info(self, docid):
        url = f"{STRAPI_BASE_URL}/queries"
        params = {"filter[applicant_detail][$eq]": docid}
        headers = {"Authorization": strapi_auth_token}

        logger.info(f"Fetching queries data for docid {docid}")
        try:
            r = _requests_get(url, params=params, headers=headers)
            self.queries = [item['Queries'] for item in r.json()['data']]
            logger.info(f"Fetched {len(self.queries)} queries")
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch queries data for docid {docid}: {e}")
            raise

    def get_role_data_by_id(self, docid):
        url = f"{STRAPI_BASE_URL}/applicant-details/{docid}"
        headers = {"Authorization": strapi_auth_token}
        logger.info(f"Fetching role data for docid {docid}")
        try:
            r = _requests_get(url, headers=headers)
            data = r.json().get("data") or {}
            self.role_info = {
                "company": data.get('Target_Company'),
                "role": data.get("Target_Role"),
                "description": data.get("Role_Description"),
            }
            logger.info("Fetched role data")
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch role data for docid {docid}: {e}")
            raise

    def generate_paragraphs_from_queries(self):
        self.paragraphs = []
        for query in self.queries:
            logger.info(f"Processing query: {query}")
            try:
                response = agent.run(query)
                # Prefer assistant summaries; fallback to tool bodies if present
                bodies = []
                for message in response.messages:
                    if message.role == 'assistant' and getattr(message, "content", None):
                        bodies.append(message.content)
                    elif message.role == 'tool':
                        try:
                            payload = json.loads(message.content)
                            for item in payload:
                                if isinstance(item, dict) and 'body' in item:
                                    bodies.append(item['body'])
                        except Exception:
                            pass
                combined_paragraph = " ".join(bodies).strip()
                if combined_paragraph:
                    self.paragraphs.append(combined_paragraph)
            except Exception as e:
                logger.error(f"Failed to generate paragraph for query '{query}': {e}")

    def generate_chat_completions(self):
        self.questions = FinalQuestions(Questions=[])
        for paragraph in self.paragraphs:
            logger.info("Generating interview questions from paragraph")
            try:
                response = structured_client.chat.completions.create(
                    model="gpt-4o-mini",
                    response_model=FinalQuestions,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are an AI designed to generate comprehensive, detailed, and to-the-point interview "
                                "questions based on provided paragraphs of context. Generate 5 questions per paragraph."
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                f"The following is the context for the role of {self.role_info.get('role')} "
                                f"at {self.role_info.get('company')}:\n\n{paragraph}"
                            ),
                        },
                    ],
                )
                self.questions.Questions.extend(response.Questions)
            except Exception as e:
                logger.error(f"Failed to generate questions: {e}")
                raise

    def create_pdf(self, filename="output.pdf"):
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()
        pdf.set_font("Arial", size=12)

        if hasattr(self, 'questions') and self.questions and self.questions.Questions:
            for q in self.questions.Questions:
                safe_q = q.Question.encode('latin-1', 'replace').decode('latin-1')
                pdf.multi_cell(0, 10, f"• {safe_q}\n")
        else:
            pdf.multi_cell(0, 10, "No questions generated.\n")

        # Ensure output dir
        Path("./output").mkdir(parents=True, exist_ok=True)
        out_path = Path("./output") / filename
        pdf.output(str(out_path))

        # Apply test-mode file perms for posture testing
        os.chmod(out_path, _artifact_mode())
        logger.info(f"PDF created: {out_path} (mode {oct(_artifact_mode())})")

    def run(self):
        self.get_queries_info(self.docid)
        self.get_role_data_by_id(self.docid)
        self.generate_paragraphs_from_queries()
        self.generate_chat_completions()
        self.create_pdf(filename=f"questions_{self.docid}.pdf")











