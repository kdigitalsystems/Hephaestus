import os
import json
import math
import re
import tempfile
from datetime import datetime, timezone
from database import SessionLocal
from models import Node, Edge

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.path.join(BASE_DIR, "docs")
EXPORT_PATH = os.path.join(DOCS_DIR, "dashboard_data.json")
HISTORY_PATH = os.path.join(DOCS_DIR, "link_history.json")

MIN_MARKET_CAP = 0 
IGNORED_SECTORS = ["Shell Companies", "Financial Services", "Real Estate"]
FALLBACK_LINKED_SECTOR = "Linked Companies"
EXPORT_AI_RESEARCH = os.environ.get("HEPHAESTUS_EXPORT_AI_RESEARCH", "0") == "1"
REVIEW_QUEUE_LIMIT = int(os.environ.get("HEPHAESTUS_REVIEW_QUEUE_LIMIT", "250"))
FOUNDRY_TERMS = (
    "advanced silicon fabrication",
    "advanced manufacturing services",
    "chip fabrication",
    "chip manufacturing",
    "chip production",
    "contract manufacturing",
    "foundry",
    "outsourced production",
    "semiconductor chips",
    "semiconductor manufacturing",
    "silicon fabrication",
    "silicon wafers",
)

def clean_num(val):
    if val is None:
        return None
    try:
        f_val = float(val)
        if math.isnan(f_val) or math.isinf(f_val):
            return None
        return f_val
    except (ValueError, TypeError):
        return val

CONSENSUS_PATTERN = re.compile(r"Consensus (\d+)/(\d+) for (\w+)")
# Model names contain colons ("qwen2.5:7b-instruct"); the rationale starts after the
# first colon that is followed by whitespace.
LEAD_RATIONALE_PATTERN = re.compile(r"Lead rationale from .+?:\s+(.+)$", re.DOTALL)
RATIONALE_MAX_CHARS = 280


def compact_rationale(text):
    text = " ".join(str(text or "").split())
    if len(text) <= RATIONALE_MAX_CHARS:
        return text
    return text[:RATIONALE_MAX_CHARS].rsplit(" ", 1)[0] + "…"


def summarize_review(note, source_url=None, review_status=None):
    """Turn a free-text review note into a small, publishable verification record.

    The dashboard shows how a link was verified (curated seed, consensus panel vote,
    single-model review, human review) without exposing internal queue mechanics.
    """
    note_text = " ".join(str(note or "").split())
    lowered = note_text.lower()
    source = str(source_url or "")
    if "manual" in source.lower() and not source.lower().startswith(("http://", "https://")) or lowered.startswith("curated seed"):
        return {"method": "curated", "label": "Curated seed relationship", "votes_for": None, "votes_total": None, "rationale": ""}
    match = CONSENSUS_PATTERN.search(note_text)
    if match:
        votes_for, votes_total = int(match.group(1)), int(match.group(2))
        rationale = LEAD_RATIONALE_PATTERN.search(note_text)
        return {
            "method": "consensus",
            "label": f"Consensus panel {votes_for}/{votes_total} models",
            "votes_for": votes_for,
            "votes_total": votes_total,
            "rationale": compact_rationale(rationale.group(1) if rationale else ""),
        }
    if lowered.startswith(("ollama review:", "ollama report review:")):
        return {
            "method": "model",
            "label": "Single-model review",
            "votes_for": 1,
            "votes_total": 1,
            "rationale": compact_rationale(note_text.split(":", 1)[1]),
        }
    if lowered.startswith("automated"):
        return {"method": "automated", "label": "Automated rule", "votes_for": None, "votes_total": None, "rationale": compact_rationale(note_text)}
    if note_text:
        return {"method": "human", "label": "Human review", "votes_for": None, "votes_total": None, "rationale": compact_rationale(note_text)}
    status = (review_status or "pending").lower()
    return {
        "method": "unreviewed" if status == "pending" else "unrecorded",
        "label": "Awaiting review" if status == "pending" else "Review record unavailable",
        "votes_for": None,
        "votes_total": None,
        "rationale": "",
    }


