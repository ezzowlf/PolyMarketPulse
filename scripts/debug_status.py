"""Debug specialized_names calculation."""

from polymarketpulse.prediction.specialized_router import ALL_SPECIALIZED_MODEL_NAMES
from polymarketpulse.prediction.types import SubmodelEstimate

# Simulate the J.D. Vance submodel estimates
submodel_estimates = [
    SubmodelEstimate(name="history", estimated_yes_probability=0.3857, weight=0.84, available=True, detail=""),
    SubmodelEstimate(name="politics", estimated_yes_probability=0.1, weight=0.1575, available=True, detail=""),
]

available_names = {s.name for s in submodel_estimates if s.available}
independent_names = available_names & {"history", "independent_evidence"}
price_anchored_names = available_names & {"momentum", "news", "event_relations"}

specialized_names = {
    s.name for s in submodel_estimates
    if s.available and s.weight > 0 and s.name in ALL_SPECIALIZED_MODEL_NAMES
}

print(f"available_names: {available_names}")
print(f"independent_names: {independent_names}")
print(f"price_anchored_names: {price_anchored_names}")
print(f"specialized_names: {specialized_names}")
print()
print(f"specialized_names check: {bool(specialized_names)}")
print(f"ALL_SPECIALIZED_MODEL_NAMES: {ALL_SPECIALIZED_MODEL_NAMES}")
print()
print(f"Politics in specialized_names: {'politics' in specialized_names}")
print(f"Politics in ALL_SPECIALIZED_MODEL_NAMES: {'politics' in ALL_SPECIALIZED_MODEL_NAMES}")