from core.predictor import DispositionPredictor

# Mock Data
# 10 days of history. 
# Case 1: 3 consecutive clause 1.
h1 = [{"is_clause1": False, "is_any": False}] * 8 + [{"is_clause1": True, "is_any": True}] * 2
print(f"Case 1 (2 streaks): {DispositionPredictor.analyze(h1)}")

# Case 2: 5 consecutive any.
h2 = [{"is_clause1": False, "is_any": False}] * 6 + [{"is_clause1": False, "is_any": True}] * 4
print(f"Case 2 (4 streaks): {DispositionPredictor.analyze(h2)}")

# Case 3: Accumulated. 
# Window [T-9...T]. Size 10.
# We want T+1 to trigger.
# So we need T-8...T+1 to have 6 hits.
# T-8...T has 9 items.
# Let's say T-8...T has 5 hits.
# T-9 (which drops) was 0.
h3 = [{"is_any":0}, {"is_any":1}, {"is_any":1}, {"is_any":1}, {"is_any":1}, {"is_any":1}, {"is_any":0}, {"is_any":0}, {"is_any":0}, {"is_any":0}]
# T-9=0. T-8=1. T-7=1... T-4=1. (5 hits). T=0.
# T+1 check: Window [T-8...T+1]. (T-8..T) has 5 hits. T+1 assumed hit -> 6. Trigger!
print(f"Case 3 (5 hits, T+1 triggers): {DispositionPredictor.analyze(h3)}")