def stable_edge_key(edge, supplier_node=None, customer_node=None):
    supplier_node = supplier_node or edge.source_node
    customer_node = customer_node or edge.target_node
    supplier = supplier_node.ticker or supplier_node.name if supplier_node else edge.source_id
    customer = customer_node.ticker or customer_node.name if customer_node else edge.target_id
    dependency_type = edge.dependency_type or "Supply Link"
    return f"{supplier}->{customer}:{dependency_type}".upper()

def edge_payload(edge, node, supplier_node=None, customer_node=None):
    connected_node = node
    source_url = edge.source_url or "Unknown"
    if source_url.startswith(("http://", "https://")):
        source_type = "Web Source"
    elif "Manual" in source_url:
        source_type = "Manual"
    elif "AI" in source_url:
        source_type = "AI Research"
    else:
        source_type = "Source"

    return {
        "edge_id": edge.id,
        "relationship_key": stable_edge_key(edge, supplier_node, customer_node),
        "name": connected_node.name,
        "ticker": connected_node.ticker or "",
        "type": edge.dependency_type,
        "product": edge.product or edge.dependency_type,
        "confidence": clean_num(edge.confidence_score),
        "source": source_url,
        "source_title": edge.source_title or source_url,
        "source_type": source_type,
        "review_status": edge.review_status or "pending",
        "review_summary": summarize_review(getattr(edge, "review_note", None), source_url, edge.review_status),
        "revenue_share": clean_num(getattr(edge, "revenue_share", None)),
        "evidence_excerpt": edge.evidence_excerpt or "",
        "last_verified": edge.last_verified.strftime('%Y-%m-%d') if edge.last_verified else "N/A"
    }

def edge_rank(payload):
    source_type_rank = {
        "Manual": 3,
        "Web Source": 2,
        "AI Research": 1,
        "Source": 0,
    }
    confidence = payload.get("confidence")
    try:
        confidence_value = float(confidence) if confidence is not None else 0.0
    except (TypeError, ValueError):
        confidence_value = 0.0

    specificity = len(str(payload.get("product") or "")) + len(str(payload.get("evidence_excerpt") or ""))
    return (
        source_type_rank.get(payload.get("source_type"), 0),
        confidence_value,
        specificity,
        -(payload.get("edge_id") or 0),
    )

def unique_join(values):
    seen = set()
    merged = []
    for value in values:
        value = str(value or "").strip()
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        merged.append(value)
    return " / ".join(merged)

def merge_relationship_group(relationships):
    # A rejected member must never contribute to a published row.
    kept = [relationship for relationship in relationships if relationship_status(relationship) != "rejected"]
    ranked = sorted(kept or relationships, key=edge_rank, reverse=True)
    primary = dict(ranked[0])
    # relationship_key and edge_id must describe the same underlying decision, so
    # both come from the highest-ranked member rather than from different rows.
    primary["edge_id"] = ranked[0].get("edge_id")
    primary["type"] = unique_join(relationship.get("type") for relationship in ranked)
    primary["product"] = unique_join(relationship.get("product") for relationship in ranked)
    primary["source"] = unique_join(relationship.get("source") for relationship in ranked)
    primary["source_title"] = unique_join(relationship.get("source_title") for relationship in ranked)
    primary["source_type"] = unique_join(relationship.get("source_type") for relationship in ranked)
    primary["evidence_excerpt"] = unique_join(relationship.get("evidence_excerpt") for relationship in ranked)
    primary["review_status"] = unique_join(relationship.get("review_status") for relationship in ranked)
    primary["last_verified"] = max(
        (relationship.get("last_verified") for relationship in ranked if relationship.get("last_verified") and relationship.get("last_verified") != "N/A"),
        default="N/A",
    )

    confidences = [
        relationship.get("confidence")
        for relationship in ranked
        if relationship.get("confidence") is not None
    ]
    primary["confidence"] = max(confidences) if confidences else None
    shares = [
        relationship.get("revenue_share")
        for relationship in ranked
        if isinstance(relationship.get("revenue_share"), (int, float))
    ]
    primary["revenue_share"] = max(shares) if shares else None
    return primary

