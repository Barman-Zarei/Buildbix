"""AI parameter tuning. The model may only return JSON for whitelisted parameters; anything else is discarded."""
import json
import re

from ..ai.llm import LLMClient
from . import registry as R

SYSTEM = """You tune parameters of a fixed, pre-approved CI workflow template. Reply with ONLY one JSON object.
Allowed keys and values (anything else is discarded): {schema}
Omit keys you are not sure about. Never output commands, YAML or explanations.
Project facts (from automated analysis): {facts}"""


def adapt_params(llm: LLMClient, template_id: str, analysis: dict, current: dict) -> tuple[dict, dict]:
    editable = [d for d in R.describe(template_id) if d["editable"]]
    facts = {k: analysis.get(k) for k in ("languages", "frameworks", "build_systems", "dependencies", "entry_points", "node", "buildozer")}
    raw = llm.complete(SYSTEM.format(schema=json.dumps(editable), facts=json.dumps(facts, default=str)[:6000]),
                       [{"role": "user", "content": f"Current values: {json.dumps(current)}. Choose parameters for this project."}])
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        data = json.loads(m.group(0)) if m and len(m.group(0)) <= 2000 else {}
    except ValueError:
        data = {}
    ok, rejected = {}, {}
    for k, v in (data.items() if isinstance(data, dict) else []):
        try:
            ok.update(R.validate_user_params(template_id, {k: v}))
        except R.ParamError as e:
            rejected[k] = str(e)
    return ok, rejected
