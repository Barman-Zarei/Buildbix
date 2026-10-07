"""Human-approval tiers (spec section 34). The MVP assistant is read-only; mutating actions are listed so
future agents must pass through authorize()."""
from enum import Enum


class Tier(str, Enum):
    SAFE = "safe"
    APPROVAL = "requires_approval"
    HIGH_RISK = "high_risk"


ACTION_TIERS = {
    "analyze_project": Tier.SAFE, "read_source": Tier.SAFE, "run_tests": Tier.SAFE, "generate_config": Tier.SAFE,
    "modify_project_files": Tier.APPROVAL, "install_dependencies": Tier.APPROVAL, "create_resource": Tier.APPROVAL,
    "delete_database": Tier.HIGH_RISK, "delete_server": Tier.HIGH_RISK, "modify_production": Tier.HIGH_RISK,
    "rotate_credentials": Tier.HIGH_RISK,
}


def authorize(action: str, *, user_approved: bool, user_confirmed_high_risk: bool = False) -> bool:
    tier = ACTION_TIERS.get(action)
    if tier is None:
        return False  # unknown actions are denied
    if tier is Tier.SAFE:
        return True
    if tier is Tier.APPROVAL:
        return user_approved
    return user_approved and user_confirmed_high_risk