def merge_relationships(relationships):
    by_ticker = {}
    for relationship in relationships:
        ticker = relationship.get("ticker") or relationship.get("name") or ""
        if not ticker:
            continue
        by_ticker.setdefault(ticker, []).append(relationship)

    return sorted(
        (merge_relationship_group(group) for group in by_ticker.values()),
        key=lambda relationship: (
            relationship.get("ticker") or "",
            relationship.get("type") or "",
            relationship.get("product") or "",
        ),
    )

def status_tokens(value):
    return {
        token.strip().lower()
        for token in str(value or "pending").replace(",", "/").split("/")
        if token.strip()
    }

def relationship_status(relationship):
    tokens = status_tokens(relationship.get("review_status"))
    if "approved" in tokens:
        return "approved"
    if "rejected" in tokens:
        return "rejected"
    return "pending"

def source_tokens(value):
    return {
        token.strip().lower()
        for token in str(value or "").replace(",", "/").split("/")
        if token.strip()
    }

def relationship_score(relationship):
    status_rank = {"approved": 3, "pending": 1, "rejected": 0}
    source_rank = {
        "manual": 3,
        "web source": 2,
        "ai research": 1,
        "source": 0,
    }
    confidence = relationship.get("confidence")
    try:
        confidence_value = float(confidence) if confidence is not None else 0.0
    except (TypeError, ValueError):
        confidence_value = 0.0
    best_source = max(
        (source_rank.get(token, 0) for token in source_tokens(relationship.get("source_type"))),
        default=0,
    )
    return (
        status_rank.get(relationship_status(relationship), 0),
        best_source,
        confidence_value,
        relationship.get("ticker") or "",
    )

def top_relationships(relationships, limit=5):
    return [
        {
            "ticker": relationship.get("ticker") or "",
            "name": relationship.get("name") or relationship.get("ticker") or "Unknown",
            "type": relationship.get("type") or "Supply Link",
            "product": relationship.get("product") or relationship.get("type") or "Supply Link",
            "confidence": relationship.get("confidence"),
            "review_status": relationship_status(relationship),
            "source_type": relationship.get("source_type") or "Source",
            "last_verified": relationship.get("last_verified") or "N/A",
        }
        for relationship in sorted(relationships, key=relationship_score, reverse=True)[:limit]
    ]

