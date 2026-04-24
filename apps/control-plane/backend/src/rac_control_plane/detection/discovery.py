# pattern: Imperative Shell
"""Rule discovery — walks detection.rules package via pkgutil and imports each
submodule to extract the module-level RULE constant.

Duplicate rule_id → DuplicateRuleIdError (startup failure).
Missing RULE → logged warning, module skipped.
"""

import importlib
import pkgutil
from types import ModuleType

import structlog

from rac_control_plane.detection.contracts import Rule

logger = structlog.get_logger(__name__)


class DuplicateRuleIdError(Exception):
    """Raised when two rule modules declare the same rule_id."""


def load_rules(package: str = "rac_control_plane.detection.rules") -> dict[str, Rule]:
    """Walk *package* via pkgutil, import each submodule, extract ``RULE``.

    Args:
        package: Dotted package name to walk. Defaults to the standard rules package.

    Returns:
        Mapping of rule_id → Rule for all successfully loaded rules.

    Raises:
        DuplicateRuleIdError: If two modules declare the same rule_id.
    """
    rules: dict[str, Rule] = {}

    # Import the root package first so pkgutil can find its __path__
    try:
        pkg: ModuleType = importlib.import_module(package)
    except ImportError as exc:
        logger.warning("rule_package_not_found", package=package, error=str(exc))
        return rules

    package_path = getattr(pkg, "__path__", None)
    if package_path is None:
        logger.warning("rule_package_has_no_path", package=package)
        return rules

    for _finder, module_name, _is_pkg in pkgutil.walk_packages(
        path=package_path,
        prefix=f"{package}.",
        onerror=lambda name: logger.warning("rule_module_walk_error", module=name),
    ):
        try:
            mod: ModuleType = importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("rule_module_import_error", module=module_name, error=str(exc))
            continue

        rule: object = getattr(mod, "RULE", None)
        if rule is None:
            logger.warning("rule_module_missing_RULE", module=module_name)
            continue

        if not isinstance(rule, Rule):
            logger.warning(
                "rule_module_RULE_wrong_type",
                module=module_name,
                got=type(rule).__name__,
            )
            continue

        if rule.rule_id in rules:
            raise DuplicateRuleIdError(
                f"Duplicate rule_id {rule.rule_id!r} found in {module_name!r} "
                f"(already registered from another module)"
            )

        rules[rule.rule_id] = rule
        logger.debug("rule_loaded", rule_id=rule.rule_id, module=module_name)

    logger.info("rules_loaded", count=len(rules), rule_ids=sorted(rules.keys()))
    return rules
