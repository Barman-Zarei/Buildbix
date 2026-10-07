import json

from .llm import LLMClient

SYSTEM = """You are the BUILDBIX project assistant. You are READ-ONLY: you cannot change files, run builds or create
resources. If the user wants an action, explain what you would do and which BUILDBIX action to use; never claim you did
it. Base answers on the project facts below; if something is unknown, say so. Answer in the user's language, concisely.
Project facts (JSON, from automated analysis; no source code is shared with you):
{facts}"""


def project_facts(name: str, analysis: dict) -> dict:
    keys = ("languages", "frameworks", "build_systems", "package_managers", "entry_points", "tests", "findings", "targets")
    return {"name": name, **{k: analysis.get(k) for k in keys}}


def answer(llm: LLMClient, project_name: str, analysis: dict, history: list[dict], message: str) -> str:
    system = SYSTEM.format(facts=json.dumps(project_facts(project_name, analysis), ensure_ascii=False))
    return llm.complete(system, history[-10:] + [{"role": "user", "content": message}])
