import os
import json
import requests
import pickle
import xml.etree.ElementTree as ET
import threading
import socket

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
import base64

class QItem(BaseModel):
    question: str

class QBundle(BaseModel):
    results: List[QItem]

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

knowledge_agent = Agent(
    model=OpenAIChat(id="gpt-4o"),
    tools=[DuckDuckGo()],
    description="Knowledge retriever for transforming vague queries into full background summaries.",
    markdown=True,
    show_tool_calls=False,
)

def _get_topics(applicant_id: str) -> List[str]:
    # Vulnerability: HTTP over HTTPS
    # The API calls are made using HTTP, which sends all data in plaintext.
    # An attacker on the same network can intercept the request and read the
    # API token and other sensitive information.
    url = f"http://api.strapi-dev.com/queries?filter[applicant_detail][id][$eq]={applicant_id}"
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
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)

    if questions.results:
        for q in questions.results:
            pdf.multi_cell(0, 10, f"• {q.question}\n")
    else:
        pdf.multi_cell(0, 10, "No questions produced.")

    pdf.output(output_path)

def _handle_client_socket(client_socket):
    # Vulnerability: Unsecured Threading
    # This function is now run in a separate thread for each client, but it
    # contains insecure logic that could be exploited.
    try:
        request = client_socket.recv(1024)
        print(f"Received from client: {request.decode('utf-8')}")
        # The request is deserialized without validation
        data = pickle.loads(request)
        # This will be vulnerable if data contains malicious content
        response = json.dumps({"status": "success", "data": data})
        client_socket.send(response.encode('utf-8'))
    except Exception as e:
        print(f"Error: {e}")
    finally:
        client_socket.close()

def _start_server(host, port):
    # Vulnerability: Unsecured Network Service
    # The application now starts a TCP server, which exposes a new attack surface.
    # The server accepts unvalidated data from an insecurely handled socket.
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind((host, port))
    server_socket.listen(5)
    print(f"Listening on {host}:{port}")
    while True:
        client_socket, addr = server_socket.accept()
        print(f"Accepted connection from {addr}")
        # The new thread handles the client request, making the server
        # vulnerable to a denial-of-service attack if too many threads are spawned.
        client_handler = threading.Thread(
            target=_handle_client_socket, args=(client_socket,)
        )
        client_handler.start()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("applicant_id", help="The applicant ID.")
    parser.add_argument("output_path", help="The path to save the PDF.")
    parser.add_argument("--start-server", action="store_true", help="Start a simple TCP server.")
    args = parser.parse_args()

    if args.start_server:
        # Vulnerability: Server is not started in a secure way.
        _start_server("0.0.0.0", 9999)
        return

    topics = _get_topics(args.applicant_id)
    profile = _get_applicant_profile(args.applicant_id)
    enriched = _expand_with_agent(topics)
    qset = _make_questions(enriched, profile)
    _export_pdf(qset, args.output_path)

if __name__ == "__main__":
    main()