def summarize_company_relationships(company):
    upstream = company.get("upstream", [])
    downstream = company.get("downstream", [])
    relationships = [*upstream, *downstream]
    status_counts = {"approved": 0, "pending": 0, "rejected": 0}
    source_counts = {"manual": 0, "web_source": 0, "ai_research": 0, "other": 0}
    confidences = []
    verified_dates = []

    for relationship in relationships:
        status_counts[relationship_status(relationship)] += 1
        sources = source_tokens(relationship.get("source_type"))
        if "manual" in sources:
            source_counts["manual"] += 1
        elif "web source" in sources:
            source_counts["web_source"] += 1
        elif "ai research" in sources:
            source_counts["ai_research"] += 1
        else:
            source_counts["other"] += 1
        confidence = relationship.get("confidence")
        try:
            if confidence is not None:
                confidences.append(float(confidence))
        except (TypeError, ValueError):
            pass
        last_verified = relationship.get("last_verified")
        if last_verified and last_verified != "N/A":
            verified_dates.append(last_verified)

    total_links = len(relationships)
    largest_side = max(len(upstream), len(downstream))
    concentration_score = round(largest_side / total_links, 3) if total_links else 0.0
    average_confidence = round(sum(confidences) / len(confidences), 3) if confidences else None
    confidence_score = int(round((average_confidence or 0) * 100))
    review_score = int(round((status_counts["approved"] / total_links) * 100)) if total_links else 0
    concentration_risk = int(round(concentration_score * 100)) if total_links else 0
    freshness_score = 100 if verified_dates else 0
    supplier_risk = int(round((len(upstream) / total_links) * concentration_risk)) if total_links else 0
    customer_risk = int(round((len(downstream) / total_links) * concentration_risk)) if total_links else 0
    risk_score = int(round((concentration_risk * 0.45) + ((100 - review_score) * 0.35) + ((100 - confidence_score) * 0.20))) if total_links else 0

    return {
        "upstream_count": len(upstream),
        "downstream_count": len(downstream),
        "total_links": total_links,
        "approved_count": status_counts["approved"],
        "pending_count": status_counts["pending"],
        "rejected_count": status_counts["rejected"],
        "manual_count": source_counts["manual"],
        "web_source_count": source_counts["web_source"],
        "ai_research_count": source_counts["ai_research"],
        "average_confidence": average_confidence,
        "concentration_score": concentration_score,
        "top_upstream": top_relationships(upstream),
        "top_downstream": top_relationships(downstream),
        "last_verified": max(verified_dates) if verified_dates else "N/A",
        "risk_score": risk_score,
        "supplier_risk": supplier_risk,
        "customer_risk": customer_risk,
        "confidence_score": confidence_score,
        "review_score": review_score,
        "freshness_score": freshness_score,
    }

def publishable_file_mode(path):
    """Keep an existing file's permissions, otherwise 0644 minus the process umask."""
    try:
        return os.stat(path).st_mode & 0o777
    except FileNotFoundError:
        current_umask = os.umask(0)
        os.umask(current_umask)
        return 0o666 & ~current_umask

def write_json_atomic(path, payload):
    """Write JSON via a temp file and rename so a failure never leaves a truncated artifact."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, allow_nan=False)
            handle.write("\n")
        # mkstemp creates 0600; a published artifact must keep world-readable bits.
        os.chmod(temporary_path, publishable_file_mode(path))
        os.replace(temporary_path, path)
    except Exception:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise

def load_link_history(history_path=HISTORY_PATH):
    if not os.path.exists(history_path):
        return []
    try:
        with open(history_path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else payload.get("history", [])

def history_entry_date(entry):
    return entry.get("generated_on") or str(entry.get("generated_at") or "")[:10]

def relationship_identity(relationship):
    return str(relationship.get("relationship_key") or relationship.get("edge_id") or "")

def relationship_direction_from_key(key):
    if "->" not in key:
        return "", ""
    source, rest = key.split("->", 1)
    target = rest.split(":", 1)[0]
    return source, target

def relationship_snapshot_entry(key, relationship):
    source_ticker, target_ticker = relationship_direction_from_key(key)
    return {
        "relationship_key": key,
        "source_ticker": source_ticker,
        "target_ticker": target_ticker,
        "type": relationship.get("type") or "Supply Link",
        "product": relationship.get("product") or relationship.get("type") or "Supply Link",
        "review_status": relationship_status(relationship),
        "confidence": relationship.get("confidence"),
        "source_type": relationship.get("source_type") or "Source",
        "last_verified": relationship.get("last_verified") or "N/A",
    }

def build_change_summary(history, current_snapshot, generated_on=None):
    """Diff the current snapshot against the latest snapshot from an earlier date.

    Export and repair both run within one pipeline invocation, so a same-day entry
    is that run's own intermediate output, not the previous day's published state.
    """
    previous_entry = None
    for entry in reversed(history):
        if generated_on is None or history_entry_date(entry) != generated_on:
            previous_entry = entry
            break
    previous_snapshot = previous_entry.get("links", {}) if previous_entry else {}
    previous_keys = set(previous_snapshot)
    current_keys = set(current_snapshot)
    new_keys = sorted(current_keys - previous_keys)
    removed_keys = sorted(previous_keys - current_keys)
    changed_keys = sorted(
        key for key in current_keys & previous_keys
        if (
            current_snapshot[key].get("review_status") != previous_snapshot[key].get("review_status")
            or current_snapshot[key].get("confidence") != previous_snapshot[key].get("confidence")
        )
    )
    rejected_keys = sorted(
        key for key, link in current_snapshot.items()
        if link.get("review_status") == "rejected"
    )

    def compact(keys, source):
        return [
            {
                "relationship_key": key,
                "source_ticker": source.get(key, {}).get("source_ticker", ""),
                "target_ticker": source.get(key, {}).get("target_ticker", ""),
                "type": source.get(key, {}).get("type", "Supply Link"),
                "product": source.get(key, {}).get("product", "Supply Link"),
                "review_status": source.get(key, {}).get("review_status", "pending"),
                "confidence": source.get(key, {}).get("confidence"),
            }
            for key in keys[:12]
        ]

    return {
        "previous_generated_on": history_entry_date(previous_entry) if previous_entry else None,
        "current_generated_on": generated_on,
        "previous_unique_links": len(previous_keys),
        "current_unique_links": len(current_keys),
        "net_change": len(current_keys) - len(previous_keys),
        "new_count": len(new_keys),
        "removed_count": len(removed_keys),
        "changed_count": len(changed_keys),
        "rejected_count": len(rejected_keys),
        "new_links": compact(new_keys, current_snapshot),
        "removed_links": compact(removed_keys, previous_snapshot),
        "changed_links": compact(changed_keys, current_snapshot),
    }

def persist_link_history(dashboard_data, history_path=HISTORY_PATH, limit=30):
    history = load_link_history(history_path)
    metrics = dashboard_data.get("investor_metrics", {})
    snapshot = metrics.get("relationship_snapshot", {})
    generated_at = dashboard_data.get("generated_at")
    if not snapshot or not generated_at:
        return history
    generated_on = generated_at[:10]
    filtered = [
        entry for entry in history
        if entry.get("generated_on", entry.get("generated_at", "")[:10]) != generated_on
    ]
    filtered.append({
        "generated_at": generated_at,
        "generated_on": generated_on,
        "unique_links": metrics.get("unique_links", 0),
        "approved_links": metrics.get("approved_links", 0),
        "pending_links": metrics.get("pending_links", 0),
        "rejected_links": metrics.get("rejected_links", 0),
        "linked_companies": metrics.get("linked_companies", 0),
        "links": snapshot,
    })
    filtered = filtered[-limit:]
    write_json_atomic(history_path, filtered)
    return filtered

def strip_transient_dashboard_fields(dashboard_data):
    dashboard_data.get("investor_metrics", {}).pop("relationship_snapshot", None)
    return dashboard_data

def publishable_dashboard(dashboard_data):
    """Shallow copy without transient fields, so history can still be persisted from the original."""
    metrics = dashboard_data.get("investor_metrics", {})
    return {
        **dashboard_data,
        "investor_metrics": {key: value for key, value in metrics.items() if key != "relationship_snapshot"},
    }

def publish_dashboard(dashboard_data, dashboard_path=EXPORT_PATH, history_path=HISTORY_PATH):
    """Write the dashboard first, then advance link history, so history never runs ahead of a failed export."""
    write_json_atomic(dashboard_path, publishable_dashboard(dashboard_data))
    persist_link_history(dashboard_data, history_path)
    return strip_transient_dashboard_fields(dashboard_data)

def annotate_dashboard_data(dashboard_data, history_path=HISTORY_PATH):
    companies = []
    unique_relationships = {}
    sector_exposure = []

    for sector, sector_companies in dashboard_data.get("industries", {}).items():
        for company in sector_companies:
            company["sector"] = sector
            company["investor_metrics"] = summarize_company_relationships(company)
            companies.append(company)
            for side in ("upstream", "downstream"):
                for relationship in company.get(side, []):
                    key = relationship.get("relationship_key") or relationship.get("edge_id")
                    if key is not None:
                        unique_relationships.setdefault(str(key), relationship)

    for sector, sector_companies in dashboard_data.get("industries", {}).items():
        linked = [company for company in sector_companies if company.get("investor_metrics", {}).get("total_links", 0) > 0]
        total_links = sum(company.get("investor_metrics", {}).get("total_links", 0) for company in sector_companies)
        sector_exposure.append({
            "sector": sector,
            "companies": len(sector_companies),
            "linked_companies": len(linked),
            "relationship_entries": total_links,
            "coverage": round(len(linked) / len(sector_companies), 3) if sector_companies else 0.0,
        })

    def unique_count(status=None):
        return sum(
            1
            for relationship in unique_relationships.values()
            if status is None or relationship_status(relationship) == status
        )

    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    generated_on = generated_at[:10]
    dashboard_data["generated_at"] = generated_at
    current_snapshot = {
        key: relationship_snapshot_entry(key, relationship)
        for key, relationship in sorted(unique_relationships.items())
        if key
    }
    # Same-day entries are this pipeline run's own intermediate output; the published
    # trend must compare against, and continue from, the previous day's snapshot.
    history = [
        entry for entry in load_link_history(history_path)
        if history_entry_date(entry) != generated_on
    ]
    change_summary = build_change_summary(history, current_snapshot, generated_on)
    linked_companies = sum(
        1
        for company in companies
        if company.get("investor_metrics", {}).get("total_links", 0) > 0
    )
    current_history_entry = {
        "generated_at": generated_at,
        "generated_on": generated_on,
        "unique_links": len(unique_relationships),
        "approved_links": unique_count("approved"),
        "pending_links": unique_count("pending"),
        "rejected_links": unique_count("rejected"),
        "linked_companies": linked_companies,
    }
    dashboard_data["investor_metrics"] = {
        "company_count": len(companies),
        "sector_count": len(dashboard_data.get("industries", {})),
        "unique_links": len(unique_relationships),
        "relationship_entries": sum(
            company.get("investor_metrics", {}).get("total_links", 0)
            for company in companies
        ),
        "approved_links": unique_count("approved"),
        "pending_links": unique_count("pending"),
        "rejected_links": unique_count("rejected"),
        "linked_companies": linked_companies,
        "most_connected": sorted(
            (
                {
                    "ticker": company.get("ticker") or "",
                    "name": company.get("name") or "Unknown",
                    "sector": company.get("sector") or "",
                    "total_links": company.get("investor_metrics", {}).get("total_links", 0),
                    "upstream_count": company.get("investor_metrics", {}).get("upstream_count", 0),
                    "downstream_count": company.get("investor_metrics", {}).get("downstream_count", 0),
                }
                for company in companies
                if company.get("investor_metrics", {}).get("total_links", 0) > 0
            ),
            key=lambda item: item["total_links"],
            reverse=True,
        )[:10],
        "highest_concentration": sorted(
            (
                {
                    "ticker": company.get("ticker") or "",
                    "name": company.get("name") or "Unknown",
                    "sector": company.get("sector") or "",
                    "concentration_score": company.get("investor_metrics", {}).get("concentration_score", 0),
                    "total_links": company.get("investor_metrics", {}).get("total_links", 0),
                }
                for company in companies
                if company.get("investor_metrics", {}).get("total_links", 0) >= 2
            ),
            key=lambda item: (item["concentration_score"], item["total_links"]),
            reverse=True,
        )[:10],
        "sector_exposure": sorted(sector_exposure, key=lambda item: item["relationship_entries"], reverse=True),
        "change_summary": change_summary,
        "history": [
            {
                "generated_at": entry.get("generated_at"),
                "generated_on": history_entry_date(entry),
                "unique_links": entry.get("unique_links", 0),
                "approved_links": entry.get("approved_links", 0),
                "pending_links": entry.get("pending_links", 0),
                "linked_companies": entry.get("linked_companies", 0),
            }
            for entry in history[-13:]
        ] + [current_history_entry],
        "relationship_snapshot": current_snapshot,
    }
    return dashboard_data

PLACEHOLDER_VALUES = {"", "none", "null", "n/a", "uncategorized", "pending update"}

def displayable(value, fallback="N/A"):
    """Yahoo returns the literal string "none" for uncovered names; never publish it."""
    text = str(value or "").strip()
    return fallback if text.lower() in PLACEHOLDER_VALUES else text

def is_ignored_sector(node):
    sector = node.sector if node.sector else "Uncategorized"
    return sector in IGNORED_SECTORS

def should_export_node(node, require_market_data=True):
    if not node or not node.ticker or is_ignored_sector(node):
        return False
    if require_market_data and (not node.market_cap or not node.current_price):
        return False
    if node.market_cap is not None and node.market_cap < MIN_MARKET_CAP:
        return False
    return True

def export_sector(node):
    sector = node.sector if node.sector else "Uncategorized"
    if sector in ("Pending Update", "Uncategorized"):
        return FALLBACK_LINKED_SECTOR
    return sector

def should_export_edge(edge):
    source_url = edge.source_url or ""
    if edge.review_status == "rejected":
        return False
    if edge.review_status == "approved":
        return True
    if "Manual" in source_url:
        return True
    if "AI" in source_url and EXPORT_AI_RESEARCH:
        return True
    return False

def looks_like_tsm_foundry_edge(edge):
    text = " ".join(
        str(value or "")
        for value in (
            edge.dependency_type,
            edge.product,
            edge.evidence_excerpt,
            edge.review_note,
        )
    ).lower()
    return any(term in text for term in FOUNDRY_TERMS)

def canonical_edge_nodes(edge):
    """Return supplier, customer after correcting known foundry direction errors."""
    source = edge.source_node
    target = edge.target_node
    if not source or not target:
        return source, target

    target_ticker = target.ticker or ""
    if (
        target_ticker == "TSM"
        and looks_like_tsm_foundry_edge(edge)
    ):
        return target, source

    return source, target

def review_edge_payload(edge):
    return {
        "edge_id": edge.id,
        "source_ticker": edge.source_node.ticker if edge.source_node else "",
        "source_name": edge.source_node.name if edge.source_node else "",
        "target_ticker": edge.target_node.ticker if edge.target_node else "",
        "target_name": edge.target_node.name if edge.target_node else "",
        "type": edge.dependency_type,
        "product": edge.product or edge.dependency_type,
        "confidence": clean_num(edge.confidence_score),
        "source_url": edge.source_url or "",
        "source_title": edge.source_title or edge.source_url or "",
        "evidence_excerpt": edge.evidence_excerpt or "",
        "review_status": edge.review_status or "pending",
        "review_note": edge.review_note or "",
        "last_verified": edge.last_verified.strftime('%Y-%m-%d') if edge.last_verified else "N/A"
    }

def export_to_json():
    session = SessionLocal()
    try:
        nodes = session.query(Node).all()
        exportable_node_ids = set()
        for edge in session.query(Edge).all():
            if not should_export_edge(edge):
                continue
            supplier_node, customer_node = canonical_edge_nodes(edge)
            if should_export_node(supplier_node, require_market_data=False) and should_export_node(customer_node, require_market_data=False):
                exportable_node_ids.add(supplier_node.id)
                exportable_node_ids.add(customer_node.id)
        pending_edges = session.query(Edge).filter(Edge.review_status == "pending").order_by(
            Edge.confidence_score.desc().nullslast(),
            Edge.id.asc()
        ).limit(REVIEW_QUEUE_LIMIT).all()
        rejected_count = session.query(Edge).filter(Edge.review_status == "rejected").count()
        approved_count = session.query(Edge).filter(Edge.review_status == "approved").count()

        dashboard_data = {
            "industries": {},
            "quality": {
                "pending_count": session.query(Edge).filter(Edge.review_status == "pending").count(),
                "approved_count": approved_count,
                "rejected_count": rejected_count,
                "review_queue": [review_edge_payload(edge) for edge in pending_edges]
            }
        }
        
        for node in nodes:
            if not should_export_node(node) and node.id not in exportable_node_ids:
                continue
                
            sector = export_sector(node)
                
            if sector not in dashboard_data["industries"]:
                dashboard_data["industries"][sector] = []
            
            # --- X-RAY LOGIC ---
            upstream = []
            downstream = []
            seen_edges = set()
            for edge in [*node.supplied_by, *node.supplies_to]:
                if not should_export_edge(edge):
                    continue
                if edge.id in seen_edges:
                    continue
                seen_edges.add(edge.id)

                supplier_node, customer_node = canonical_edge_nodes(edge)
                if supplier_node is None or customer_node is None:
                    # SQLite does not enforce foreign keys here, so an orphaned edge
                    # must be skipped rather than crash the whole export.
                    continue
                supplier_exportable = should_export_node(supplier_node, require_market_data=False)
                customer_exportable = should_export_node(customer_node, require_market_data=False)
                if node.id == customer_node.id and supplier_exportable:
                    upstream.append(edge_payload(edge, supplier_node, supplier_node, customer_node))
                elif node.id == supplier_node.id and customer_exportable:
                    downstream.append(edge_payload(edge, customer_node, supplier_node, customer_node))
            upstream = merge_relationships(upstream)
            downstream = merge_relationships(downstream)
            # ------------------------

            dashboard_data["industries"][sector].append({
                "id": node.id,
                "name": node.name,
                "ticker": node.ticker,
                "industry": displayable(node.industry),
                "price": clean_num(node.current_price),
                "change": clean_num(node.percent_change) or 0.0,
                "market_cap": clean_num(node.market_cap),
                "enterprise_value": clean_num(node.enterprise_value),
                "trailing_pe": clean_num(node.trailing_pe),
                "forward_pe": clean_num(node.forward_pe),
                "price_to_book": clean_num(node.price_to_book),
                "dividend": node.dividend_yield or "N/A",
                "high_52w": clean_num(node.fifty_two_week_high),
                "low_52w": clean_num(node.fifty_two_week_low),
                "revenue": clean_num(node.total_revenue),
                "margin": clean_num(node.gross_margin),
                "target_price": clean_num(node.target_price),
                "recommendation": displayable(node.recommendation),
                "ceo": displayable(node.ceo_name),
                "employees": node.employees,
                "summary": node.business_summary or "No summary available.",
                "last_updated": node.last_updated.strftime('%Y-%m-%d') if node.last_updated else "N/A",
                "upstream": upstream,       # Added to JSON
                "downstream": downstream    # Added to JSON
            })
            
        os.makedirs(DOCS_DIR, exist_ok=True)
        annotate_dashboard_data(dashboard_data, HISTORY_PATH)
        # Pass the module-level paths explicitly so they are read at call time.
        publish_dashboard(dashboard_data, EXPORT_PATH, HISTORY_PATH)

        mode = "manual plus AI research" if EXPORT_AI_RESEARCH else "reviewed/manual only"
        print(f"Export Complete with Supply Chain X-Ray metrics included ({mode}).")
        
    except Exception as e:
        raise SystemExit(f"Error exporting database: {e}") from e
    finally:
        session.close()

if __name__ == "__main__":
    export_to_json()
